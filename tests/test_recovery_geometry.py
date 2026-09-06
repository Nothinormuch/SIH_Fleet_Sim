"""Recovery must improve real motion, not just replace rack contacts with a hold."""

import math
from dataclasses import replace

import pytest

from src.amr import (AMRBrain, POLICY_BIOS_PIBT_V5, POLICY_BIOS_PIBT_V6,
                     POLICY_BIOS_PIBT_V7, RECOVERY_MAP_MARGIN_M, ST_TO_DROP,
                     ST_RETREAT, Task)
from src.environment import FREE, RACK, Warehouse
from src.geometry import cell_center, dist, segment_rectangle_distance, to_cell
from src.settings import DEFAULT
from src.world import Actuation, Detection, World


def _directed_cut_fixture(policy=POLICY_BIOS_PIBT_V7):
    from src.scenarios import SHOWCASE_SCENARIOS
    scenario = SHOWCASE_SCENARIOS["showcase_grand_challenge"]["builder"](
        n_robots=10, seed=0)
    world = World(scenario.env, DEFAULT, seed=0)
    world.add_robot("AMR01", (19, 6))
    world.add_robot("CUT_EAST", (21, 6))
    world.add_robot("CUT_NORTH", (19, 4))
    brain = AMRBrain("AMR01", scenario.env, DEFAULT, policy=policy)
    brain.task = Task("CUT_TASK", (22, 9), (1, 1))
    brain.goal = brain.task.pick
    brain.state = "to_pick"
    brain._last_progress_t = -30.0  # A sustained radio-blind stall, not a brief crossing.
    brain._dynamic_blocked_until = {(19, 4): 1000.0, (21, 6): 1000.0}
    return brain, world


@pytest.mark.parametrize("policy", (POLICY_BIOS_PIBT_V6, POLICY_BIOS_PIBT_V7))
def test_anonymous_directed_cut_stages_three_cells_but_executes_one_leased_step(policy):
    brain, world = _directed_cut_fixture(policy)
    sensors = world.sense(brain.rid, pose_noise_m=0)
    original_blocks = dict(brain._dynamic_blocked_until)
    original_goal = brain.goal
    assert not brain._v6_dynamic_clearance(0, sensors)
    assert brain._v6_dynamic_cut_escape(0, sensors)
    assert brain.path == [(19, 6), (18, 6)]
    assert brain._retreat_for == "dynamic-cut"
    assert brain._cell_repair_target == (18, 6)
    assert brain.goal == original_goal
    assert brain._dynamic_blocked_until == original_blocks
    assert not brain.circulation.allows(brain.env, (19, 6), (18, 6))
    outbox = []
    brain._traffic_loop(0, sensors, outbox)
    assert brain._hold and brain.blocked_on == "gate"
    brain._bios_claim(0, sensors, (18, 6), outbox)
    assert outbox  # Its ordinary cell claim is sent, not an immediate free move.
    brain._traffic_loop(1.0, sensors, [])
    assert not brain._hold
    zone = brain._cell_zone_id((18, 6))
    brain._claims[zone] = ("OTHER", 20.0, 0, 0, None)
    brain._traffic_loop(1.1, sensors, [])
    assert brain._hold and brain.blocked_on == "OTHER"


def test_cut_escape_never_replaces_a_valid_directed_route_or_parks_idle_robots():
    brain, world = _directed_cut_fixture()
    sensors = world.sense(brain.rid, pose_noise_m=0)
    brain._dynamic_blocked_until.pop((21, 6))
    assert not brain._v6_dynamic_cut_escape(0, sensors)
    brain._dynamic_blocked_until[(21, 6)] = 1000.0
    brain.task = None
    assert not brain._v6_dynamic_cut_escape(2, sensors)
    assert brain.path == []


def test_cut_escape_does_not_interrupt_a_brief_empty_route_or_a_live_mesh():
    from src.amr import Peer
    brain, world = _directed_cut_fixture()
    sensors = world.sense(brain.rid, pose_noise_m=0)
    brain._last_progress_t = 0.0
    assert not brain._v6_dynamic_cut_escape(1, sensors)
    brain._last_progress_t = -30.0
    peer = Peer("CONNECTED")
    peer.last_seen = 2.0
    brain.peers[peer.rid] = peer
    assert not brain._v6_dynamic_cut_escape(2, sensors)


@pytest.mark.parametrize("kind", ("dynamic-obstacle", "dynamic-cut", None))
def test_every_validated_retreat_has_time_to_reach_its_centre_but_stays_bounded(kind):
    brain, world, target = _fixture(racks=False)
    sensors = world.sense(brain.rid, pose_noise_m=0)
    brain.path = [sensors.cell, target]
    brain.pidx = 1
    brain.state = ST_RETREAT
    brain.retreat_target = target
    brain._retreat_for = kind
    brain._retreat_since = 0.0
    brain._cell_repair_target = target
    # Entering the destination grid cell is not enough; the validated metric
    # route must finish. Eight seconds is still inside its bounded travel budget.
    at_boundary = replace(sensors, cell=target)
    brain._route_loop(8.0, at_boundary, [])
    assert brain.state == ST_RETREAT and brain.retreat_target == target
    brain._route_loop(21.0, at_boundary, [])
    assert brain.state != ST_RETREAT  # A physically blocked retreat still expires.


def _idle_chain_fixture():
    from src.amr import Peer
    from src.scenarios import SHOWCASE_SCENARIOS
    scenario = SHOWCASE_SCENARIOS["showcase_grand_challenge"]["builder"](
        n_robots=10, seed=1)
    world = World(scenario.env, DEFAULT, seed=1)
    world.add_robot("FRONT", (12, 1))
    brain = AMRBrain("FRONT", scenario.env, DEFAULT, policy=POLICY_BIOS_PIBT_V7)
    for rid, cell, blocker in (("A", (13, 1), "FRONT"),
                               ("B", (13, 2), "A"),
                               ("C", (13, 3), "B")):
        peer = Peer(rid)
        peer.cell, peer.blocked_on, peer.last_seen = cell, blocker, 0.0
        peer.goal, peer.task_id = (12, 1), f"T_{rid}"
        peer.intent = [(13, 1), (12, 1), (11, 1), (10, 1)]
        brain.peers[rid] = peer
    return brain, world


def test_idle_clearance_wait_chain_releases_future_intent_not_physical_occupancy():
    from src.amr import Peer
    brain, world = _idle_chain_fixture()
    sensors = world.sense(brain.rid, pose_noise_m=0)
    assert [p.rid for p in brain._clearance_dependents(0)] == ["A", "B", "C"]
    brain._vacate_if_in_the_way(0, sensors)
    assert brain.goal == (11, 1) and brain.path == [(12, 1), (11, 1)]
    occupant = Peer("PHYSICAL")
    occupant.cell, occupant.last_seen = (11, 1), 0.0
    brain.peers[occupant.rid] = occupant
    brain.goal, brain.path = None, []
    brain._vacate_if_in_the_way(0, sensors)
    assert brain.goal != (11, 1)


def test_clearance_dependency_chains_stop_at_stale_nodes_and_cycles():
    from src.amr import Peer
    brain, _world = _idle_chain_fixture()
    brain.peers["B"].last_seen = -20.0
    for rid, blocker in (("LOOP1", "LOOP2"), ("LOOP2", "LOOP1")):
        peer = Peer(rid)
        peer.last_seen, peer.blocked_on = 0.0, blocker
        brain.peers[rid] = peer
    assert [p.rid for p in brain._clearance_dependents(0)] == ["A"]


def test_offcentre_singleton_parking_yields_to_an_explicit_loaded_request():
    brain, world = _idle_chain_fixture()
    robot = world.robots[brain.rid]
    robot.x += 0.3
    sensors = world.sense(brain.rid, pose_noise_m=0)
    brain.goal = sensors.cell
    brain.path, brain.pidx = [sensors.cell], 0
    brain._task_loop(0, sensors, [])
    assert brain.goal is None and not brain.path
    brain._task_loop(0.02, sensors, [])
    assert brain.goal == (11, 1) and len(brain.path) == 2


def test_a_real_one_cell_parking_move_finishes_after_crossing_quantisation_boundary():
    brain, world = _idle_chain_fixture()
    robot = world.robots[brain.rid]
    robot.x -= 0.8
    sensors = world.sense(brain.rid, pose_noise_m=0)
    assert sensors.cell == (11, 1)
    brain.goal = (11, 1)
    brain.path, brain.pidx = [(12, 1), (11, 1)], 1
    brain._task_loop(0, sensors, [])
    assert brain.goal == (11, 1) and brain.path == [(12, 1), (11, 1)]


@pytest.mark.parametrize("policy", (POLICY_BIOS_PIBT_V6, POLICY_BIOS_PIBT_V7))
def test_countercirculation_idle_bay_is_one_leased_step_not_a_fifty_cell_parking_loop(policy):
    from src.amr import Peer
    brain, world = _idle_chain_fixture()
    brain.policy = policy
    body = world.robots[brain.rid]
    body.x, body.y = cell_center((13, 1), DEFAULT.cell_m)
    body.y -= 0.2
    brain.peers.clear()
    for rid, cell in (("REQUESTER", (13, 2)), ("EAST", (14, 1))):
        peer = Peer(rid)
        peer.cell, peer.last_seen = cell, 0.0
        if rid == "REQUESTER":
            peer.goal, peer.task_id, peer.blocked_on = (12, 1), "TASK", brain.rid
            peer.intent = [(13, 1), (12, 1), (11, 1)]
        brain.peers[rid] = peer
        world.add_robot(rid, cell)
    sensors = world.sense(brain.rid, pose_noise_m=0)
    brain._last_cell = sensors.cell
    brain._vacate_if_in_the_way(0, sensors)
    assert brain.path == [(13, 1), (13, 0)]
    assert brain.goal != (12, 1)  # The requesting task's destination stays excluded.
    assert brain.state == ST_RETREAT and brain._retreat_for == "idle-clearance"
    assert brain._hold and brain.blocked_on == "gate"
    before = list(brain.path)
    brain._task_loop(0.02, sensors, [])
    assert brain.task is None and brain.path == before
    for _ in range(1500):
        sensors = world.sense(brain.rid, pose_noise_m=0.02)
        act, _outbox = brain.step(world.t, sensors, [])
        world.step(0.02, {brain.rid: act})
        assert not world.contacts
        if brain.state != ST_RETREAT and brain.goal is None:
            assert sensors.cell == (13, 0)
            assert dist(sensors.pose[:2], cell_center((13, 0), DEFAULT.cell_m)) < 0.16
            break
    else:
        pytest.fail(f"one-cell idle clearance did not finish: {sensors.pose}, {brain.path}")


def test_cut_escape_does_not_enter_an_observed_person_or_search_without_a_bound():
    brain, world = _directed_cut_fixture()
    sensors = world.sense(brain.rid, pose_noise_m=0)
    # The only three-step staging route starts west; a fresh anonymous return
    # there must veto it even though it has not yet earned a blocked-cell TTL.
    px, py = cell_center((18, 6), DEFAULT.cell_m)
    obstructed = replace(sensors, detections=[*sensors.detections,
                          Detection(px, py, 0.3, 0, 0)])
    assert not brain._v6_dynamic_cut_escape(0, obstructed)
    # Four cells from the same staging junction exceeds this local escape bound.
    robot = world.robots[brain.rid]
    robot.x, robot.y = cell_center((20, 6), DEFAULT.cell_m)
    sensors = world.sense(brain.rid, pose_noise_m=0)
    assert not brain._v6_dynamic_cut_escape(2, sensors)


@pytest.mark.parametrize("policy", (POLICY_BIOS_PIBT_V6, POLICY_BIOS_PIBT_V7))
def test_directed_cut_recovery_reaches_connected_region_without_contacts(policy):
    brain, world = _directed_cut_fixture(policy)
    staged = False
    for tick in range(1800):
        sensors = world.sense(brain.rid, pose_noise_m=0.02)
        brain._last_cell = sensors.cell
        if tick % 25 == 0:
            brain._route_loop(world.t, sensors, [])
        outbox = []
        if tick % 5 == 0:
            brain._traffic_loop(world.t, sensors, outbox)
        brain._bios_claim(world.t, sensors, brain._next_cell(), outbox)
        act = brain._safety(sensors, brain._follow(world.t, sensors))
        world.step(0.02, {brain.rid: act})
        assert not world.contacts
        staged |= brain._retreat_for == "dynamic-cut"
        if staged and brain.state != ST_RETREAT and brain.path:
            assert sensors.cell == (16, 6)
            assert brain.path[-1] == brain.goal == (22, 9)
            break
    else:
        pytest.fail(f"cut escape stranded: {sensors.pose}, {brain.path}, {brain.state}")


@pytest.mark.parametrize("policy", (POLICY_BIOS_PIBT_V6, POLICY_BIOS_PIBT_V7))
def test_fullstep_cut_staging_survives_normal_sensor_ttl_and_finishes_task(policy):
    _old_brain, world = _directed_cut_fixture(policy)
    brain = AMRBrain("AMR01", world.env, DEFAULT, policy=policy, home=(19, 6))
    brain.queue = [Task("CUT_TASK", (22, 9), (1, 1))]
    saw_episode = saw_expired_observation = saw_detour = False
    for _ in range(15000):
        sensors = world.sense(brain.rid, pose_noise_m=0.02)
        previous_witness = set(brain._dynamic_cut_witness)
        act, _outbox = brain.step(world.t, sensors, [])
        if brain._dynamic_cut_path:
            saw_episode = True
            assert len(brain._dynamic_cut_path) <= 4
            assert len(brain.path) <= 2
            if (21, 6) not in brain._dynamic_blocked_until:
                saw_expired_observation = True
                assert (21, 6) in brain._dynamic_cut_witness
        if previous_witness and not brain._dynamic_cut_path and brain.path:
            # The first ordinary route from staging must use its retained cut
            # witness, rather than undoing all three local recovery steps.
            assert not previous_witness.intersection(brain.path)
            saw_detour = True
        world.step(0.02, {brain.rid: act})
        assert not world.contacts
        if brain.completed:
            break
    else:
        pytest.fail(f"normal-TTL cut recovery did not finish: {sensors.pose}, {brain.path}")
    assert saw_episode and saw_expired_observation and saw_detour
    assert not brain._dynamic_cut_path and not brain._dynamic_cut_witness


@pytest.mark.parametrize("changed", ("task", "goal", "generation", "descriptor", "epoch"))
def test_staging_episode_is_time_bounded_and_cannot_follow_a_replaced_task(changed):
    brain, world = _directed_cut_fixture()
    sensors = world.sense(brain.rid, pose_noise_m=0)
    assert brain._v6_dynamic_cut_escape(0, sensors)
    assert 0 < brain._dynamic_cut_until <= 63.0
    assert not brain._cut_episode_valid(brain._dynamic_cut_until)
    assert not brain._dynamic_cut_path and not brain._dynamic_cut_witness
    assert brain.state != ST_RETREAT and not brain.path
    brain, world = _directed_cut_fixture()
    assert brain._v6_dynamic_cut_escape(0, world.sense(brain.rid, pose_noise_m=0))
    if changed == "task":
        brain.task = Task("REPLACEMENT", (1, 1), (2, 2))
    elif changed == "goal":
        brain.goal = (1, 1)
    elif changed == "generation":
        brain.task.generation += 1
    elif changed == "descriptor":
        brain.task.descriptor_hash = "replacement-descriptor"
    else:
        brain.task.auction_epoch += 1
    assert not brain._cut_episode_valid(4)
    assert brain.state != ST_RETREAT and not brain.path


def test_normal_task_lease_renewal_does_not_cancel_valid_staging():
    brain, world = _directed_cut_fixture()
    assert brain._v6_dynamic_cut_escape(0, world.sense(brain.rid, pose_noise_m=0))
    brain.task.lease_until += 100.0
    assert brain._cut_episode_valid(1)


def test_fullstep_cut_deadline_stops_motion_at_control_rate_not_route_period():
    _old, world = _directed_cut_fixture()
    brain = AMRBrain("AMR01", world.env, DEFAULT, policy=POLICY_BIOS_PIBT_V7,
                     home=(19, 6))
    brain.queue = [Task("CUT_TASK", (22, 9), (1, 1))]
    for _ in range(2000):
        sensors = world.sense(brain.rid, pose_noise_m=0.02)
        act, _ = brain.step(world.t, sensors, [])
        world.step(0.02, {brain.rid: act})
        if brain._retreat_for == "dynamic-cut" and sensors.v > 0.15:
            # A test-injected short deadline isolates the follower-rate contract;
            # no physical parameter or acceptance workload is changed.
            brain._dynamic_cut_until = world.t - 0.001
            sensors = world.sense(brain.rid, pose_noise_m=0.02)
            stopped, _ = brain.step(world.t, sensors, [])
            assert stopped.v == 0.0
            assert not brain._dynamic_cut_path and not brain._dynamic_cut_witness
            assert brain._cell_repair_target is None and brain.state != ST_RETREAT
            break
    else:
        pytest.fail("fixture never reached an executing cut-recovery step")
    assert not world.contacts


def test_cut_continuation_revalidates_new_bodies_and_its_wait_has_a_deadline():
    brain, world = _directed_cut_fixture()
    assert brain._v6_dynamic_cut_escape(0, world.sense(brain.rid, pose_noise_m=0))
    body = world.robots[brain.rid]
    body.x, body.y = cell_center((18, 6), DEFAULT.cell_m)
    brain._clear_recovery_route()
    brain.path = []
    world.add_robot("NEW_BODY", (17, 6))
    sensors = replace(world.sense(brain.rid, pose_noise_m=0), t=10)
    assert not brain._v6_dynamic_cut_escape(10, sensors)
    assert brain._dynamic_cut_path and brain.path == []
    deadline = brain._dynamic_cut_until
    assert not brain._v6_dynamic_cut_escape(deadline, replace(sensors, t=deadline))
    assert not brain._dynamic_cut_path and not brain._dynamic_cut_witness


def _rotate_cell(cell, turns, n=7):
    for _ in range(turns):
        cell = (n - 1 - cell[1], cell[0])
    return cell


def _rotate_point(point, turns, extent=7 * DEFAULT.cell_m):
    x, y, theta = point
    for _ in range(turns):
        x, y, theta = extent - y, x, theta + math.pi / 2
    return x, y, theta


def _fixture(turns=0, policy=POLICY_BIOS_PIBT_V7, seed=0, racks=True):
    grid = [[FREE] * 7 for _ in range(7)]
    if racks:
        for cell in ((2, 2), (4, 2), (2, 4), (4, 4), (2, 5), (4, 5),
                     (5, 2), (5, 4)):
            x, y = _rotate_cell(cell, turns)
            grid[y][x] = RACK
    env = Warehouse(7, 7, tuple(map(tuple, grid)), ((0, 0),), ((6, 6),))
    world = World(env, DEFAULT, seed)
    start = _rotate_cell((3, 3), turns)
    target = _rotate_cell((3, 4), turns)
    robot = world.add_robot("AMR24", start)
    # Exact physical state 3.98 s before the retained 50-AMR first rack contact,
    # translated by (-35,-11.2) m. Neighbour bodies use the captured stationary poses.
    robot.x, robot.y, robot.theta = _rotate_point(
        (5.549068918095095, 4.927285187135282, 2.356319261903692), turns)
    robot.v, robot.omega = 0.016, -0.3128411154540701
    for rid, point in (
            ("E", (6.4414549860264, 4.900599116184014, 0.0)),
            ("S", (4.90453184227868, 4.169717629937002, 0.0)),
            ("F", (4.94099717112688, 2.024704755003897, 0.0))):
        pose = _rotate_point(point, turns)
        other = world.add_robot(rid, to_cell(pose[:2], DEFAULT.cell_m))
        other.x, other.y, other.theta = pose
    brain = AMRBrain("AMR24", env, DEFAULT, policy=policy)
    brain.state = ST_TO_DROP
    brain.goal = target
    brain.path, brain.pidx = [start, target], 1
    brain._last_cell = start
    return brain, world, target


def test_exact_rectangle_distance_detects_corner_and_segment_crossing():
    rect = (40.6, 16.8, 42.0, 18.2)
    assert segment_rectangle_distance((40.549068918095095, 16.127285187135282),
                                      (39.9, 17.5), rect) == pytest.approx(0.333602065631)
    assert segment_rectangle_distance((0, 1), (4, 1), (1, 0, 2, 2)) == 0
    assert segment_rectangle_distance((0, 3), (4, 3), (1, 0, 2, 2)) == 1
    assert segment_rectangle_distance((0, 0), (0, 0), (1, 1, 2, 2)) == pytest.approx(math.sqrt(2))


@pytest.mark.parametrize("turns", range(4))
def test_captured_corner_rejects_direct_segment_and_finds_safe_staged_route(turns):
    brain, world, target = _fixture(turns)
    sensors = world.sense(brain.rid)
    assert not brain._recovery_static_segment_clear(sensors.pose[:2], cell_center(target, DEFAULT.cell_m))
    route = brain._recovery_route(sensors, target)
    assert route is not None and len(route) == 2
    assert brain._recovery_route_clear(sensors, route)


@pytest.mark.parametrize("policy", (POLICY_BIOS_PIBT_V6, POLICY_BIOS_PIBT_V7))
@pytest.mark.parametrize("turns", range(4))
@pytest.mark.parametrize("noise,seed", ((0.0, 0), (0.02, 0), (0.02, 17)))
def test_captured_recovery_actually_completes_without_contacts(policy, turns, noise, seed):
    brain, world, target = _fixture(turns, policy, seed)
    sensors = world.sense(brain.rid, pose_noise_m=noise)
    route = brain._recovery_route(sensors, target)
    if route:
        brain._install_recovery_route(0.0, sensors, target, route)
    else:
        # A noisy individual observation may have no certifiably clear departure.
        # It must stop/revalidate, then make progress from subsequent observations.
        assert noise > 0
        brain._cell_repair_target = target
    for _ in range(1600):
        sensors = world.sense(brain.rid, pose_noise_m=noise)
        brain._last_cell = sensors.cell
        act = brain._safety(sensors, brain._follow(world.t, sensors))
        if brain._cell_repair_target is not None:
            assert act.v <= 0.20
        world.step(0.02, {brain.rid: act})
        assert not world.contacts
        if (brain._cell_repair_target is None
                and dist((world.robots[brain.rid].x, world.robots[brain.rid].y),
                         cell_center(target, DEFAULT.cell_m)) < 0.15):
            break
    else:
        pytest.fail(f"recovery stranded: pose={sensors.pose}, route={brain._recovery_waypoints}, stats={brain.stats}")


def test_uncertainty_band_allows_monotonic_departure_but_not_more_erosion():
    brain, _, _ = _fixture()
    # Rack starts at x=5.6,y=5.6. This point has only 0.07m nominal body gap.
    start = (5.18, 6.0)
    assert brain._recovery_static_segment_clear(start, (5.0, 6.0))
    assert not brain._recovery_static_segment_clear(start, (5.20, 6.0))
    assert not brain._recovery_static_segment_clear((5.3, 6.0), (5.0, 6.0))


def test_final_contact_snapshot_commands_braking_not_acceleration():
    brain, world, target = _fixture()
    robot = world.robots[brain.rid]
    robot.x, robot.y, robot.theta = (5.299334882728026, 5.419355179009258, 1.9964627951286564)
    robot.v, robot.omega = 0.488, -0.02154530360292326
    sensors = world.sense(brain.rid)
    brain._cell_repair_target = target
    result = brain._safety(sensors, Actuation(1.1998657599515716, -0.026924148588370937))
    assert result.v == 0 and result.safety_stop
    # This is already too close to guarantee stopping from the captured momentum;
    # the preceding trajectory test proves prevention before reaching this state.


def test_clear_floor_preserves_direct_recovery_and_old_policy_interface():
    brain, world, target = _fixture(racks=False)
    world.robots = {brain.rid: world.robots[brain.rid]}
    sensors = world.sense(brain.rid)
    assert brain._recovery_route(sensors, target) == [cell_center(target, DEFAULT.cell_m)]
    old = AMRBrain("old", brain.env, DEFAULT, policy=POLICY_BIOS_PIBT_V5)
    old.path, old.pidx = brain.path, brain.pidx
    old._cell_repair_target = target
    assert old._safety(sensors, Actuation(0.8)).v == pytest.approx(0.8)


def test_recovery_cap_outlives_peer_omni_field_and_state_clears_on_new_route():
    brain, world, target = _fixture(racks=False)
    world.robots = {brain.rid: world.robots[brain.rid]}
    sensors = world.sense(brain.rid)
    brain._install_recovery_route(0, sensors, target, brain._recovery_route(sensors, target))
    assert brain._safety(sensors, Actuation(1.2)).v <= 0.20
    brain.path = [sensors.cell, (2, 3)]
    brain._follow(1.0, sensors)
    assert brain._cell_repair_target is None
    assert not brain._recovery_waypoints


def test_map_uncertainty_is_added_to_unchanged_physical_radius():
    brain, world, _ = _fixture()
    assert brain.cfg.robot == DEFAULT.robot
    assert world.cfg.robot.radius_m == 0.35
    assert RECOVERY_MAP_MARGIN_M >= 0.10
    sensors = world.sense(brain.rid)
    blocked = replace(sensors, pose=(5.30, 6.0, 0.0), cell=(3, 4))
    assert brain._recovery_route(blocked, (3, 3)) is None


def test_traffic_hold_preserves_in_place_steering_after_braking():
    brain, world, target = _fixture(racks=False)
    world.robots = {brain.rid: world.robots[brain.rid]}
    robot = world.robots[brain.rid]
    robot.theta, robot.v, robot.omega = 0.0, 0.0, 0.0
    sensors = world.sense(brain.rid)
    brain._install_recovery_route(0, sensors, target, brain._recovery_route(sensors, target))
    brain._hold = True
    act = brain._safety(sensors, brain._follow(0.0, sensors))
    assert act.v == 0 and act.omega != 0
    moving = replace(sensors, v=0.3)
    brake = brain._follow(0.02, moving)
    assert brake.v == brake.omega == 0


@pytest.mark.parametrize("noise,seed", ((0.0, 0), (0.02, 0), (0.02, 3)))
def test_physically_clear_robot_inside_uncertainty_band_can_recover(noise, seed):
    brain, world, _ = _fixture(seed=seed)
    world.robots = {brain.rid: world.robots[brain.rid]}
    robot = world.robots[brain.rid]
    robot.x, robot.y, robot.theta, robot.v, robot.omega = 5.18, 6.0, 0.0, 0.0, 0.0
    target = (3, 4)
    brain.path, brain.pidx = [(3, 3), target], 1
    brain._cell_repair_target = target
    for _ in range(700):
        sensors = world.sense(brain.rid, pose_noise_m=noise)
        act = brain._safety(sensors, brain._follow(world.t, sensors))
        world.step(0.02, {brain.rid: act})
        assert not world.contacts
        if brain._cell_repair_target is None:
            break
    else:
        pytest.fail("uncertainty padding stranded a physically clear departure")
    assert dist((robot.x, robot.y), cell_center(target, DEFAULT.cell_m)) < 0.15


@pytest.mark.parametrize("heading", (0, math.pi / 2, math.pi, -math.pi / 2))
def test_new_unlabelled_person_cannot_be_overridden_by_committed_recovery(heading):
    brain, world, target = _fixture(racks=False)
    world.robots = {brain.rid: world.robots[brain.rid]}
    sensors = world.sense(brain.rid)
    brain._install_recovery_route(0, sensors, target, brain._recovery_route(sensors, target))
    x, y = sensors.pose[:2]
    human = Detection(x + 0.9 * math.cos(heading), y + 0.9 * math.sin(heading),
                      0.30, 0.9, -math.cos(heading), -math.sin(heading))
    sensors = replace(sensors, pose=(x, y, heading), v=0.0, omega=0.0,
                      clearance_dynamic_m=0.25, clearance_omni_m=0.25,
                      detections=[human])
    act = brain._safety(sensors, Actuation(0.20, 0.0))
    assert act.v == 0 and act.safety_stop
