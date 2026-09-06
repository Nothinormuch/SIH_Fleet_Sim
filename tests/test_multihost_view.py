"""The multi-host viewer is passive, bounded and never invents missing evidence."""
from __future__ import annotations

import json
from pathlib import Path
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from backend import multihost_view as view
from backend.server import Handler


def _config():
    return {"session": "display-test", "robots": 3, "policy": "BIOS_PIBT.7",
            "scenario": "edge_overlap", "seed": 2, "duration_s": 20,
            "hosts": {"mac": {"ip": "192.0.2.1", "indices": [0, 1]}, "windows": {"ip": "192.0.2.2", "indices": [2]}},
            "sensor_cut": {"robot": "AMR03", "at_s": 3, "duration_s": 2}}


def _snapshot(t=1):
    return {"world": {"t": t, "robots": []},
            "hosts": {"windows": {"hostname": "Measured-Windows"}},
            "nodes": [{"id": "AMR03", "host": "windows", "running": True}],
            "tasks": [], "packets": [], "contacts": {}, "observed_completed": []}


def test_display_normalizes_hosts_reports_and_preserves_unavailable_timing(tmp_path, monkeypatch):
    target = tmp_path / "live.json"
    monkeypatch.setattr(view, "DEFAULT_STATE_PATH", target)
    publisher = view.MultiHostPublisher(target)
    try:
        publisher.begin(_config())
        snapshot = _snapshot()
        publisher(snapshot)
        snapshot["nodes"][0]["host"] = "mutated-after-publish"
        assert publisher.flush()
        state = view.read_state()
        node = state["snapshot"]["nodes"][0]
        assert node["host"] == "windows"
        assert node["host_ip"] == "192.0.2.2"
        assert node["host_hostname"] == "Measured-Windows"
        assert state["read_only"] and state["snapshot_age_s"] >= 0
        assert publisher.finish({"success": False, "host_reports": {
            "windows": {"nodes": [{"robot_id": "AMR03"}]},
        }, "sensor_cut_evidence": {"robot": "AMR03", "recovered": True}})
        final = view.read_state()
        assert final["state"] == "finished"
        assert final["result"]["nodes"] == [{"robot_id": "AMR03"}]
        assert not final["result"]["separate_edge_nodes"]
        assert "runtime" not in final["result"]["nodes"][0]
        assert final["result"]["sensor_cut_evidence"]["robot"] == "AMR03"
        assert final["result"]["sensor_cut_evidence"]["recovered_after_sensor_return"]
        assert not final["snapshot"]["nodes"][0]["running"]
    finally:
        publisher.close()
    assert not list(tmp_path.glob(".multihost-view-*"))


def test_pending_slot_keeps_only_latest_frame_and_is_atomically_readable(tmp_path, monkeypatch):
    target = tmp_path / "live.json"
    monkeypatch.setattr(view, "DEFAULT_STATE_PATH", target)
    entered, release = threading.Event(), threading.Event()
    real_replace = view.os.replace
    def blocked_replace(source, destination):
        entered.set()
        assert release.wait(2)
        real_replace(source, destination)
    monkeypatch.setattr(view.os, "replace", blocked_replace)
    publisher = view.MultiHostPublisher(target)
    try:
        publisher.begin(_config())
        assert entered.wait(1)
        for t in range(30):
            publisher(_snapshot(t))
        assert publisher._pending[1]["snapshot"]["world"]["t"] == 29
        release.set()
        assert publisher.flush()
        assert view.read_state()["snapshot"]["world"]["t"] == 29
        assert json.loads(target.read_text())["source"] == "multihost"
    finally:
        release.set()
        publisher.close()


def test_missing_corrupt_and_oversized_state_fail_to_unavailable(tmp_path, monkeypatch):
    target = tmp_path / "live.json"
    monkeypatch.setattr(view, "DEFAULT_STATE_PATH", target)
    assert view.read_state()["snapshot"] is None
    target.write_text("incomplete json")
    assert view.read_state()["error"]
    monkeypatch.setattr(view, "MAX_STATE_BYTES", 10)
    target.write_text("x" * 11)
    assert view.read_state()["error"]


def test_http_status_ignores_path_parameter_and_never_launches_nodes(tmp_path, monkeypatch):
    target = tmp_path / "live.json"
    monkeypatch.setattr(view, "DEFAULT_STATE_PATH", target)
    publisher = view.MultiHostPublisher(target)
    publisher.begin(_config())
    assert publisher.flush()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/api/multihost/status?path=/private.key"
        with urllib.request.urlopen(url, timeout=2) as response:
            data = json.load(response)
            assert response.status == 200
            assert response.headers["Cache-Control"] == "no-store"
        assert data["read_only"]
        assert data["state"] == "starting"
        assert data["policy"] == "BIOS_PIBT.7"
    finally:
        server.shutdown()
        server.server_close()
        publisher.close()


def test_browser_source_mode_blocks_mutations_and_preserves_v7_choice():
    root = Path(__file__).resolve().parent.parent
    script = (root / "frontend/js/edge-lab.js").read_text()
    page = (root / "frontend/edge-lab.html").read_text()
    assert "get('source') === 'multihost'" in script
    assert "if (busy || readOnlyMultihost) return;" in script
    assert "readOnlyMultihost && (payload !== undefined || path !== 'status')" in script
    assert "readOnlyMultihost || busy || !live" in script
    assert "fault?.robot || latest?.sensor_cut?.robot" in script
    assert "n.full_cycle?.loop_max_ms || 0" not in script
    assert 'value="BIOS_PIBT.7"' in page
    assert "INCOMPLETE NODE REPORTS" in script
    assert "const fleetSum=values=>rosterComplete?sumMetrics(values):null;" in script


def test_process_identity_uses_observed_host_pid_not_just_robot_names(tmp_path, monkeypatch):
    target = tmp_path / "live.json"
    monkeypatch.setattr(view, "DEFAULT_STATE_PATH", target)
    publisher = view.MultiHostPublisher(target)
    publisher.begin(_config())
    result = {"host_reports": {"mac": {"nodes": [{"robot_id": "AMR01"}, {"robot_id": "AMR02"}]},
                               "windows": {"nodes": [{"robot_id": "AMR03"}]}},
              "hosts": {"mac": {"pids": {"AMR01": 101, "AMR02": 102}},
                        "windows": {"pids": {"AMR03": 101}}}}
    try:
        assert publisher.finish(result)
        assert view.read_state()["result"]["separate_edge_nodes"]  # same PID on different hosts is valid
        result["hosts"]["mac"]["pids"]["AMR02"] = 101
        assert publisher.finish(result)
        assert not view.read_state()["result"]["separate_edge_nodes"]
        result["hosts"]["mac"]["pids"]["AMR02"] = 102
        del result["hosts"]["windows"]["pids"]["AMR03"]
        assert publisher.finish(result)
        assert not view.read_state()["result"]["separate_edge_nodes"]
        result["host_reports"]["windows"]["nodes"] = []
        assert publisher.finish(result)
        assert not view.read_state()["result"]["node_report_roster_complete"]
    finally:
        publisher.close()
