"""Deployment-boundary tests for real clocks, real UDP, and hardware packets."""

import socket

import pytest

from src.amr import AMRBrain, POLICY_BIOS_PIBT_V3
from src.distributed_demo import run_distributed_demo
from src.edge_runtime import EdgeRuntime, sensors_from_dict
from src.environment import open_floor
from src.settings import DEFAULT
from src.world import World


class FakeTransport:
    def __init__(self):
        self.stats = {"sent": 0}
        self.sent = []

    def poll(self, max_msgs=256):
        return []

    def send(self, message):
        self.sent.append(message)
        self.stats["sent"] += 1

    def close(self):
        pass


def _available_udp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def test_edge_runtime_uses_local_clock_and_emits_peer_traffic():
    env = open_floor(8, 8)
    world = World(env, DEFAULT, seed=0)
    world.add_robot("A", (1, 1))
    brain = AMRBrain("A", env, DEFAULT, policy=POLICY_BIOS_PIBT_V3,
                     home=(1, 1))
    transport = FakeTransport()
    runtime = EdgeRuntime(brain, transport)

    runtime.tick(50_000.0, world.sense("A"))

    assert transport.sent
    assert all(message.t == 50_000.0 for message in transport.sent)
    assert runtime.metrics.ticks == 1


def test_hardware_sensor_schema_rejects_non_finite_values():
    frame = {
        "pose": [1.0, 2.0, 0.0], "v": 0.0, "omega": 0.0,
        "battery_frac": 1.0, "cell": [1, 2], "clearance_m": 3.0,
    }
    assert sensors_from_dict(frame).cell == (1, 2)
    frame["v"] = float("nan")
    with pytest.raises(ValueError):
        sensors_from_dict(frame)
    frame["v"] = 0.0
    frame["on_dock"] = "false"
    with pytest.raises(ValueError):
        sensors_from_dict(frame)


def test_three_real_processes_exchange_authenticated_multicast():
    result = run_distributed_demo(
        robots=3,
        duration_s=0.4,
        port=_available_udp_port(),
        realtime=True,
    )

    assert result["success"]
    assert result["separate_processes"]
    assert result["peer_messages_observed"]
    assert result["authenticated_transport"]
    assert result["control_deadlines_met"]
    assert len(set(result["clock_offsets_s"])) == 3
    assert result["contacts"]["robot-robot"] == 0
