"""Measured 50-AMR tail reduced to four bodies, without relaxing the world."""
from dataclasses import replace

import pytest

from src.amr import (AMRBrain, Peer, Task, POLICY_BIOS_PIBT_V6,
                     POLICY_BIOS_PIBT_V7, ST_CHARGING, ST_RETREAT)
from src.bios7_study import build_case
from src.geometry import cell_center
from src.settings import DEFAULT
from src.transport import SimNetwork
from src.world import Detection, Sensors, World


def _tail(policy=POLICY_BIOS_PIBT_V7, seed=0):
    env = build_case("fixed_floor", 50, 0).env
    world = World(env, DEFAULT, seed=seed)
    # Actual final poses in frozen 5cce5de fixed-floor 50-AMR seed 0. The task
    # owner is not in the protective field; two taskless parking robots are.
    rows = {
        "AMR08": ((14, 16), (20.81515401221581, 23.11878895685179, -0.02270751087217658),
                  "idle", None, [], 0, None),
        "AMR10": ((13, 16), (18.89624452554144, 23.264888102559045, -0.1479303650088237),
                  "blocked", (18, 16), [(x, 16) for x in range(13, 19)], 1, "AMR08"),
        "AMR14": ((16, 16), (23.098413801994187, 22.631649289335336, 1.5634098322913073),
                  "idle", (16, 16), [(16, 16)], 0, None),
        "AMR38": ((15, 16), (22.240765274920346, 23.136410351068218, -0.043972926246142435),
                  "blocked", (17, 16), [(15, 16), (16, 16), (17, 16)], 1, "AMR14"),
    }
    brains = {}
    for rid, (cell, pose, state, goal, path, pidx, blocker) in rows.items():
        body = world.add_robot(rid, cell)
        body.x, body.y, body.theta = pose
        brain = AMRBrain(rid, env, DEFAULT, policy=policy, home=cell)
        brain.state, brain.goal, brain.path, brain.pidx = state, goal, path, pidx
        brain.blocked_on, brain._hold = blocker, blocker is not None
        brain.blocked_since = -10.0 if blocker else None
        brain._stall_since = -10.0 if rid in ("AMR14", "AMR38") else None
        brain._last_progress_t, brain._last_cell = -10.0, cell
        brains[rid] = brain
    brains["AMR10"].task = Task("CAP-0093", (29, 30), (18, 16))
    world.robots["AMR10"].carrying = "CAP-0093"
    for rid, brain in brains.items():
        for other_id, other in brains.items():
            if other_id == rid:
                continue
            body = world.robots[other_id]
            brain.peers[other_id] = Peer(
                other_id, cell=other._last_cell, pose=(body.x, body.y, body.theta),
                state=other.state, goal=other.goal, blocked_on=other.blocked_on,
                intent=list(other.path[other.pidx:]), last_seen=0.0,
                task_id=other.task.tid if other.task else None)
    return brains, world


@pytest.mark.parametrize("policy", (POLICY_BIOS_PIBT_V6, POLICY_BIOS_PIBT_V7))
@pytest.mark.parametrize("seed", (0, 1, 2))
def test_measured_four_body_idle_tail_completes_without_contacts_or_parking_churn(policy, seed):
    brains, world = _tail(policy, seed)
    net = SimNetwork(DEFAULT, seed=seed)
    for rid in brains:
        net.register(rid)
    for _ in range(round(90 * DEFAULT.rates.world_hz)):
        t, commands = world.t, {}
        for rid, brain in brains.items():
            body = world.robots[rid]
            net.set_position(rid, (body.x / DEFAULT.cell_m, body.y / DEFAULT.cell_m))
            sensors = world.sense(rid, pose_noise_m=0.02)
            act, out = brain.step(t, sensors, net.poll(t, rid))
            commands[rid] = act
            for message in out:
                net.send(t, rid, message)
        world.step(1.0 / DEFAULT.rates.world_hz, commands)
        if brains["AMR10"].completed:
            break
    assert not world.contacts
    assert [task for task, _, _ in brains["AMR10"].completed] == ["CAP-0093"]
    assert world.t < 90.0
    # The old path was created/cancelled 25 times per second. Bound actual
    # decision events, not only the fixed-size visible tail of the event log.
    assert brains["AMR08"].stats["decision_events"] < 20
    assert brains["AMR38"].stats["decision_events"] < 20


@pytest.mark.parametrize("condition", ("no_request", "stale", "cycle", "brief",
                                        "centred", "active", "charging"))
def test_idle_center_escape_requires_real_sustained_refusal_and_fresh_request(condition):
    brains, world = _tail()
    brain = brains["AMR14"]
    if condition == "no_request":
        brain.peers["AMR38"].blocked_on = None
    elif condition == "stale":
        brain.peers["AMR38"].last_seen = -20.0
    elif condition == "cycle":
        brain.peers["AMR38"].blocked_on = "AMR08"
        brain.peers["AMR08"].blocked_on = "AMR38"
    elif condition == "brief":
        brain._stall_since = -0.1
    elif condition == "centred":
        world.robots[brain.rid].x, world.robots[brain.rid].y = cell_center((16, 16), DEFAULT.cell_m)
    elif condition == "active":
        brain.task = Task("WORK", (16, 16), (17, 16))
    elif condition == "charging":
        brain.state = ST_CHARGING
        brain.goal = brain.env.docks[0]
        world.robots[brain.rid].battery_wh = 0.5 * DEFAULT.robot.battery_full_wh
    brain._task_loop(0.0, world.sense(brain.rid), [])
    assert not any(event["code"] == "IDLE_CENTER_CLEARANCE" for event in brain.decision_log)


def test_idle_center_escape_requires_destination_lease_and_cannot_override_an_owner():
    brains, world = _tail()
    brain = brains["AMR14"]
    sensors = world.sense(brain.rid)
    brain._task_loop(0, sensors, [])
    assert brain.state == ST_RETREAT and brain.goal == (16, 15)
    assert brain._retreat_for == "idle-clearance"
    brain._traffic_loop(0, sensors, [])
    assert brain._hold and brain.blocked_on == "gate"
    zone = brain._cell_zone_id(brain.goal)
    brain._claims[zone] = ("OTHER", 10.0, 0, 0, None)
    brain._traffic_loop(1.0, replace(sensors, t=1.0), [])
    assert brain._hold and brain.blocked_on == "OTHER"
    assert brain._safety(sensors, brain._follow(1.0, sensors)).v == 0


@pytest.mark.parametrize("kind", ("task", "charging", "occupied"))
def test_idle_center_escape_keeps_active_goals_chargers_and_occupied_cells_excluded(kind):
    brains, world = _tail()
    brain = brains["AMR14"]
    peer = Peer("EXCLUDED", last_seen=0.0, cell=(20, 16), goal=(16, 15))
    if kind == "task":
        peer.task_id = "OTHER_TASK"
    elif kind == "charging":
        peer.state = ST_CHARGING
    else:
        peer.cell = (16, 15)
    brain.peers[peer.rid] = peer
    brain._task_loop(0.0, world.sense(brain.rid), [])
    assert brain.retreat_target != (16, 15)


def _forward_parking():
    brains, world = _tail()
    brain = brains["AMR38"]
    body = world.robots[brain.rid]
    body.x, body.y = cell_center((17, 16), DEFAULT.cell_m)
    brain.goal, brain.path, brain.pidx = (35, 16), [(x, 16) for x in range(17, 36)], 1
    brain._last_cell, brain._stall_since = (17, 16), None
    brain.peers.clear()
    brain.peers["LOADED"] = Peer("LOADED", cell=(16, 16), goal=(18, 16),
        intent=[(17, 16), (18, 16)], task_id="TASK", blocked_on=brain.rid, last_seen=0)
    return brain, world


def test_forward_parking_is_not_cancelled_when_only_neighbor_is_a_task_goal():
    brain, world = _forward_parking()
    original = list(brain.path)
    sensors = world.sense(brain.rid)
    assert not brain._idle_clearance_options(0, sensors.cell)[1]
    brain._task_loop(0, sensors, [])
    assert brain.goal == (35, 16) and brain.path == original
    # The task goal remains excluded as a parking endpoint; traversing it still
    # uses ordinary admission and will wait for a physical/leased occupant.
    brain.peers["OCCUPANT"] = Peer("OCCUPANT", cell=(18, 16), last_seen=0)
    zone = brain._cell_zone_id((18, 16))
    brain._claims[zone] = (brain.rid, 20.0, 0, 0, None)
    brain._gate_committed.add(zone)
    brain._traffic_loop(1.0, replace(sensors, t=1.0), [])
    assert brain._hold and brain.blocked_on == "OCCUPANT"
    assert brain._follow(1.0, sensors).v == 0.0


def test_a_legal_local_clearance_still_cancels_the_remote_parking_trip():
    brain, world = _forward_parking()
    body = world.robots[brain.rid]
    body.x, body.y = cell_center((19, 16), DEFAULT.cell_m)
    brain.path, brain.pidx = [(x, 16) for x in range(19, 36)], 1
    brain.peers["LOADED"].cell = (18, 16)
    brain.peers["LOADED"].intent = [(19, 16), (20, 16)]
    sensors = world.sense(brain.rid)
    assert brain._idle_clearance_options(0, sensors.cell)[1]
    brain._task_loop(0, sensors, [])
    assert brain.goal is None and not brain.path


def test_stale_intent_cannot_erase_a_real_idle_clearance_option():
    brain, world = _forward_parking()
    brain.peers["LOADED"].goal = (30, 16)
    brain.peers["STALE"] = Peer("STALE", cell=(18, 16), intent=[(18, 16)], last_seen=-20)
    assert (18, 16) in brain._idle_clearance_options(0, world.sense(brain.rid).cell)[1]


def test_anonymous_bodies_in_every_local_bay_do_not_restart_an_admitted_parking_route():
    brain, world = _forward_parking()
    body = world.robots[brain.rid]
    body.x, body.y = cell_center((19, 16), DEFAULT.cell_m)
    brain.path, brain.pidx = [(x, 16) for x in range(19, 36)], 1
    brain.peers["LOADED"].cell = (18, 16)
    brain.peers["LOADED"].intent = [(19, 16), (20, 16)]
    sensors = world.sense(brain.rid)
    options = brain._idle_clearance_options(0, sensors.cell)[1]
    assert options
    detections = [Detection(*cell_center(cell, DEFAULT.cell_m), 0.3, 1.4)
                  for cell in options]
    sensors = replace(sensors, detections=detections)
    original = list(brain.path)
    brain._task_loop(0, sensors, [])
    assert brain.goal == (35, 16) and brain.path == original
    assert brain._last_idle_clearance_plan == 0.0
    assert not brain._can_clear_idle_locally(0.02, sensors)


def test_actual_idle_bay_selection_uses_the_same_physical_candidates_as_cancellation():
    brain, world = _forward_parking()
    body = world.robots[brain.rid]
    body.x, body.y = cell_center((19, 16), DEFAULT.cell_m)
    brain.path, brain.pidx = [(x, 16) for x in range(19, 36)], 1
    brain.peers["LOADED"].cell = (18, 16)
    brain.peers["LOADED"].intent = [(19, 16), (20, 16)]
    blocked_bay = (19, 15)
    sensors = replace(world.sense(brain.rid), detections=[
        Detection(*cell_center(blocked_bay, DEFAULT.cell_m), 0.3, 1.4)])
    assert brain._recovery_route(sensors, blocked_bay) is None
    assert brain._recovery_route(sensors, (20, 16)) is not None
    brain._task_loop(0, sensors, [])
    assert brain.goal is None
    brain._task_loop(0.12, replace(sensors, t=0.12), [])
    assert brain.goal != blocked_bay
    assert brain.goal is not None and brain._recovery_route(sensors, brain.goal) is not None


@pytest.mark.parametrize("policy", (POLICY_BIOS_PIBT_V6, POLICY_BIOS_PIBT_V7))
def test_idle_singleton_clearance_cannot_bypass_a_task_only_corridor_lease(policy):
    from src.environment import chokepoint_warehouse
    env = chokepoint_warehouse(length=5)
    world = World(env, DEFAULT)
    body = world.add_robot("IDLE", (5, 4))
    body.x -= 0.3
    world.add_robot("WAITING", (4, 4))
    brain = AMRBrain("IDLE", env, DEFAULT, policy=policy)
    brain.goal, brain.path, brain.pidx = (5, 4), [(5, 4)], 0
    brain._stall_since = -10.0
    brain.peers["WAITING"] = Peer("WAITING", cell=(4, 4), goal=(13, 4),
        task_id="TASK", blocked_on=brain.rid, last_seen=0, intent=[(5, 4)])
    controlled = brain._controlled_block((6, 4))
    assert controlled is not None and brain._controlled_block((5, 4)) is None
    brain._claims[controlled] = ("OWNER", 10.0, 0, 0, None)
    sensors = world.sense(brain.rid)
    brain._task_loop(0, sensors, [])
    assert brain.retreat_target != (6, 4)
    assert brain.retreat_target is None or brain._controlled_block(brain.retreat_target) is None
    assert brain._claims[controlled][0] == "OWNER"


@pytest.mark.parametrize("policy", (POLICY_BIOS_PIBT_V6, POLICY_BIOS_PIBT_V7))
def test_measured_mixed_traffic_staging_strip_keeps_the_full_map_and_body_margin(policy):
    from src.scenarios import SHOWCASE_SCENARIOS
    env = SHOWCASE_SCENARIOS["showcase_grand_challenge"]["builder"](n_robots=10, seed=0).env
    brain = AMRBrain("AMR03", env, DEFAULT, policy=policy)
    # Captured from the unchanged 800 s mixed10 regression. Both the left idle
    # body and loaded follower behind are inside the conservative omni field.
    sensors = Sensors(t=800, pose=(2.046527660051577, 10.842527644978167,
                                  -1.5493889191947856),
        v=0, omega=0.01042782110635967, battery_frac=0.7, cell=(1, 7),
        clearance_m=0.24, detections=[
            Detection(2.1151922428471543, 11.770244989739309, 0.35, 0.93695),
            Detection(1.0604607352878606, 10.55618458541374, 0.35, 1.04195),
            Detection(2.1002877938406392, 13.153703030338288, 0.35, 2.3193)])
    target = (1, 6)
    assert not brain._recovery_route_clear(sensors, [cell_center(target, DEFAULT.cell_m)], 0.15)
    # Explicit witness shows the original search failure was discretization, not
    # infeasibility. Production must find a route with the same full 0.15 m margin.
    assert brain._recovery_route_clear(sensors, [(2.24, 10.29), (2.1, 9.1)], 0.15)
    route = brain._recovery_route(sensors, target)
    assert route is not None and len(route) == 2
    assert brain._recovery_route_clear(sensors, route, 0.15)
