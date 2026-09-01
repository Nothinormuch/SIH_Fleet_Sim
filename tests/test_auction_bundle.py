"""Correctness and hardening gates for the experimental bounded future auction."""

from dataclasses import replace

from src import messages as msg
from src.amr import (AMRBrain, POLICY_BIOS_PIBT_V6, POLICY_CENTRAL, Peer, ST_IDLE,
                     ST_TO_DROP, Task)
from src.environment import FREE, Warehouse, open_floor
from src.settings import DEFAULT
from src.task_allocation import (ALLOCATION_AUCTION,
                                 ALLOCATION_AUCTION_BUNDLE,
                                 ALLOCATION_HUNGARIAN,
                                 ALLOCATION_POLICIES)
from src.transport import UdpMulticastTransport
from src.world import World


def _busy_brain(*, cfg=DEFAULT, peer_state=ST_TO_DROP):
    env = open_floor(16, 10)
    world = World(env, cfg, seed=0)
    world.add_robot("AMR01", (2, 2))
    brain = AMRBrain(
        "AMR01", env, cfg, policy=POLICY_BIOS_PIBT_V6,
        allocation_policy=ALLOCATION_AUCTION_BUNDLE,
    )
    active = Task("ACTIVE", (2, 2), (5, 2), auction_epoch=3)
    brain.task = active
    brain.state = ST_TO_DROP
    brain.goal = active.drop
    brain.open_tasks[active.tid] = active
    brain._task_claims[active.tid] = (3, 1.0, brain.rid, 100.0)
    brain._known_peer_ids.add("AMR02")
    brain.peers["AMR02"] = Peer(
        "AMR02", cell=(12, 8), state=peer_state, goal=(13, 8),
        last_seen=2.0, battery_frac=1.0, task_id="OTHER",
    )
    brain._future_network_candidate_since = 0.0
    return brain, world


def test_bundle_policy_is_selectable_and_ordinary_auction_stays_idle_only():
    assert ALLOCATION_AUCTION_BUNDLE in ALLOCATION_POLICIES
    brain, world = _busy_brain()
    brain.allocation_policy = ALLOCATION_AUCTION
    brain.open_tasks["NEXT"] = Task("NEXT", (6, 2), (8, 2))
    outbox = []

    brain._run_v3_batch_auction(2.0, world.sense(brain.rid), outbox)

    assert outbox == []
    assert brain._future_bid is None


def test_busy_robot_sends_only_best_one_future_bid_and_never_exceeds_capacity():
    brain, world = _busy_brain()
    brain.open_tasks.update({
        "NEAR": Task("NEAR", (6, 2), (8, 2)),
        "FAR": Task("FAR", (14, 8), (13, 8)),
        "MID": Task("MID", (8, 4), (10, 4)),
    })
    first = []

    brain._run_v3_batch_auction(2.0, world.sense(brain.rid), first)
    second = []
    brain._run_v3_batch_auction(2.1, world.sense(brain.rid), second)

    assert len(first) == 1
    assert first[0].type == msg.BID
    assert first[0].body["task"] == "NEAR"
    assert first[0].body["future"] is True
    assert second == []
    assert brain._future_bid is not None

    closing = []
    brain._run_v3_batch_auction(2.7, world.sense(brain.rid), closing)
    assert brain.future_task is not None
    assert brain.future_task.tid == "NEAR"

    after_capacity = []
    brain._run_v3_batch_auction(3.5, world.sense(brain.rid), after_capacity)
    assert after_capacity == []
    assert brain.future_task.tid == "NEAR"


def test_future_cost_includes_active_wait_and_defers_to_faster_idle_peer():
    brain, world = _busy_brain(peer_state=ST_IDLE)
    task = Task("NEXT", (6, 2), (8, 2))
    sensors = world.sense(brain.rid)
    short_cost = brain._future_bid_cost(task, sensors, 2.0)
    brain.task.drop = (15, 9)
    brain.goal = brain.task.drop
    long_cost = brain._future_bid_cost(task, sensors, 2.0)
    assert long_cost > short_cost

    brain.task.drop = (5, 2)
    brain.goal = brain.task.drop
    brain.peers["AMR02"].cell = task.pick
    brain.peers["AMR02"].goal = None
    brain.open_tasks[task.tid] = task
    outbox = []
    brain._run_v3_batch_auction(2.0, sensors, outbox)

    assert outbox == []
    assert brain.stats["future_hysteresis_prevented"] >= 1


def test_future_sequence_rejects_payload_energy_deadline_and_missing_charger():
    brain, world = _busy_brain()
    sensors = world.sense(brain.rid)
    overweight = Task(
        "HEAVY", (6, 2), (8, 2),
        cargo_weight=DEFAULT.robot.max_payload_kg + 1.0,
    )
    assert not brain._future_sequence_feasible(overweight, sensors, 2.0)[0]

    low_battery = replace(sensors, battery_frac=DEFAULT.traffic.energy_reserve_frac)
    normal = Task("NORMAL", (6, 2), (14, 8))
    feasible, _required, _reserve, reason = brain._future_sequence_feasible(
        normal, low_battery, 2.0)
    assert not feasible
    assert reason == "energy"

    urgent = Task("URGENT", (6, 2), (14, 8), deadline=2.5)
    assert brain._future_sequence_feasible(urgent, sensors, 2.0)[3] == "future_deadline"

    no_dock = Warehouse(
        8, 5, tuple(tuple(FREE for _ in range(8)) for _ in range(5)),
        (), (), "no-dock",
    )
    no_dock_world = World(no_dock, DEFAULT, seed=0)
    no_dock_world.add_robot("AMR01", (1, 1))
    no_dock_brain = AMRBrain(
        "AMR01", no_dock, DEFAULT, policy=POLICY_BIOS_PIBT_V6,
        allocation_policy=ALLOCATION_AUCTION_BUNDLE,
    )
    no_dock_brain.task = Task("A", (1, 1), (2, 1))
    no_dock_brain.state = ST_TO_DROP
    no_dock_brain.goal = (2, 1)
    future = Task("F", (3, 1), (5, 1))
    assert no_dock_brain._future_sequence_feasible(
        future, no_dock_world.sense("AMR01"), 1.0)[3] == "path_or_charger"


def test_stale_bundle_award_cannot_consume_future_slot():
    brain, world = _busy_brain()
    future = Task("NEXT", (6, 2), (8, 2), auction_epoch=4)
    brain.open_tasks[future.tid] = future
    context = (brain.task.tid, brain.task.auction_epoch, brain._future_generation)
    cost = brain._future_bid_cost(future, world.sense(brain.rid), 2.0)
    brain._future_bid = (
        future.tid, future.auction_epoch, context[0], context[1], context[2], cost)
    brain._bids[future.tid] = {(future.auction_epoch, brain.rid): cost}
    brain._bid_seen_t[(future.tid, future.auction_epoch, brain.rid)] = 2.0
    brain._future_bid_contexts[(future.tid, future.auction_epoch, brain.rid)] = context

    stale = msg.award(
        "AMR02", 9, 2.1, future.tid, cost, winner=brain.rid,
        epoch=future.auction_epoch, lease_until=20.0,
        active_task=context[0], active_epoch=context[1],
        bundle_version=context[2] + 1,
    )
    brain._ingest(2.1, [stale])
    brain._consume_future_nomination(2.1, world.sense(brain.rid), [])

    assert brain.future_task is None
    assert future.tid not in brain._peer_future_nominations
    assert future.tid not in brain._task_claims


def test_future_lease_renews_expires_and_promotion_revalidates_transactionally():
    brain, world = _busy_brain()
    future = Task("NEXT", (6, 2), (8, 2), auction_epoch=2)
    brain.open_tasks[future.tid] = future
    context = (brain.task.tid, brain.task.auction_epoch, brain._future_generation)
    assert brain._reserve_future(2.0, future, 4.0, 8.0, context)

    outbox = []
    brain._broadcast_future_lease(3.0, outbox)
    assert len(outbox) == 1
    assert outbox[0].body["future"] is True
    assert brain._task_claims[future.tid][3] > 8.0

    brain._expire_task_claims(brain._task_claims[future.tid][3] + 0.1)
    assert brain.future_task is None
    assert brain.stats["future_lease_expiries"] == 1

    brain, world = _busy_brain()
    future = Task("NEXT", (6, 2), (8, 2), auction_epoch=2)
    brain.open_tasks[future.tid] = future
    context = (brain.task.tid, brain.task.auction_epoch, brain._future_generation)
    assert brain._reserve_future(2.0, future, 4.0, 30.0, context)
    brain.task = None
    brain.state = ST_IDLE
    brain.goal = None
    assert brain._promote_future(3.0, world.sense(brain.rid), [])
    assert brain.task is future
    assert brain.future_task is None
    assert brain.stats["future_promotions"] == 1


def test_cancelled_or_completed_future_is_released_without_resurrection():
    brain, world = _busy_brain()
    future = Task("NEXT", (6, 2), (8, 2), auction_epoch=2)
    brain.open_tasks[future.tid] = future
    context = (brain.task.tid, brain.task.auction_epoch, brain._future_generation)
    assert brain._reserve_future(2.0, future, 4.0, 30.0, context)
    brain.open_tasks.pop(future.tid)
    brain.completed_tasks.add(future.tid)
    brain.task = None
    brain.state = ST_IDLE
    outbox = []

    assert not brain._promote_future(3.0, world.sense(brain.rid), outbox)
    assert brain.future_task is None
    assert future.tid not in brain.open_tasks
    assert not any(message.type == msg.TASK_NEW for message in outbox)


def test_manager_award_is_locally_revalidated_before_execution():
    env = open_floor(10, 8)
    world = World(env, DEFAULT, seed=0)
    world.add_robot("AMR01", (1, 1))
    brain = AMRBrain(
        "AMR01", env, DEFAULT, policy=POLICY_CENTRAL,
        allocation_policy=ALLOCATION_HUNGARIAN,
    )
    task = Task(
        "OVERWEIGHT", (2, 1), (6, 1),
        cargo_weight=DEFAULT.robot.max_payload_kg + 1.0,
    )
    brain.open_tasks[task.tid] = task

    brain.step(1.0, world.sense(brain.rid), [
        msg.award("FM0", 1, 1.0, task.tid, 1.0, dst=brain.rid)
    ])

    assert brain.task is None
    assert brain.stats["rejected_directed_awards"] == 1


def test_network_degradation_disables_new_future_bidding_and_requires_recovery_hold():
    degraded = replace(DEFAULT, net=replace(DEFAULT.net, loss=0.05))
    brain, world = _busy_brain(cfg=degraded)
    brain.open_tasks["NEXT"] = Task("NEXT", (6, 2), (8, 2))
    assert not brain._future_network_healthy(2.0)
    outbox = []
    brain._run_v3_batch_auction(2.0, world.sense(brain.rid), outbox)
    assert outbox == []

    brain, _world = _busy_brain()
    brain._future_network_candidate_since = None
    assert not brain._future_network_healthy(2.0)
    brain.peers["AMR02"].last_seen = 2.5
    assert not brain._future_network_healthy(2.5)
    brain.peers["AMR02"].last_seen = 3.1
    assert brain._future_network_healthy(3.1)


def test_ownership_hardening_rejects_unknown_bids_epoch_poison_and_conflicts():
    env = open_floor(10, 8)
    brain = AMRBrain(
        "AMR01", env, DEFAULT, policy=POLICY_BIOS_PIBT_V6,
        allocation_policy=ALLOCATION_AUCTION_BUNDLE,
    )
    brain._ingest(1.0, [msg.bid("AMR02", 1, 1.0, "UNKNOWN", 1.0)])
    assert brain._bids == {}
    assert len(brain._pending_unknown_bids["UNKNOWN"]) == 1
    assert brain.stats["deferred_unknown_bids"] == 1

    for index in range(DEFAULT.traffic.auction_unknown_bid_cache_max + 5):
        brain._ingest(1.1, [
            msg.bid("AMR02", index + 2, 1.1, f"U{index}", 2.0)
        ])
    assert sum(len(entries) for entries in brain._pending_unknown_bids.values()) \
        == DEFAULT.traffic.auction_unknown_bid_cache_max
    brain._ingest(1.2, [
        msg.task_new("WMS", 999, 1.2, "U132", (1, 1), (2, 2))
    ])
    assert brain._bids["U132"][(0, "AMR02")] == 2.0

    original = msg.task_new("AMR02", 2, 1.0, "T1", (1, 1), (2, 2))
    poisoned = msg.task_new(
        "AMR02", 3, 1.1, "T1", (1, 1), (2, 2),
        epoch=msg.MAX_AUCTION_EPOCH,
    )
    correction = msg.task_new("WMS", 4, 1.2, "T1", (3, 1), (4, 2))
    conflict = msg.task_new("AMR03", 5, 1.3, "T1", (5, 1), (6, 2))
    brain._ingest(2.0, [original, poisoned, correction, conflict])

    assert brain.open_tasks["T1"].auction_epoch == 0
    assert brain.open_tasks["T1"].pick == (3, 1)
    assert brain.open_tasks["T1"].drop == (4, 2)
    assert brain.stats["rejected_epoch_jumps"] == 1
    assert brain.stats["rejected_task_conflicts"] == 1


def test_unauthorized_directed_award_and_stale_completion_cannot_change_owner():
    env = open_floor(10, 8)
    brain = AMRBrain(
        "AMR01", env, DEFAULT, policy=POLICY_BIOS_PIBT_V6,
        allocation_policy=ALLOCATION_AUCTION,
    )
    task = Task("T1", (1, 1), (5, 1), auction_epoch=2)
    brain.open_tasks[task.tid] = task
    brain._task_claims[task.tid] = (2, 3.0, "AMR02", 20.0)
    brain._ingest(2.0, [
        msg.award("AMR03", 1, 1.0, task.tid, 0.0, dst=brain.rid, epoch=2),
        msg.task_done("AMR02", 2, 1.1, task.tid, epoch=1, owner="AMR02"),
        msg.task_done("AMR03", 3, 1.2, task.tid, epoch=2, owner="AMR04"),
    ])

    assert task.tid not in brain._awarded
    assert task.tid in brain.open_tasks
    assert task.tid not in brain.completed_tasks
    assert brain.stats["rejected_directed_awards"] == 1
    assert brain.stats["rejected_task_completions"] == 2


def test_replay_session_state_is_bounded_and_stale_entries_expire():
    transport = UdpMulticastTransport.__new__(UdpMulticastTransport)
    transport._replay = {}
    transport._replay_seen = {}
    transport._replay_max_sessions = 3
    transport._replay_session_ttl_s = 10.0

    for index in range(4):
        transport._replay_window(("AMR01", f"boot-{index}"), float(index))
    assert len(transport._replay) == 3
    assert ("AMR01", "boot-0") not in transport._replay

    transport._replay_window(("AMR02", "fresh"), 20.0)
    assert set(transport._replay) == {("AMR02", "fresh")}
