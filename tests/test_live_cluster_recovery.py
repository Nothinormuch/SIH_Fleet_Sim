"""Continuous geometry captured from the failed 10-process overlap diagnostic."""
import math
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.amr import (AMRBrain, Peer, POLICY_BIOS_PIBT_V6, POLICY_BIOS_PIBT_V7,
                     ST_RETREAT, ST_TO_DROP, Task)
from src.environment import open_floor
from src.geometry import cell_center, dist, to_cell
from src.priority import PriorityKey
from src.settings import DEFAULT
from src.vendor_adapter import SafeCommandGate
from src.world import Actuation, Detection, World


def _captured_cluster(policy, rotation=0):
    env = open_floor(16, 16)
    world = World(env, DEFAULT, seed=0)
    extent = env.width * DEFAULT.cell_m

    def turn_xy(x, y):
        for _ in range(rotation):
            x, y = extent - y, x
        return x, y

    def turn_cell(cell):
        x, y = cell
        for _ in range(rotation):
            x, y = env.width - 1 - y, x
        return x, y

    # AMR10 stopped before crossing a quantisation boundary. AMR08 shares its
    # reported cell; AMR04 blocks the cell beyond it. Neither body is removed.
    for rid, x, y, heading in (("AMR10", 3.505, 11.115, -1.563),
                               ("AMR08", 3.495, 10.209, 3.069),
                               ("AMR04", 4.039, 11.959, -3.112)):
        x, y = turn_xy(x, y)
        robot = world.add_robot(rid, to_cell((x, y), DEFAULT.cell_m))
        robot.x, robot.y = x, y
        robot.theta = heading + rotation * math.pi / 2
    brain = AMRBrain("AMR10", env, DEFAULT, policy=policy)
    brain.task = Task("EDGE-09-0", turn_cell((9, 11)), turn_cell((2, 2)))
    brain.goal, brain.state = brain.task.drop, ST_TO_DROP
    brain._last_cell = world.sense(brain.rid).cell
    brain.path, brain.pidx = [brain._last_cell, turn_cell((2, 8))], 1
    for rid in ("AMR08", "AMR04"):
        s = world.sense(rid)
        brain.peers[rid] = Peer(rid, cell=s.cell, pose=s.pose,
            last_seen=world.t, pose_seen_t=world.t, state=ST_TO_DROP,
            task_id="T_" + rid, goal=s.cell,
            priority_key=PriorityKey(robot_id=rid))
    return brain, world, turn_cell((1, 7))


@pytest.mark.parametrize("policy", [POLICY_BIOS_PIBT_V6, POLICY_BIOS_PIBT_V7])
@pytest.mark.parametrize("rotation", range(4))
def test_offcentre_cluster_has_a_valid_admitted_cell_staging_route(policy, rotation):
    brain, world, target = _captured_cluster(policy, rotation)
    sensors = world.sense(brain.rid)
    assert not brain._recovery_route_clear(sensors, (cell_center(target, DEFAULT.cell_m),))
    assert brain._recovery_route(sensors, target) is None
    route = brain._recovery_route(sensors, target, boundary_fallback=True)
    assert route is not None
    assert len(route) == 2
    assert to_cell(route[0], DEFAULT.cell_m) in (sensors.cell, target)
    assert brain._recovery_route_clear(sensors, route)


@pytest.mark.parametrize("policy", [POLICY_BIOS_PIBT_V6, POLICY_BIOS_PIBT_V7])
@pytest.mark.parametrize("rotation", range(4))
@pytest.mark.parametrize("noise,seed", [(0.0, 0), (0.02, 0), (0.02, 7)])
def test_duplicate_clearance_executes_past_the_captured_cluster(policy, rotation, noise, seed):
    brain, world, target = _captured_cluster(policy, rotation)
    world.rng.seed(seed)
    start = world.sense(brain.rid).pose[:2]
    gate = SafeCommandGate(DEFAULT.robot.v_max, DEFAULT.robot.omega_max)
    # Close-body translation is limited to 0.12 m/s; the bounded two-cell
    # manoeuvre includes turning and destination commit rounds (under 30 s).
    for _ in range(1500):
        for rid, peer in brain.peers.items():
            s = world.sense(rid)
            peer.cell, peer.pose = s.cell, s.pose
            peer.last_seen = peer.pose_seen_t = world.t
        command, _ = brain.step(world.t, world.sense(brain.rid, pose_noise_m=noise), [])
        gate.accept(command, world.t)
        world.step(.02, {brain.rid: gate.command(world.t), "AMR08": Actuation(safety_stop=True),
                         "AMR04": Actuation(safety_stop=True)})
        if dist(world.sense(brain.rid).pose[:2], cell_center(target, DEFAULT.cell_m)) < .12:
            break
    assert not world.contacts
    assert dist(world.sense(brain.rid).pose[:2], start) > 1.0
    assert dist(world.sense(brain.rid).pose[:2], cell_center(target, DEFAULT.cell_m)) < .12


@pytest.mark.parametrize("policy", [POLICY_BIOS_PIBT_V6, POLICY_BIOS_PIBT_V7])
def test_duplicate_escape_commits_only_after_admission_and_remains_bounded(policy):
    brain, world, target = _captured_cluster(policy)
    sensors = world.sense(brain.rid)
    assert brain._repair_duplicate_cell(0.0, sensors)
    assert brain._hold and brain.state == ST_RETREAT
    assert brain._retreat_for == "duplicate-clearance"
    original = list(brain._recovery_waypoints)
    brain._traffic_loop(0.1, sensors, [])
    assert brain._hold and brain.blocked_on == "cell-gate"
    brain._traffic_loop(1.0, sensors, [])
    assert not brain._hold
    # A noisy cell-boundary report cannot change the committed destination or
    # let the normal no-progress replan discard its still-valid metric route.
    boundary = replace(sensors, cell=target)
    brain._route_loop(21.0, boundary, [])
    assert brain.state == ST_RETREAT
    assert brain._recovery_waypoints == original
    brain._route_loop(30.0, sensors, [])
    assert brain.state != ST_RETREAT and brain._cell_repair_target is None


@pytest.mark.parametrize("policy", [POLICY_BIOS_PIBT_V6, POLICY_BIOS_PIBT_V7])
def test_duplicate_escape_rechecks_occupancy_priority_and_new_obstacle(policy):
    brain, world, target = _captured_cluster(policy)
    sensors = world.sense(brain.rid)
    assert brain._repair_duplicate_cell(0.0, sensors)
    brain._traffic_loop(0.1, sensors, [])
    peer = brain.peers["AMR08"]
    peer.cell = target
    brain._traffic_loop(1.0, sensors, [])
    assert brain._hold and brain.blocked_on == peer.rid
    peer.cell = sensors.cell
    peer.intent = [target]
    peer.intent_seen_t = 1.0
    peer.priority_key = PriorityKey(robot_id=peer.rid, waiting_age=100)
    brain._traffic_loop(1.0, sensors, [])
    assert brain._hold and brain.blocked_on == peer.rid
    peer.intent = []
    brain._traffic_loop(2.0, sensors, [])
    assert not brain._hold
    # A newly detected person on the departure segment still forbids motion.
    person = Detection(x=2.9, y=11.14, r=.3, range_m=.61)
    blocked = replace(sensors, detections=[*sensors.detections, person])
    command = brain._follow_recovery(2.0, blocked)
    assert command is not None and command.v == 0.0


@pytest.mark.parametrize("policy", [POLICY_BIOS_PIBT_V6, POLICY_BIOS_PIBT_V7])
def test_inherited_side_step_preserves_metric_route_while_turning(monkeypatch, policy):
    from src import amr
    brain, world, target = _captured_cluster(policy)
    sensors = world.sense(brain.rid)
    decision = SimpleNamespace(next_cells={brain.rid: target}, inherited_from={brain.rid: "AMR04"},
                               blocked_by={}, backtracks=0)
    monkeypatch.setattr(amr, "pibt_step", lambda *args, **kwargs: decision)
    original_goal = brain.goal
    assert brain._bios_pibt_coordinate(0.0, sensors, (2, 8)) == "cell-gate"
    assert brain.state == ST_RETREAT and brain._retreat_for == "pibt-clearance"
    assert brain._hold and brain.goal == original_goal
    committed = list(brain._recovery_waypoints)
    # A different proposed step while it is still steering cannot replace the
    # committed move; ordinary cell admission remains active in _traffic_loop.
    decision.next_cells[brain.rid] = (2, 6)
    brain._traffic_loop(0.1, sensors, [])
    brain._traffic_loop(1.0, sensors, [])
    assert brain._cell_repair_target == target
    assert brain._recovery_waypoints == committed
    assert not brain._hold


def test_committed_recovery_on_directed_map_requires_live_cell_claim():
    from src.scenarios import SHOWCASE_SCENARIOS
    sc = SHOWCASE_SCENARIOS["showcase_grand_challenge"]["builder"](n_robots=10, seed=0)
    world = World(sc.env, DEFAULT, seed=0)
    world.add_robot("AMR01", (19, 6))
    brain = AMRBrain("AMR01", sc.env, DEFAULT, policy=POLICY_BIOS_PIBT_V7)
    assert brain.circulation.enabled
    brain.task = Task("DIRECTED", (22, 9), (1, 1))
    brain.goal = brain.task.pick
    sensors = world.sense(brain.rid)
    target = (18, 6)
    route = brain._recovery_route(sensors, target)
    assert route is not None
    brain._commit_admitted_recovery(0.0, sensors, target, route, "duplicate-clearance")
    brain._traffic_loop(0.0, sensors, [])
    assert brain._hold
    brain._bios_claim(0.0, sensors, target, [])
    brain._traffic_loop(1.0, sensors, [])
    assert not brain._hold
    brain._claims[brain._cell_zone_id(target)] = ("OTHER", 20.0, 0, 0, None)
    brain._traffic_loop(1.1, sensors, [])
    assert brain._hold and brain.blocked_on == "OTHER"
