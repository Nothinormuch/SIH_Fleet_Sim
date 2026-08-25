"""Multi-process referee proving that coordination runs outside the batch simulator.

The parent process is only a physics and lidar referee.  It sends each child its local
sensor frame over an OS pipe and applies the returned wheel command to ``World``.  Every
child owns an independent ``AMRBrain``, clock epoch, task state, replay window, and real
authenticated UDP multicast socket.  The parent never forwards peer traffic and never
chooses a movement, priority, or auction winner.

This is the bridge between fast deterministic evidence and Raspberry Pi deployment:
replace the referee pipe with ``UdpJsonHardwareIO`` (or a ROS2/vendor driver) and run the
same edge runtime on each robot.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import platform
import time
from multiprocessing.connection import Connection

try:  # POSIX only. The deployment target (Raspberry Pi) has it; Windows does not.
    import resource
except ImportError:  # pragma: no cover - platform dependent
    resource = None

from . import messages as msg
from .amr import AMRBrain, POLICY_BIOS_PIBT_V3, Task
from .edge_runtime import EdgeRuntime
from .scenarios import SCENARIOS
from .settings import DEFAULT
from .task_allocation import ALLOCATION_AUCTION, ALLOCATION_PREASSIGNED
from .transport import DEFAULT_GROUP, DEFAULT_PORT, UdpMulticastTransport
from .world import Actuation, Sensors, World


def _robot_worker(connection: Connection, rid: str, env, home,
                  queue: list[Task], policy: str, allocation_policy: str,
                  group: str, port: int, interface: str, shared_key: str,
                  clock_offset_s: float) -> None:
    transport = None
    cpu_started = time.process_time()
    try:
        brain = AMRBrain(
            rid, env, DEFAULT, policy=policy, home=home,
            allocation_policy=allocation_policy,
        )
        if allocation_policy == ALLOCATION_PREASSIGNED:
            brain.queue = [Task(**task.__dict__) for task in queue]
        transport = UdpMulticastTransport(
            rid, group=group, port=port, interface=interface,
            shared_key=shared_key, require_auth=True,
        )
        runtime = EdgeRuntime(brain, transport, DEFAULT)
        connection.send({
            "op": "ready", "pid": os.getpid(),
            "robot_id": rid, "clock_offset_s": clock_offset_s,
            "session_id": transport.session_id,
        })
        while True:
            command = connection.recv()
            if command["op"] == "stop":
                report = runtime.report()
                report.update({
                    "pid": os.getpid(),
                    "clock_offset_s": clock_offset_s,
                    "session_id": transport.session_id,
                    "cpu_time_s": time.process_time() - cpu_started,
                    "max_rss_mb": _max_rss_mb(),
                    "platform": platform.platform(),
                    "python": platform.python_version(),
                })
                connection.send({"op": "report", "report": report})
                break
            if command["op"] != "tick":
                raise ValueError(f"unknown referee command {command['op']!r}")
            sensors = command["sensors"]
            if not isinstance(sensors, Sensors):
                raise TypeError("referee sensor payload is not Sensors")
            local_t = clock_offset_s + float(command["elapsed_s"])
            actuation = runtime.tick(local_t, sensors)
            connection.send({
                "op": "actuation",
                "actuation": actuation,
                "state": brain.state,
                "task": brain.task.tid if brain.task else None,
                "done": len(brain.completed_tasks),
                "msgs_recv": brain.stats["msgs_recv"],
            })
    except BaseException as exc:
        try:
            connection.send({"op": "error", "error": repr(exc),
                             "pid": os.getpid(), "robot_id": rid})
        except (BrokenPipeError, EOFError, OSError):
            pass
        raise
    finally:
        if transport is not None:
            transport.close()
        connection.close()


def _max_rss_mb() -> float:
    """Peak resident set size in MiB.

    This number is reported as evidence, so the Windows branch measures rather
    than returning a convenient 0.0 - a fabricated zero in an evidence field is
    worse than an import error, because nothing ever catches it.
    """
    if resource is not None:
        maximum = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        # ru_maxrss is bytes on macOS and KiB on Linux/Pi.
        divisor = 1024.0 * 1024.0 if platform.system() == "Darwin" else 1024.0
        return round(maximum / divisor, 3)

    import ctypes
    from ctypes import wintypes

    class _MemCounters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]

    # The signatures are declared rather than left to ctypes' defaults: a HANDLE
    # is pointer-sized, and defaulting it to int truncates the pseudo-handle on
    # 64-bit, which fails in a way that looks like the API refusing the call.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.argtypes = []
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE,
                                           ctypes.POINTER(_MemCounters),
                                           wintypes.DWORD]

    counters = _MemCounters()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(),
                                      ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return round(counters.PeakWorkingSetSize / (1024.0 * 1024.0), 3)


def run_distributed_demo(scenario_name: str = "open_floor_control",
                         robots: int = 3, seed: int = 0,
                         duration_s: float = 5.0,
                         policy: str = POLICY_BIOS_PIBT_V3,
                         allocation_policy: str = ALLOCATION_PREASSIGNED,
                         group: str = DEFAULT_GROUP,
                         port: int = DEFAULT_PORT,
                         interface: str = "0.0.0.0",
                         shared_key: str = "local-sih-demo-key",
                         realtime: bool = True) -> dict:
    if robots < 3:
        raise ValueError("distributed SIH demo requires at least three robots")
    if duration_s <= 0.0:
        raise ValueError("duration_s must be positive")
    scenario = SCENARIOS[scenario_name](n_robots=robots, seed=seed)
    world = World(scenario.env, DEFAULT, seed=seed)
    robot_ids = [f"AMR{i + 1:02d}" for i in range(robots)]
    for rid, start in zip(robot_ids, scenario.starts):
        world.add_robot(rid, start)
    for index, route in enumerate(scenario.humans):
        world.add_human(f"H{index + 1}", route)

    context = mp.get_context("spawn")
    children: list[mp.Process] = []
    connections: dict[str, Connection] = {}
    ready: list[dict] = []
    task_source = None
    try:
        for index, rid in enumerate(robot_ids):
            parent, child = context.Pipe()
            process = context.Process(
                target=_robot_worker,
                args=(
                    child, rid, scenario.env, scenario.starts[index],
                    scenario.assignments[index], policy, allocation_policy,
                    group, port, interface, shared_key,
                    # Deliberately unrelated epochs prove that no body timestamp is
                    # compared across hosts as if monotonic clocks were synchronized.
                    10_000.0 * (index + 1),
                ),
                name=f"sih-{rid}",
            )
            process.start()
            child.close()
            children.append(process)
            connections[rid] = parent

        for rid in robot_ids:
            if not connections[rid].poll(10.0):
                raise TimeoutError(f"{rid} did not start within 10 seconds")
            event = connections[rid].recv()
            if event.get("op") != "ready":
                raise RuntimeError(f"{rid} failed to start: {event}")
            ready.append(event)

        if allocation_policy == ALLOCATION_AUCTION:
            task_source = UdpMulticastTransport(
                "WMS", group=group, port=port, interface=interface,
                shared_key=shared_key, require_auth=True,
            )
            tasks = (scenario.unassigned or
                     [task for queue in scenario.assignments for task in queue])
            for sequence, task in enumerate(tasks, 1):
                task_source.send(msg.task_new(
                    "WMS", sequence, 0.0, task.tid, task.pick, task.drop,
                    epoch=0, bid_until=DEFAULT.traffic.auction_bid_window_s,
                ))

        dt = 1.0 / DEFAULT.rates.world_hz
        ticks = max(1, int(duration_s / dt))
        latest: dict[str, dict] = {}
        started = time.monotonic()
        next_tick = started
        for tick in range(ticks):
            elapsed = tick * dt
            for rid in robot_ids:
                connections[rid].send({
                    "op": "tick",
                    "elapsed_s": elapsed,
                    "sensors": world.sense(rid, scenario.pose_noise_m),
                })
            commands: dict[str, Actuation] = {}
            for rid in robot_ids:
                if not connections[rid].poll(2.0):
                    raise TimeoutError(f"{rid} missed its control deadline")
                event = connections[rid].recv()
                if event.get("op") == "error":
                    raise RuntimeError(f"{rid} failed: {event['error']}")
                if event.get("op") != "actuation":
                    raise RuntimeError(f"unexpected {rid} response: {event}")
                commands[rid] = event["actuation"]
                latest[rid] = {key: value for key, value in event.items()
                               if key not in ("op", "actuation")}
            world.step(dt, commands)
            if realtime:
                next_tick += dt
                time.sleep(max(0.0, next_tick - time.monotonic()))

        reports = []
        for rid in robot_ids:
            connections[rid].send({"op": "stop"})
        for rid in robot_ids:
            if not connections[rid].poll(5.0):
                raise TimeoutError(f"{rid} did not return its final report")
            event = connections[rid].recv()
            if event.get("op") != "report":
                raise RuntimeError(f"unexpected final {rid} response: {event}")
            reports.append(event["report"])

        contacts = {
            kind: sum(event.kind == kind for event in world.contacts)
            for kind in ("robot-robot", "robot-human", "robot-rack")
        }
        distinct_pids = len({item["pid"] for item in reports}) == robots
        peer_messages = all(item["brain"]["msgs_recv"] > 0 for item in reports)
        authenticated = all(
            item["transport"]["auth_failed"] == 0
            and item["transport"]["malformed"] == 0
            and item["transport"]["replayed"] == 0
            for item in reports)
        timing_ok = all(
            item["runtime"]["deadline_misses"] == 0
            and item["runtime"]["sensor_timeouts"] == 0
            for item in reports)
        contact_free = all(value == 0 for value in contacts.values())
        return {
            "success": (distinct_pids and peer_messages and authenticated
                        and timing_ok and contact_free),
            "scenario": scenario_name,
            "policy": policy,
            "allocation_policy": allocation_policy,
            "robots": robots,
            "duration_s": duration_s,
            "wall_time_s": time.monotonic() - started,
            "separate_processes": distinct_pids,
            "peer_messages_observed": peer_messages,
            "authenticated_transport": authenticated,
            "control_deadlines_met": timing_ok,
            "clock_offsets_s": [item["clock_offset_s"] for item in reports],
            "contacts": contacts,
            "latest": latest,
            "nodes": reports,
        }
    finally:
        if task_source is not None:
            task_source.close()
        for connection in connections.values():
            connection.close()
        for process in children:
            process.join(timeout=2.0)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2.0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run independent AMR processes against a physics-only referee")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS),
                        default="open_floor_control")
    parser.add_argument("--robots", type=int, default=3)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--duration", type=float, default=5.0)
    parser.add_argument("--policy", default=POLICY_BIOS_PIBT_V3)
    parser.add_argument("--allocation-policy",
                        choices=(ALLOCATION_PREASSIGNED, ALLOCATION_AUCTION),
                        default=ALLOCATION_PREASSIGNED)
    parser.add_argument("--group", default=DEFAULT_GROUP)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--interface", default="0.0.0.0")
    parser.add_argument("--psk-env", default="SIH_FLEET_PSK")
    parser.add_argument("--no-realtime", action="store_true",
                        help="run as fast as IPC permits (less representative)")
    parser.add_argument("--output", help="write the JSON result to this path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    secret = os.environ.get(args.psk_env, "local-sih-demo-key")
    result = run_distributed_demo(
        scenario_name=args.scenario,
        robots=args.robots,
        seed=args.seed,
        duration_s=args.duration,
        policy=args.policy,
        allocation_policy=args.allocation_policy,
        group=args.group,
        port=args.port,
        interface=args.interface,
        shared_key=secret,
        realtime=not args.no_realtime,
    )
    encoded = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        from pathlib import Path
        Path(args.output).write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if result["success"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
