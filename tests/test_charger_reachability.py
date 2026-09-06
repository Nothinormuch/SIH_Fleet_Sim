"""Configured-but-unreachable chargers must not become zero-energy return legs.

This is an intentional eligibility correction, not a cache-parity optimization.
Deliberately dock-free legacy maps keep ordinary active-task compatibility; future
reservations retain their existing stricter requirement for a reachable charger.
"""
from __future__ import annotations

import pytest

from src.amr import AMRBrain, POLICY_BIOS_PIBT_V6, POLICY_BIOS_PIBT_V7, ST_TO_DROP, Task
from src.environment import DOCK, FREE, RACK, Warehouse
from src.messages import AWARD, BID, task_new
from src.planner import astar
from src.settings import DEFAULT
from src.task_allocation import ALLOCATION_AUCTION_BUNDLE
from src.world import World

POLICIES = (POLICY_BIOS_PIBT_V6, POLICY_BIOS_PIBT_V7)
START, PICK, DROP = (1, 2), (1, 1), (2, 3)
REMOTE_DOCK, LOCAL_DOCK = (7, 2), (3, 2)


def _fixture(policy, docks):
    # Every task endpoint is in the left component. The full-height rack wall
    # separates the configured remote charger without making the task itself
    # unreachable. The map is immutable and no planner/sensor is mocked.
    grid = [[RACK if x == 4 else FREE for x in range(9)] for _ in range(5)]
    for x, y in docks:
        grid[y][x] = DOCK
    env = Warehouse(9, 5, tuple(map(tuple, grid)), (PICK, DROP),
                    tuple(docks), "charger_reachability_witness")
    world = World(env, DEFAULT, seed=0)
    body = world.add_robot("AMR01", START)
    body.battery_wh = 0.8 * DEFAULT.robot.battery_full_wh
    brain = AMRBrain("AMR01", env, DEFAULT, policy=policy, home=START,
                     allocation_policy=ALLOCATION_AUCTION_BUNDLE)
    task = Task("TASK", PICK, DROP, cargo_type="heavy", cargo_weight=20,
                deadline=100)
    assert astar(env, START, PICK) and astar(env, PICK, DROP)
    return brain, world, task


def _announce_and_step(brain, world, task):
    packet = task_new("WMS", 1, 0, task.tid, task.pick, task.drop,
                      cargo_type=task.cargo_type, cargo_weight=task.cargo_weight,
                      deadline=task.deadline)
    auction_messages = []
    # The real bid window is 0.6 s. Two simulated seconds exercise opening,
    # award and acceptance without running a dashboard or real-time process.
    for tick in range(100):
        sensors = world.sense(brain.rid, pose_noise_m=0)
        act, outgoing = brain.step(world.t, sensors, [packet] if tick == 0 else [])
        auction_messages.extend(message for message in outgoing
                                if message.type in (BID, AWARD))
        world.step(0.02, {brain.rid: act})
    assert not world.contacts
    assert task.tid in brain.open_tasks  # Announcement was actually ingested.
    return auction_messages


@pytest.mark.parametrize("policy", POLICIES)
def test_configured_unreachable_charger_rejects_actual_bid_and_award(policy):
    brain, world, task = _fixture(policy, (REMOTE_DOCK,))
    assert astar(brain.env, DROP, REMOTE_DOCK) == []
    assert _announce_and_step(brain, world, task) == []
    assert brain.task is None
    assert brain.stats["energy_bids_suppressed"] > 0
    # Both ordinary/cached estimates and nonempty-cost estimates must fail closed.
    assert brain._task_estimate(task, START) is None
    assert brain._task_estimate(task, START, extra_cost={(2, 2): 0.3}) is None
    assert not brain._energy_feasible(task, world.sense(brain.rid), t=world.t)[0]


@pytest.mark.parametrize("policy", POLICIES)
@pytest.mark.parametrize("docks", ((REMOTE_DOCK, LOCAL_DOCK), (REMOTE_DOCK, DROP), ()))
def test_reachable_alternative_zero_step_dock_and_legacy_no_docks_still_bid(policy, docks):
    brain, world, task = _fixture(policy, docks)
    messages = _announce_and_step(brain, world, task)
    assert any(message.type == BID and message.body["task"] == task.tid
               for message in messages)
    assert any(message.type == AWARD and message.body["task"] == task.tid
               for message in messages)
    assert brain.task is not None and brain.task.tid == task.tid
    estimate = brain._task_estimate(task, START)
    assert estimate is not None
    legacy, _, same_task = _fixture(policy, ())
    no_return_leg = legacy._task_estimate(same_task, START)
    assert no_return_leg is not None
    assert estimate[1] == no_return_leg[1]  # Completion time ends at delivery.
    if LOCAL_DOCK in docks:
        assert estimate[0] > no_return_leg[0]  # Real return leg consumes energy.
    else:
        assert estimate == no_return_leg  # At-drop dock is valid, not unreachable.


@pytest.mark.parametrize("policy", POLICIES)
@pytest.mark.parametrize("docks,expected", (((REMOTE_DOCK,), False), ((), False),
                                            ((REMOTE_DOCK, LOCAL_DOCK), True),
                                            ((REMOTE_DOCK, DROP), True)))
def test_future_reservation_keeps_existing_reachable_charger_requirement(policy, docks, expected):
    brain, world, _ = _fixture(policy, docks)
    brain.task = Task("ACTIVE", PICK, (2, 1))
    brain.goal, brain.state = brain.task.drop, ST_TO_DROP
    future = Task("FUTURE", (2, 2), DROP)
    sensors = world.sense(brain.rid, pose_noise_m=0)
    estimate = brain._future_sequence_estimate(future, sensors, world.t)
    feasible = brain._future_sequence_feasible(future, sensors, world.t)
    assert (estimate is not None) is expected
    assert feasible[0] is expected
    if not expected:
        assert feasible[3] == "path_or_charger"
