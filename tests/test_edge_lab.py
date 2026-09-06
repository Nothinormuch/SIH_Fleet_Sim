"""Exercise actual child processes and telemetry through the visual lab boundary."""
import time

import pytest

from src.edge_lab import EdgeLab


def _wait(lab, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = lab.status()
        assert state["state"] != "failed", state.get("error")
        if predicate(state):
            return state
        time.sleep(.05)
    pytest.fail(f"Lab did not reach expected state: {lab.status()['state']}")


def test_live_snapshots_sensor_fault_recovery_and_process_cleanup():
    lab = EdgeLab()
    try:
        lab.start()
        with pytest.raises(RuntimeError, match="already active"):
            lab.start()
        state = _wait(lab, lambda s: s["state"] == "running" and
                      all(n["peer_packets_observed"] > 0 for n in s["snapshot"]["nodes"]))
        nodes = state["snapshot"]["nodes"]
        assert len({n["pid"] for n in nodes}) == 3
        assert all(n["sensor_frames"] > 0 and n["actuator_frames"] > 0 for n in nodes)
        assert all(n["visual_status"]["id"] == n["id"] for n in nodes)
        assert all(len(n["visual_status"]["path"]) <= 8 for n in nodes)
        assert state["run_id"]
        before_frames = nodes[1]["sensor_frames"]
        lab.cut_sensor("AMR01")
        cut = _wait(lab, lambda s: s["snapshot"]["nodes"][0]["sensor_cut"] and
                    s["snapshot"]["nodes"][0]["command"]["safety_stop"])
        assert cut["snapshot"]["nodes"][0]["command"]["v"] == 0
        held_frames = cut["snapshot"]["nodes"][0]["sensor_frames"]
        time.sleep(.2)
        assert lab.status()["snapshot"]["nodes"][0]["sensor_frames"] == held_frames
        restored = _wait(lab, lambda s: not s["snapshot"]["nodes"][0]["sensor_cut"] and
                         not s["snapshot"]["nodes"][0]["command"]["safety_stop"])
        assert restored["snapshot"]["nodes"][1]["sensor_frames"] > before_frames
        assert len(restored["faults"]) == 1
        lab.stop()
        ended = _wait(lab, lambda s: s["state"] == "cancelled")
        assert ended["result"]["process_failures"] == []
        assert ended["result"]["cancelled"]
        assert not ended["result"]["success"]
    finally:
        lab.close()


def test_lab_rejects_invalid_controls():
    lab = EdgeLab()
    with pytest.raises(ValueError):
        lab.start("unknown")
    with pytest.raises(ValueError, match="policy"):
        lab.start(policy="arbitrary-policy")
    with pytest.raises(ValueError):
        lab.cut_sensor("AMR99")
    with pytest.raises(RuntimeError):
        lab.cut_sensor("AMR01")
