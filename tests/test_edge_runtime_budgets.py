"""Finite intake and stale-output rejection, not hard-real-time certification."""

import json
import socket
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.amr import AMRBrain, POLICY_BIOS_PIBT_V6
from src.edge_runtime import UdpJsonHardwareIO, run_edge_node, sensors_to_dict
from src.environment import open_floor
from src.settings import DEFAULT
from src.transport import UdpMulticastTransport
from src.world import Actuation, World


def test_windows_process_stop_uses_scoped_console_break(monkeypatch):
    from src import hil_demo

    monkeypatch.setattr(hil_demo, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(hil_demo, "signal", SimpleNamespace(CTRL_BREAK_EVENT=1))
    process = Mock()
    hil_demo._request_node_stop(process)
    process.send_signal.assert_called_once_with(1)
    process.terminate.assert_not_called()
    process.send_signal.side_effect = OSError("console already detached")
    hil_demo._request_node_stop(process)
    process.terminate.assert_called_once_with()


class NullTransport:
    stats = {}

    def poll(self, max_msgs=256):
        return []

    def send(self, message):
        pass

    def close(self):
        pass


def test_udp_peer_poll_bounds_invalid_packet_flood():
    class FloodSocket:
        calls = 0

        def recvfrom(self, size):
            self.calls += 1
            return b"invalid", ("127.0.0.1", 1)

    transport = UdpMulticastTransport.__new__(UdpMulticastTransport)
    transport.sock = FloodSocket()
    transport.shared_key = None
    transport.require_auth = False
    transport.stats = dict.fromkeys((
        "recv", "bytes_recv", "malformed", "auth_failed", "oversized",
        "receive_budget_exhausted"), 0)
    assert transport.poll(max_msgs=4, max_time_s=1.0) == []
    assert transport.sock.calls == 4
    assert transport.stats["receive_budget_exhausted"] == 1
    assert transport.receive_degraded


def test_udp_peer_poll_has_elapsed_time_budget(monkeypatch):
    from src import transport as module

    class UnexpectedSocket:
        def recvfrom(self, size):
            raise AssertionError("expired budget must not read")

    transport = UdpMulticastTransport.__new__(UdpMulticastTransport)
    transport.sock = UnexpectedSocket()
    transport.stats = {"receive_budget_exhausted": 0}
    clock = iter([0.0, 0.003])
    monkeypatch.setattr(module.time, "perf_counter", lambda: next(clock))
    assert transport.poll() == []
    assert transport.stats["receive_budget_exhausted"] == 1


def test_peer_backlog_is_discarded_until_empty_socket():
    class QueuedSocket:
        packets = [b"old"]

        def recvfrom(self, size):
            if self.packets:
                return self.packets.pop(), ("127.0.0.1", 1)
            raise BlockingIOError

    transport = UdpMulticastTransport.__new__(UdpMulticastTransport)
    transport.sock = QueuedSocket()
    transport._quarantine_receive = True
    transport.stats = dict.fromkeys((
        "recv", "bytes_recv", "receive_quarantine_polls", "backlog_discarded"), 0)
    assert transport.poll() == []
    assert transport.receive_degraded
    assert transport.stats["backlog_discarded"] == 1
    assert not transport._quarantine_receive
    assert transport.poll() == []
    assert not transport.receive_degraded


def test_visual_callback_runs_after_actual_command_is_sent():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(1.0)
        observed = []

        def visual_callback():
            # Would time out if the callback still preceded the command send.
            observed.append(json.loads(receiver.recvfrom(4096)[0]))
            return {"id": "AMR01"}

        hardware = UdpJsonHardwareIO(
            "127.0.0.1", 0, "127.0.0.1", receiver.getsockname()[1],
            visual_status=visual_callback)
        try:
            hardware.write_actuation(Actuation(safety_stop=True), 1.0)
            assert observed[0]["safety_stop"]
        finally:
            hardware.close()


def test_sensor_backlog_exhaustion_does_not_refresh_stale_sample():
    hardware = UdpJsonHardwareIO(
        "127.0.0.1", 0, "127.0.0.1", 9,
        max_sensor_frames=2, sensor_poll_budget_s=1.0)

    class FloodSocket:
        calls = 0

        def recvfrom(self, size):
            self.calls += 1
            return b"invalid", ("127.0.0.1", 1)

        def close(self):
            pass

    hardware.sensor.close()
    hardware.sensor = FloodSocket()
    hardware._received_at = time.monotonic()
    try:
        _, received_at = hardware.read_sensors()
        assert hardware.sensor.calls == 2
        assert received_at is None
        assert hardware.stats["sensor_receive_budget_exhausted"] == 1
    finally:
        hardware.close()


@pytest.mark.parametrize("delay, timeout, counter", [
    (0.03, 0.2, "late_command_stops"),
    (0.008, 0.003, "stale_command_stops"),
])
def test_planning_cannot_publish_expired_motion(monkeypatch, delay, timeout, counter):
    env = open_floor(8, 8)
    world = World(env, DEFAULT, seed=0)
    world.add_robot("AMR01", (1, 1))
    brain = AMRBrain("AMR01", env, DEFAULT, policy=POLICY_BIOS_PIBT_V6,
                     home=(1, 1))

    def delayed_step(*args):
        time.sleep(delay)
        return Actuation(v=0.5, omega=0.0), []

    monkeypatch.setattr(brain, "step", delayed_step)

    class Hardware:
        commands = []
        stats = {}

        def read_sensors(self):
            return world.sense("AMR01"), time.monotonic()

        def write_actuation(self, actuation, timestamp):
            self.commands.append(actuation)

        def close(self):
            pass

    hardware = Hardware()
    report = run_edge_node(brain, NullTransport(), hardware,
                           duration_s=0.04, sensor_timeout_s=timeout)
    assert hardware.commands
    assert all(command.safety_stop and command.v == 0.0
               for command in hardware.commands)
    assert report[counter] > 0


def test_sensor_receive_budget_validation_does_not_open_sockets(monkeypatch):
    def unexpected_socket(*args):
        raise AssertionError("validation must precede socket creation")

    monkeypatch.setattr(socket, "socket", unexpected_socket)
    with pytest.raises(ValueError, match="budgets"):
        UdpJsonHardwareIO("127.0.0.1", 0, "127.0.0.1", 9, max_sensor_frames=0)


def test_bridge_sample_token_correlates_actual_sensor_frame_only():
    env = open_floor(8, 8)
    world = World(env, DEFAULT, seed=0)
    world.add_robot("AMR01", (1, 1))
    frame = sensors_to_dict(world.sense("AMR01"))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
        peer.bind(("127.0.0.1", 0))
        peer.settimeout(1.0)
        hardware = UdpJsonHardwareIO(
            "127.0.0.1", 0, "127.0.0.1", peer.getsockname()[1],
            sensor_poll_budget_s=1.0)

        def deliver():
            before = hardware.stats["sensor_frames"] + hardware.stats["invalid_sensor_frames"]
            peer.sendto(json.dumps(frame).encode(), hardware.sensor.getsockname())
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                result = hardware.read_sensors()
                if hardware.stats["sensor_frames"] + hardware.stats["invalid_sensor_frames"] > before:
                    return result
                time.sleep(0.001)
            pytest.fail("local sensor datagram did not arrive")

        try:
            frame["bridge_sample_id"] = 42
            assert deliver()[1] is not None
            hardware.write_actuation(Actuation(safety_stop=True), 1.0)
            assert json.loads(peer.recvfrom(4096)[0])["bridge_sample_id"] == 42
            frame["bridge_sample_id"] = True
            deliver()
            assert hardware.stats["invalid_sensor_frames"] == 1
            assert hardware._bridge_sample_id == 42
            del frame["bridge_sample_id"]
            deliver()
            hardware.write_actuation(Actuation(safety_stop=True), 2.0)
            assert "bridge_sample_id" not in json.loads(peer.recvfrom(4096)[0])
        finally:
            hardware.close()
