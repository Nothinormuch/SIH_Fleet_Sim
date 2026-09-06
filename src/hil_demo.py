"""Closed-loop software-in-the-loop proof through the real deployment sockets.

Unlike :mod:`src.distributed_demo`, this referee does not use Python pipes or import an
edge runtime into its process.  It launches the public ``edge_node.py`` executable once
per AMR, sends the documented sensor JSON over UDP, receives actuator JSON over UDP,
and applies those commands to the warehouse physics model.  Peer coordination remains
authenticated UDP multicast between the child processes; the referee never forwards a
peer message, assigns a task, chooses a bid, or changes an actuation.

The result is evidence that the production process boundary works.  It is deliberately
called software-in-the-loop rather than Raspberry Pi or physical-safety evidence: run
the same command on a Pi to measure that hardware, and keep a commercial AMR's safety
controller authoritative when replacing this referee with a vendor adapter.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import messages as msg
from .amr import POLICY_BIOS_PIBT_V6
from .edge_runtime import actuation_from_dict, sensors_to_dict
from .scenarios import SCENARIOS
from .settings import DEFAULT
from .task_allocation import (ALLOCATION_AUCTION, ALLOCATION_AUCTION_BUNDLE,
                              ALLOCATION_PREASSIGNED)
from .transport import DEFAULT_GROUP, DEFAULT_PORT, UdpMulticastTransport
from .vendor_adapter import SafeCommandGate
from .world import Actuation, World


@dataclass
class _NodeProcess:
    rid: str
    process: subprocess.Popen
    sensor_target: tuple[str, int]
    actuator_socket: socket.socket
    report_path: Path
    log_path: Path
    log_stream: object
    command_gate: SafeCommandGate = field(default_factory=lambda: SafeCommandGate(
        DEFAULT.robot.v_max, DEFAULT.robot.omega_max,
    ))
    last_actuation: Actuation = field(
        default_factory=lambda: Actuation(safety_stop=True)
    )
    last_actuation_t: float | None = None
    actuator_frames: int = 0
    invalid_actuator_frames: int = 0
    stale_actuator_ticks: int = 0
    actuation_events: list[tuple[float, bool]] = field(default_factory=list)
    visual_status: dict = field(default_factory=dict)


def _encode_sensor_packet(sensors) -> bytes:
    return json.dumps(
        sensors_to_dict(sensors), separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _announce_tasks(source: UdpMulticastTransport, tasks) -> None:
    for sequence, task in enumerate(tasks, 1):
        source.send(msg.task_new(
            "WMS", sequence, 0.0, task.tid, task.pick, task.drop,
            epoch=0, bid_until=DEFAULT.traffic.auction_bid_window_s,
            cargo_type=task.cargo_type, cargo_weight=task.cargo_weight,
            priority=task.priority, deadline=task.deadline,
            generation=task.generation,
            descriptor_hash=task.descriptor_hash or None,
            descriptor_deadline_s=(
                task.descriptor_deadline_s
                if task.descriptor_deadline_s is not None else task.deadline
            ),
        ))


def _receive_actuations(nodes: list[_NodeProcess]) -> None:
    for node in nodes:
        received_fresh = False
        while True:
            try:
                raw, _source = node.actuator_socket.recvfrom(65_536)
            except BlockingIOError:
                break
            except OSError:
                break
            try:
                payload = json.loads(raw.decode("utf-8"))
                actuation, timestamp = actuation_from_dict(payload)
                if (node.last_actuation_t is not None
                        and timestamp < node.last_actuation_t):
                    raise ValueError("actuator timestamp moved backwards")
            except (KeyError, TypeError, ValueError, UnicodeDecodeError,
                    json.JSONDecodeError):
                node.invalid_actuator_frames += 1
                continue
            node.last_actuation = actuation
            status = payload.get("visual_status")
            if isinstance(status, dict) and status.get("id") == node.rid:
                node.visual_status = status
            node.command_gate.accept(actuation, time.monotonic())
            node.last_actuation_t = timestamp
            node.actuator_frames += 1
            node.actuation_events.append((time.monotonic(), actuation.safety_stop))
            received_fresh = True
        if not received_fresh:
            node.stale_actuator_ticks += 1


def _wait_for_hardware_ready(nodes: list[_NodeProcess], world: World,
                             sensor_socket: socket.socket,
                             pose_noise_m: float, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    period = 1.0 / DEFAULT.rates.world_hz
    while time.monotonic() < deadline:
        for node in nodes:
            sensor_socket.sendto(
                _encode_sensor_packet(world.sense(node.rid, pose_noise_m)),
                node.sensor_target,
            )
        time.sleep(period)
        _receive_actuations(nodes)
        failed = [node for node in nodes if node.process.poll() is not None]
        if failed:
            raise RuntimeError(
                "edge node exited during hardware handshake: "
                + ", ".join(node.rid for node in failed)
            )
        if all(node.actuator_frames > 0 for node in nodes):
            for node in nodes:
                # Readiness polling is not part of the measured control window.
                node.stale_actuator_ticks = 0
            return
    missing = [node.rid for node in nodes if node.actuator_frames == 0]
    raise TimeoutError(f"no actuator handshake from {', '.join(missing)}")


def _request_node_stop(process: subprocess.Popen) -> None:
    """Stop the controller gracefully on both platforms, without blocking physics."""
    try:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            process.terminate()
    except OSError:
        process.terminate()


def _stop_nodes(nodes: list[_NodeProcess]) -> tuple[list[dict], list[dict]]:
    reports: list[dict] = []
    failures: list[dict] = []
    for node in nodes:
        if node.process.poll() is None:
            # Windows terminate() bypasses Python's cleanup/report handlers.
            # The process is launched in its own console group so CTRL_BREAK is
            # a scoped graceful stop, with the existing kill timeout as fallback.
            _request_node_stop(node.process)
    for node in nodes:
        try:
            returncode = node.process.wait(timeout=8.0)
        except subprocess.TimeoutExpired:
            node.process.kill()
            returncode = node.process.wait(timeout=3.0)
        try:
            node.log_stream.close()
        except Exception:
            pass
        if returncode == 0 and node.report_path.exists():
            try:
                reports.append(json.loads(node.report_path.read_text(encoding="utf-8")))
                continue
            except (OSError, ValueError) as exc:
                detail = f"invalid report: {exc}"
        else:
            detail = f"exit code {returncode}; report exists={node.report_path.exists()}"
        try:
            log_tail = node.log_path.read_text(encoding="utf-8")[-4000:]
        except OSError:
            log_tail = ""
        failures.append({"robot_id": node.rid, "detail": detail,
                         "log_tail": log_tail})
    return reports, failures


def _raspberry_pi_model() -> str | None:
    """Return a board model only when the Linux device tree identifies a Pi."""
    model_path = Path("/proc/device-tree/model")
    if platform.system() != "Linux" or not model_path.exists():
        return None
    try:
        model = model_path.read_bytes().rstrip(b"\x00").decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return model if "Raspberry Pi" in model else None


def run_hil_demo(
    scenario_name: str = "open_floor_control",
    robots: int = 3,
    seed: int = 0,
    duration_s: float = 20.0,
    policy: str = POLICY_BIOS_PIBT_V6,
    allocation_policy: str = ALLOCATION_AUCTION_BUNDLE,
    group: str = DEFAULT_GROUP,
    peer_port: int = DEFAULT_PORT,
    interface: str = "0.0.0.0",
    sensor_base_port: int = 27_101,
    actuator_base_port: int = 28_101,
    shared_key: str = "local-sih-hil-demo-key",
    require_task_completion: bool = False,
    sensor_cut_robot: str | None = None,
    sensor_cut_at_s: float = 0.0,
    sensor_cut_duration_s: float = 0.0,
    on_snapshot: Callable[[dict], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    sensor_cut_control: Callable[[str], bool] | None = None,
    finish_when_complete: bool = False,
) -> dict:
    """Run N public edge-node executables against a socket-only physics referee."""
    if robots < 3:
        raise ValueError("SIH HIL proof requires at least three robots")
    if duration_s <= 0.0:
        raise ValueError("duration_s must be positive")
    if not 1 <= sensor_base_port <= 65_535 - robots:
        raise ValueError("sensor port range is invalid")
    if not 1 <= actuator_base_port <= 65_535 - robots:
        raise ValueError("actuator port range is invalid")
    if shared_key == "":
        raise ValueError("authenticated HIL proof requires a non-empty shared key")
    ranges = [set(range(sensor_base_port, sensor_base_port + robots)),
              set(range(actuator_base_port, actuator_base_port + robots)), {peer_port}]
    if any(ranges[i] & ranges[j] for i in range(3) for j in range(i)):
        raise ValueError("sensor, actuator and peer ports must not overlap")
    if sensor_cut_robot is not None:
        if sensor_cut_robot not in [f"AMR{index + 1:02d}" for index in range(robots)]:
            raise ValueError("sensor_cut_robot must identify a configured AMR")
        if sensor_cut_at_s < 0.0 or sensor_cut_duration_s <= 0.20:
            raise ValueError("sensor cut must start at or after zero and exceed 0.20 s")
        if sensor_cut_at_s + sensor_cut_duration_s >= duration_s:
            raise ValueError("sensor cut must end before the evidence window")

    scenario = SCENARIOS[scenario_name](n_robots=robots, seed=seed)
    if scenario.robot_restart_at or scenario.partition_at is not None:
        raise ValueError("Socket runner does not yet support restart or network partition events")
    if len(scenario.starts) != robots:
        raise ValueError("scenario starts must match requested process count")
    world = World(scenario.env, DEFAULT, seed=seed)
    world.human_randomized = scenario.human_randomized
    robot_ids = [f"AMR{index + 1:02d}" for index in range(robots)]
    for rid, start in zip(robot_ids, scenario.starts):
        world.add_robot(rid, start)
    for index, route in enumerate(scenario.humans):
        world.add_human(f"H{index + 1}", route)

    tasks = (scenario.unassigned
             or [task for queue in scenario.assignments for task in queue])
    repo_root = Path(__file__).resolve().parent.parent
    # Pin the working source, including uncommitted edits; HEAD alone is insufficient
    # evidence for an experimental run. Capture before any controller is launched.
    source_paths = sorted((repo_root / "src").glob("*.py"))
    source_paths += [repo_root / "edge_node.py"]
    source_hashes = {str(path.relative_to(repo_root)):
                     hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths}
    sensor_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    nodes: list[_NodeProcess] = []
    task_source = None
    temp = tempfile.TemporaryDirectory(prefix="sih-hil-")
    temp_root = Path(temp.name)
    started = time.monotonic()
    reports: list[dict] = []
    process_failures: list[dict] = []
    try:
        for index, rid in enumerate(robot_ids):
            actuator_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            actuator_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            actuator_socket.bind(("127.0.0.1", actuator_base_port + index))
            actuator_socket.setblocking(False)
            report_path = temp_root / f"{rid}-report.json"
            journal_path = temp_root / f"{rid}-terminal.json"
            log_path = temp_root / f"{rid}.log"
            log_stream = log_path.open("w", encoding="utf-8")
            command = [
                sys.executable, str(repo_root / "edge_node.py"),
                "--robot-id", rid,
                "--robot-index", str(index),
                "--robots", str(robots),
                "--scenario", scenario_name,
                "--seed", str(seed),
                "--policy", policy,
                "--allocation-policy", allocation_policy,
                "--group", group,
                "--peer-port", str(peer_port),
                "--interface", interface,
                "--sensor-host", "127.0.0.1",
                "--sensor-port", str(sensor_base_port + index),
                "--actuator-host", "127.0.0.1",
                "--actuator-port", str(actuator_base_port + index),
                "--clock-offset", str(10_000.0 * (index + 1)),
                "--terminal-journal", str(journal_path),
                "--report", str(report_path),
            ]
            child_env = os.environ.copy()
            if on_snapshot is not None:
                command.append("--visual-telemetry")
            child_env["SIH_FLEET_PSK"] = shared_key
            process = subprocess.Popen(
                command, cwd=repo_root, env=child_env,
                stdout=log_stream, stderr=subprocess.STDOUT,
                creationflags=(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                               if os.name == "nt" else 0),
            )
            nodes.append(_NodeProcess(
                rid=rid,
                process=process,
                sensor_target=("127.0.0.1", sensor_base_port + index),
                actuator_socket=actuator_socket,
                report_path=report_path,
                log_path=log_path,
                log_stream=log_stream,
            ))

        task_source = UdpMulticastTransport(
            "WMS", group=group, port=peer_port, interface=interface,
            shared_key=shared_key, require_auth=True,
        )
        _wait_for_hardware_ready(
            nodes, world, sensor_socket, scenario.pose_noise_m,
        )
        if allocation_policy in (ALLOCATION_AUCTION, ALLOCATION_AUCTION_BUNDLE):
            _announce_tasks(task_source, tasks)

        dt = 1.0 / DEFAULT.rates.world_hz
        ticks = max(1, int(duration_s / dt))
        window_started = time.monotonic()
        next_tick = window_started
        cancelled = False
        observed_packets = {rid: 0 for rid in robot_ids}
        observed_completions: set[str] = set()
        recent_packets: list[dict] = []
        sensor_counts = {rid: 0 for rid in robot_ids}
        expected_stops: set[str] = set()
        activated_obstacles: set[str] = set()
        cleared_obstacles: set[str] = set()
        events: list[dict] = []
        work_finished_at: float | None = None
        cut_started_wall: float | None = None
        cut_ended_wall: float | None = None
        for tick in range(ticks):
            if should_stop is not None and should_stop():
                cancelled = True
                break
            elapsed_s = tick * dt
            for node in nodes:
                if (node.rid in scenario.robot_fail_at and node.rid not in expected_stops
                        and elapsed_s >= scenario.robot_fail_at[node.rid]):
                    if node.process.poll() is not None:
                        raise RuntimeError(f"{node.rid} exited before scheduled failure")
                    expected_stops.add(node.rid)
                    _request_node_stop(node.process)
                    events.append({"type": "controller_process_stop", "robot": node.rid,
                                   "t": world.t, "task": node.visual_status.get("task")})
            for event in scenario.obstacles:
                if (event.oid not in activated_obstacles and elapsed_s >= event.appear_at
                        and (event.clear_at is None or elapsed_s < event.clear_at)):
                    if world.add_obstacle(event.oid, event.cell, event.radius_m) is not None:
                        activated_obstacles.add(event.oid)
                        events.append({"type": "obstacle_appeared", "id": event.oid,
                                       "cell": event.cell, "t": world.t})
                if (event.clear_at is not None and elapsed_s >= event.clear_at
                        and event.oid not in cleared_obstacles):
                    world.remove_obstacle(event.oid)
                    cleared_obstacles.add(event.oid)
                    events.append({"type": "obstacle_cleared", "id": event.oid, "t": world.t})
            cut_nodes = set()
            for node in nodes:
                if node.rid in expected_stops:
                    continue
                sensor_is_cut = (
                    node.rid == sensor_cut_robot
                    and sensor_cut_at_s <= elapsed_s
                    < sensor_cut_at_s + sensor_cut_duration_s
                )
                if node.rid == sensor_cut_robot:
                    if sensor_is_cut and cut_started_wall is None:
                        cut_started_wall = time.monotonic()
                    elif not sensor_is_cut and cut_started_wall is not None and cut_ended_wall is None:
                        cut_ended_wall = time.monotonic()
                if sensor_cut_control is not None:
                    sensor_is_cut = sensor_is_cut or sensor_cut_control(node.rid)
                if sensor_is_cut:
                    cut_nodes.add(node.rid)
                    continue
                sensor_socket.sendto(
                    _encode_sensor_packet(
                        world.sense(node.rid, scenario.pose_noise_m)
                    ),
                    node.sensor_target,
                )
                sensor_counts[node.rid] += 1
            next_tick += dt
            time.sleep(max(0.0, next_tick - time.monotonic()))
            _receive_actuations(nodes)
            command_time = time.monotonic()
            commands = {
                node.rid: node.command_gate.command(command_time)
                for node in nodes
            }
            world.step(dt, commands)
            if on_snapshot is not None:
                # Observe a multicast subscription; never forward or inject peer traffic.
                for packet in task_source.poll():
                    if packet.src not in observed_packets:
                        continue
                    observed_packets[packet.src] += 1
                    if packet.type == msg.TASK_DONE:
                        observed_completions.add(str(packet.body.get("task", "")))
                    recent_packets.append({
                        "src": packet.src, "type": packet.type,
                        "seq": packet.seq, "t": round(world.t, 2),
                        "task": packet.body.get("task"),
                    })
                recent_packets = recent_packets[-24:]
                if tick % max(1, round(DEFAULT.rates.world_hz / 10)) == 0 or tick == ticks - 1:
                    on_snapshot({
                        "world": world.snapshot(), "map": world.env.to_json(),
                        "cell_m": DEFAULT.cell_m,
                        "duration_s": duration_s,
                        "scenario": scenario_name, "events": list(events),
                        "human_behavior_events": world.human_behavior_events,
                        "tasks": [{"id": t.tid, "pick": t.pick, "drop": t.drop,
                                   "cargo_type": t.cargo_type} for t in tasks],
                        "observed_completed": sorted(observed_completions),
                        "packets": list(recent_packets),
                        "contacts": {kind: sum(e.kind == kind for e in world.contacts)
                                     for kind in ("robot-robot", "robot-human", "robot-rack")},
                        "nodes": [{
                            "id": n.rid, "pid": n.process.pid,
                            "running": n.process.poll() is None,
                            "sensor_cut": n.rid in cut_nodes,
                            "sensor_frames": sensor_counts[n.rid],
                            "actuator_frames": n.actuator_frames,
                            "peer_packets_observed": observed_packets[n.rid],
                            "visual_status": dict(n.visual_status),
                            "command": {"v": commands[n.rid].v,
                                        "omega": commands[n.rid].omega,
                                        "safety_stop": commands[n.rid].safety_stop},
                        } for n in nodes],
                    })
            failed = [node for node in nodes if node.process.poll() is not None
                      and node.rid not in expected_stops]
            if failed:
                raise RuntimeError(
                    "edge node exited inside evidence window: "
                    + ", ".join(node.rid for node in failed)
                )
            if finish_when_complete and on_snapshot is not None:
                events_complete = (
                    len(expected_stops) == len(scenario.robot_fail_at)
                    and len(activated_obstacles) == len(scenario.obstacles)
                    and all(e.clear_at is None or e.oid in cleared_obstacles
                            for e in scenario.obstacles)
                    and (sensor_cut_robot is None or elapsed_s >=
                         sensor_cut_at_s + sensor_cut_duration_s + 2.0)
                )
                if {t.tid for t in tasks} <= observed_completions and events_complete:
                    work_finished_at = work_finished_at or elapsed_s
                    if elapsed_s - work_finished_at >= 1.0:
                        break
    finally:
        if task_source is not None:
            task_source.close()
        reports, process_failures = _stop_nodes(nodes)
        sensor_socket.close()
        for node in nodes:
            node.actuator_socket.close()

    contacts = {
        kind: sum(event.kind == kind for event in world.contacts)
        for kind in ("robot-robot", "robot-human", "robot-rack")
    }
    completed = sorted({
        task_id for report in reports for task_id in report.get("completed_tasks", [])
    })
    process_pids = {node.rid: node.process.pid for node in nodes}
    distinct_processes = (
        len(reports) == robots
        and len({report.get("robot_id") for report in reports}) == robots
        and len(set(process_pids.values())) == robots
    )
    hardware_boundary = (
        len(reports) == robots
        and all(report.get("hardware", {}).get("sensor_frames", 0) > 0
                and report.get("hardware", {}).get("actuator_frames", 0) > 0
                for report in reports)
        and all(node.actuator_frames > 0 and node.invalid_actuator_frames == 0
                for node in nodes)
    )
    peers_observed = (
        len(reports) == robots
        and all(report.get("brain", {}).get("msgs_recv", 0) > 0
                for report in reports)
    )
    authenticated = (
        len(reports) == robots
        and all(report.get("transport", {}).get("auth_failed", 0) == 0
                and report.get("transport", {}).get("malformed", 0) == 0
                and report.get("transport", {}).get("replayed", 0) == 0
                for report in reports)
    )
    deadlines_met = (
        len(reports) == robots
        and all(report.get("runtime", {}).get("deadline_misses", 0) == 0
                and report.get("full_cycle", {}).get("deadline_misses", 0) == 0
                and report.get("scheduling_late_ticks", 0) == 0
                and report.get("skipped_schedule_slots", 0) == 0
                for report in reports)
    )
    task_gate = (not require_task_completion or len(completed) == len(tasks))
    contact_free = all(value == 0 for value in contacts.values())
    event_coverage = (len(activated_obstacles) == len(scenario.obstacles)
                      and len(expected_stops) == len(scenario.robot_fail_at))
    failed_active_tasks = [r.get("task") for r in reports if r.get("robot_id") in expected_stops]
    failure_work_recovered = all(tid and tid in completed for tid in failed_active_tasks)
    event_gate = event_coverage and failure_work_recovered
    sensor_cut_evidence = None
    sensor_cut_gate = True
    if sensor_cut_robot is not None:
        target = next(node for node in nodes if node.rid == sensor_cut_robot)
        relative_events = [
            (event_t - window_started, safety_stop)
            for event_t, safety_stop in target.actuation_events
            if event_t >= window_started
        ]
        actual_cut = (cut_started_wall - window_started
                      if cut_started_wall is not None else None)
        actual_return = (cut_ended_wall - window_started
                         if cut_ended_wall is not None else None)
        moving_before_cut = actual_cut is not None and any(
            not stopped and actual_cut - 0.5 <= event_t < actual_cut
            for event_t, stopped in relative_events)
        stop_events = [
            event_t for event_t, safety_stop in relative_events
            if safety_stop and actual_cut is not None and event_t >= actual_cut
            and (actual_return is None or event_t < actual_return)
        ]
        first_stop = min(stop_events) if stop_events else None
        recovered = any(
            not safety_stop
            and actual_return is not None and event_t >= actual_return
            for event_t, safety_stop in relative_events
        )
        response_s = None if first_stop is None else first_stop - actual_cut
        sensor_cut_gate = (
            response_s is not None
            and response_s <= 0.30
            and recovered
            and moving_before_cut
        )
        sensor_cut_evidence = {
            "robot_id": sensor_cut_robot,
            "cut_at_s": sensor_cut_at_s,
            "cut_duration_s": sensor_cut_duration_s,
            "actual_cut_wall_s": actual_cut,
            "actual_return_wall_s": actual_return,
            "non_stop_command_observed_before_cut": moving_before_cut,
            "first_safety_stop_after_cut_s": first_stop,
            "response_s": response_s,
            "recovered_after_sensor_return": recovered,
            "pass": sensor_cut_gate,
        }
    success = all((distinct_processes, hardware_boundary, peers_observed,
                   authenticated, deadlines_met, contact_free, task_gate,
                   sensor_cut_gate, event_gate, not process_failures, not cancelled))
    pi_model = _raspberry_pi_model()
    result = {
        "success": success,
        "source_sha256": source_hashes,
        "cancelled": cancelled,
        "proof_scope": "closed_loop_software_in_the_loop",
        "physical_amr_tested": False,
        "raspberry_pi_tested": pi_model is not None,
        "raspberry_pi_model": pi_model,
        "host_platform": platform.platform(),
        "scenario": scenario_name,
        "seed": seed,
        "policy": policy,
        "allocation_policy": allocation_policy,
        "robots": robots,
        "duration_s": duration_s,
        "wall_time_s": time.monotonic() - started,
        "process_boundary": "edge_node.py subprocess per AMR",
        "hardware_boundary": "JSON/UDP sensors and actuator commands",
        "controller_boundary_exercised": hardware_boundary,
        "peer_transport": f"authenticated UDP multicast {group}:{peer_port}",
        "referee_selects_winners": False,
        "referee_forwards_peer_messages": False,
        "separate_edge_nodes": distinct_processes,
        "edge_node_pids": process_pids,
        "peer_messages_observed": peers_observed,
        "authenticated_transport": authenticated,
        "control_deadlines_met": deadlines_met,
        "events": events,
        "event_coverage": event_coverage,
        "failed_active_task_ids": failed_active_tasks,
        "failure_work_recovered": failure_work_recovered,
        "human_behavior_events": world.human_behavior_events,
        "humans": len(world.humans),
        "simulation_time_s": world.t,
        "event_scope": "physical obstacle events and controlled process stops; no RF fault emulation",
        "contacts": contacts,
        "tasks_announced": len(tasks),
        "tasks_completed": len(completed),
        "completed_task_ids": completed,
        "full_task_completion_required": require_task_completion,
        "sensor_cut_evidence": sensor_cut_evidence,
        "actuator_bridge": {
            node.rid: {
                "frames": node.actuator_frames,
                "invalid_frames": node.invalid_actuator_frames,
                "ticks_without_fresh_frame": node.stale_actuator_ticks,
                "controller_command_gate": node.command_gate.report(),
            }
            for node in nodes
        },
        "process_failures": process_failures,
        "nodes": reports,
        "claim_boundary": (
            "Proves the executable, socket adapter, authenticated peer transport, "
            "and warehouse physics close the loop on this host. It does not prove "
            "physical safety certification or performance on unmeasured hardware."
        ),
    }
    temp.cleanup()
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run public edge nodes through the deployment sensor/actuator sockets",
    )
    parser.add_argument("--scenario", choices=sorted(SCENARIOS),
                        default="open_floor_control")
    parser.add_argument("--robots", type=int, default=3)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--duration", type=float, default=20.0)
    parser.add_argument("--policy", default=POLICY_BIOS_PIBT_V6)
    parser.add_argument(
        "--allocation-policy",
        choices=(ALLOCATION_PREASSIGNED, ALLOCATION_AUCTION,
                 ALLOCATION_AUCTION_BUNDLE),
        default=ALLOCATION_AUCTION_BUNDLE,
    )
    parser.add_argument("--group", default=DEFAULT_GROUP)
    parser.add_argument("--peer-port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--interface", default="0.0.0.0")
    parser.add_argument("--sensor-base-port", type=int, default=27_101)
    parser.add_argument("--actuator-base-port", type=int, default=28_101)
    parser.add_argument("--psk-env", default="SIH_FLEET_PSK")
    parser.add_argument("--require-task-completion", action="store_true")
    parser.add_argument("--sensor-cut-robot")
    parser.add_argument("--sensor-cut-at", type=float, default=0.0)
    parser.add_argument("--sensor-cut-duration", type=float, default=0.0)
    parser.add_argument("--output", help="write the JSON evidence result")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    shared_key = os.environ.get(args.psk_env, "local-sih-hil-demo-key")
    result = run_hil_demo(
        scenario_name=args.scenario,
        robots=args.robots,
        seed=args.seed,
        duration_s=args.duration,
        policy=args.policy,
        allocation_policy=args.allocation_policy,
        group=args.group,
        peer_port=args.peer_port,
        interface=args.interface,
        sensor_base_port=args.sensor_base_port,
        actuator_base_port=args.actuator_base_port,
        shared_key=shared_key,
        require_task_completion=args.require_task_completion,
        sensor_cut_robot=args.sensor_cut_robot,
        sensor_cut_at_s=args.sensor_cut_at,
        sensor_cut_duration_s=args.sensor_cut_duration,
    )
    encoded = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if result["success"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
