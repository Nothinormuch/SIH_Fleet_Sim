"""Additive execution identity is authenticated, optional, and radio-draw neutral."""

from dataclasses import replace
import json

import pytest

from src import messages as msg
from src.amr import (AMRBrain, POLICY_BIOS_PIBT_V6, POLICY_BIOS_PIBT_V7,
                     ST_TO_DROP, Task)
from src.environment import chokepoint_warehouse, open_floor
from src.settings import DEFAULT
from src.transport import SimNetwork
from src.world import World


def _packet(*, extended=True, seq=1, t=1.0):
    metadata = {} if not extended else {
        "task_generation": 2, "task_descriptor_hash": "a" * 64,
        "task_auction_epoch": 3,
    }
    return msg.heartbeat("B", seq, t, (17.5, 6.3, 0.0), (12, 4), 0.8,
                         "DEGRADED_P2P", ST_TO_DROP, "JOB", goal=(22, 4),
                         **metadata)


def test_execution_identity_authenticated_round_trip_and_legacy_compatibility():
    secret = b"unit-test-only-execution-key"
    for extended in (False, True):
        packet = _packet(extended=extended)
        wire = msg.encode(packet, secret, "session")
        decoded, reason = msg.decode_packet(wire, secret, require_auth=True)
        assert reason is None and decoded.body == packet.body
        assert len(wire) < msg.MAX_DATAGRAM_BYTES
        if extended:
            tampered = json.loads(wire)
            tampered["body"]["tg"] += 1
            decoded, reason = msg.decode_packet(json.dumps(tampered).encode(),
                                                secret, require_auth=True)
            assert decoded is None and reason == "invalid_auth"


@pytest.mark.parametrize("change", (
    {"tg": None}, {"tg": True}, {"tg": -1}, {"tg": msg.MAX_AUCTION_EPOCH + 1},
    {"tdh": "not-a-hash"}, {"tdh": None}, {"te": True}, {"te": -1},
    {"te": msg.MAX_AUCTION_EPOCH + 1}, {"task": None},
))
def test_malformed_execution_identity_is_rejected(change):
    packet = _packet()
    packet = replace(packet, body={**packet.body, **change})
    decoded, reason = msg.decode_packet(json.dumps(packet.to_dict()).encode())
    assert decoded is None and reason == "invalid_heartbeat_task_identity"
    with pytest.raises(ValueError, match="invalid_heartbeat_task_identity"):
        msg.encode(packet)


@pytest.mark.parametrize("missing", ("tg", "tdh", "te"))
def test_partial_execution_identity_is_not_legacy(missing):
    packet = _packet()
    body = dict(packet.body)
    body.pop(missing)
    decoded, reason = msg.decode_packet(json.dumps(replace(packet, body=body).to_dict()).encode())
    assert decoded is None and reason == "invalid_heartbeat_task_identity"


def test_execution_metadata_preserves_seeded_channel_but_counts_actual_bytes():
    legacy, upgraded = _packet(extended=False), _packet()
    assert msg.delivery_identity_body(upgraded) == legacy.body
    cfg = replace(DEFAULT, net=replace(DEFAULT.net, loss=0.35))
    outcomes = []
    for extended in (False, True):
        net = SimNetwork(cfg, seed=7)
        for rid in ("A", "B", "C"):
            net.register(rid)
        for seq in range(100):
            packet = _packet(extended=extended, seq=seq, t=seq * 0.02)
            net.send(packet.t, "B", packet)
        schedules = {
            rid: [(arrival, tie, packet.seq) for arrival, tie, packet in queue]
            for rid, queue in net._inbox.items()
        }
        outcomes.append((schedules, dict(net.stats)))
    assert outcomes[0][0] == outcomes[1][0]
    for stat in ("sent", "delivered", "dropped_loss", "dropped_deadzone", "dropped_partition"):
        assert outcomes[0][1][stat] == outcomes[1][1][stat]
    assert outcomes[1][1]["bytes"] > outcomes[0][1]["bytes"]


def _sender(policy=POLICY_BIOS_PIBT_V7, *, release=True, open_map=False):
    env = open_floor(25, 9) if open_map else chokepoint_warehouse(length=13)
    cfg = replace(DEFAULT, traffic=replace(DEFAULT.traffic, v7_passage_release=release))
    brain = AMRBrain("B", env, cfg, policy=policy, allocation_policy="auction_bundle")
    brain.task = Task("JOB", (2, 4), (22, 4), generation=4, auction_epoch=3)
    brain.goal, brain.state = brain.task.drop, ST_TO_DROP
    world = World(env, cfg)
    world.add_robot("B", (2, 4))
    return brain, world.sense("B")


@pytest.mark.parametrize("policy,release,open_map,expected", (
    (POLICY_BIOS_PIBT_V6, True, False, False),
    (POLICY_BIOS_PIBT_V7, True, False, True),
    (POLICY_BIOS_PIBT_V7, False, False, True),
    (POLICY_BIOS_PIBT_V7, True, True, False),
))
def test_only_passage_capable_v7_sends_identity_and_ablation_has_same_overhead(
        policy, release, open_map, expected):
    brain, sensors = _sender(policy, release=release, open_map=open_map)
    outbox = []
    brain._broadcast(0.0, sensors, outbox)
    heartbeat = next(packet for packet in outbox if packet.type == msg.HEARTBEAT)
    assert ("tdh" in heartbeat.body) is expected
    if expected:
        assert (heartbeat.body["tg"], heartbeat.body["tdh"], heartbeat.body["te"]) == (
            brain.task.generation, brain.task.descriptor_hash, brain.task.auction_epoch)


def test_directed_circulation_does_not_send_unused_identity():
    from src.scenarios import SHOWCASE_SCENARIOS
    scenario = SHOWCASE_SCENARIOS["showcase_grand_challenge"]["builder"](n_robots=3, seed=0)
    brain = AMRBrain("B", scenario.env, DEFAULT, policy=POLICY_BIOS_PIBT_V7)
    assert brain.circulation.enabled
    brain.task = Task("JOB", scenario.env.stations[0], scenario.env.docks[0])
    world = World(scenario.env, DEFAULT)
    world.add_robot("B", scenario.env.stations[0])
    outbox = []
    brain._broadcast(0.0, world.sense("B"), outbox)
    heartbeat = next(packet for packet in outbox if packet.type == msg.HEARTBEAT)
    assert not {"tg", "tdh", "te"}.intersection(heartbeat.body)


def test_identity_transition_forces_fresh_heartbeat_even_when_pose_is_unchanged():
    brain, sensors = _sender()
    # Catalog identity must not replace the actual execution identity on the wire.
    brain.open_tasks[brain.task.tid] = replace(brain.task, generation=99)
    first = []
    brain._broadcast(0.0, sensors, first)
    brain.task.auction_epoch += 1
    second = []
    brain._broadcast(0.01, replace(sensors, t=0.01), second)
    heartbeat = next(packet for packet in second if packet.type == msg.HEARTBEAT)
    assert heartbeat.body["te"] == 4 and heartbeat.body["tg"] == 4


def test_v7_ablation_and_enabled_metadata_have_identical_encoded_size():
    sizes = []
    for enabled in (False, True):
        brain, sensors = _sender(release=enabled)
        outbox = []
        brain._broadcast(0.0, sensors, outbox)
        packet = next(packet for packet in outbox if packet.type == msg.HEARTBEAT)
        sizes.append(len(msg.encode(packet, b"unit-test-key", "session")))
    assert sizes[0] == sizes[1]


def test_sender_uses_compact_reference_then_regular_full_refresh():
    brain, sensors = _sender()
    packets = []
    for t in (0.0, 1.0, 2.1):
        outbox = []
        brain._broadcast(t, replace(sensors, t=t), outbox)
        packets.append(next(packet for packet in outbox if packet.type == msg.HEARTBEAT))
    assert packets[0].body["tdh"] == brain.task.descriptor_hash
    assert packets[1].body["tr"] == packets[0].seq
    assert not {"tg", "tdh", "te"}.intersection(packets[1].body)
    assert packets[2].body["tdh"] == brain.task.descriptor_hash
    assert packets[2].seq > packets[1].seq
    legacy = replace(packets[1], body=msg.delivery_identity_body(packets[1]))
    assert len(msg.encode(packets[1])) - len(msg.encode(legacy)) == len(str(packets[0].seq)) + 6


@pytest.mark.parametrize("reference", (True, -1, None, "1", 10, 11))
def test_malformed_unknown_shape_or_nonpreceding_reference_is_rejected(reference):
    packet = _packet(extended=False, seq=10)
    packet = replace(packet, body={**packet.body, "tr": reference})
    decoded, reason = msg.decode_packet(json.dumps(packet.to_dict()).encode())
    assert decoded is None and reason == "invalid_heartbeat_task_reference"


def test_full_identity_and_reference_cannot_be_mixed():
    packet = _packet(seq=10)
    packet = replace(packet, body={**packet.body, "tr": 1})
    with pytest.raises(ValueError, match="invalid_heartbeat_task_identity"):
        msg.encode(packet)


def test_reference_is_authenticated_and_preserves_legacy_radio_identity():
    packet = _packet(extended=False, seq=10)
    reference = replace(packet, body={**packet.body, "tr": 1})
    assert msg.delivery_identity_body(reference) == packet.body
    wire = msg.encode(reference, b"unit-key", "session")
    decoded, reason = msg.decode_packet(wire, b"unit-key", require_auth=True)
    assert reason is None and decoded.body["tr"] == 1
    data = json.loads(wire)
    data["body"]["tr"] = 2
    decoded, reason = msg.decode_packet(json.dumps(data).encode(), b"unit-key", require_auth=True)
    assert decoded is None and reason == "invalid_auth"
