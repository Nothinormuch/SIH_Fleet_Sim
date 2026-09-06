"""A taskless token owner must not stop beyond a waiter's intent horizon."""
import math

import pytest

from src.amr import (AMRBrain, Peer, Task, POLICY_BIOS_PIBT_V6,
                     POLICY_BIOS_PIBT_V7, POLICY_BIOS_PIBT_V3,
                     ST_BLOCKED, ST_CHARGING)
from src.environment import chokepoint_warehouse
from src.geometry import cell_center
from src.settings import DEFAULT
from src.transport import SimNetwork
from src.world import World


def _drain(policy=POLICY_BIOS_PIBT_V7):
    env = chokepoint_warehouse(length=13)
    world = World(env, DEFAULT, seed=0)
    rows = (("AMR09", (13, 4), math.pi),
            ("AMR03", (19, 5), -math.pi / 2),
            ("AMR10", (20, 4), math.pi))
    brains = {}
    for rid, cell, heading in rows:
        world.add_robot(rid, cell, heading)
        brains[rid] = AMRBrain(rid, env, DEFAULT, policy=policy, home=cell)
    idle = brains["AMR09"]
    idle._claims[0] = (idle.rid, 4.0, 0.0, 0, None)
    idle._claim_cid = 0
    for rid, pick, drop in (("AMR03", (24, 8), (3, 6)),
                            ("AMR10", (20, 8), (1, 8))):
        brain = brains[rid]
        brain.task = Task(f"WORK-{rid}", pick, drop)
        brain.goal, brain.state = drop, ST_BLOCKED
        brain.blocked_on, brain.blocked_since = idle.rid, -10.0
        brain._hold = True
        brain._replan(0.0, world.sense(rid).cell)
        brain._claims[0] = idle._claims[0]
        world.robots[rid].carrying = brain.task.tid
    for rid, brain in brains.items():
        for other_id, other in brains.items():
            if other_id == rid:
                continue
            body = world.robots[other_id]
            brain.peers[other_id] = Peer(
                other_id, cell=world.sense(other_id).cell,
                pose=(body.x, body.y, body.theta), state=other.state,
                goal=other.goal, task_id=other.task.tid if other.task else None,
                blocked_on=other.blocked_on,
                intent=list(other.path[other.pidx:])[:DEFAULT.traffic.intent_horizon],
                last_seen=0.0)
    # Match the decisive tail: the waiters' truncated intent ends one cell short
    # of the taskless physical token owner; their explicit wait is still fresh.
    for peer in idle.peers.values():
        peer.intent = [(x, 4) for x in range(19, 13, -1)]
    return brains, world


@pytest.mark.parametrize("policy", (POLICY_BIOS_PIBT_V6, POLICY_BIOS_PIBT_V7))
def test_idle_owner_continues_beyond_finite_intent_without_releasing_authority(policy):
    brains, world = _drain(policy)
    idle = brains["AMR09"]
    sensors = world.sense(idle.rid)
    assert all(sensors.cell not in peer.intent and peer.goal != sensors.cell
               for peer in idle.peers.values())
    claims = dict(idle._claims)
    idle._vacate_if_in_the_way(0.0, sensors)
    assert idle.goal == (12, 4)
    assert idle.path == [(13, 4), (12, 4)]
    assert idle.task is None
    assert idle._task_claims == {}
    assert idle._claims == claims


@pytest.mark.parametrize("policy", (POLICY_BIOS_PIBT_V6, POLICY_BIOS_PIBT_V7))
@pytest.mark.parametrize("condition", ("stale", "absent", "cycle", "active", "charging", "outside"))
def test_non_evidence_does_not_request_an_unrelated_idle_step(policy, condition):
    brains, world = _drain(policy)
    idle = brains["AMR09"]
    if condition == "stale":
        for peer in idle.peers.values():
            peer.last_seen = -10.0
    elif condition == "absent":
        for peer in idle.peers.values():
            peer.blocked_on = None
    elif condition == "cycle":
        idle.peers["AMR03"].blocked_on = "AMR10"
        idle.peers["AMR10"].blocked_on = "AMR03"
    elif condition == "active":
        idle.task = Task("OWN", (13, 4), (3, 2))
    elif condition == "charging":
        idle.state = ST_CHARGING
    elif condition == "outside":
        body = world.robots[idle.rid]
        body.x, body.y = cell_center((22, 2), DEFAULT.cell_m)
    idle._vacate_if_in_the_way(0.0, world.sense(idle.rid))
    assert idle.goal is None
    assert idle.path == []


def test_legacy_v3_vacate_behavior_is_unchanged():
    brains, world = _drain(POLICY_BIOS_PIBT_V3)
    idle = brains["AMR09"]
    idle._vacate_if_in_the_way(0.0, world.sense(idle.rid))
    assert idle.goal is None


@pytest.mark.parametrize("policy", (POLICY_BIOS_PIBT_V6, POLICY_BIOS_PIBT_V7))
def test_full_step_corridor_owner_drains_and_loaded_waiters_complete_safely(policy):
    brains, world = _drain(policy)
    net = SimNetwork(DEFAULT, seed=0)
    for rid in brains:
        net.register(rid)
    for _ in range(round(150 * DEFAULT.rates.world_hz)):
        t, commands = world.t, {}
        for rid, brain in brains.items():
            body = world.robots[rid]
            net.set_position(rid, (body.x / DEFAULT.cell_m, body.y / DEFAULT.cell_m))
            act, messages = brain.step(t, world.sense(rid), net.poll(t, rid))
            commands[rid] = act
            for message in messages:
                net.send(t, rid, message)
        world.step(1.0 / DEFAULT.rates.world_hz, commands)
        if all(brains[rid].completed for rid in ("AMR03", "AMR10")):
            break
    assert not world.contacts
    assert all(brains[rid].completed for rid in ("AMR03", "AMR10"))
    assert world.t < 150.0
    assert brains["AMR09"].task is None
    assert brains["AMR09"]._controlled_block(world.sense("AMR09").cell) is None
