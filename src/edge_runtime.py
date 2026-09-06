"""Real-time edge-node control loop and hardware bridge contracts.

The simulation calls :class:`AMRBrain` directly because it is the fastest way to gather
evidence.  This module is the deployment boundary: one operating-system process owns one
brain, one monotonic clock, one UDP peer transport, and one sensor/actuator adapter.

The included JSON/UDP adapter is intentionally small and inspectable.  A Raspberry Pi
deployment can put a ROS2, CAN, serial, or vendor SDK bridge on the other side without
importing those dependencies into the safety/coordination core.  If sensor frames become
stale, the runtime writes an explicit stop and never asks the planner to continue on old
world data.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import socket
import statistics
import time
from dataclasses import dataclass, field, replace
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .amr import AMRBrain, Task
from .release_profile import DEFAULT_ALLOCATION_POLICY, DEFAULT_ROUTE_POLICY
from .scenarios import SCENARIOS
from .settings import DEFAULT, Config
from .site_config import SiteConfigError, load_site_config
from .task_allocation import ALLOCATION_PREASSIGNED
from .terminal_journal import TerminalJournal, TerminalJournalError
from .transport import DEFAULT_GROUP, DEFAULT_PORT, UdpMulticastTransport
from .world import Actuation, Detection, Sensors


class PeerTransport(Protocol):
    stats: dict[str, int]

    def poll(self, max_msgs: int = 256): ...
    def send(self, message) -> None: ...
    def close(self) -> None: ...


class HardwareIO(Protocol):
    """Minimal boundary implemented by a robot driver or a simulation referee."""

    def read_sensors(self) -> tuple[Sensors | None, float | None]:
        """Return the newest frame and its local receive time."""

    def write_actuation(self, actuation: Actuation, t: float) -> None: ...
    def close(self) -> None: ...


class SystemdNotifier:
    """Minimal stdlib implementation of systemd's readiness/watchdog protocol."""

    def __init__(self, address: str | None = None) -> None:
        self.address = os.environ.get("NOTIFY_SOCKET") if address is None else address
        self.sent = 0
        self.failed = 0

    def notify(self, state: str) -> bool:
        if not self.address:
            return False
        address = self.address
        if address.startswith("@"):
            address = "\0" + address[1:]
        sock = None
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            sock.sendto(state.encode("utf-8"), address)
            self.sent += 1
            return True
        except (AttributeError, OSError):
            self.failed += 1
            return False
        finally:
            if sock is not None:
                sock.close()


@dataclass
class EdgeMetrics:
    ticks: int = 0
    sensor_timeouts: int = 0
    deadline_misses: int = 0
    max_loop_s: float = 0.0
    loop_samples_s: list[float] = field(default_factory=list)

    def record_loop(self, duration_s: float, period_s: float) -> None:
        self.ticks += 1
        self.max_loop_s = max(self.max_loop_s, duration_s)
        if len(self.loop_samples_s) < 100_000:
            self.loop_samples_s.append(duration_s)
        if duration_s > period_s:
            self.deadline_misses += 1

    def to_dict(self) -> dict:
        ordered = sorted(self.loop_samples_s)

        def percentile(q: float) -> float:
            if not ordered:
                return 0.0
            index = min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))
            return ordered[index]

        return {
            "ticks": self.ticks,
            "sensor_timeouts": self.sensor_timeouts,
            "deadline_misses": self.deadline_misses,
            "loop_mean_ms": (statistics.fmean(ordered) * 1000.0 if ordered else 0.0),
            "loop_p95_ms": percentile(0.95) * 1000.0,
            "loop_p99_ms": percentile(0.99) * 1000.0,
            "loop_max_ms": self.max_loop_s * 1000.0,
        }


class EdgeRuntime:
    """One brain plus one real peer transport; safe to drive from any hardware I/O."""

    def __init__(self, brain: AMRBrain, transport: PeerTransport,
                 cfg: Config = DEFAULT,
                 terminal_journal: TerminalJournal | None = None) -> None:
        self.brain = brain
        self.transport = transport
        self.cfg = cfg
        self.terminal_journal = terminal_journal
        self._persisted_terminal_records = brain.export_terminal_records()
        self._pending_terminal_records: list[dict] | None = None
        self.metrics = EdgeMetrics()
        self.cycle_metrics = EdgeMetrics()
        self.phase_max_ms: dict[str, float] = {}
        self.scheduling_late_ticks = 0
        self.skipped_schedule_slots = 0
        self.stale_command_stops = 0
        self.late_command_stops = 0
        self.peer_backlog_stops = 0

    def tick(self, local_t: float, sensors: Sensors) -> Actuation:
        started = time.perf_counter()
        cpu_started = time.thread_time()
        local_sensors = replace(sensors, t=local_t)
        inbox = self.transport.poll()
        polled = time.perf_counter()
        actuation, outbox = self.brain.step(local_t, local_sensors, inbox)
        if getattr(self.transport, "receive_degraded", False):
            self.peer_backlog_stops += 1
            actuation = Actuation(v=0.0, omega=0.0, safety_stop=True)
        planned = time.perf_counter()
        for message in outbox:
            self.transport.send(message)
        records = self.brain.export_terminal_records()
        if records != self._persisted_terminal_records:
            self._pending_terminal_records = records
        self.metrics.record_loop(
            time.perf_counter() - started,
            1.0 / self.cfg.rates.safety_hz,
        )
        for name, value in {
            "peer_receive": polled - started,
            "brain": planned - polled,
            "send_and_records": time.perf_counter() - planned,
            "thread_cpu": time.thread_time() - cpu_started,
        }.items():
            self.phase_max_ms[name] = max(self.phase_max_ms.get(name, 0.0), value * 1000)
        return actuation

    def flush_terminal_records(self) -> None:
        """Persist terminal state after the current actuation has already been sent."""
        if self.terminal_journal is None or self._pending_terminal_records is None:
            return
        records = self._pending_terminal_records
        self.terminal_journal.sync(records)
        self._persisted_terminal_records = records
        self._pending_terminal_records = None

    def report(self) -> dict:
        return {
            "robot_id": self.brain.rid,
            "runtime": self.metrics.to_dict(),
            "full_cycle": self.cycle_metrics.to_dict(),
            "phase_max_ms": dict(self.phase_max_ms),
            "scheduling_late_ticks": self.scheduling_late_ticks,
            "skipped_schedule_slots": self.skipped_schedule_slots,
            "stale_command_stops": self.stale_command_stops,
            "late_command_stops": self.late_command_stops,
            "peer_backlog_stops": self.peer_backlog_stops,
            "transport": dict(self.transport.stats),
            "peer_sources": sorted(getattr(self.transport, "accepted_by_source", {})),
            "peer_sources_authenticated": bool(getattr(self.transport, "require_auth", False)),
            "brain": dict(self.brain.stats),
            "state": self.brain.state,
            "task": self.brain.task.tid if self.brain.task else None,
            "completed_tasks": sorted(self.brain.completed_tasks),
            "terminal_journal": (
                dict(self.terminal_journal.stats)
                if self.terminal_journal is not None else None),
        }

    def close(self) -> None:
        try:
            self.flush_terminal_records()
        finally:
            self.transport.close()


class UdpJsonHardwareIO:
    """Non-blocking JSON/UDP bridge for sensors in and actuator commands out.

    Sensor packet schema (SI units): ``pose=[x,y,theta]``, ``v``, ``omega``,
    ``battery_frac``, ``cell=[x,y]``, the four clearance fields, optional detections,
    and optional ``on_dock``.  Actuator packets contain ``v``, ``omega``,
    ``safety_stop``, and the edge-node monotonic timestamp.
    """

    def __init__(self, sensor_host: str, sensor_port: int,
                 actuator_host: str, actuator_port: int,
                 max_packet_bytes: int = 65_535,
                 visual_status: Callable[[], dict] | None = None,
                 max_sensor_frames: int = 64,
                 sensor_poll_budget_s: float = 0.002) -> None:
        if (max_sensor_frames < 1 or not math.isfinite(sensor_poll_budget_s)
                or sensor_poll_budget_s <= 0):
            raise ValueError("sensor receive budgets must be positive and finite")
        self.max_packet_bytes = max_packet_bytes
        self.max_sensor_frames = max_sensor_frames
        self.sensor_poll_budget_s = sensor_poll_budget_s
        self.sensor = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sensor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sensor.bind((sensor_host, sensor_port))
        self.sensor.setblocking(False)
        self.actuator = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.actuator_target = (actuator_host, actuator_port)
        self.stats = {"sensor_frames": 0, "invalid_sensor_frames": 0,
                      "actuator_frames": 0, "actuator_send_failed": 0,
                      "sensor_receive_budget_exhausted": 0}
        self._latest: Sensors | None = None
        self._received_at: float | None = None
        self.visual_status = visual_status
        self._visual_at = float("-inf")
        self._visual_cache: dict = {}
        self._bridge_sample_id: int | None = None

    def read_sensors(self) -> tuple[Sensors | None, float | None]:
        deadline = time.perf_counter() + self.sensor_poll_budget_s
        exhausted = True
        for _ in range(self.max_sensor_frames):
            if time.perf_counter() >= deadline:
                break
            try:
                raw, _source = self.sensor.recvfrom(self.max_packet_bytes + 1)
            except BlockingIOError:
                exhausted = False
                break
            except OSError:
                exhausted = False
                break
            if len(raw) > self.max_packet_bytes:
                self.stats["invalid_sensor_frames"] += 1
                continue
            try:
                frame = json.loads(raw.decode("utf-8"))
                candidate = sensors_from_dict(frame)
                sample_id = frame.get("bridge_sample_id")
                if (sample_id is not None
                        and (type(sample_id) is not int or not 0 <= sample_id <= 2**53 - 1)):
                    raise ValueError("bridge_sample_id must be a bounded nonnegative integer")
            except (TypeError, ValueError, KeyError, UnicodeDecodeError,
                    json.JSONDecodeError):
                self.stats["invalid_sensor_frames"] += 1
                continue
            self._latest = candidate
            self._received_at = time.monotonic()
            self._bridge_sample_id = sample_id
            self.stats["sensor_frames"] += 1
        if exhausted:
            # We did not observe an empty queue. Its tail may contain newer frames;
            # do not describe an arbitrary backlog prefix as a fresh latest sample.
            # Fail closed until a subsequent bounded poll catches up.
            self.stats["sensor_receive_budget_exhausted"] += 1
            self._received_at = None
        return self._latest, self._received_at

    def write_actuation(self, actuation: Actuation, t: float) -> None:
        frame = {
            "v": actuation.v,
            "omega": actuation.omega,
            "safety_stop": actuation.safety_stop,
            "t": t,
        }
        if self._bridge_sample_id is not None:
            # A transport correlation token, never an input to the robot brain.
            # The referee can measure round-trip sensor-to-command age without
            # comparing unsynchronized clocks on separate physical hosts.
            frame["bridge_sample_id"] = self._bridge_sample_id
        if self.visual_status is not None:
            # Send the last bounded telemetry sample; do not run a UI callback
            # between freshness validation and the actual protective command.
            frame["visual_status"] = self._visual_cache
        payload = json.dumps(frame, separators=(",", ":"), allow_nan=False).encode("utf-8")
        try:
            self.actuator.sendto(payload, self.actuator_target)
            self.stats["actuator_frames"] += 1
        except OSError:
            self.stats["actuator_send_failed"] += 1
        if self.visual_status is not None:
            now = time.monotonic()
            if now - self._visual_at >= 0.1:
                self._visual_cache = self.visual_status()
                self._visual_at = now

    def close(self) -> None:
        self.sensor.close()
        self.actuator.close()


def _finite(value, minimum: float, maximum: float) -> float:
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError("numeric sensor value outside allowed range")
    return number


def brain_visual_status(brain: AMRBrain) -> dict:
    """Read-only, bounded telemetry for live rendering; never an input to control."""
    task = brain.task
    return {
        "id": brain.rid, "state": brain.state,
        "task": task.tid if task else None,
        "carry": task.tid if task and brain.goal == task.drop else None,
        "pick": list(task.pick) if task else None,
        "drop": list(task.drop) if task else None,
        "goal": list(brain.goal) if brain.goal else None,
        "cargo_type": task.cargo_type if task else None,
        "done": len(brain.completed),
        "path": [list(cell) for cell in brain.path[brain.pidx:brain.pidx + 8]],
        # Bounded read-only diagnostics distinguish a peer hold from a geometric
        # recovery stall. Local timestamps are not compared across controllers.
        "coordination": {
            "hold": brain._hold,
            "blocked_on": brain.blocked_on,
            "blocked_since": brain.blocked_since,
            "stall_since": brain._stall_since,
            "creep_until": brain._creep_until,
            "recovery_target": (list(brain._cell_repair_target)
                                if brain._cell_repair_target is not None else None),
            "recovery_waypoints": [list(point) for point in brain._recovery_waypoints[:2]],
            "retreat_reason": brain._retreat_for,
        },
    }


def sensors_from_dict(data: object) -> Sensors:
    if not isinstance(data, dict):
        raise TypeError("sensor frame must be an object")
    pose = data["pose"]
    cell = data["cell"]
    if not isinstance(pose, list) or len(pose) != 3:
        raise ValueError("pose must have three entries")
    if (not isinstance(cell, list) or len(cell) != 2
            or any(isinstance(v, bool) or not isinstance(v, int) for v in cell)):
        raise ValueError("cell must contain two integers")
    detections_data = data.get("detections", [])
    if not isinstance(detections_data, list) or len(detections_data) > 1024:
        raise ValueError("invalid detections")
    detections = []
    for item in detections_data:
        if not isinstance(item, dict):
            raise ValueError("detection must be an object")
        detections.append(Detection(
            _finite(item["x"], -1e6, 1e6),
            _finite(item["y"], -1e6, 1e6),
            _finite(item["r"], 0.0, 100.0),
            _finite(item["range_m"], 0.0, 1e6),
            _finite(item.get("vx", 0.0), -100.0, 100.0),
            _finite(item.get("vy", 0.0), -100.0, 100.0),
        ))
    on_dock = data.get("on_dock", False)
    if not isinstance(on_dock, bool):
        raise ValueError("on_dock must be a boolean")
    return Sensors(
        t=0.0,
        pose=tuple(_finite(v, -1e7, 1e7) for v in pose),
        v=_finite(data["v"], -20.0, 20.0),
        omega=_finite(data["omega"], -100.0, 100.0),
        battery_frac=_finite(data["battery_frac"], 0.0, 1.0),
        cell=(cell[0], cell[1]),
        clearance_m=_finite(data["clearance_m"], 0.0, 1e6),
        clearance_static_m=_finite(data.get("clearance_static_m", 99.0), 0.0, 1e6),
        clearance_dynamic_m=_finite(data.get("clearance_dynamic_m", 99.0), 0.0, 1e6),
        clearance_omni_m=_finite(data.get("clearance_omni_m", 99.0), 0.0, 1e6),
        detections=detections,
        on_dock=on_dock,
    )


def sensors_to_dict(sensors: Sensors) -> dict:
    """Encode the vendor-neutral sensor contract using JSON-safe SI units.

    Keeping the inverse next to :func:`sensors_from_dict` prevents a simulator,
    hardware-in-the-loop referee, or vendor adapter from quietly drifting away from
    the packet schema consumed by a deployed edge node.
    """
    return {
        "pose": list(sensors.pose),
        "v": sensors.v,
        "omega": sensors.omega,
        "battery_frac": sensors.battery_frac,
        "cell": list(sensors.cell),
        "clearance_m": sensors.clearance_m,
        "clearance_static_m": sensors.clearance_static_m,
        "clearance_dynamic_m": sensors.clearance_dynamic_m,
        "clearance_omni_m": sensors.clearance_omni_m,
        "detections": [
            {
                "x": detection.x,
                "y": detection.y,
                "r": detection.r,
                "range_m": detection.range_m,
                "vx": detection.vx,
                "vy": detection.vy,
            }
            for detection in sensors.detections
        ],
        "on_dock": sensors.on_dock,
    }


def actuation_from_dict(data: object) -> tuple[Actuation, float]:
    """Validate one actuator packet returned by a deployed BIOS edge node."""
    if not isinstance(data, dict):
        raise TypeError("actuator frame must be an object")
    safety_stop = data["safety_stop"]
    if not isinstance(safety_stop, bool):
        raise ValueError("safety_stop must be a boolean")
    return (
        Actuation(
            v=_finite(data["v"], -20.0, 20.0),
            omega=_finite(data["omega"], -100.0, 100.0),
            safety_stop=safety_stop,
        ),
        _finite(data["t"], -1e12, 1e12),
    )


def run_edge_node(brain: AMRBrain, transport: PeerTransport, hardware: HardwareIO,
                  cfg: Config = DEFAULT, duration_s: float | None = None,
                  sensor_timeout_s: float = 0.20,
                  startup_sensor_wait_s: float = 5.0,
                  clock_offset_s: float = 0.0,
                  terminal_journal: TerminalJournal | None = None,
                  notifier: SystemdNotifier | None = None) -> dict:
    """Run the real-time 50 Hz loop until interrupted or ``duration_s`` elapses."""
    if not math.isfinite(sensor_timeout_s) or sensor_timeout_s <= 0.0:
        raise ValueError("sensor_timeout_s must be positive and finite")
    if not math.isfinite(startup_sensor_wait_s) or startup_sensor_wait_s < 0.0:
        raise ValueError("startup_sensor_wait_s must be non-negative and finite")
    runtime = EdgeRuntime(brain, transport, cfg, terminal_journal)
    stop_requested = False

    def request_stop(_signum, _frame) -> None:
        nonlocal stop_requested
        stop_requested = True

    old_int = signal.signal(signal.SIGINT, request_stop)
    old_term = signal.signal(signal.SIGTERM, request_stop)
    break_signal = getattr(signal, "SIGBREAK", None)
    old_break = (signal.signal(break_signal, request_stop)
                 if break_signal is not None else None)
    period = 1.0 / cfg.rates.safety_hz
    notifier = notifier or SystemdNotifier()
    waiting_started = time.monotonic()
    next_watchdog = waiting_started
    initial_sensor_ready = False
    while (not stop_requested
           and time.monotonic() - waiting_started < startup_sensor_wait_s):
        now = time.monotonic()
        sensors, received_at = hardware.read_sensors()
        if (sensors is not None and received_at is not None
                and now - received_at <= sensor_timeout_s):
            initial_sensor_ready = True
            break
        hardware.write_actuation(
            Actuation(v=0.0, omega=0.0, safety_stop=True), clock_offset_s,
        )
        if now >= next_watchdog:
            notifier.notify("WATCHDOG=1\nSTATUS=Waiting safely for first sensor frame")
            next_watchdog = now + 1.0
        time.sleep(period)
    notifier.notify(
        "READY=1\nSTATUS=" + (
            "BIOS edge node active with fresh sensors"
            if initial_sensor_ready
            else "BIOS edge node active in sensor fail-safe"
        )
    )
    started = time.monotonic()
    next_tick = started
    next_watchdog = started
    try:
        while not stop_requested:
            now = time.monotonic()
            cycle_started = time.perf_counter()
            if now - next_tick > period:
                runtime.scheduling_late_ticks += 1
            if now >= next_watchdog:
                notifier.notify("WATCHDOG=1\nSTATUS=BIOS control loop healthy")
                next_watchdog = now + 1.0
            elapsed = now - started
            if duration_s is not None and elapsed >= duration_s:
                break
            sensors, received_at = hardware.read_sensors()
            local_t = clock_offset_s + elapsed
            sensed_at = time.monotonic()
            if (sensors is None or received_at is None
                    or not 0 <= sensed_at - received_at <= sensor_timeout_s):
                runtime.metrics.sensor_timeouts += 1
                actuation = Actuation(v=0.0, omega=0.0, safety_stop=True)
            else:
                actuation = runtime.tick(local_t, sensors)
            # Coordination/authentication can take longer than expected. Recheck
            # freshness AFTER it, and never transmit a newly selected motion
            # command whose computation has already consumed the control period.
            # This rejects stale output; it does not certify scheduler timing or
            # replace the downstream actuator watchdog / physical safety chain.
            if received_at is not None and time.monotonic() - received_at > sensor_timeout_s:
                runtime.stale_command_stops += 1
                actuation = Actuation(v=0.0, omega=0.0, safety_stop=True)
            if time.perf_counter() - cycle_started > period:
                runtime.late_command_stops += 1
                actuation = Actuation(v=0.0, omega=0.0, safety_stop=True)
            hardware.write_actuation(actuation, local_t)
            # Atomic disk persistence is deliberately after the actuation write: a
            # slow filesystem may delay the next coordination tick, but never the
            # protective command already selected for this sensor frame.
            runtime.flush_terminal_records()
            runtime.cycle_metrics.record_loop(time.perf_counter() - cycle_started, period)
            next_tick += period
            # Do not burst stale catch-up control cycles after an OS/disk stall.
            # Record missed slots explicitly; keep the 20 ms acceptance budget intact.
            lag = time.monotonic() - next_tick
            if lag >= period:
                skipped = int(lag / period)
                runtime.skipped_schedule_slots += skipped
                next_tick += skipped * period
            time.sleep(max(0.0, next_tick - time.monotonic()))
    finally:
        notifier.notify("STOPPING=1\nSTATUS=BIOS edge node stopping safely")
        try:
            hardware.write_actuation(
                Actuation(safety_stop=True),
                clock_offset_s + time.monotonic() - started,
            )
        finally:
            try:
                runtime.close()
            finally:
                hardware.close()
                signal.signal(signal.SIGINT, old_int)
                signal.signal(signal.SIGTERM, old_term)
                if break_signal is not None:
                    signal.signal(break_signal, old_break)
    report = runtime.report()
    report["hardware"] = dict(getattr(hardware, "stats", {}))
    report["service_notify"] = {
        "enabled": bool(notifier.address),
        "sent": notifier.sent,
        "failed": notifier.failed,
    }
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one independent SIH AMR edge node")
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--robot-index", type=int, required=True)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="dense_aisles")
    parser.add_argument("--site-config",
                        help="validated facility map and AMR profile JSON")
    parser.add_argument("--robots", type=int)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--policy", default=DEFAULT_ROUTE_POLICY)
    parser.add_argument("--allocation-policy", default=DEFAULT_ALLOCATION_POLICY)
    parser.add_argument("--group", default=DEFAULT_GROUP)
    parser.add_argument("--peer-port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--interface", default="0.0.0.0")
    parser.add_argument("--sensor-host", default="127.0.0.1")
    parser.add_argument("--sensor-port", type=int, required=True)
    parser.add_argument("--actuator-host", default="127.0.0.1")
    parser.add_argument("--actuator-port", type=int, required=True)
    parser.add_argument("--duration", type=float)
    parser.add_argument("--sensor-timeout", type=float, default=0.20)
    parser.add_argument("--startup-sensor-wait", type=float, default=5.0)
    parser.add_argument("--clock-offset", type=float, default=0.0)
    parser.add_argument("--psk-env", default="SIH_FLEET_PSK")
    parser.add_argument("--allow-unauthenticated", action="store_true")
    parser.add_argument("--report", help="write the final JSON report to this path")
    parser.add_argument("--visual-telemetry", action="store_true",
                        help="include bounded read-only task status in actuator frames")
    parser.add_argument(
        "--terminal-journal",
        help=("completion journal path; default: platform user-state directory "
              "under sih-fleet-priority"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    deployment = None
    if args.site_config:
        try:
            site = load_site_config(args.site_config)
        except SiteConfigError as exc:
            raise SystemExit(f"refusing invalid site configuration: {exc}") from exc
        robots = args.robots if args.robots is not None else len(site.starts)
        if robots != len(site.starts):
            raise SystemExit(
                "--robots must equal the number of starts in --site-config"
            )
        environment = site.environment
        starts = site.starts
        cfg = site.config
        deployment = {
            "source": "site_config",
            "fleet_id": site.fleet_id,
            "map_name": site.environment.name,
            "map_version": site.map_version,
            "map_frame": site.map_frame,
            "robot_model": site.robot_model,
            "site_fingerprint_sha256": site.fingerprint,
        }
    else:
        robots = args.robots if args.robots is not None else 4
        scenario = SCENARIOS[args.scenario](n_robots=robots, seed=args.seed)
        environment = scenario.env
        starts = scenario.starts
        cfg = DEFAULT
        deployment = {
            "source": "built_in_scenario",
            "scenario": args.scenario,
            "seed": args.seed,
        }
    if not 0 <= args.robot_index < robots:
        raise SystemExit("--robot-index must be within the configured fleet")
    start = starts[args.robot_index]
    if args.terminal_journal:
        journal_path = Path(args.terminal_journal)
    else:
        state_root = Path(os.environ.get(
            "XDG_STATE_HOME", Path.home() / ".local" / "state"))
        journal_path = (
            state_root / "sih-fleet-priority" /
            f"{args.robot_id}-terminal.json")
    terminal_journal = TerminalJournal(journal_path)
    try:
        terminal_records = terminal_journal.load()
    except TerminalJournalError as exc:
        raise SystemExit(
            f"refusing to start with an invalid terminal journal: {exc}") from exc
    brain = AMRBrain(
        args.robot_id, environment, cfg, policy=args.policy, home=start,
        allocation_policy=args.allocation_policy,
        terminal_records=terminal_records,
    )
    if args.allocation_policy == ALLOCATION_PREASSIGNED:
        if args.site_config:
            raise SystemExit(
                "preassigned allocation is unavailable with --site-config; "
                "inject live tasks using auction or auction_bundle"
            )
        brain.queue = [Task(**task.__dict__)
                       for task in scenario.assignments[args.robot_index]]
    secret = os.environ.get(args.psk_env)
    if not secret and not args.allow_unauthenticated:
        raise SystemExit(
            f"{args.psk_env} is unset; configure a fleet PSK or pass "
            "--allow-unauthenticated for an isolated development network")
    transport = UdpMulticastTransport(
        args.robot_id, group=args.group, port=args.peer_port,
        interface=args.interface, shared_key=secret,
        require_auth=not args.allow_unauthenticated,
    )
    hardware = UdpJsonHardwareIO(
        args.sensor_host, args.sensor_port,
        args.actuator_host, args.actuator_port,
        visual_status=(lambda: brain_visual_status(brain)) if args.visual_telemetry else None,
    )
    report = run_edge_node(
        brain, transport, hardware, cfg=cfg, duration_s=args.duration,
        sensor_timeout_s=args.sensor_timeout,
        startup_sensor_wait_s=args.startup_sensor_wait,
        clock_offset_s=args.clock_offset,
        terminal_journal=terminal_journal,
    )
    report["deployment"] = deployment
    encoded = json.dumps(report, indent=2, sort_keys=True)
    if args.report:
        Path(args.report).write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
