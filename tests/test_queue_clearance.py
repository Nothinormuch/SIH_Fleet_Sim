"""A trapped stationary leader may need a follower to open a leased bay."""
from dataclasses import replace

import pytest

from src.amr import (AMRBrain, Peer, POLICY_BIOS_PIBT_V6, POLICY_BIOS_PIBT_V7,
                     ST_BLOCKED, ST_RETREAT, Task)
from src.geometry import cell_center, dist
from src.scenarios import SHOWCASE_SCENARIOS
from src.settings import DEFAULT
from src.vendor_adapter import SafeCommandGate
from src.world import Actuation, World


def _fixture(policy=POLICY_BIOS_PIBT_V7):
    scenario = SHOWCASE_SCENARIOS["showcase_grand_challenge"]["builder"](n_robots=10, seed=4)
    world = World(scenario.env, DEFAULT, seed=4)
    world.t = 20.0
    for rid, cell, pose in (
        ("AMR06", (16, 5), (23.057, 8.113, 1.536)),
        ("AMR04", (16, 6), (22.691, 9.099, 0.0)),
        ("AMR02", (15, 6), (21.774, 9.108, 0.0)),
    ):
        robot = world.add_robot(rid, cell)
        robot.x, robot.y, robot.theta = pose
    brain = AMRBrain("AMR06", scenario.env, DEFAULT, policy=policy)
    brain.task = Task("LOADED", (0, 6), (30, 6))
    brain.goal = brain.task.drop
    brain.state = ST_BLOCKED
    brain.blocked_on, brain.blocked_since, brain._stall_since = "AMR04", 0.0, 0.0
    brain.path, brain.pidx = [(16, 5), (16, 6)], 1
    for rid in ("AMR04", "AMR02"):
        sensors = world.sense(rid)
        brain.peers[rid] = Peer(rid, cell=sensors.cell, pose=sensors.pose,
                               last_seen=world.t,
                               blocked_on="AMR04" if rid == "AMR02" else None)
    return brain, world


@pytest.mark.parametrize("policy", [POLICY_BIOS_PIBT_V6, POLICY_BIOS_PIBT_V7])
@pytest.mark.parametrize("leader_task", [None, "OTHER_TASK"])
def test_stalled_follower_executes_one_reserved_step_without_losing_task(policy, leader_task):
    brain, world = _fixture(policy)
    task = brain.task
    goal = brain.goal
    brain.peers["AMR04"].task_id = leader_task
    sensors = world.sense(brain.rid)
    assert brain._yield_for_stationary_gridlock(world.t, sensors, "AMR04")
    assert brain.state == ST_RETREAT and brain._retreat_for == "queue-clearance"
    assert brain._cell_repair_target == (16, 4)
    assert brain.task is task and brain.goal == goal
    brain._traffic_loop(world.t, sensors, [])
    assert brain._hold  # First announce/observe, never an immediate lease bypass.
    zone = brain._cell_zone_id((16, 4))
    brain._claims[zone] = ("OTHER", world.t + 10.0, 0, 0, None)
    brain._traffic_loop(world.t, sensors, [])
    assert brain._hold and brain.blocked_on == "OTHER"
    del brain._claims[zone]
    gate = SafeCommandGate(DEFAULT.robot.v_max, DEFAULT.robot.omega_max)
    for _ in range(1500):
        sensors = world.sense(brain.rid, pose_noise_m=0)
        for peer in brain.peers.values():
            peer.last_seen = world.t
        brain._traffic_loop(world.t, sensors, [])
        brain._bios_claim(world.t, sensors, brain._next_cell(), [])
        command = brain._safety(sensors, brain._follow(world.t, sensors))
        assert gate.accept(command, world.t)
        world.step(.02, {brain.rid: gate.command(world.t),
                         "AMR04": Actuation(), "AMR02": Actuation()})
        if dist(world.sense(brain.rid).pose[:2], cell_center((16, 4), DEFAULT.cell_m)) < .12:
            break
    else:
        pytest.fail("follower did not finish its bounded clearance step")
    assert brain.task is task and brain.goal == goal
    assert world.contacts == []


@pytest.mark.parametrize("guard", ["brief", "stale", "retreating-leader", "charging-leader",
                                  "single-follower", "moving-leader", "no-scanner"])
def test_clearance_is_not_triggered_for_ordinary_or_unverified_queue(guard):
    brain, world = _fixture()
    sensors = world.sense(brain.rid)
    if guard == "brief":
        brain.blocked_since = world.t - 1
    elif guard == "stale":
        brain.peers["AMR04"].last_seen = -100
    elif guard == "retreating-leader":
        brain.peers["AMR04"].state = ST_RETREAT
    elif guard == "charging-leader":
        brain.peers["AMR04"].state = "charging"
    elif guard == "single-follower":
        del brain.peers["AMR02"]
    elif guard == "moving-leader":
        sensors = replace(sensors, detections=[replace(d, vx=.2) for d in sensors.detections])
    else:
        sensors = replace(sensors, detections=[])
    assert not brain._yield_for_stationary_gridlock(world.t, sensors, "AMR04")
    assert brain.state == ST_BLOCKED and brain._cell_repair_target is None


def test_fan_in_recovery_never_uses_an_occupied_escape_cell():
    brain, world = _fixture()
    brain.peers["OCCUPANT"] = Peer("OCCUPANT", cell=(16, 4), last_seen=world.t)
    assert not brain._yield_for_stationary_gridlock(world.t, world.sense(brain.rid), "AMR04")
    assert brain.state == ST_BLOCKED and brain._cell_repair_target is None
