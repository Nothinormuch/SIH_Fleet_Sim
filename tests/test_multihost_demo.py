"""Boundaries and local smoke for the two-computer deployment runner."""

from concurrent.futures import ThreadPoolExecutor
import copy
import json
import socket
import time

import pytest

from src import messages as msg
from src.amr import Task
from src.multihost_demo import (AuthChannel, _completion_owner, _line, _reserve_sensor, _timing_pass, accept_agent, make_config,
                                run_agent, run_referee, validate_config)
from src.task_protocol import CompletionCertificate, task_descriptor_hash


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
