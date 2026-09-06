"""BIOS 7 passage admission must never turn stale absence into clearance."""

from dataclasses import replace

from src import messages as msg
from src.amr import (AMRBrain, POLICY_BIOS_PIBT_V6, POLICY_BIOS_PIBT_V7,
                     Peer, ST_TO_DROP, Task)
from src.environment import chokepoint_warehouse
from src.geometry import cell_center
from src.settings import DEFAULT
from src.world import World


def _observer(policy=POLICY_BIOS_PIBT_V7):
    env = chokepoint_warehouse(length=13)
    brain = AMRBrain("A", env, DEFAULT, policy=policy,
                     allocation_policy="auction_bundle")
    world = World(env, DEFAULT, seed=0)
    world.add_robot("A", (2, 2))
    task = Task("JOB", (2, 4), (22, 4), auction_epoch=3)
    brain._ensure_task_identity(task)
    brain.open_tasks[task.tid] = task
    brain._task_claims[task.tid] = (3, 2.0, "B", 100.0)
    brain.peers["B"] = Peer("B", state=ST_TO_DROP, goal=task.drop,
                            task_id=task.tid)
    return brain, task, world.sense("A")


def _position(brain, cell, t):
    peer = brain.peers["B"]
    peer.cell = cell
    peer.pose = (*cell_center(cell, DEFAULT.cell_m), 0.0)
    peer.last_seen = peer.pose_seen_t = t


def test_release_requires_inside_witness_and_full_exit_body_clearance():
    brain, task, sensors = _observer()
    _position(brain, (21, 4), 0.0)
    brain._v7_observe_passages(0.0, sensors)
    assert brain._v7_pending_corridors(task, 0.0) == {0: (6, 4)}

    _position(brain, (12, 4), 1.0)
    brain._v7_observe_passages(1.0, sensors)
    assert brain.stats["v7_passages_observed"] == 1
    _position(brain, (19, 4), 2.0)
    # The cell says outside but the physical body still overlaps the exit zone.
    brain.peers["B"].pose = (19 * DEFAULT.cell_m + 0.01, 4.5 * DEFAULT.cell_m, 0.0)
    brain._v7_observe_passages(2.0, sensors)
    assert brain._v7_pending_corridors(task, 2.0) == {0: (6, 4)}

    _position(brain, (21, 4), 3.0)
    brain._v7_observe_passages(3.0, sensors)
    assert brain._v7_pending_corridors(task, 3.0) == {}
    assert brain._task_claims[task.tid] == (3, 2.0, "B", 100.0)
    assert task.tid not in brain.completed_tasks


def test_retreating_to_entry_side_does_not_release_loaded_passage():
    brain, task, sensors = _observer()
    _position(brain, (12, 4), 0.0)
    brain._v7_observe_passages(0.0, sensors)
    _position(brain, (3, 4), 1.0)
    brain._v7_observe_passages(1.0, sensors)
    assert brain._v7_pending_corridors(task, 1.0) == {0: (6, 4)}
    assert brain.stats["v7_passage_releases"] == 0


def test_stale_pose_epoch_change_and_expiry_never_establish_release():
    brain, task, sensors = _observer()
    _position(brain, (12, 4), 0.0)
    brain._v7_observe_passages(0.0, sensors)
    _position(brain, (21, 4), 1.0)
    brain._v7_observe_passages(1.0, sensors)
    assert brain._v7_pending_corridors(task, 1.0) == {}
    # Fresh INTENT traffic is not a fresh position observation.
    brain.peers["B"].last_seen = 5.0
    assert brain._v7_pending_corridors(task, 5.0) == {0: (6, 4)}
    _position(brain, (21, 4), 6.0)
    task.auction_epoch = 4
    brain._task_claims[task.tid] = (4, 2.0, "B", 100.0)
    brain._v7_observe_passages(6.0, sensors)
    assert brain._v7_pending_corridors(task, 6.0) == {0: (6, 4)}
    assert brain._v7_pending_corridors(task, 101.0) == {0: (6, 4)}


def test_legacy_v6_and_disabled_ablation_keep_full_task_admission():
    for policy, enabled in ((POLICY_BIOS_PIBT_V6, True), (POLICY_BIOS_PIBT_V7, False)):
        brain, task, sensors = _observer(policy)
        brain.cfg = replace(DEFAULT, traffic=replace(DEFAULT.traffic, v7_passage_release=enabled))
        _position(brain, (12, 4), 0.0)
        brain._v7_observe_passages(0.0, sensors)
        _position(brain, (21, 4), 1.0)
        brain._v7_observe_passages(1.0, sensors)
        assert brain._v7_pending_corridors(task, 1.0) == {0: (6, 4)}


def test_reordered_heartbeat_cannot_replace_newer_passage_position():
    brain, task, _sensors = _observer()
    newer = msg.heartbeat("B", 10, 1.0, (12.5, 4.5, 0.0), (12, 4),
                          1.0, "DEGRADED_P2P", ST_TO_DROP, task.tid, goal=task.drop)
    older = replace(newer, seq=9, body={**newer.body, "c": [21, 4], "p": [21.5, 4.5, 0.0]})
    brain._ingest(1.0, [newer])
    brain._ingest(2.0, [older])
    assert brain.peers["B"].cell == (12, 4)
    assert brain.peers["B"].pose_seen_t == 1.0


def test_ordinary_auction_has_real_compute_samples():
    brain, task, sensors = _observer()
    brain._task_claims.clear()
    brain._run_v3_batch_auction(0.0, sensors, [])
    assert brain.allocation_compute_ms
    assert brain.allocation_compute_ms[-1] > 0.0


def test_passage_reentry_revokes_release_and_never_changes_spatial_lock():
    brain, task, sensors = _observer()
    _position(brain, (12, 4), 0.0)
    brain._v7_observe_passages(0.0, sensors)
    _position(brain, (21, 4), 1.0)
    brain._v7_observe_passages(1.0, sensors)
    assert brain._v7_pending_corridors(task, 1.0) == {}
    brain._claims[0] = ("B", 20.0, 1.0, 2, None)
    # Task-admission release cannot grant a movement token held by another owner.
    assert brain._bios_lock(0, 1.0) == ("B", 20.0)
    _position(brain, (18, 4), 2.0)
    brain._v7_observe_passages(2.0, sensors)
    assert brain._v7_pending_corridors(task, 2.0) == {0: (6, 4)}
    assert brain._bios_lock(0, 2.0)[0] == "B"


def test_new_owner_cannot_reuse_previous_owners_passage_witness():
    brain, task, sensors = _observer()
    _position(brain, (12, 4), 0.0)
    brain._v7_observe_passages(0.0, sensors)
    _position(brain, (21, 4), 1.0)
    brain._v7_observe_passages(1.0, sensors)
    assert brain._v7_pending_corridors(task, 1.0) == {}
    brain.peers["C"] = replace(brain.peers["B"], rid="C")
    brain._task_claims[task.tid] = (3, 1.0, "C", 100.0)
    brain._v7_observe_passages(1.2, sensors)
    assert brain._v7_pending_corridors(task, 1.2) == {0: (6, 4)}
    assert not brain._v7_passages_cleared


def test_entry_side_reappearance_after_blackout_revokes_old_exit():
    brain, task, sensors = _observer()
    _position(brain, (12, 4), 0.0)
    brain._v7_observe_passages(0.0, sensors)
    _position(brain, (21, 4), 1.0)
    brain._v7_observe_passages(1.0, sensors)
    assert brain._v7_pending_corridors(task, 1.0) == {}
    _position(brain, (3, 4), 9.0)
    brain._v7_observe_passages(9.0, sensors)
    assert brain._v7_pending_corridors(task, 9.0) == {0: (6, 4)}


def test_busy_corridor_guard_does_not_emit_fake_allocation_cost_samples():
    brain, task, sensors = _observer()
    brain.task = task
    brain.state = ST_TO_DROP
    brain.goal = task.drop
    brain._run_v3_batch_auction(0.0, sensors, [])
    assert brain.allocation_compute_ms == []
