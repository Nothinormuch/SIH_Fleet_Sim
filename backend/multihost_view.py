"""Passive, latest-only view of a CLI-owned multi-host run.

The HTTP server reads one fixed ignored JSON file. It has no network-controller,
task assignment, launch or fault-injection authority through this interface.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import time

DEFAULT_STATE_PATH = Path(__file__).resolve().parent.parent / "artifacts/deployment/multihost-live.json"
MAX_STATE_BYTES = 8 * 1024 * 1024


def _idle(error: str | None = None) -> dict:
    return {"source": "multihost", "read_only": True, "state": "idle",
            "snapshot": None, "result": None, "snapshot_age_s": None,
            "faults": [], "error": error}


def read_state() -> dict:
    """Read only the commissioned cache; HTTP parameters cannot select a path."""
    try:
        with DEFAULT_STATE_PATH.open("rb") as stream:
            raw = stream.read(MAX_STATE_BYTES + 1)
        if len(raw) > MAX_STATE_BYTES:
            return _idle("Multi-host view exceeds its bounded display size.")
        state = json.loads(raw)
        if not isinstance(state, dict) or state.get("source") != "multihost":
            return _idle("Multi-host view has an invalid format.")
        updated = state.pop("snapshot_updated_at", None)
        state["snapshot_age_s"] = max(0.0, time.time() - updated) if isinstance(updated, (int, float)) else None
        state["read_only"] = True
        return state
    except FileNotFoundError:
        return _idle()
    except (OSError, ValueError, TypeError):
        return _idle("Multi-host telemetry unavailable; no control action was taken.")


class MultiHostPublisher:
    """One background writer and one replaceable pending snapshot, never a queue.

    Snapshot callbacks copy data but do no serialization or disk I/O. The fixed
    output is atomically replaced, so readers see a complete old or new state.
    finish() waits at most two seconds for the final display state; it does not
    alter the separately saved authoritative run evidence.
    """

    def __init__(self, path: Path = DEFAULT_STATE_PATH):
        self.path = Path(path)
        self._condition = threading.Condition()
        self._pending: tuple[int, dict] | None = None
        self._serial = self._written = 0
        self._closing = False
        self._state = _idle()
        self._config: dict = {}
        self.error: str | None = None
        self._thread = threading.Thread(target=self._writer, name="bios-multihost-view", daemon=True)
        self._thread.start()

    def _submit(self, state: dict) -> int:
        with self._condition:
            if self._closing:
                raise RuntimeError("Multi-host publisher is closed")
            self._serial += 1
            self._state = state
            self._pending = (self._serial, state)
            self._condition.notify()
            return self._serial

    def begin(self, config: dict) -> None:
        self._config = copy.deepcopy(config)
        self._submit({**_idle(), "state": "starting", "mode": "multihost",
                      "run_id": config.get("session"), "config": self._config,
                      "robots": config.get("robots", 3), "policy": config.get("policy"),
                      "profile": config.get("scenario"), "seed": config.get("seed", 0),
                      "duration_s": config.get("duration_s"),
                      "sensor_cut": copy.deepcopy(config.get("sensor_cut"))})

    def __call__(self, snapshot: dict) -> None:
        view = copy.deepcopy(snapshot)
        for node in view.get("nodes", []):
            role = node.get("host")
            host = self._config.get("hosts", {}).get(role, {})
            observed = view.get("hosts", {}).get(role, {})
            node["host_ip"] = host.get("ip")
            node["host_hostname"] = observed.get("hostname")
        self._submit({**self._state, "state": "running", "snapshot": view,
                      "snapshot_updated_at": time.time()})

    def finish(self, result: dict) -> bool:
        evidence = copy.deepcopy(result)
        # Normalize only structural aliases; missing timing data remains missing.
        reports = []
        processes = []
        identity_valid = True
        for host, host_report in evidence.get("host_reports", {}).items():
            commissioned = self._config.get("hosts", {}).get(host, {})
            observed = evidence.get("hosts", {}).get(host, {})
            assigned = {f"AMR{i + 1:02d}" for i in commissioned.get("indices", [])}
            host_ip = commissioned.get("ip") if observed else None
            for node in host_report.get("nodes", []):
                reports.append(node)
                rid = node.get("robot_id")
                observed_pid = observed.get("pids", {}).get(rid)
                pid = node.get("pid", observed_pid)
                valid = (rid in assigned and isinstance(host_ip, str) and bool(host_ip)
                         and type(pid) is int and pid > 0
                         and (observed_pid is None or pid == observed_pid))
                identity_valid = identity_valid and valid
                processes.append((host_ip, pid) if valid else None)
        evidence["nodes"] = reports
        evidence["robots"] = self._config.get("robots")
        ids = [node.get("robot_id") for node in reports]
        expected = {f"AMR{i + 1:02d}" for i in range(evidence["robots"] or 0)}
        evidence["separate_edge_nodes"] = (
            bool(ids) and identity_valid and set(ids) == expected
            and len(set(processes)) == len(ids) == evidence["robots"])
        evidence["node_report_roster_complete"] = bool(ids) and set(ids) == expected and len(ids) == len(expected)
        fault = evidence.get("sensor_cut_evidence")
        if fault is not None and "recovered_after_sensor_return" not in fault:
            fault["recovered_after_sensor_return"] = fault.get("recovered")
        snapshot = copy.deepcopy(self._state.get("snapshot"))
        if snapshot:
            for node in snapshot.get("nodes", []):
                node["running"] = False
        serial = self._submit({**self._state, "state": "finished", "result": evidence,
                               "snapshot": snapshot, "error": evidence.get("failure")})
        return self.flush(serial=serial)

    def flush(self, timeout: float = 2.0, serial: int | None = None) -> bool:
        target = self._serial if serial is None else serial
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._written < target and self.error is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return self._written >= target and self.error is None

    def close(self) -> None:
        self.flush()
        with self._condition:
            self._closing = True
            self._condition.notify()
        self._thread.join(timeout=2.0)

    def _writer(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._pending is not None or self._closing)
                if self._pending is None:
                    return
                serial, state = self._pending
                self._pending = None
            temporary = None
            try:
                payload = json.dumps(state, separators=(",", ":"), allow_nan=False).encode("utf-8")
                if len(payload) > MAX_STATE_BYTES:
                    raise ValueError("multi-host view exceeds display size bound")
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=self.path.parent, prefix=".multihost-view-", delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(payload)
                os.replace(temporary, self.path)
                temporary = None
                with self._condition:
                    self._written = serial
                    self.error = None
            except (OSError, ValueError, TypeError) as exc:
                with self._condition:
                    self.error = str(exc)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
                with self._condition:
                    self._condition.notify_all()
