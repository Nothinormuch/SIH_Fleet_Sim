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
from pathlib import Path
from typing import Protocol

from .amr import AMRBrain, POLICY_BIOS_PIBT_V6, Task
from .scenarios import SCENARIOS
from .settings import DEFAULT, Config
from .task_allocation import ALLOCATION_AUCTION, ALLOCATION_PREASSIGNED
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

    def tick(self, local_t: float, sensors: Sensors) -> Actuation:
        started = time.perf_counter()
        local_sensors = replace(sensors, t=local_t)
        inbox = self.transport.poll()
        actuation, outbox = self.brain.step(local_t, local_sensors, inbox)
        for message in outbox:
            self.transport.send(message)
        records = self.brain.export_terminal_records()
        if records != self._persisted_terminal_records:
            self._pending_terminal_records = records
        self.metrics.record_loop(
            time.perf_counter() - started,
            1.0 / self.cfg.rates.safety_hz,
        )
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
            "transport": dict(self.transport.stats),
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
                 max_packet_bytes: int = 65_535) -> None:
        self.max_packet_bytes = max_packet_bytes
        self.sensor = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sensor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sensor.bind((sensor_host, sensor_port))
        self.sensor.setblocking(False)
        self.actuator = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.actuator_target = (actuator_host, actuator_port)
        self.stats = {"sensor_frames": 0, "invalid_sensor_frames": 0,
                      "actuator_frames": 0, "actuator_send_failed": 0}
        self._latest: Sensors | None = None
        self._received_at: float | None = None

    def read_sensors(self) -> tuple[Sensors | None, float | None]:
        while True:
            try:
                raw, _source = self.sensor.recvfrom(self.max_packet_bytes + 1)
            except BlockingIOError:
                break
            except OSError:
                break
            if len(raw) > self.max_packet_bytes:
                self.stats["invalid_sensor_frames"] += 1
                continue
            try:
                candidate = sensors_from_dict(json.loads(raw.decode("utf-8")))
            except (TypeError, ValueError, KeyError, UnicodeDecodeError,
                    json.JSONDecodeError):
                self.stats["invalid_sensor_frames"] += 1
                continue
            self._latest = candidate
            self._received_at = time.monotonic()
            self.stats["sensor_frames"] += 1
        return self._latest, self._received_at

    def write_actuation(self, actuation: Actuation, t: float) -> None:
        payload = json.dumps({
            "v": actuation.v,
            "omega": actuation.omega,
            "safety_stop": actuation.safety_stop,
            "t": t,
        }, separators=(",", ":"), allow_nan=False).encode("utf-8")
        try:
            self.actuator.sendto(payload, self.actuator_target)
            self.stats["actuator_frames"] += 1
        except OSError:
            self.stats["actuator_send_failed"] += 1

    def close(self) -> None:
        self.sensor.close()
        self.actuator.close()


def _finite(value, minimum: float, maximum: float) -> float:
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError("numeric sensor value outside allowed range")
    return number


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


def run_edge_node(brain: AMRBrain, transport: PeerTransport, hardware: HardwareIO,
                  cfg: Config = DEFAULT, duration_s: float | None = None,
                  sensor_timeout_s: float = 0.25,
                  clock_offset_s: float = 0.0,
                  terminal_journal: TerminalJournal | None = None) -> dict:
    """Run the real-time 50 Hz loop until interrupted or ``duration_s`` elapses."""
    runtime = EdgeRuntime(brain, transport, cfg, terminal_journal)
    stop_requested = False

    def request_stop(_signum, _frame) -> None:
        nonlocal stop_requested
        stop_requested = True

    old_int = signal.signal(signal.SIGINT, request_stop)
    old_term = signal.signal(signal.SIGTERM, request_stop)
    period = 1.0 / cfg.rates.safety_hz
    started = time.monotonic()
    next_tick = started
    try:
        while not stop_requested:
            now = time.monotonic()
            elapsed = now - started
            if duration_s is not None and elapsed >= duration_s:
                break
            sensors, received_at = hardware.read_sensors()
            local_t = clock_offset_s + elapsed
            if (sensors is None or received_at is None
                    or now - received_at > sensor_timeout_s):
                runtime.metrics.sensor_timeouts += 1
                actuation = Actuation(v=0.0, omega=0.0, safety_stop=True)
            else:
                actuation = runtime.tick(local_t, sensors)
            hardware.write_actuation(actuation, local_t)
            # Atomic disk persistence is deliberately after the actuation write: a
            # slow filesystem may delay the next coordination tick, but never the
            # protective command already selected for this sensor frame.
            runtime.flush_terminal_records()
            next_tick += period
            time.sleep(max(0.0, next_tick - time.monotonic()))
    finally:
        hardware.write_actuation(Actuation(safety_stop=True),
                                  clock_offset_s + time.monotonic() - started)
        runtime.close()
        hardware.close()
        signal.signal(signal.SIGINT, old_int)
        signal.signal(signal.SIGTERM, old_term)
    report = runtime.report()
    report["hardware"] = dict(getattr(hardware, "stats", {}))
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one independent SIH AMR edge node")
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--robot-index", type=int, required=True)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="dense_aisles")
    parser.add_argument("--robots", type=int, default=4)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--policy", default=POLICY_BIOS_PIBT_V6)
    parser.add_argument("--allocation-policy", default=ALLOCATION_AUCTION)
    parser.add_argument("--group", default=DEFAULT_GROUP)
    parser.add_argument("--peer-port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--interface", default="0.0.0.0")
    parser.add_argument("--sensor-host", default="127.0.0.1")
    parser.add_argument("--sensor-port", type=int, required=True)
    parser.add_argument("--actuator-host", default="127.0.0.1")
    parser.add_argument("--actuator-port", type=int, required=True)
    parser.add_argument("--duration", type=float)
    parser.add_argument("--clock-offset", type=float, default=0.0)
    parser.add_argument("--psk-env", default="SIH_FLEET_PSK")
    parser.add_argument("--allow-unauthenticated", action="store_true")
    parser.add_argument("--report", help="write the final JSON report to this path")
    parser.add_argument(
        "--terminal-journal",
        help=("completion journal path; default: platform user-state directory "
              "under sih-fleet-priority"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 0 <= args.robot_index < args.robots:
        raise SystemExit("--robot-index must be within the configured fleet")
    scenario = SCENARIOS[args.scenario](n_robots=args.robots, seed=args.seed)
    start = scenario.starts[args.robot_index]
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
        args.robot_id, scenario.env, DEFAULT, policy=args.policy, home=start,
        allocation_policy=args.allocation_policy,
        terminal_records=terminal_records,
    )
    if args.allocation_policy == ALLOCATION_PREASSIGNED:
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
    )
    report = run_edge_node(
        brain, transport, hardware, duration_s=args.duration,
        clock_offset_s=args.clock_offset,
        terminal_journal=terminal_journal,
    )
    encoded = json.dumps(report, indent=2, sort_keys=True)
    if args.report:
        Path(args.report).write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
