"""Two-host software deployment proof; no fleet planner runs in the referee.

LAN sensor/actuator frames have session-bound HMAC authentication and strict sequence
numbers. Local driver UDP sockets remain loopback-only. This is integrity/authenticity,
not encryption or protection against a compromised host possessing the shared key.
Robot peer packets never pass through this bridge.
"""

from __future__ import annotations

import argparse
from collections import deque
import hashlib
import hmac
import ipaddress
import json
import math
import os
from pathlib import Path
import platform
import secrets
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import time

from .amr import POLICY_BIOS_PIBT_V6
from . import messages as msg
from .edge_runtime import actuation_from_dict, sensors_to_dict
from .hil_demo import _announce_tasks, _raspberry_pi_model
from .scenarios import SCENARIOS, workload_fingerprint
from .settings import DEFAULT, NetSpec
from .task_allocation import ALLOCATION_AUCTION_BUNDLE
from .task_protocol import CompletionCertificate, task_descriptor_hash
from .transport import DEFAULT_GROUP, UdpMulticastTransport
from .vendor_adapter import SafeCommandGate
from .world import World

PROTOCOL = 1
MAX_FRAME = 2_000_000
ROOT = Path(__file__).resolve().parent.parent


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def source_fingerprint(root: Path = ROOT) -> str:
    """Pin controller, simulation and bridge source, not merely a possibly dirty HEAD."""
    paths = sorted((root / "src").glob("*.py"))
    paths += [root / "edge_node.py", root / "multihost_demo.py"]
    hashes = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in paths}
    return hashlib.sha256(_json(hashes)).hexdigest()


def config_fingerprint(config: dict) -> str:
    return hashlib.sha256(_json(config)).hexdigest()


def _scenario(config: dict):
    return SCENARIOS[config["scenario"]](n_robots=config["robots"], seed=config["seed"])


def _supported_scenario(sc, robots: int) -> None:
    if (len(sc.starts) != robots or sc.partition_at is not None or sc.robot_restart_at
            or sc.kill_manager_at is not None or sc.net != NetSpec()):
        raise ValueError("scenario has unsupported network/manager/restart events for the real LAN runner")


def _completion_owner(packet, task) -> str | None:
    """An authenticated direct owner must bind its completion to this exact job."""
    certificate = CompletionCertificate.from_mapping(packet.body)
    if certificate is None or certificate.owner != packet.src or packet.body.get("relay"):
        return None
    deadline = (task.descriptor_deadline_s if task.descriptor_deadline_s is not None
                else task.deadline)
    identity = task.descriptor_hash or task_descriptor_hash(
        task.tid, task.generation, task.pick, task.drop, task.cargo_type,
        task.cargo_weight, task.priority, deadline)
    if certificate.key != (task.tid, task.generation, identity):
        return None
    return certificate.owner


def _timing_pass(report: dict) -> bool:
    """Missing measurements and non-finite values cannot become zero by default."""
    for name in ("runtime", "full_cycle"):
        row = report.get(name)
        if not isinstance(row, dict) or type(row.get("ticks")) is not int or row["ticks"] <= 0:
            return False
        for field in ("deadline_misses", "loop_max_ms"):
            value = row.get(field)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                return False
        if row["deadline_misses"] != 0 or row["loop_max_ms"] > 1000 / DEFAULT.rates.safety_hz:
            return False
    return all(type(report.get(field)) is int and report[field] == 0
               for field in ("scheduling_late_ticks", "skipped_schedule_slots"))


def make_config(mac_ip: str, windows_ip: str, robots: int = 3,
                scenario: str = "deployment_socket_acceptance", duration_s: float = 25,
                seed: int = 0, policy: str = POLICY_BIOS_PIBT_V6,
                bridge_port: int = 29600, peer_port: int = 29601,
                sensor_cut: bool = True) -> dict:
    if not 3 <= robots <= 10:
        raise ValueError("use 3 through 10 AMRs for the live two-host proof")
    for address in (mac_ip, windows_ip):
        if ipaddress.ip_address(address).version != 4:
            raise ValueError("IPv4 addresses are required")
    if not math.isfinite(duration_s) or not 3 <= duration_s <= 1800:
        raise ValueError("duration must be between 3 and 1800 seconds")
    if not 1024 <= bridge_port <= 65535 or not 1024 <= peer_port <= 65535:
        raise ValueError("ports must be between 1024 and 65535")
    if bridge_port == peer_port:
        raise ValueError("use distinct bridge and peer ports")
    if sensor_cut and duration_s < 8:
        raise ValueError("sensor-cut proof needs at least 8 seconds")
    local_count = 2 if robots == 3 else (robots + 1) // 2
    config = {
        "protocol": PROTOCOL, "session": secrets.token_hex(16),
        "referee_ip": mac_ip, "bridge_port": bridge_port, "peer_port": peer_port,
        "group": DEFAULT_GROUP, "robots": robots, "scenario": scenario,
        "duration_s": duration_s, "seed": seed, "policy": policy,
        "allocation_policy": ALLOCATION_AUCTION_BUNDLE,
        "hosts": {
            "mac": {"ip": mac_ip, "indices": list(range(local_count))},
            "windows": {"ip": windows_ip, "indices": list(range(local_count, robots))},
        },
        "sensor_cut": ({"robot": f"AMR{local_count + 1:02d}", "at_s": 3.0,
                        "duration_s": 2.0} if sensor_cut else None),
        "source_sha256": source_fingerprint(),
    }
    sc = _scenario(config)
    _supported_scenario(sc, robots)
    config["workload_sha256"] = workload_fingerprint(sc, DEFAULT,
                                                     config["allocation_policy"])
    return config


def validate_config(config: dict) -> None:
    if config.get("protocol") != PROTOCOL:
        raise ValueError("incompatible multi-host protocol")
    if config.get("source_sha256") != source_fingerprint():
        raise ValueError("source mismatch: copy the exact candidate to both computers")
    sc = _scenario(config)
    if config.get("workload_sha256") != workload_fingerprint(
            sc, DEFAULT, config["allocation_policy"]):
        raise ValueError("scenario/settings mismatch; refusing different worlds")
    indices = [i for host in config["hosts"].values() for i in host["indices"]]
    if sorted(indices) != list(range(config["robots"])):
        raise ValueError("each AMR must belong to exactly one host")
    _supported_scenario(sc, config["robots"])


class AuthChannel:
    """Bounded newline framing, per-direction HMAC and strictly increasing sequences."""

    def __init__(self, sock: socket.socket, key: bytes, context: str, role: str):
        self.sock, self.key, self.context, self.role = sock, key, context, role
        self.send_seq = self.recv_seq = 0
        self.buffer = bytearray()
        self.disconnected = False
        self.pending_frames: list[dict] = []
        self.sock.settimeout(0.25)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def encode(self, payload: dict) -> bytes:
        body = {"context": self.context, "role": self.role,
                "seq": self.send_seq, "payload": payload}
        encoded = _json(body)
        frame = _json({"body": body,
                       "mac": hmac.new(self.key, encoded, hashlib.sha256).hexdigest()})
        if len(frame) > MAX_FRAME:
            raise ValueError("bridge frame exceeds bound")
        self.send_seq += 1
        return frame + b"\n"

    def decode(self, raw: bytes) -> dict:
        if len(raw) > MAX_FRAME:
            raise ValueError("bridge frame exceeds bound")
        frame = json.loads(raw)
        body = frame["body"]
        expected = hmac.new(self.key, _json(body), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, frame["mac"]):
            raise ValueError("bridge authentication failed")
        if body["context"] != self.context or body["role"] == self.role:
            raise ValueError("wrong bridge session or reflected frame")
        if (body["role"] not in ("agent", "referee") or type(body["seq"]) is not int
                or body["seq"] != self.recv_seq):
            raise ValueError("bridge replay or sequence gap")
        if not isinstance(body["payload"], dict):
            raise ValueError("bridge payload must be an object")
        self.recv_seq += 1
        return body["payload"]

    def send(self, payload: dict) -> None:
        self.sock.sendall(self.encode(payload))

    def poll(self) -> list[dict]:
        import select
        if self.pending_frames:
            frames, self.pending_frames = self.pending_frames, []
            return frames
        if self.disconnected:
            raise ConnectionError("bridge connection closed")
        frames = []
        read_bytes = 0
        deadline = time.perf_counter() + .003
        while len(frames) < 32 and read_bytes < 262_144 and time.perf_counter() < deadline:
            if b"\n" in self.buffer:
                raw, _, remaining = self.buffer.partition(b"\n")
                self.buffer = bytearray(remaining)
                frames.append(self.decode(raw))
                continue
            if not select.select([self.sock], [], [], 0)[0]:
                break
            try:
                data = self.sock.recv(65536)
            except ConnectionResetError:
                self.disconnected = True
                if frames:
                    break  # retain a decoded final report before surfacing the reset
                raise
            if not data:
                self.disconnected = True
                if frames:
                    break
                raise ConnectionError("bridge connection closed")
            read_bytes += len(data)
            self.buffer.extend(data)
            if len(self.buffer) > MAX_FRAME:
                raise ValueError("unterminated bridge frame exceeds bound")
        return frames

    def close(self) -> None:
        self.sock.close()


def _line(sock: socket.socket) -> dict:
    raw = bytearray()
    deadline = time.monotonic() + 5
    while not raw.endswith(b"\n"):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("absolute handshake deadline exceeded")
        sock.settimeout(remaining)
        data = sock.recv(1)
        if not data:
            raise ConnectionError("handshake disconnected")
        raw.extend(data)
        if len(raw) > 8192:
            raise ValueError("oversized bridge handshake")
    return json.loads(raw)


def _proof(key: bytes, value: dict) -> str:
    return hmac.new(key, _json(value), hashlib.sha256).hexdigest()


def accept_agent(sock: socket.socket, address: str, config: dict,
                 key: bytes) -> tuple[str, AuthChannel]:
    if address not in {row["ip"] for row in config["hosts"].values()}:
        raise ValueError("unconfigured host IP")
    sock.settimeout(5)
    challenge = {"protocol": PROTOCOL, "session": config["session"],
                 "nonce": secrets.token_hex(32), "config": config_fingerprint(config)}
    sock.sendall(_json(challenge) + b"\n")
    hello = _line(sock)
    body = hello["body"]
    host = body["host"]
    if (host not in config["hosts"] or address != config["hosts"][host]["ip"]
            or body["challenge"] != challenge
            or not hmac.compare_digest(hello["mac"], _proof(key, body))):
        raise ValueError("host identity/config/session authentication failed")
    context = hashlib.sha256(_json(body)).hexdigest()
    channel = AuthChannel(sock, key, context, "referee")
    channel.send({"type": "authenticated", "host": host})
    return host, channel


def connect_agent(config: dict, host: str, key: bytes) -> AuthChannel:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    sock.bind((config["hosts"][host]["ip"], 0))
    sock.connect((config["referee_ip"], config["bridge_port"]))
    challenge = _line(sock)
    if (challenge.get("protocol") != PROTOCOL
            or challenge.get("session") != config["session"]
            or challenge.get("config") != config_fingerprint(config)):
        sock.close()
        raise ValueError("referee configuration/session mismatch")
    body = {"host": host, "challenge": challenge, "nonce": secrets.token_hex(32)}
    sock.sendall(_json({"body": body, "mac": _proof(key, body)}) + b"\n")
    channel = AuthChannel(sock, key, hashlib.sha256(_json(body)).hexdigest(), "agent")
    # Verify the server also possesses the key BEFORE spawning any controller.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        frames = channel.poll()
        if frames:
            if frames[0] != {"type": "authenticated", "host": host}:
                raise ValueError("unexpected authentication acknowledgement")
            channel.pending_frames.extend(frames[1:])
            return channel
        time.sleep(0.005)
    channel.close()
    raise TimeoutError("referee authentication timed out")


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        process.kill()
        process.wait(timeout=3)


def _free_udp() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.setblocking(False)
    return sock


def _reserve_sensor(config: dict, index: int) -> socket.socket:
    """Keep sensor candidates outside our OS-chosen ephemeral actuator range.

    Releasing an ephemeral reservation then immediately asking the OS for another
    actuator socket can reuse the not-yet-bound child's sensor port. Deterministic
    disjoint robot offsets avoid that self-race on the supported Mac/Windows hosts.
    This is not a claim against another local program deliberately racing the bind.
    """
    base = 20_000 + (int(config["session"], 16) % 200) * 16
    for attempt in range(16):
        port = base + attempt * 3200 + index
        if port >= 49_152 or port == config["peer_port"]:
            continue
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(("127.0.0.1", port))
            return sock
        except OSError:
            sock.close()
    raise RuntimeError(f"no free bounded sensor port for AMR{index + 1:02d}")


def run_agent(config: dict, host: str, key: bytes) -> dict:
    validate_config(config)
    channel = connect_agent(config, host, key)
    nodes: dict[str, dict] = {}
    sender = _free_udp()
    failure: str | None = None
    stop_expected: set[str] = set()
    temp = tempfile.TemporaryDirectory(prefix="bios-multihost-")
    temp_root = Path(temp.name)
    try:
        for index in config["hosts"][host]["indices"]:
            rid = f"AMR{index + 1:02d}"
            actuator = _free_udp()
            reserved_sensor = _reserve_sensor(config, index)
            sensor_port = reserved_sensor.getsockname()[1]
            reserved_sensor.close()
            report_path = temp_root / f"{rid}.json"
            log = (temp_root / f"{rid}.log").open("w", encoding="utf-8")
            command = [sys.executable, str(ROOT / "edge_node.py"),
                       "--robot-id", rid, "--robot-index", str(index),
                       "--robots", str(config["robots"]),
                       "--scenario", config["scenario"], "--seed", str(config["seed"]),
                       "--policy", config["policy"],
                       "--allocation-policy", config["allocation_policy"],
                       "--group", config["group"], "--peer-port", str(config["peer_port"]),
                       "--interface", config["hosts"][host]["ip"],
                       "--sensor-host", "127.0.0.1", "--sensor-port", str(sensor_port),
                       "--actuator-host", "127.0.0.1", "--actuator-port",
                       str(actuator.getsockname()[1]), "--visual-telemetry",
                       "--terminal-journal", str(temp_root / f"{rid}-terminal.json"),
                       "--report", str(report_path),
                       "--duration", str(config["duration_s"] + 180)]
            child_env = os.environ.copy()
            child_env["SIH_FLEET_PSK"] = key.decode("utf-8")
            process = subprocess.Popen(command, cwd=ROOT, env=child_env,
                                       stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP
                                                      if os.name == "nt" else 0))
            nodes[rid] = {"process": process, "socket": actuator,
                          "sensor_port": sensor_port, "report": report_path, "log": log,
                          "frames": 0, "invalid": 0, "startup_stops_ignored": 0}
        validate_config(config)  # refuse edits that raced controller startup
        channel.send({"type": "started", "host": host, "platform": platform.platform(),
                      "hostname": platform.node(), "python_version": platform.python_version(),
                      "pi_model": _raspberry_pi_model(),
                      "pids": {rid: n["process"].pid for rid, n in nodes.items()}})
        last_input = time.monotonic()
        last_sent = 0.0
        while True:
            stop = False
            for frame in channel.poll():
                last_input = time.monotonic()
                if frame["type"] == "stop":
                    stop = True
                    break
                if frame["type"] != "sensors":
                    raise ValueError("unexpected referee message")
                for rid in frame.get("stop_nodes", []):
                    if rid not in nodes:
                        raise ValueError("referee tried to stop a foreign robot")
                    stop_expected.add(rid)
                    _stop_process(nodes[rid]["process"])
                for rid, payload in frame["sensors"].items():
                    if rid not in nodes or rid in stop_expected:
                        raise ValueError("sensor frame is bound to another/stopped robot")
                    sender.sendto(_json(payload), ("127.0.0.1", nodes[rid]["sensor_port"]))
            if stop:
                break
            actuations = {}
            for rid, node in nodes.items():
                receive_deadline = time.perf_counter() + .002
                for _ in range(64):
                    if time.perf_counter() >= receive_deadline:
                        break
                    try:
                        raw, source = node["socket"].recvfrom(65536)
                    except BlockingIOError:
                        break
                    try:
                        if source[0] != "127.0.0.1":
                            raise ValueError("non-loopback driver packet")
                        payload = json.loads(raw)
                        command, _ = actuation_from_dict(payload)
                        if payload.get("visual_status", {}).get("id") != rid:
                            if (not payload.get("visual_status") and node["frames"] == 0
                                    and command.safety_stop):
                                # Visual metadata is intentionally produced after
                                # the protective write. Its first empty-cache stop
                                # is harmless but cannot satisfy robot readiness.
                                node["startup_stops_ignored"] += 1
                                continue
                            raise ValueError("actuator identity mismatch")
                        actuations[rid] = payload
                        node["frames"] += 1
                    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
                        node["invalid"] += 1
                if node["process"].poll() is not None and rid not in stop_expected:
                    raise RuntimeError(f"{rid} controller exited unexpectedly")
            now = time.monotonic()
            if actuations or now - last_sent >= 0.2:
                channel.send({"type": "actuations", "frames": actuations})
                last_sent = now
            if now - last_input > 2:
                raise TimeoutError("referee lost; stopping every local controller")
            time.sleep(0.003)
    except (OSError, ValueError, KeyError, RuntimeError, KeyboardInterrupt) as exc:
        failure = f"{type(exc).__name__}: {exc}"
    finally:
        for node in nodes.values():
            _stop_process(node["process"])
            node["log"].close()
            node["socket"].close()
        sender.close()
    reports, missing = [], []
    for rid, node in nodes.items():
        try:
            report = json.loads(node["report"].read_text(encoding="utf-8"))
            report["bridge"] = {"frames": node["frames"], "invalid_frames": node["invalid"],
                                "startup_stops_ignored": node["startup_stops_ignored"]}
            reports.append(report)
        except (OSError, ValueError):
            missing.append({"robot": rid, "exit_code": node["process"].returncode,
                            "log_tail": (temp_root / f"{rid}.log").read_text()[-2000:]})
    result = {"type": "report", "host": host, "nodes": reports, "missing": missing,
              "failure": failure, "expected_stops": sorted(stop_expected)}
    try:
        channel.send(result)
    except OSError:
        pass
    channel.close()
    temp.cleanup()
    return result


def run_referee(config: dict, key: bytes, ready_timeout_s: float = 120,
                on_snapshot=None) -> dict:
    validate_config(config)
    sc = _scenario(config)
    world = World(sc.env, DEFAULT, seed=config["seed"])
    world.human_randomized = sc.human_randomized
    ids = [f"AMR{i + 1:02d}" for i in range(config["robots"])]
    for i, rid in enumerate(ids):
        world.add_robot(rid, sc.starts[i])
        if i < len(sc.initial_battery_fracs):
            world.robots[rid].battery_wh = sc.initial_battery_fracs[i] * DEFAULT.robot.battery_full_wh
    for i, route in enumerate(sc.humans):
        world.add_human(f"H{i + 1}", route)
    tasks = sc.unassigned or [t for queue in sc.assignments for t in queue]
    task_catalog = {task.tid: task for task in tasks}
    owners = {f"AMR{i + 1:02d}": host for host, row in config["hosts"].items()
              for i in row["indices"]}
    gates = {rid: SafeCommandGate(DEFAULT.robot.v_max, DEFAULT.robot.omega_max) for rid in ids}
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((config["referee_ip"], config["bridge_port"]))
    listener.listen(4)
    listener.settimeout(0.1)
    channels, started_hosts, host_reports = {}, {}, {}
    last_seen, last_stamp, latest_status = {}, {}, {}
    events, command_events = [], {rid: deque(maxlen=100_000) for rid in ids}
    motion_events = {rid: deque(maxlen=100_000) for rid in ids}
    completion_events: list[dict] = []
    sample_ids = dict.fromkeys(ids, 0)
    sample_sent = {rid: {} for rid in ids}
    sample_ages = {rid: deque(maxlen=100_000) for rid in ids}
    stale_motion = dict.fromkeys(ids, 0)
    expected_stale_motion = dict.fromkeys(ids, 0)
    frame_counts = dict.fromkeys(ids, 0)
    expected_stops: set[str] = set()
    obstacles_added: set[str] = set()
    obstacles_removed: set[str] = set()
    cut_start = cut_end = None
    max_tick_lag_s = 0.0
    referee_late_ticks = 0
    failure = None
    wms = None
    peer_observed = dict.fromkeys(ids, 0)
    observed_completed: set[str] = set()
    packet_trace = deque(maxlen=24)
    window_started = None
    ready_started = time.monotonic()

    def receive():
        for host, channel in channels.items():
            for frame in channel.poll():
                last_seen[host] = time.monotonic()
                if frame["type"] == "started":
                    expected = {rid for rid in ids if owners[rid] == host}
                    if set(frame["pids"]) != expected or host in started_hosts:
                        raise ValueError("duplicate or wrong controller readiness roster")
                    started_hosts[host] = frame
                elif frame["type"] == "actuations":
                    for rid, payload in frame["frames"].items():
                        if owners.get(rid) != host:
                            raise ValueError("host attempted to command a foreign robot")
                        command, stamp = actuation_from_dict(payload)
                        sample_id = payload.get("bridge_sample_id")
                        sent_at = sample_sent[rid].get(sample_id)
                        if sent_at is None:
                            if command.safety_stop:
                                gates[rid].accept(command)
                                continue  # startup stops cannot satisfy readiness
                            raise ValueError("actuation lacks a recent sensor causality token")
                        if rid in last_stamp and stamp <= last_stamp[rid]:
                            raise ValueError("actuator timestamp stale or repeated")
                        last_stamp[rid] = stamp
                        age = time.monotonic() - sent_at
                        # Token round-trip age includes sensors, driver scheduling,
                        # controller compute and return transport. It is NOT a
                        # one-way network-latency estimate. Stale stops remain safe.
                        if not command.safety_stop:
                            sample_ages[rid].append(age)
                            if age > gates[rid].command_timeout_s:
                                stale_motion[rid] += 1
                                cut = config["sensor_cut"]
                                if (cut and cut["robot"] == rid and cut_start is not None
                                        and cut_end is None):
                                    expected_stale_motion[rid] += 1
                                continue
                        # Preserve the original sensor sample's expiry; receiving
                        # a 99 ms-old command must not renew it for another 100 ms.
                        gates[rid].accept(command, received_at=sent_at)
                        frame_counts[rid] += 1
                        latest_status[rid] = payload.get("visual_status", {})
                        command_events[rid].append((time.monotonic(), command.safety_stop))
                elif frame["type"] == "report":
                    host_reports[host] = frame
                else:
                    raise ValueError("unexpected agent frame")

    def sensors(elapsed: float, stop_nodes: set[str] | None = None):
        nonlocal cut_start, cut_end
        cut = config["sensor_cut"]
        cut_active = bool(cut and cut["at_s"] <= elapsed < cut["at_s"] + cut["duration_s"])
        if cut_active and cut_start is None:
            cut_start = time.monotonic()
        if cut and cut_start is not None and not cut_active and cut_end is None:
            cut_end = time.monotonic()
        for host, channel in channels.items():
            packets = {}
            for rid in ids:
                if (owners[rid] != host or rid in expected_stops
                        or (cut_active and rid == cut["robot"])):
                    continue
                sample_ids[rid] += 1
                sample_id = sample_ids[rid]
                sample_sent[rid][sample_id] = time.monotonic()
                while len(sample_sent[rid]) > 256:
                    del sample_sent[rid][next(iter(sample_sent[rid]))]
                packets[rid] = sensors_to_dict(world.sense(rid, sc.pose_noise_m))
                packets[rid]["bridge_sample_id"] = sample_id
            channel.send({"type": "sensors", "sensors": packets,
                          "stop_nodes": [rid for rid in (stop_nodes or ()) if owners[rid] == host]})

    try:
        print(f"Referee ready at {config['referee_ip']}:{config['bridge_port']}; waiting for hosts",
              flush=True)
        while len(channels) < len(config["hosts"]):
            if time.monotonic() - ready_started > ready_timeout_s:
                raise TimeoutError("not all hosts connected before readiness timeout")
            try:
                sock, address = listener.accept()
            except socket.timeout:
                # Already connected hosts keep receiving valid sensors while the
                # other operator opens their terminal. No tasks exist before ALL
                # controllers cross the readiness barrier.
                if channels:
                    sensors(-1)
                    receive()
                continue
            try:
                host, channel = accept_agent(sock, address[0], config, key)
                if host in channels:
                    raise ValueError("duplicate host connection")
                channels[host] = channel
                last_seen[host] = time.monotonic()
            except (ValueError, OSError, KeyError):
                sock.close()
                raise
        ready_deadline = time.monotonic() + 20
        while not all(frame_counts.values()) or len(started_hosts) != len(channels):
            if time.monotonic() > ready_deadline:
                raise TimeoutError("controllers did not complete sensor/actuator readiness barrier")
            sensors(-1)
            time.sleep(0.02)
            receive()
        wms = UdpMulticastTransport("WMS", group=config["group"], port=config["peer_port"],
                                    interface=config["referee_ip"],
                                    shared_key=key.decode(), require_auth=True)
        validate_config(config)
        _announce_tasks(wms, tasks)
        window_started = time.monotonic()
        dt = 1 / DEFAULT.rates.world_hz
        next_tick = window_started
        for tick in range(math.ceil(config["duration_s"] / dt)):
            elapsed = tick * dt
            new_stops = {rid for rid, at in sc.robot_fail_at.items()
                         if elapsed >= at and rid not in expected_stops}
            for rid in new_stops:
                task = latest_status.get(rid, {}).get("task")
                events.append({"type": "controller_stop", "robot": rid, "t": world.t,
                               "task": task, "active_at_stop": bool(task and task not in observed_completed)})
            expected_stops.update(new_stops)
            for event in sc.obstacles:
                if event.oid not in obstacles_added and elapsed >= event.appear_at:
                    if world.add_obstacle(event.oid, event.cell, event.radius_m) is not None:
                        obstacles_added.add(event.oid)
                        events.append({"type": "obstacle_added", "id": event.oid, "t": world.t})
                if (event.clear_at is not None and elapsed >= event.clear_at
                        and event.oid not in obstacles_removed):
                    world.remove_obstacle(event.oid)
                    obstacles_removed.add(event.oid)
            sensors(elapsed, new_stops)
            next_tick += dt
            time.sleep(max(0, next_tick - time.monotonic()))
            receive()
            now = time.monotonic()
            max_tick_lag_s = max(max_tick_lag_s, now - next_tick)
            if now - next_tick > dt:
                referee_late_ticks += 1
            if any(now - last_seen[host] > 1 for host in channels):
                raise TimeoutError("host bridge stopped responding")
            commands = {rid: gate.command(now) for rid, gate in gates.items()}
            world.step(dt, commands)
            for rid in ids:
                motion_events[rid].append((now, abs(world.robots[rid].v)))
            # This subscription observes traffic; it never relays peer packets.
            for packet in wms.poll():
                if packet.src not in peer_observed:
                    continue
                peer_observed[packet.src] += 1
                if packet.type == msg.TASK_DONE:
                    tid = str(packet.body.get("task", ""))
                    owner = _completion_owner(packet, task_catalog[tid]) if tid in task_catalog else None
                    if owner and tid not in observed_completed:
                        observed_completed.add(tid)
                        completion_events.append({"task": tid, "owner": owner, "t": world.t})
                packet_trace.append({"src": packet.src, "type": packet.type,
                                     "seq": packet.seq, "t": round(world.t, 2),
                                     "task": packet.body.get("task")})
            if on_snapshot and tick % 5 == 0:
                cut = config["sensor_cut"]
                on_snapshot({"world": world.snapshot(), "map": world.env.to_json(),
                             "hosts": started_hosts, "statuses": latest_status,
                             "scenario": config["scenario"], "cell_m": DEFAULT.cell_m,
                             "events": list(events), "packets": list(packet_trace),
                             "observed_completed": sorted(observed_completed),
                             "tasks": [{"id": t.tid, "pick": t.pick, "drop": t.drop,
                                        "cargo_type": t.cargo_type} for t in tasks],
                             "contacts": {kind: sum(e.kind == kind for e in world.contacts)
                                          for kind in ("robot-robot", "robot-human", "robot-rack")},
                             "nodes": [{"id": rid, "host": owners[rid],
                                        "pid": started_hosts[owners[rid]]["pids"][rid],
                                        "running": rid not in expected_stops,
                                        "sensor_frames": sample_ids[rid],
                                        "actuator_frames": frame_counts[rid],
                                        "peer_packets_observed": peer_observed[rid],
                                        "sensor_cut": bool(cut and rid == cut["robot"] and
                                                           cut["at_s"] <= elapsed < cut["at_s"] + cut["duration_s"]),
                                        "visual_status": latest_status.get(rid, {}),
                                        "command": {"v": commands[rid].v, "omega": commands[rid].omega,
                                                    "safety_stop": commands[rid].safety_stop}}
                                       for rid in ids],
                             "duration_s": config["duration_s"]})
    except (OSError, ValueError, KeyError, RuntimeError, KeyboardInterrupt) as exc:
        failure = f"{type(exc).__name__}: {exc}"
    finally:
        for channel in channels.values():
            try:
                channel.send({"type": "stop"})
            except OSError:
                pass
        deadline = time.monotonic() + 12
        pending = set(channels)
        while pending and time.monotonic() < deadline:
            for host in list(pending):
                try:
                    for frame in channels[host].poll():
                        if frame.get("type") == "report":
                            host_reports[host] = frame
                            pending.remove(host)
                except (OSError, ValueError, KeyError):
                    pending.discard(host)
            time.sleep(0.01)
        for channel in channels.values():
            channel.close()
        listener.close()
        if wms:
            wms.close()
    reports = [node for report in host_reports.values() for node in report["nodes"]]
    final_reported = {tid for report in reports for tid in report.get("completed_tasks", [])}
    completed = sorted(observed_completed & final_reported)
    contacts = {kind: sum(e.kind == kind for e in world.contacts)
                for kind in ("robot-robot", "robot-human", "robot-rack")}
    cut_evidence = None
    if config["sensor_cut"]:
        observations = command_events[config["sensor_cut"]["robot"]]
        motion = motion_events[config["sensor_cut"]["robot"]]
        moving = bool(cut_start and any(speed > .02 and cut_start - .5 <= at < cut_start
                                       for at, speed in motion))
        stops = [at for at, stop in observations if stop and cut_start and at >= cut_start
                 and (cut_end is None or at < cut_end)]
        response = min(stops) - cut_start if stops else None
        recovered = bool(cut_end and any(speed > .02 and at > cut_end for at, speed in motion))
        cut_evidence = {"robot": config["sensor_cut"]["robot"], "response_s": response,
                        "moving_before": moving, "recovered": recovered,
                        "pass": bool(moving and recovered and response is not None and response <= .3)}
    timing = len(reports) == len(ids) and all(_timing_pass(r) for r in reports)
    host_gate = (len(host_reports) == len(config["hosts"])
                 and all(not r["missing"] and not r["failure"] for r in host_reports.values()))
    event_gate = (len(expected_stops) == len(sc.robot_fail_at)
                  and len(obstacles_added) == len(sc.obstacles)
                  and all(e.clear_at is None or e.oid in obstacles_removed for e in sc.obstacles))
    recovered_failures = all(
        e["active_at_stop"] and any(c["task"] == e["task"] and c["owner"] != e["robot"]
                                   and c["owner"] not in expected_stops and c["t"] > e["t"]
                                   for c in completion_events)
        for e in events if e["type"] == "controller_stop")
    cross_host_peers = {
        r["robot_id"]: sorted(peer for peer in r.get("peer_sources", [])
                              if peer in owners and owners[peer] != owners[r["robot_id"]])
        for r in reports if r.get("robot_id") in owners}
    peers = (sorted(r.get("robot_id", "") for r in reports) == ids
             and all(r.get("peer_sources_authenticated") for r in reports)
             and all(cross_host_peers.get(rid) for rid in ids))
    completion_gate = {t.tid for t in tasks} <= set(completed)
    boundary = (all(frame_counts.values()) and len(reports) == len(ids)
                and all(r.get("hardware", {}).get("sensor_frames", 0) > 0
                        and r.get("hardware", {}).get("actuator_frames", 0) > 0
                        and r.get("bridge", {}).get("invalid_frames") == 0 for r in reports))
    safety = not any(contacts.values())
    source_unchanged = source_fingerprint() == config["source_sha256"]
    unexpected_stale = {rid: stale_motion[rid] - expected_stale_motion[rid] for rid in ids}
    software_pass = (not failure and host_gate and boundary and peers and timing and safety
               and source_unchanged
               and referee_late_ticks == 0
               and not any(unexpected_stale.values())
               and completion_gate and event_gate and recovered_failures
               and (cut_evidence is None or cut_evidence["pass"]))
    real_multihost = len({r.get("hostname") for r in started_hosts.values()}) > 1
    return {"success": bool(software_pass and real_multihost), "failure": failure,
            "software_boundary_pass": bool(software_pass),
            "proof_scope": "networked_software_in_the_loop", "physical_amr_tested": False,
            "config": config, "hosts": started_hosts, "host_reports": host_reports,
            "real_multihost": real_multihost,
            "referee_selects_winners": False, "referee_forwards_peer_messages": False,
            "bridge_authentication": "HMAC-SHA256, session challenge and sequence; no encryption",
            "driver_udp_scope": "loopback only; trust local host processes",
            "source_and_workload_verified_at_startup": True,
            "source_unchanged_at_end": source_unchanged,
            "readiness_barrier_passed": bool(window_started),
            "simulation_time_s": world.t, "max_referee_tick_lag_ms": max_tick_lag_s * 1000,
            "referee_deadline_misses": referee_late_ticks,
            "tasks_announced": len(tasks), "tasks_completed": len(completed),
            "completed_task_ids": completed, "contacts": contacts,
            "completion_events_in_window": completion_events,
            "reported_completed_after_shutdown": sorted(final_reported),
            "sensor_cut_evidence": cut_evidence, "control_deadlines_met": timing,
            "event_coverage": event_gate, "failure_work_recovered": recovered_failures,
            "events": events, "actuator_frames": frame_counts,
            "stale_motion_frames_rejected": stale_motion,
            "expected_sensor_cut_stale_motion_rejected": expected_stale_motion,
            "unexpected_stale_motion_rejected": unexpected_stale,
            "stale_motion_accepted": 0,
            "sensor_to_motion_actuation_age_ms": {
                rid: {"samples": len(ages), "max": max(ages, default=0) * 1000,
                      "p99": (sorted(ages)[max(0, math.ceil(.99 * len(ages)) - 1)] * 1000
                              if ages else None)} for rid, ages in sample_ages.items()},
            "age_scope": ("same sensor token send-to-command return; includes both transports and computation, "
                          "including intentionally stale commands rejected during injected sensor outage"),
            "command_gates": {rid: gate.report() for rid, gate in gates.items()},
            "peer_messages_observed": peers,
            "cross_host_peer_sources_by_robot": cross_host_peers,
            "claim_boundary": "Measured hosts only; software physics, not physical safety certification."}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare", help="pin candidate and generate shared session configuration")
    prepare.add_argument("--mac-ip", default="169.254.90.241")
    prepare.add_argument("--windows-ip", default="169.254.250.160")
    prepare.add_argument("--robots", type=int, default=3)
    prepare.add_argument("--scenario", choices=sorted(SCENARIOS), default="deployment_socket_acceptance")
    prepare.add_argument("--duration", type=float, default=25)
    prepare.add_argument("--policy", default=POLICY_BIOS_PIBT_V6)
    prepare.add_argument("--seed", type=int, default=0)
    prepare.add_argument("--bridge-port", type=int, default=29600)
    prepare.add_argument("--peer-port", type=int, default=29601)
    prepare.add_argument("--no-sensor-cut", action="store_true")
    prepare.add_argument("--windows-repo", default=r"C:\BIOS7")
    prepare.add_argument("--output", required=True)
    for name in ("agent", "referee"):
        command = sub.add_parser(name)
        command.add_argument("--config", required=True)
        command.add_argument("--key-file", required=True)
        command.add_argument("--output", help="machine-readable evidence path")
        if name == "agent":
            command.add_argument("--host", choices=("mac", "windows"), required=True)
        else:
            command.add_argument("--ready-timeout", type=float, default=120)
            command.add_argument("--no-view", action="store_true",
                                 help="Do not publish the passive local dashboard snapshot")
    args = parser.parse_args(argv)
    if args.command == "prepare":
        config = make_config(args.mac_ip, args.windows_ip, args.robots, args.scenario,
                             args.duration, args.seed, args.policy, args.bridge_port,
                             args.peer_port, not args.no_sensor_cut)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists() or output.with_suffix(".key").exists():
            raise SystemExit("refusing to replace an existing session; choose a new output path")
        output.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        key_path = output.with_suffix(".key")
        with key_path.open("x", encoding="utf-8") as stream:
            os.chmod(key_path, 0o600)
            stream.write(secrets.token_hex(32) + "\n")
        print("Copy the exact candidate source, session JSON and private .key to both computers.")
        print("Do not commit/share the .key publicly. Put JSON and .key in the Windows repo root.")
        common = ["--config", str(output.resolve()), "--key-file", str(key_path.resolve())]
        program = [sys.executable, str(ROOT / "multihost_demo.py")]
        print("Mac referee: " + shlex.join(program + ["referee"] + common
              + ["--output", str(output.with_name(output.stem + "-result.json").resolve())]))
        print("Mac agent, another terminal: " + shlex.join(program + ["agent"] + common
              + ["--host", "mac", "--output", str(output.with_name(output.stem + "-agent-mac-report.json").resolve())]))
        def ps_quote(value):
            return "'" + str(value).replace("'", "''") + "'"
        print("Windows PowerShell: Set-Location " + ps_quote(args.windows_repo))
        print("Windows PowerShell: .\\.venv\\Scripts\\python.exe multihost_demo.py agent "
              + "--config " + ps_quote(output.name) + " --key-file " + ps_quote(key_path.name)
              + " --host windows --output " + ps_quote(output.stem + "-agent-windows-report.json"))
        print(f"Allow TCP {args.bridge_port} into Mac and UDP {args.peer_port} on both Ethernet interfaces.")
        return 0
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    key = Path(args.key_file).read_text(encoding="utf-8").strip().encode("utf-8")
    if len(key) < 32:
        raise SystemExit("session key must contain at least 32 characters")
    if args.command == "agent":
        result = run_agent(config, args.host, key)
    else:
        publisher = None
        if not args.no_view:
            from backend.multihost_view import MultiHostPublisher
            publisher = MultiHostPublisher()
            publisher.begin(config)
        try:
            result = run_referee(config, key, args.ready_timeout, on_snapshot=publisher)
            if publisher:
                publisher.finish(result)
        except BaseException as exc:
            if publisher:
                publisher.finish({"success": False, "failure": f"{type(exc).__name__}: {exc}"})
            raise
        finally:
            if publisher:
                publisher.close()
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.command == "referee":
        print(f"MULTI-HOST PROOF: {'PASS' if result['success'] else 'FAIL'}")
        print(f"Software boundary checks: {'PASS' if result['software_boundary_pass'] else 'FAIL'}")
        if not result["real_multihost"]:
            print("Two physical hosts NOT measured; a loopback run cannot pass the multi-host gate.")
        print(f"Tasks: {result['tasks_completed']}/{result['tasks_announced']}; contacts={result['contacts']}")
        print(f"Different hostnames observed: {result['real_multihost']}; timing={result['control_deadlines_met']}")
        if result["failure"]:
            print(result["failure"])
        return 0 if result["success"] else 1
    print(f"Agent {args.host}: {len(result['nodes'])} controller reports, "
          f"{len(result['missing'])} missing")
    if result["failure"]:
        print("Agent failure: " + result["failure"])
    for missing in result["missing"]:
        print(f"{missing['robot']}: exit={missing['exit_code']}; {missing['log_tail']}")
    return 0 if not result["failure"] and not result["missing"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
