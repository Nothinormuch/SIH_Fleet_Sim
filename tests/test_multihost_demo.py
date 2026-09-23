"""Boundaries and local smoke for the two-computer deployment runner."""

from concurrent.futures import ThreadPoolExecutor
import copy
import errno
import json
from pathlib import PurePosixPath, PureWindowsPath
import socket
import time
from unittest.mock import Mock

import pytest

from src import messages as msg
from src import multihost_demo as demo
from src.amr import Task
from src.multihost_demo import (AuthChannel, _completion_owner, _line, _reserve_sensor, _timing_pass, accept_agent, make_config,
                                run_agent, run_referee, source_fingerprint, validate_config)
from src.task_protocol import CompletionCertificate, task_descriptor_hash


def test_source_fingerprint_is_portable_but_still_checks_exact_bytes():
    contents = {"src/brain.py": b"brain = 7\n", "edge_node.py": b"node\n",
                "multihost_demo.py": b"demo\n"}

    class SourcePath:
        def __init__(self, name, path_type):
            self.name, self.path_type = name, path_type

        def glob(self, pattern):
            assert self.name == "src" and pattern == "*.py"
            return [SourcePath("src/brain.py", self.path_type)]

        def relative_to(self, root):
            return self.path_type(self.name)

        def read_bytes(self):
            return contents[self.name]

    class SourceRoot:
        def __init__(self, path_type):
            self.path_type = path_type

        def __truediv__(self, name):
            return SourcePath(name, self.path_type)

    posix = SourceRoot(PurePosixPath)
    windows = SourceRoot(PureWindowsPath)
    expected = source_fingerprint(posix)
    assert source_fingerprint(windows) == expected
    contents["src/brain.py"] = b"brain = 8\n"
    assert source_fingerprint(windows) != expected
    # No CRLF normalization: the exact source-byte check remains intentional.
    contents["src/brain.py"] = b"brain = 7\r\n"
    assert source_fingerprint(windows) != expected


def _channels():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    left = socket.create_connection(listener.getsockname())
    right, _ = listener.accept()
    listener.close()
    return (AuthChannel(left, b"k" * 32, "session-a", "referee"),
            AuthChannel(right, b"k" * 32, "session-a", "agent"))


def test_bridge_rejects_tampering_replay_and_cross_session():
    sender, receiver = _channels()
    try:
        raw = sender.encode({"type": "sensors", "sensors": {}}).strip()
        tampered = json.loads(raw)
        tampered["body"]["payload"]["type"] = "stop"
        with pytest.raises(ValueError, match="authentication"):
            receiver.decode(json.dumps(tampered).encode())
        assert receiver.decode(raw)["type"] == "sensors"
        with pytest.raises(ValueError, match="replay"):
            receiver.decode(raw)
        receiver.context = "session-b"
        with pytest.raises(ValueError, match="session"):
            receiver.decode(sender.encode({"type": "stop"}).strip())
    finally:
        sender.close()
        receiver.close()


def test_bridge_delivers_last_report_before_eof():
    sender, receiver = _channels()
    try:
        sender.send({"type": "report"})
        sender.close()
        time.sleep(.01)
        assert receiver.poll() == [{"type": "report"}]
        with pytest.raises(ConnectionError):
            receiver.poll()
    finally:
        receiver.close()


def test_decoded_final_report_is_retained_before_connection_reset(monkeypatch):
    sender, receiver = _channels()
    original_socket = receiver.sock
    raw = sender.encode({"type": "report"})

    class ResetAfterFrame:
        sent = False

        def recv(self, _size):
            if self.sent:
                raise ConnectionResetError("peer closed with unread input")
            self.sent = True
            return raw

    receiver.sock = ResetAfterFrame()
    monkeypatch.setattr("select.select", lambda readers, *_args: (readers, [], []))
    try:
        assert receiver.poll() == [{"type": "report"}]
        with pytest.raises(ConnectionError):
            receiver.poll()
    finally:
        sender.close()
        original_socket.close()


def test_bridge_wrong_key_and_reflected_frames_fail_closed():
    sender, receiver = _channels()
    try:
        raw = sender.encode({"type": "stop"}).strip()
        receiver.key = b"not-the-session-key"
        with pytest.raises(ValueError, match="authentication"):
            receiver.decode(raw)
        with pytest.raises(ValueError, match="reflected"):
            sender.decode(raw)
    finally:
        sender.close()
        receiver.close()


def test_bridge_receive_batch_is_bounded_without_losing_coalesced_frames():
    sender, receiver = _channels()
    try:
        for i in range(65):
            sender.send({"index": i})
        received = []
        deadline = time.monotonic() + 2
        while len(received) < 65 and time.monotonic() < deadline:
            batch = receiver.poll()
            assert len(batch) <= 32
            received.extend(batch)
            time.sleep(.001)
        assert received == [{"index": i} for i in range(65)]
    finally:
        sender.close()
        receiver.close()


def test_handshake_has_absolute_deadline_not_a_per_byte_extension(monkeypatch):
    class Trickle:
        def settimeout(self, timeout):
            assert timeout <= 5

        def recv(self, _size):
            return b"{"

    instants = iter([10.0, 10.1, 15.1])
    monkeypatch.setattr("src.multihost_demo.time.monotonic", lambda: next(instants))
    with pytest.raises(TimeoutError, match="absolute handshake"):
        _line(Trickle())


def test_unconfigured_ip_is_rejected_before_handshake():
    config = make_config("127.0.0.1", "127.0.0.1")
    with pytest.raises(ValueError, match="unconfigured host IP"):
        accept_agent(None, "192.0.2.10", config, b"k" * 32)


@pytest.mark.parametrize("scenario", ["manager_dies", "dead_zone_infra", "dead_zone_mesh",
                                      "partition_recovery"])
def test_unimplemented_radio_manager_faults_are_not_silently_claimed(scenario):
    with pytest.raises(ValueError, match="unsupported"):
        make_config("127.0.0.1", "127.0.0.1", scenario=scenario)


def test_completion_requires_direct_owner_and_exact_task_descriptor():
    task = Task("JOB-1", (1, 2), (5, 2), 0)
    identity = task_descriptor_hash(task.tid, task.generation, task.pick, task.drop)
    certificate = CompletionCertificate.create(task.tid, task.generation, identity,
                                                "AMR01", 1, 4)
    packet = msg.task_done("AMR01", 1, 4, task.tid, certificate=certificate)
    assert _completion_owner(packet, task) == "AMR01"
    relay = msg.task_done("AMR02", 1, 5, task.tid, certificate=certificate)
    assert _completion_owner(relay, task) is None
    changed_task = Task("JOB-1", (1, 2), (6, 2), 0)
    assert _completion_owner(packet, changed_task) is None


def test_missing_or_nonfinite_timing_evidence_cannot_pass():
    report = {"runtime": {"ticks": 10, "deadline_misses": 0, "loop_max_ms": 2.0},
              "full_cycle": {"ticks": 10, "deadline_misses": 0, "loop_max_ms": 3.0},
              "scheduling_late_ticks": 0, "skipped_schedule_slots": 0}
    assert _timing_pass(report)
    missing = copy.deepcopy(report)
    del missing["runtime"]["deadline_misses"]
    assert not _timing_pass(missing)
    report["full_cycle"]["loop_max_ms"] = float("nan")
    assert not _timing_pass(report)
    assert not _timing_pass({})


def test_controller_lifetime_covers_whole_pinned_readiness_and_run():
    config = make_config("127.0.0.1", "127.0.0.1", duration_s=25,
                         readiness_timeout_s=1800)
    assert demo._controller_lifetime(config) == 1855
    with pytest.raises(ValueError, match="readiness budget"):
        run_referee(config, b"x" * 32, ready_timeout_s=1801)
    for invalid in (True, 0, -1, 3601, float("nan")):
        with pytest.raises(ValueError, match="readiness_timeout_s"):
            make_config("127.0.0.1", "127.0.0.1", readiness_timeout_s=invalid)


def test_scheduled_failure_requests_stop_without_blocking_other_controllers():
    process = Mock()
    process.poll.return_value = None
    node = {"process": process}
    demo._request_node_stop(node, 10)
    demo._request_node_stop(node, 11)
    process.send_signal.assert_called_once()
    process.wait.assert_not_called()
    demo._poll_node_stop(node, 14)
    process.kill.assert_not_called()
    demo._poll_node_stop(node, 15.1)
    process.kill.assert_called_once()
    process.wait.assert_not_called()
    process.poll.return_value = 0
    process.returncode = 0
    demo._poll_node_stop(node, 15.2)
    assert node["stop_confirmed_after_s"] == pytest.approx(5.2)


def test_scheduled_failure_cannot_claim_an_already_dead_or_unstoppable_controller():
    process = Mock()
    process.poll.return_value = 1
    with pytest.raises(RuntimeError, match="before its scheduled failure"):
        demo._request_node_stop({"process": process}, 10)
    process.poll.return_value = None
    with pytest.raises(TimeoutError, match="eight seconds"):
        demo._poll_node_stop({"process": process, "stop_requested_at": 10}, 18.1)


def test_shutdown_signals_all_siblings_before_waiting_and_survives_one_signal_error(monkeypatch):
    first, second = Mock(), Mock()
    signalled = set()

    def first_signal(_signal):
        signalled.add("AMR01")
        raise ProcessLookupError("exit race")

    def second_signal(_signal):
        signalled.add("AMR02")

    first.send_signal.side_effect = first_signal
    second.send_signal.side_effect = second_signal
    first.poll.side_effect = lambda: 0 if len(signalled) == 2 else None
    second.poll.side_effect = lambda: 0 if len(signalled) == 2 else None
    assert demo._stop_nodes({"AMR01": {"process": first}, "AMR02": {"process": second}}) == []
    first.wait.assert_not_called()
    second.wait.assert_not_called()


def test_failure_recovery_requires_confirmed_exit_active_work_and_new_surviving_owner():
    events = [{"type": "controller_stop", "robot": "AMR01", "task": "JOB-1",
               "active_at_stop": True, "t": 2.0}]
    done = [{"task": "JOB-1", "owner": "AMR02", "t": 3.0}]
    confirmed = {"AMR01": {"exit_code": 0, "delay_s": .04, "observed_at_s": 2.1}}
    assert demo._failure_recovery_evidence(events, done, confirmed)[0]["pass"]
    assert not demo._failure_recovery_evidence(events, done, {})[0]["pass"]
    for owner, t in (("AMR01", 3.0), ("AMR02", 1.9)):
        invalid = [{"task": "JOB-1", "owner": owner, "t": t}]
        assert not demo._failure_recovery_evidence(events, invalid, confirmed)[0]["pass"]
    events[0]["active_at_stop"] = False
    assert not demo._failure_recovery_evidence(events, done, confirmed)[0]["pass"]


def test_completion_before_observed_delayed_exit_cannot_prove_failure_recovery():
    events = [{"type": "controller_stop", "robot": "AMR01", "task": "JOB-1",
               "active_at_stop": True, "t": 2.0}]
    done = [{"task": "JOB-1", "owner": "AMR02", "t": 4.0}]
    confirmed = {"AMR01": {"exit_code": 0, "delay_s": 5.0, "observed_at_s": 7.1}}
    assert not demo._failure_recovery_evidence(events, done, confirmed)[0]["pass"]
    done[0]["t"] = 7.2
    assert demo._failure_recovery_evidence(events, done, confirmed)[0]["pass"]


def test_live_exit_confirmation_is_owned_expected_unique_and_uses_referee_time():
    frame = {"robot": "AMR01", "delay_s": .05, "exit_code": 0, "forced": False}
    owners, expected = {"AMR01": "mac"}, {"AMR01"}
    rid, evidence = demo._stop_confirmation(frame, "mac", owners, expected, {}, 2.1)
    assert rid == "AMR01" and evidence["observed_at_s"] == 2.1
    for host, stopped, previous in (("windows", expected, {}), ("mac", set(), {}),
                                    ("mac", expected, {rid: evidence})):
        with pytest.raises(ValueError, match="confirmation"):
            demo._stop_confirmation(frame, host, owners, stopped, previous, 2.2)
    with pytest.raises(ValueError, match="invalid"):
        demo._stop_confirmation({**frame, "delay_s": float("nan")}, "mac", owners, expected, {}, 2.1)


@pytest.mark.parametrize("robots", [3, 10])
@pytest.mark.parametrize("scenario", ["edge_overlap", "edge_chokepoint", "edge_human_crossing",
                                      "blocked_aisle", "robot_failure_reassignment"])
def test_shared_floor_and_fault_profiles_have_supported_pinned_inputs(robots, scenario):
    # This verifies configuration support only, not liveness, timing or completion.
    config = make_config("127.0.0.1", "127.0.0.1", robots=robots, scenario=scenario,
                         duration_s=180, sensor_cut=False)
    validate_config(config)
    assert len(demo._scenario(config).starts) == robots


def _campaign_fixture(tmp_path, sessions=2):
    entries = []
    for index in range(sessions):
        config = make_config("127.0.0.1", "127.0.0.1", bridge_port=29600 + index * 2)
        (tmp_path / f"session-{index}.json").write_text(json.dumps(config), encoding="utf-8")
        (tmp_path / f"session-{index}.key").write_text(str(index) * 32, encoding="utf-8")
        entries.append({"config": f"session-{index}.json", "key_file": f"session-{index}.key",
                        "output": f"reports/session-{index}.json"})
    manifest = tmp_path / "campaign.json"
    manifest.write_text(json.dumps({"schema": 1, "entries": entries}), encoding="utf-8")
    return manifest


def test_campaign_validates_every_config_before_start_and_refuses_old_outputs(tmp_path, monkeypatch):
    manifest = _campaign_fixture(tmp_path)
    assert len(demo.load_campaign(manifest, "windows")) == 2
    bad = json.loads((tmp_path / "session-1.json").read_text())
    bad["source_sha256"] = "outdated"
    (tmp_path / "session-1.json").write_text(json.dumps(bad))
    runner = Mock()
    monkeypatch.setattr(demo, "run_agent", runner)
    with pytest.raises(ValueError, match="source mismatch"):
        demo.run_agent_campaign(manifest, "windows")
    runner.assert_not_called()
    assert not (tmp_path / "reports").exists()
    _campaign_fixture(tmp_path)
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports/session-0.json").write_text("previous evidence")
    with pytest.raises(ValueError, match="overwrite"):
        demo.load_campaign(manifest, "windows")
    assert (tmp_path / "reports/session-0.json").read_text() == "previous evidence"


@pytest.mark.parametrize("value", ["../report.json", "/tmp/report.json", "C:/report.json",
                                  "C:report.json", "nested\\report.json", "reports/file:stream",
                                  "reports/NUL.json", "COM1/output.json", "reports/trailing."])
def test_campaign_rejects_paths_outside_its_directory(tmp_path, value):
    with pytest.raises(ValueError, match="manifest"):
        demo._manifest_path(tmp_path, value)


def test_campaign_rejects_symlink_escape_and_reused_peer_key(tmp_path):
    manifest = _campaign_fixture(tmp_path)
    (tmp_path / "escape").symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        demo._manifest_path(tmp_path, "escape/report.json")
    (tmp_path / "session-1.key").write_text((tmp_path / "session-0.key").read_text())
    with pytest.raises(ValueError, match="own peer authentication key"):
        demo.load_campaign(manifest, "windows")


def test_campaign_rejects_reused_tcp_listener_but_allows_shared_peer_udp_port(tmp_path):
    manifest = _campaign_fixture(tmp_path)
    rows = demo.load_campaign(manifest, "windows")
    assert len({row["configuration"]["peer_port"] for row in rows}) == 1
    config = rows[1]["configuration"]
    config["bridge_port"] = rows[0]["configuration"]["bridge_port"]
    (tmp_path / "session-1.json").write_text(json.dumps(config))
    with pytest.raises(ValueError, match="distinct referee TCP ports"):
        demo.load_campaign(manifest, "windows")


def test_campaign_runs_ordered_sessions_but_never_claims_experiment_pass(tmp_path, monkeypatch, capsys):
    manifest = _campaign_fixture(tmp_path)
    seen = []

    def agent(config, host, key, *, connect_wait_s):
        seen.append(config["session"])
        return {"nodes": [{}], "missing": [], "failure": None, "host": host}

    monkeypatch.setattr(demo, "run_agent", agent)
    assert demo.run_agent_campaign(manifest, "windows", 1) == 0
    assert len(set(seen)) == 2
    for index in range(2):
        report = json.loads((tmp_path / f"reports/session-{index}.json").read_text())
        assert report["campaign"]["index"] == index + 1
        assert "success" not in report
    assert "No performance pass is implied" in capsys.readouterr().out


def test_campaign_preserves_failed_attempt_and_does_not_start_remaining_sessions(tmp_path, monkeypatch):
    manifest = _campaign_fixture(tmp_path)
    runner = Mock(side_effect=ValueError("session mismatch"))
    monkeypatch.setattr(demo, "run_agent", runner)
    assert demo.run_agent_campaign(manifest, "windows", 1) == 1
    runner.assert_called_once()
    first = json.loads((tmp_path / "reports/session-0.json").read_text())
    assert first["failure"] == "ValueError: session mismatch"
    assert not (tmp_path / "reports/session-1.json").exists()


def test_campaign_connection_retry_never_retries_authentication_failure(monkeypatch):
    monkeypatch.setattr(demo.time, "sleep", lambda _: None)
    channel = object()
    connector = Mock(side_effect=[ConnectionRefusedError(errno.ECONNREFUSED, "not listening"), channel])
    monkeypatch.setattr(demo, "connect_agent", connector)
    assert demo._connect_when_ready({}, "windows", b"x" * 32, 1) is channel
    assert connector.call_count == 2
    connector.reset_mock(side_effect=True)
    connector.side_effect = ValueError("wrong authentication")
    with pytest.raises(ValueError, match="authentication"):
        demo._connect_when_ready({}, "windows", b"x" * 32, 1)
    connector.assert_called_once()


def test_sensor_ports_are_disjoint_and_busy_ports_have_bounded_fallback():
    config = make_config("127.0.0.1", "127.0.0.1")
    sockets = []
    try:
        sockets.append(_reserve_sensor(config, 0))
        sockets.append(_reserve_sensor(config, 1))
        sockets.append(_reserve_sensor(config, 0))
        ports = [sock.getsockname()[1] for sock in sockets]
        assert len(set(ports)) == 3
        assert all(20_000 <= port < 49_152 for port in ports)
    finally:
        for sock in sockets:
            sock.close()


def test_configuration_pins_source_and_world_and_unique_ownership():
    config = make_config("127.0.0.1", "127.0.0.1")
    validate_config(config)
    bad = copy.deepcopy(config)
    bad["source_sha256"] = "wrong"
    with pytest.raises(ValueError, match="source mismatch"):
        validate_config(bad)
    bad = copy.deepcopy(config)
    bad["workload_sha256"] = "wrong"
    with pytest.raises(ValueError, match="scenario/settings mismatch"):
        validate_config(bad)
    bad = copy.deepcopy(config)
    bad["hosts"]["windows"]["indices"] = [0]
    with pytest.raises(ValueError, match="exactly one host"):
        validate_config(bad)


def test_two_host_agents_loopback_smoke_does_not_claim_two_physical_hosts():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    bridge_port = listener.getsockname()[1]
    listener.close()
    peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    peer.bind(("127.0.0.1", 0))
    peer_port = peer.getsockname()[1]
    peer.close()
    config = make_config("127.0.0.1", "127.0.0.1", duration_s=3,
                         bridge_port=bridge_port, peer_port=peer_port, sensor_cut=False)
    key = b"private-loopback-test-session-key"
    with ThreadPoolExecutor(max_workers=3) as pool:
        referee = pool.submit(run_referee, config, key, 5)
        time.sleep(.15)
        mac = pool.submit(run_agent, config, "mac", key)
        # Real operators do not launch both terminals in the same millisecond.
        # The early host must keep getting fresh sensors before the full roster.
        time.sleep(.6)
        windows = pool.submit(run_agent, config, "windows", key)
        result = referee.result(timeout=35)
        assert mac.result(timeout=5)["failure"] is None
        assert windows.result(timeout=5)["failure"] is None
    assert result["failure"] is None
    assert result["readiness_barrier_passed"]
    assert not result["real_multihost"]
    assert not result["physical_amr_tested"]
    assert not result["referee_selects_winners"]
    assert not result["referee_forwards_peer_messages"]
    assert all(result["actuator_frames"].values())
    assert result["contacts"] == {"robot-robot": 0, "robot-human": 0, "robot-rack": 0}
    assert not any(result["unexpected_stale_motion_rejected"].values())
    assert sum(len(r["nodes"]) for r in result["host_reports"].values()) == 3
    assert all(n["bridge"]["invalid_frames"] == 0
               for r in result["host_reports"].values() for n in r["nodes"])
    # A short smoke proves boundaries, not full completion or real-time certification.
    assert not result["success"]
