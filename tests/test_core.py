"""Regression tests for the parts of the system whose correctness is settled.

Each test here exists because something was actually wrong at some point. They are
deliberately about *properties* rather than golden numbers: a benchmark result will
move as the policy is tuned, but a swept collision check must never miss a swap, and a
protective field must never permit a speed the chassis cannot stop from.

Run with:  python -m pytest tests -q
"""

import json
import math
from dataclasses import replace

import pytest

from src.amr import (AMRBrain, POLICY_BIOS_PIBT_V3, POLICY_BIOS_PIBT_V6,
                     POLICY_CENTRAL, POLICY_DECENTRALIZED, POLICY_STOP_WAIT,
                     ST_CHARGING, Peer, Task)
from src.assignment import hungarian
from src.environment import (RACK, chokepoint_warehouse, classic_warehouse,
                             corridors, open_floor)
from src.fleet_manager import FleetManager
from src.geometry import (cell_center, manhattan, segments_min_distance, to_cell,
                          wrap_angle)
from src.messages import (MGR_BEACON, PLAN_RSP, award, bid, decode,
                          decode_packet, encode, experience, heartbeat, intent,
                          task_new)
from src.metrics import poisson_rate_ci
from src.planner import Reservations, astar, prioritized_plan
from src.settings import DEFAULT
from src.task_allocation import (ALLOCATION_AUCTION, ALLOCATION_HUNGARIAN,
                                 ALLOCATION_PREASSIGNED)
from src.transport import ReplayWindow, SimNetwork
from src.world import Actuation, Detection, Sensors, World


# ----------------------------------------------------------------- geometry


def test_swept_distance_catches_position_swap():
    """Two robots exchanging cells never share a sample point but do collide.

    Endpoint-only collision checking misses this entirely, and missing it is the
    easiest way to report a spuriously perfect safety record.
    """
    assert segments_min_distance((0, 0), (1, 0), (1, 0), (0, 0)) == pytest.approx(0.0)
    assert segments_min_distance((0, 0), (1, 0), (0, 5), (1, 5)) == pytest.approx(5.0)


def test_wrap_angle_is_bounded():
    for a in (-10.0, -math.pi, 0.0, math.pi, 7.5):
        assert -math.pi - 1e-9 <= wrap_angle(a) <= math.pi + 1e-9


# ----------------------------------------------------------------- planner


def test_hungarian_finds_global_minimum_assignment():
    costs = [
        [4, 1, 3],
        [2, 0, 5],
        [3, 2, 2],
    ]
    pairs = hungarian(costs)
    assert pairs == [(0, 1), (1, 0), (2, 2)]
    assert sum(costs[row][column] for row, column in pairs) == 5


def test_astar_can_avoid_an_experienced_directed_edge():
    env = open_floor(3, 3)
    direct = astar(env, (0, 1), (2, 1))
    guided = astar(
        env, (0, 1), (2, 1),
        edge_cost={((0, 1), (1, 1)): 10.0})
    assert direct == [(0, 1), (1, 1), (2, 1)]
    assert ((0, 1), (1, 1)) not in set(zip(guided, guided[1:]))
    assert len(guided) > len(direct)


def test_v6_prediction_is_a_soft_cost_that_can_avoid_future_occupancy():
    env = open_floor(8, 8)
    brain = AMRBrain("A", env, DEFAULT, policy=POLICY_BIOS_PIBT_V6)
    brain.goal = (5, 2)
    brain._last_cell = (0, 2)
    brain._predicted_cell_cost[(2, 2)] = (5.0, 10.0, "moving-obstacle")

    brain._replan(0.0, (0, 2))

    assert brain.path
    assert (2, 2) not in brain.path
    assert brain.stats["predictive_reroutes"] == 1
    assert brain.decision_log[-1]["code"] == "PREDICTIVE_REROUTE"


def test_v6_prediction_falls_back_to_v5_routes_in_a_dead_zone():
    cfg = replace(
        DEFAULT,
        net=replace(DEFAULT.net, dead_zones=((0.0, 0.0, 2.0),)),
    )
    env = open_floor(8, 8)
    brain = AMRBrain("A", env, cfg, policy=POLICY_BIOS_PIBT_V6)
    brain.goal = (5, 2)
    brain._predicted_cell_cost[(2, 2)] = (5.0, 10.0, "moving-obstacle")

    assert brain._v6_prediction_costs(0.0, (0, 2)) == {}


def test_v6_charger_selection_avoids_a_fresh_busy_dock():
    env = open_floor(20, 15)
    brain = AMRBrain("A", env, DEFAULT, policy=POLICY_BIOS_PIBT_V6)
    nearest = min(env.docks, key=lambda cell: (manhattan((0, 2), cell), cell))
    brain.peers["B"] = Peer(
        "B", cell=nearest, state=ST_CHARGING, goal=nearest, last_seen=0.0)

    selected = brain._v6_select_charger(0.0, (0, 2))

    assert selected in env.docks
    assert selected != nearest
    assert brain.stats["charger_contentions_avoided"] == 1


def test_central_allocator_uses_global_assignment():
    env = open_floor(10, 10)
    manager = FleetManager(env, DEFAULT)
    manager.robot_cells = {"A": (0, 0), "B": (4, 0)}
    manager.robot_state = {"A": "idle", "B": "idle"}
    manager.robot_task = {"A": None, "B": None}
    manager.open_tasks = {
        "T1": ((1, 0), (1, 1)),
        "T2": ((0, 0), (0, 1)),
    }

    awards = manager._assign_tasks(0.0)
    assignment = {m.body["task"]: m.body["dst"] for m in awards}
    assert assignment == {"T1": "B", "T2": "A"}
    assert all(m.body["dst"] in {"A", "B"} for m in awards)


def test_manager_award_is_accepted_by_its_destination_robot():
    env = open_floor(10, 10)
    world = World(env, DEFAULT, seed=0)
    world.add_robot("A", (0, 0), 0.0)
    brain = AMRBrain("A", env, DEFAULT, policy=POLICY_CENTRAL, home=(0, 0))
    task = Task("T1", (1, 0), (2, 0))
    brain.open_tasks[task.tid] = task

    brain.step(0.0, world.sense("A"),
               [award("FM0", 1, 0.0, task.tid, 1.0, dst="A")])

    assert brain.task is not None
    assert brain.task.tid == "T1"
    assert task.tid not in brain._task_claims


def test_allocation_only_manager_never_controls_routes():
    manager = FleetManager(
        open_floor(10, 10), DEFAULT,
        allocation_policy=ALLOCATION_HUNGARIAN,
        route_planning=False,
    )

    outbox = manager.step(1.0, [])

    assert not any(message.type in (MGR_BEACON, PLAN_RSP) for message in outbox)


def test_decentralized_safety_guard_does_not_creep_inside_contact_envelope():
    env = open_floor(10, 10)
    brain = AMRBrain("A", env, DEFAULT, policy=POLICY_DECENTRALIZED)
    brain._creep_until = 10.0
    sensors = Sensors(
        t=1.0, pose=(2.5, 2.5, 0.0), v=0.0, omega=0.0,
        battery_frac=1.0, cell=(2, 2), clearance_m=0.0,
        clearance_omni_m=0.0)

    act = brain._safety(sensors, Actuation(v=0.3, omega=0.4))

    assert act.v == 0.0
    assert act.safety_stop


def test_v3_recovery_motion_must_increase_close_peer_clearance():
    env = open_floor(10, 10)
    brain = AMRBrain("A", env, DEFAULT, policy=POLICY_BIOS_PIBT_V3)
    peer = Detection(x=2.7, y=2.5, r=0.35, range_m=0.2)
    sensors = Sensors(
        t=1.0, pose=(2.5, 2.5, 0.0), v=0.0, omega=0.0,
        battery_frac=1.0, cell=(1, 1), clearance_m=0.0,
        clearance_omni_m=0.0, detections=(peer,),
    )

    assert not brain._escape_motion_increases_clearance(
        sensors, Actuation(v=0.2, omega=0.0))
    away = Sensors(**{**sensors.__dict__, "pose": (2.5, 2.5, math.pi)})
    assert brain._escape_motion_increases_clearance(
        away, Actuation(v=0.2, omega=0.0))


def test_task_protocol_carries_epoch_deadline_and_lease():
    new = task_new("WMS", 1, 0.0, "T1", (1, 1), (2, 2),
                   epoch=3, bid_until=0.6, cargo_type="hazardous",
                   cargo_weight=25.0, priority=4, deadline=90.0)
    offer = bid("AMR02", 2, 0.1, "T1", 7.5, epoch=3)
    claim = award("AMR02", 3, 0.7, "T1", 7.5,
                  epoch=3, lease_until=20.7)

    assert new.body["e"] == 3 and new.body["ttl"] == 0.6
    assert new.body["ct"] == "hazardous" and new.body["cw"] == 25.0
    assert new.body["pr"] == 4 and new.body["due"] == 90.0
    assert offer.body["e"] == 3
    assert claim.body["e"] == 3 and claim.body["ttl"] == 20.0


@pytest.mark.parametrize("raw", [b"[]", b"null", b'"HB"', b"{}"])
def test_wire_decoder_drops_valid_json_with_invalid_shape(raw):
    message, reason = decode_packet(raw)
    assert message is None
    assert reason is not None


def test_wire_decoder_validates_message_body_before_ingest():
    raw = json.dumps({
        "v": 1, "type": "HB", "src": "A", "seq": 1, "t": 1.0,
        "body": {},
    }).encode()
    message, reason = decode_packet(raw)
    assert message is None
    assert reason == "invalid_heartbeat"


def test_wire_decoder_rejects_non_finite_signed_payload_without_raising():
    raw = (b'{"v":1,"type":"HB","src":"A","seq":1,"t":NaN,'
           b'"body":{},"mac":"' + b"0" * 64 + b'"}')
    message, reason = decode_packet(raw, secret="fleet-secret", require_auth=True)
    assert message is None
    assert reason == "invalid_json"


def test_wire_hmac_rejects_tampering_and_unsigned_packets():
    original = heartbeat("A", 1, 1.0, (1.0, 2.0, 0.0), (1, 2), 1.0,
                         "p2p", "idle", None)
    signed = encode(original, secret="fleet-secret", session_id="boot1")
    assert decode(signed, secret="fleet-secret", require_auth=True) is not None

    tampered = signed.replace(b'"idle"', b'"move"')
    _message, reason = decode_packet(
        tampered, secret="fleet-secret", require_auth=True)
    assert reason == "invalid_auth"
    _message, reason = decode_packet(
        encode(original), secret="fleet-secret", require_auth=True)
    assert reason == "auth_missing"


def test_replay_window_accepts_reordering_but_rejects_duplicates_and_old_data():
    window = ReplayWindow(width=8)
    assert window.accept(10)
    assert window.accept(12)
    assert window.accept(11)
    assert not window.accept(11)
    assert not window.accept(1)


def test_received_protocol_times_are_derived_from_receiver_clock():
    env = open_floor(10, 10)
    brain = AMRBrain("B", env, DEFAULT, policy=POLICY_BIOS_PIBT_V3,
                     allocation_policy=ALLOCATION_AUCTION)
    sender_t = 50_000.0
    receiver_t = 7.0
    packets = [
        intent("A", 1, sender_t, [(2, 2)],
               [(sender_t + 1.0, sender_t + 2.0)], 1.0, 0),
        task_new("WMS", 2, sender_t, "T1", (1, 1), (2, 2),
                 bid_until=sender_t + 0.6, cargo_type="fragile",
                 cargo_weight=12.0, priority=3,
                 deadline=sender_t + 120.0),
        award("A", 3, sender_t, "T1", 1.0,
              lease_until=sender_t + 20.0),
    ]

    brain._ingest(receiver_t, packets)

    assert brain.peers["A"].windows == [(8.0, 9.0)]
    assert brain.open_tasks["T1"].bid_deadline == pytest.approx(7.6)
    assert brain.open_tasks["T1"].deadline == pytest.approx(127.0)
    assert brain.open_tasks["T1"].cargo_type == "fragile"
    assert brain.open_tasks["T1"].cargo_weight == 12.0
    assert brain.open_tasks["T1"].priority == 3
    assert brain._task_claims["T1"][3] == pytest.approx(27.0)
    assert brain.open_tasks["T1"].lease_owner == "A"
    assert brain.open_tasks["T1"].lease_until == pytest.approx(27.0)


def test_expired_peer_claim_reopens_a_new_auction_epoch():
    env = open_floor(10, 10)
    brain = AMRBrain("A", env, DEFAULT, policy=POLICY_DECENTRALIZED)
    task = Task("T1", (1, 1), (2, 2), 0.0, 0, 0.6)
    brain.open_tasks[task.tid] = task
    brain._task_claims[task.tid] = (0, 4.0, "B", 1.0)

    brain._expire_task_claims(2.0)

    assert task.auction_epoch == 1
    assert task.bid_deadline > 2.0
    assert task.tid not in brain._task_claims


def test_decentralized_dashboard_run_has_no_fleet_manager():
    from src.main import run_for_dashboard

    payload = run_for_dashboard("dense_aisles", POLICY_DECENTRALIZED,
                                robots=2, seed=0, duration=1.0)
    assert payload["meta"]["policy"] == POLICY_DECENTRALIZED
    assert payload["meta"]["allocation_policy"] == ALLOCATION_AUCTION
    assert not any(frame["manager_alive"] for frame in payload["frames"])
    events = [event for frame in payload["frames"]
              for event in frame.get("auction_events", [])]
    assert {event["type"] for event in events} >= {"TN", "BD", "AW"}


def test_v3_auction_has_no_fleet_manager_or_central_route_mode():
    from src.main import run_for_dashboard

    payload = run_for_dashboard(
        "dense_aisles", POLICY_BIOS_PIBT_V3,
        allocation_policy=ALLOCATION_AUCTION,
        robots=2, seed=0, duration=2.0,
    )

    assert not any(frame["manager_alive"] for frame in payload["frames"])
    assert payload["meta"]["allocation_policy"] == ALLOCATION_AUCTION


def test_allocation_is_selected_by_policy_on_a_normal_scenario():
    from src.main import run_for_dashboard

    auction = run_for_dashboard(
        "dense_aisles", POLICY_DECENTRALIZED, allocation_policy=ALLOCATION_AUCTION,
        robots=2, seed=0, duration=2.0)
    auction_events = [event for frame in auction["frames"]
                      for event in frame.get("auction_events", [])]
    assert auction["meta"]["tasks"] == 8
    assert auction["meta"]["allocation_policy"] == ALLOCATION_AUCTION
    assert not any(frame["manager_alive"] for frame in auction["frames"])
    assert {event["type"] for event in auction_events} >= {"TN", "BD", "AW"}

    hungarian_run = run_for_dashboard(
        "dense_aisles", POLICY_DECENTRALIZED,
        allocation_policy=ALLOCATION_HUNGARIAN, robots=2, seed=0, duration=2.0)
    hungarian_events = [event for frame in hungarian_run["frames"]
                        for event in frame.get("auction_events", [])]
    assert hungarian_run["meta"]["tasks"] == 8
    assert hungarian_run["meta"]["allocation_policy"] == ALLOCATION_HUNGARIAN
    assert any(frame["manager_alive"] for frame in hungarian_run["frames"])
    assert {event["type"] for event in hungarian_events} >= {"TN", "AW"}
    assert not any(event["type"] == "BD" for event in hungarian_events)

    preassigned = run_for_dashboard(
        "dense_aisles", POLICY_BIOS_PIBT_V3,
        allocation_policy=ALLOCATION_PREASSIGNED,
        robots=2, seed=0, duration=1.0)
    assert preassigned["meta"]["allocation_policy"] == ALLOCATION_PREASSIGNED
    assert preassigned["meta"]["tasks"] == 8


def test_astar_respects_racks():
    env = classic_warehouse()
    path = astar(env, (0, 0), (env.width - 1, env.height - 1))
    assert path, "a route must exist across the standard map"
    assert all(env.grid[y][x] != RACK for x, y in path)
    for a, b in zip(path, path[1:]):
        assert abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1, "4-connected steps only"


def test_space_time_planner_resolves_head_on_corridor():
    """The case a time-independent planner cannot express at all."""
    env = chokepoint_warehouse(length=9)
    a, b = (1, 4), (env.width - 2, 4)
    plans = prioritized_plan(env, [("A", a, b), ("B", b, a)])
    assert plans["A"] and plans["B"], "both robots must get a plan"

    occupied: dict[tuple, str] = {}
    for owner, timed in plans.items():
        for cell, step in timed:
            assert occupied.setdefault((cell, step), owner) == owner, \
                "two robots scheduled into one cell at one timestep"


def test_reservations_ban_edge_swaps():
    res = Reservations()
    res.reserve_path("A", [((0, 0), 0), ((1, 0), 1)])
    assert not res.edge_free((1, 0), (0, 0), 0, "B"), \
        "the reverse traversal must be banned, or head-on swaps pass a vertex check"


def test_prioritized_planning_reports_failure_rather_than_lying():
    env = chokepoint_warehouse(length=5)
    plans = prioritized_plan(env, [("A", (1, 4), (env.width - 2, 4))])
    assert isinstance(plans["A"], list)


# ----------------------------------------------------------------- map


def test_corridor_blocks_are_found_and_have_two_mouths():
    env = chokepoint_warehouse(length=13)
    cm = corridors(env)
    assert len(cm.members) == 1, "the map has exactly one single-file run"
    cid = next(iter(cm.members))
    assert len(cm.ends[cid]) == 2, "a linear corridor has two mouths"
    assert len(cm.members[cid]) == 13


def test_open_floor_has_no_long_blocks():
    """The negative control must contain nothing for block control to act on."""
    cm = corridors(open_floor(20, 20))
    assert all(len(v) < 6 for v in cm.members.values())


# ----------------------------------------------------------------- safety


def test_protective_field_scales_with_speed():
    spec = DEFAULT.robot
    assert spec.stop_field_m(0.0) == pytest.approx(spec.safety_margin_m)
    assert spec.stop_field_m(spec.v_max) > spec.stop_field_m(0.5) > spec.stop_field_m(0.0)


def test_speed_limit_is_the_inverse_of_the_braking_equation():
    """Whatever speed is permitted must be one the robot can still stop from."""
    spec = DEFAULT.robot
    for gap in (0.2, 0.5, 1.0, 1.5, 3.0):
        v = spec.max_speed_for_clearance(gap)
        assert spec.stop_field_m(v) <= gap + 1e-6, gap


def test_closing_traffic_shrinks_the_permitted_speed():
    """Head-on traffic closes at up to twice v_max while each robot budgets for one.

    This was a real collision source: both robots braked correctly for their own speed
    and hit anyway.
    """
    spec = DEFAULT.robot
    solo = spec.max_speed_for_clearance(2.0, 0.0)
    facing = spec.max_speed_for_clearance(2.0, spec.v_max)
    assert facing < solo


def test_world_blocks_racks_instead_of_letting_robots_tunnel():
    env = chokepoint_warehouse(length=9)
    w = World(env, DEFAULT, seed=0)
    w.add_robot("A", (8, 4), math.pi / 2)          # nose into the shelving
    for _ in range(200):
        w.step(0.02, {"A": Actuation(v=1.2)})
    x, y = w.robots["A"].x, w.robots["A"].y
    cell = to_cell((x, y), DEFAULT.cell_m)
    assert env.grid[cell[1]][cell[0]] != RACK
    assert y < 5.0 * DEFAULT.cell_m, \
        "the robot must stop at the rack face, not pass through it"


def test_timed_obstacle_waits_for_an_occupied_cell_to_clear():
    env = open_floor(10, 10)
    world = World(env, DEFAULT, seed=0)
    robot = world.add_robot("A", (4, 4), 0.0)

    assert world.add_obstacle("pallet", (4, 4)) is None
    assert "pallet" not in world.obstacles

    robot.x, robot.y = cell_center((1, 1), DEFAULT.cell_m)
    assert world.add_obstacle("pallet", (4, 4)) is not None
    assert "pallet" in world.obstacles


def test_lidar_detections_carry_no_identity():
    """The reactive layer must work off anonymous blobs, or it is blind to anything
    that does not broadcast - humans included."""
    env = open_floor(10, 10)
    w = World(env, DEFAULT, seed=0)
    w.add_robot("A", (2, 2), 0.0)
    w.add_human("H1", [(4, 2), (8, 2)])
    dets = w.sense("A").detections
    assert dets
    assert not any(hasattr(d, "rid") or hasattr(d, "id") for d in dets)


# ----------------------------------------------------------------- protocol


def test_messages_round_trip_and_reject_garbage():
    m = heartbeat("AMR01", 1, 1.5, (1.0, 2.0, 0.5), (1, 2), 0.8, "p2p", "idle", None)
    assert decode(encode(m)).src == "AMR01"
    assert decode(b"not json") is None
    assert decode(b'{"type":"NOPE","src":"x","seq":1,"t":0}') is None


def test_experience_message_round_trip_and_validation():
    packet = experience(
        "AMR01", 2, 1.5,
        [((1, 2), (2, 2), 3.25, 4), ((2, 2), (2, 3), 0.5, 1)])
    decoded = decode(encode(packet))
    assert decoded is not None
    assert decoded.body["edges"][0] == [1, 2, 2, 2, 3.25, 4]

    invalid = packet.to_dict()
    invalid["body"]["edges"][0][4] = -1.0
    assert decode(json.dumps(invalid).encode()) is None


def test_network_model_is_deterministic_for_a_seed():
    outs = []
    for _ in range(2):
        net = SimNetwork(DEFAULT, seed=7)
        for rid in ("A", "B", "C"):
            net.register(rid)
        for i in range(50):
            net.send(i * 0.01, "A", heartbeat("A", i, i * 0.01, (0, 0, 0),
                                              (0, 0), 1.0, "p2p", "idle", None))
        outs.append(len(net.poll(10.0, "B")))
    assert outs[0] == outs[1]


def test_unrelated_packet_does_not_shift_later_loss_draws():
    from dataclasses import replace

    cfg = replace(DEFAULT, net=replace(DEFAULT.net, loss=0.5))

    def target_delivered(with_extra: bool) -> bool:
        net = SimNetwork(cfg, seed=19)
        for rid in ("A", "B", "C"):
            net.register(rid)
        if with_extra:
            net.send(0.5, "C", heartbeat(
                "C", 1, 0.5, (3, 3, 0), (3, 3), 1.0,
                "p2p", "idle", None))
        # Sequence deliberately differs: an event-triggered sender has consumed fewer
        # sequence numbers, but the same semantic packet should face the same channel.
        seq = 99 if with_extra else 2
        net.send(1.0, "A", heartbeat(
            "A", seq, 1.0, (1, 1, 0), (1, 1), 1.0,
            "p2p", "idle", None))
        return any(message.src == "A" for message in net.poll(10.0, "B"))

    assert target_delivered(False) == target_delivered(True)


def test_dead_zone_kills_peer_traffic_when_the_ap_relays_it():
    """The problem statement's central claim, tested.

    In infrastructure-mode Wi-Fi a peer-to-peer frame is relayed by the access point,
    so a robot in a dead zone loses its peers exactly as it loses the server. P2P buys
    nothing. Only a genuinely different link layer changes that.
    """
    from dataclasses import replace
    cfg = replace(DEFAULT, net=replace(DEFAULT.net, dead_zones=((5.0, 5.0, 3.0),),
                                       peer_traffic_via_ap=True))
    net = SimNetwork(cfg, seed=0)
    net.register("A")
    net.register("B")
    net.set_position("A", (5.0, 5.0))              # inside the hole
    net.set_position("B", (15.0, 15.0))            # well outside
    net.send(0.0, "A", heartbeat("A", 1, 0.0, (0, 0, 0), (0, 0), 1.0, "p2p", "idle", None))
    assert net.poll(1.0, "B") == [], "the AP cannot relay a frame it never received"

    mesh = replace(cfg, net=replace(cfg.net, peer_traffic_via_ap=False))
    net2 = SimNetwork(mesh, seed=0)
    net2.register("A")
    net2.register("B")
    net2.set_position("A", (15.0, 15.0))
    net2.set_position("B", (16.0, 16.0))           # both outside, direct link
    net2.send(0.0, "A", heartbeat("A", 1, 0.0, (0, 0, 0), (0, 0), 1.0, "p2p", "idle", None))
    assert net2.poll(1.0, "B"), "a direct link should deliver"


# ----------------------------------------------------------------- statistics


def test_zero_events_give_the_rule_of_three():
    """Observing nothing bounds a rate; it never proves zero."""
    point, lo, hi = poisson_rate_ci(0, 100.0)
    assert point == 0.0 and lo == 0.0
    assert hi == pytest.approx(3.0 / 100.0, rel=0.15)
    assert hi > 0, "an upper bound of zero would be a claim we cannot make"


def test_more_exposure_tightens_the_bound():
    _, _, hi_small = poisson_rate_ci(0, 10.0)
    _, _, hi_large = poisson_rate_ci(0, 1000.0)
    assert hi_large < hi_small


# ----------------------------------------------------------------- integration


def test_a_lone_robot_completes_its_queue():
    """The end-to-end sanity check: no traffic, no excuses."""
    from src.amr import Task
    env = chokepoint_warehouse(length=9)
    w = World(env, DEFAULT, seed=0)
    w.add_robot("A", (1, 4), 0.0)
    net = SimNetwork(DEFAULT, seed=0)
    net.register("A")
    brain = AMRBrain("A", env, DEFAULT, policy=POLICY_STOP_WAIT, home=(1, 4))
    brain.queue = [Task("T1", (2, 1), (env.width - 3, 7))]

    dt = 1.0 / DEFAULT.rates.world_hz
    for k in range(int(180 / dt)):
        t = k * dt
        act, out = brain.step(t, w.sense("A"), net.poll(t, "A"))
        w.step(dt, {"A": act})
        if brain.completed:
            break
    assert brain.completed, "a single robot with a clear map must finish its task"
    assert not [e for e in w.contacts if e.kind == "robot-rack"]
