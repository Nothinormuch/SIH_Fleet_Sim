"""Bounded live demo sessions for the virtual onboard-computer dashboard.

Runs the real socket demonstration. UI polling reads snapshots; it never steps a
robot, selects an auction winner, or invents hardware measurements.
"""
from __future__ import annotations

import copy
import secrets
import socket
import threading
import time

from .hil_demo import run_hil_demo


def _available_ports() -> int:
    """Probe a contiguous block; actual binds still fail explicitly if raced."""
    for _ in range(100):
        base = 35000 + secrets.randbelow(20000)
        sockets = []
        try:
            for port in range(base, base + 7):
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sockets.append(sock)
                sock.bind(("127.0.0.1", port))
            return base
        except OSError:
            continue
        finally:
            for sock in sockets:
                sock.close()
    raise RuntimeError("No available UDP ports for the edge lab")


class EdgeLab:
    def __init__(self):
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._cut_until: dict[str, float] = {}
        self._state: dict = {"state": "idle", "snapshot": None, "result": None,
                            "error": None, "faults": []}

    def status(self) -> dict:
        with self._lock:
            result = copy.deepcopy(self._state)
        result["snapshot_age_s"] = (
            max(0, time.monotonic() - result.pop("updated_at"))
            if "updated_at" in result else None
        )
        return result

    def start(self, mode: str = "normal") -> dict:
        if mode not in ("normal", "sensor_demo"):
            raise ValueError("mode must be normal or sensor_demo")
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("An edge lab run is already active")
            self._stop.clear()
            self._cut_until.clear()
            self._state = {"state": "starting", "mode": mode, "snapshot": None,
                           "result": None, "error": None, "faults": [],
                           "run_id": secrets.token_hex(8)}
            self._thread = threading.Thread(target=self._run, args=(mode,),
                                            name="bios-edge-lab", daemon=True)
            self._thread.start()
        return self.status()

    def cut_sensor(self, rid: str) -> dict:
        if rid not in ("AMR01", "AMR02", "AMR03"):
            raise ValueError("Unknown robot")
        with self._lock:
            if self._state["state"] != "running":
                raise RuntimeError("Start a live run before disconnecting a sensor")
            if self._cut_until.get(rid, 0) > time.monotonic():
                raise RuntimeError("This sensor is already disconnected")
            self._cut_until[rid] = time.monotonic() + 2.0
            self._state["faults"].append({"robot": rid, "duration_s": 2.0,
                                         "t": self._state["snapshot"]["world"]["t"]})
            self._state["faults"] = self._state["faults"][-30:]
        return self.status()

    def stop(self) -> dict:
        with self._lock:
            if self._state["state"] in ("starting", "running"):
                self._state["state"] = "stopping"
            self._stop.set()
        return self.status()

    def close(self):
        self.stop()
        if self._thread is not None:
            self._thread.join(timeout=15)

    def _cut(self, rid: str) -> bool:
        with self._lock:
            return time.monotonic() < self._cut_until.get(rid, 0)

    def _snapshot(self, snapshot: dict):
        with self._lock:
            self._state["snapshot"] = snapshot
            self._state["updated_at"] = time.monotonic()
            if not self._stop.is_set():
                self._state["state"] = "running"

    def _run(self, mode: str):
        try:
            base = _available_ports()
            kwargs = {}
            if mode == "sensor_demo":
                kwargs = {"sensor_cut_robot": "AMR01", "sensor_cut_at_s": 3.0,
                          "sensor_cut_duration_s": 2.0}
            result = run_hil_demo(
                scenario_name="deployment_socket_acceptance", robots=3,
                duration_s=20.0, require_task_completion=True,
                peer_port=base, sensor_base_port=base + 1,
                actuator_base_port=base + 4, shared_key=secrets.token_hex(24),
                on_snapshot=self._snapshot, should_stop=self._stop.is_set,
                sensor_cut_control=self._cut, **kwargs,
            )
            with self._lock:
                self._state["result"] = result
                self._state["state"] = "cancelled" if result["cancelled"] else "finished"
        except Exception as exc:
            with self._lock:
                self._state["state"] = "failed"
                self._state["error"] = str(exc)
