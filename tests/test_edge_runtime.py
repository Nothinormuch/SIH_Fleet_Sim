"""Deployment-boundary tests for real clocks, real UDP, and hardware packets."""

import json
import socket

import pytest

from src.amr import AMRBrain, POLICY_BIOS_PIBT_V3, POLICY_BIOS_PIBT_V7
from src.distributed_demo import run_distributed_demo
from src.edge_runtime import (EdgeRuntime, SystemdNotifier, UdpJsonHardwareIO, actuation_from_dict, brain_visual_status,
                              build_parser, sensors_from_dict, sensors_to_dict)
from src.environment import open_floor
from src.settings import DEFAULT
from src.world import Actuation, World


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


def test_visual_coordination_diagnostics_are_bounded_and_detached():
    brain = AMRBrain("A", open_floor(8, 8), DEFAULT, policy=POLICY_BIOS_PIBT_V7)
    brain._hold = True
    brain.blocked_on = "B"
    brain._stall_since = 42.0
    brain._cell_repair_target = (2, 3)
    brain._recovery_waypoints = [(1.5, 2.5), (3.5, 4.5), (5.5, 6.5)]
    row = brain_visual_status(brain)["coordination"]
    assert row["hold"] and row["blocked_on"] == "B"
    assert row["stall_since"] == 42.0
    assert row["recovery_target"] == [2, 3]
    assert len(row["recovery_waypoints"]) == 2
    row["recovery_target"][0] = 99
    row["recovery_waypoints"][0][0] = 99
    assert brain._cell_repair_target == (2, 3)
    assert brain._recovery_waypoints[0] == (1.5, 2.5)


def _available_udp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def test_edge_node_default_policy_is_bios7():
    args = build_parser().parse_args([
        "--robot-id", "AMR01", "--robot-index", "0",
        "--sensor-port", "5001", "--actuator-port", "5002",
    ])

    from src.release_profile import DEFAULT_ROUTE_POLICY
    assert args.policy == DEFAULT_ROUTE_POLICY
    assert args.allocation_policy == "auction_bundle"
    assert not args.visual_telemetry


def test_optional_visual_status_preserves_udp_actuator_contract():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(1)
        status = {"id": "AMR01", "state": "to_drop", "carry": "T1"}
        for provider in (None, lambda: status):
            hardware = UdpJsonHardwareIO("127.0.0.1", 0, "127.0.0.1",
                                          receiver.getsockname()[1], visual_status=provider)
            try:
                hardware.write_actuation(Actuation(v=.2, omega=.1), 3.0)
                frame = json.loads(receiver.recvfrom(4096)[0])
                actuation, timestamp = actuation_from_dict(frame)
                assert actuation.v == .2 and actuation.omega == .1 and timestamp == 3.0
                assert not actuation.safety_stop
                if provider is None:
                    assert set(frame) == {"v", "omega", "safety_stop", "t"}
                else:
                    # Initial metadata is unknown; its provider runs only after
                    # the first actuator command has been transmitted.
                    assert frame["visual_status"] == {}
                    hardware.write_actuation(Actuation(safety_stop=True), 3.02)
                    assert json.loads(receiver.recvfrom(4096)[0])["visual_status"] == status
            finally:
                hardware.close()


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


def test_hardware_contract_round_trips_world_sensor_and_validates_actuation():
    env = open_floor(8, 8)
    world = World(env, DEFAULT, seed=4)
    world.add_robot("A", (1, 1))
    sensors = world.sense("A")

    decoded = sensors_from_dict(sensors_to_dict(sensors))
    assert decoded.pose == sensors.pose
    assert decoded.cell == sensors.cell
    assert decoded.battery_frac == sensors.battery_frac
    assert len(decoded.detections) == len(sensors.detections)

    actuation, timestamp = actuation_from_dict({
        "v": 0.5, "omega": -0.2, "safety_stop": False, "t": 123.0,
    })
    assert actuation.v == 0.5
    assert actuation.omega == -0.2
    assert not actuation.safety_stop
    assert timestamp == 123.0

    with pytest.raises(ValueError):
        actuation_from_dict({
            "v": 0.0, "omega": 0.0, "safety_stop": 0, "t": 0.0,
        })


def test_systemd_notifier_is_a_safe_noop_without_socket():
    notifier = SystemdNotifier(address="")

    assert not notifier.notify("READY=1")
    assert notifier.sent == 0
    assert notifier.failed == 0


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
