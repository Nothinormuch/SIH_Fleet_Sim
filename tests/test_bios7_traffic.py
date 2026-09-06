"""BIOS 7 passage admission must never turn stale absence into clearance."""

from dataclasses import replace

import pytest

from src import messages as msg
from src.amr import (AMRBrain, POLICY_BIOS_PIBT_V6, POLICY_BIOS_PIBT_V7,
                     Peer, ST_TO_DROP, Task)
from src.environment import chokepoint_warehouse
from src.geometry import cell_center
from src.settings import DEFAULT
from src.world import World


def _observer(policy=POLICY_BIOS_PIBT_V7, *, session="owner-session", reverse=False):
    env = chokepoint_warehouse(length=13)
    brain = AMRBrain("A", env, DEFAULT, policy=policy,
                     allocation_policy="auction_bundle")
    world = World(env, DEFAULT, seed=0)
    world.add_robot("A", (2, 2))
    pick, drop = ((22, 4), (2, 4)) if reverse else ((2, 4), (22, 4))
    task = Task("JOB", pick, drop, auction_epoch=3)
    brain._ensure_task_identity(task)
    brain.open_tasks[task.tid] = task
    brain._task_claims[task.tid] = (3, 2.0, "B", 100.0)
    brain.peers["B"] = Peer("B", state=ST_TO_DROP, goal=task.drop,
                            task_id=task.tid, task_generation=task.generation,
                            task_descriptor_hash=task.descriptor_hash,
                            task_auction_epoch=task.auction_epoch, pose_session=session)
    brain._v7_accept_pose_session("B", session)
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


@pytest.mark.parametrize("cell,offset", (((19, 4), (0.0, 0.0)),
                                        ((19, 3), (0.0, 0.3))))
def test_shared_exit_junction_must_clear_the_whole_body(cell, offset):
    brain, task, sensors = _observer()
    _position(brain, (12, 4), 0.0)
    brain._v7_observe_passages(0.0, sensors)
    brain._claims[0] = ("B", 20.0, 1.0, 2, None)
    _position(brain, cell, 1.0)
    x, y, theta = brain.peers["B"].pose
    brain.peers["B"].pose = (x + offset[0], y + offset[1], theta)
    brain._v7_observe_passages(1.0, sensors)
    assert brain._v7_pending_corridors(task, 1.0) == {0: (6, 4)}
    assert brain._bios_lock(0, 1.0) == ("B", 20.0)


def test_exit_junction_reentry_revokes_at_use_time_before_next_sample():
    brain, task, sensors = _observer()
    _position(brain, (12, 4), 0.0)
    brain._v7_observe_passages(0.0, sensors)
    _position(brain, (21, 4), 1.0)
    brain._v7_observe_passages(1.0, sensors)
    assert brain._v7_pending_corridors(task, 1.0) == {}
    _position(brain, (19, 4), 1.02)
    assert brain._v7_pending_corridors(task, 1.02) == {0: (6, 4)}
    assert brain._v7_passage_observed_at == 1.0


@pytest.mark.parametrize("reverse", (False, True))
def test_turning_out_of_either_exit_releases_admission_only_after_junction_clear(reverse):
    brain, task, sensors = _observer(reverse=reverse)
    original_claim = brain._task_claims[task.tid]
    _position(brain, (12, 4), 0.0)
    brain._v7_observe_passages(0.0, sensors)
    mouth = 5 if reverse else 19
    _position(brain, (mouth, 4), 1.0)
    brain._v7_observe_passages(1.0, sensors)
    assert brain._v7_pending_corridors(task, 1.0)
    _position(brain, (mouth, 3), 2.0)
    brain._v7_observe_passages(2.0, sensors)
    assert brain._v7_pending_corridors(task, 2.0) == {}
    assert brain._task_claims[task.tid] == original_claim
    assert brain.completed_tasks == set()
    assert not brain._claims  # No physical token was invented by admission release.


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
    brain._v7_accept_pose_session("C", brain.peers["C"].pose_session)
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


def _execution_heartbeat(task, cell, t, seq, *, legacy=False, owner="B"):
    identity = {} if legacy else {
        "task_generation": task.generation,
        "task_descriptor_hash": task.descriptor_hash,
        "task_auction_epoch": task.auction_epoch,
    }
    return msg.heartbeat(owner, seq, t, (*cell_center(cell, DEFAULT.cell_m), 0.0),
                         cell, 0.8, "DEGRADED_P2P", ST_TO_DROP, task.tid,
                         goal=task.drop, **identity)


def _authenticated(packet, session="owner-session"):
    # Test-only key. Authentication covers the full additive execution metadata.
    secret = b"passage-unit-test-key-not-for-deployment"
    decoded, reason = msg.decode_packet(msg.encode(packet, secret, session),
                                        secret, require_auth=True)
    assert reason is None and decoded is not None
    return decoded


@pytest.mark.parametrize("mismatch", ("generation", "descriptor", "epoch", "legacy"))
def test_authenticated_same_id_wrong_execution_cannot_release_admission(mismatch):
    brain, task, sensors = _observer()
    actual = replace(task)
    if mismatch == "generation":
        actual.generation = task.generation + 1
        actual.descriptor_hash = ""
        brain._ensure_task_identity(actual)
    elif mismatch == "descriptor":
        actual.descriptor_hash = "f" * 64
    elif mismatch == "epoch":
        actual.auction_epoch += 1
    original_claim = brain._task_claims[task.tid]
    brain._claims[0] = ("SPATIAL_OWNER", 100.0, 1.0, 0, None)
    for seq, t, cell in ((10, 0.0, (12, 4)), (11, 1.0, (21, 4))):
        packet = _execution_heartbeat(actual, cell, t, seq,
                                      legacy=mismatch == "legacy")
        brain._ingest(t, [_authenticated(packet)])
        brain._v7_observe_passages(t, sensors)
        assert brain._v7_pending_corridors(task, t) == {0: (6, 4)}
    assert brain._v7_passages_cleared == set()
    assert brain._task_claims[task.tid] == original_claim
    assert brain._claims[0][0] == "SPATIAL_OWNER"
    assert task.tid not in brain.completed_tasks


def test_authenticated_old_generation_pose_cannot_prove_new_wms_job_passage():
    brain, old_task, sensors = _observer()
    newer = replace(old_task, generation=1, descriptor_hash="", auction_epoch=0)
    brain._ensure_task_identity(newer)
    brain._ingest(0.0, [
        _authenticated(msg.task_new("WMS", 1, 0.0, newer.tid, newer.pick, newer.drop,
            generation=newer.generation, descriptor_hash=newer.descriptor_hash)),
        _authenticated(msg.award("C", 1, 0.0, newer.tid, 2.0, winner="B",
            generation=newer.generation, descriptor_hash=newer.descriptor_hash,
            lease_until=100.0)),
    ])
    current = brain.open_tasks[newer.tid]
    assert current.generation == 1
    for seq, t, cell in ((10, 0.0, (12, 4)), (11, 1.0, (21, 4))):
        brain._ingest(t, [_authenticated(_execution_heartbeat(old_task, cell, t, seq))])
        brain._v7_observe_passages(t, sensors)
        assert brain._v7_pending_corridors(current, t) == {0: (6, 4)}
    assert not brain._v7_passages_cleared


def test_authenticated_matching_execution_releases_only_admission_then_reentry_revokes():
    brain, task, sensors = _observer()
    for seq, t, cell in ((10, 0.0, (12, 4)), (11, 1.0, (21, 4))):
        brain._ingest(t, [_authenticated(_execution_heartbeat(task, cell, t, seq))])
        brain._v7_observe_passages(t, sensors)
    assert brain._v7_pending_corridors(task, 1.0) == {}
    brain._ingest(2.0, [_authenticated(_execution_heartbeat(task, (12, 4), 2.0, 12))])
    brain._v7_observe_passages(2.0, sensors)
    assert brain._v7_pending_corridors(task, 2.0) == {0: (6, 4)}


@pytest.mark.parametrize("change", ("legacy", "generation", "descriptor", "epoch", "session"))
def test_execution_transition_inside_observer_interval_discards_old_witness(change):
    brain, task, sensors = _observer()
    for seq, t, cell in ((10, 0.0, (12, 4)), (11, 1.0, (21, 4))):
        brain._ingest(t, [_authenticated(_execution_heartbeat(task, cell, t, seq))])
        brain._v7_observe_passages(t, sensors)
    assert brain._v7_pending_corridors(task, 1.0) == {}
    different = replace(task)
    if change == "epoch":
        different.auction_epoch += 1
    elif change == "generation":
        different.generation += 1
    elif change == "descriptor":
        different.descriptor_hash = "f" * 64
    packet = _execution_heartbeat(different, (21, 4), 1.01, 12, legacy=change == "legacy")
    brain._ingest(1.01, [_authenticated(packet,
        session="restarted" if change == "session" else "owner-session")])
    brain._ingest(1.02, [_authenticated(_execution_heartbeat(task, (21, 4), 1.02, 13))])
    assert not brain._v7_passages_entered and not brain._v7_passages_cleared
    brain._v7_observe_passages(1.2, sensors)
    assert brain._v7_pending_corridors(task, 1.2) == {0: (6, 4)}


@pytest.mark.parametrize("mismatch", (None, "generation", "descriptor", "epoch"))
def test_self_passage_binds_actual_execution_not_catalog_only(mismatch):
    brain, task, sensors = _observer()
    brain._task_claims[task.tid] = (3, 2.0, brain.rid, 100.0)
    brain.task = replace(task)
    brain.goal = task.drop
    if mismatch == "generation":
        brain.task.generation += 1
    elif mismatch == "descriptor":
        brain.task.descriptor_hash = "f" * 64
    elif mismatch == "epoch":
        brain.task.auction_epoch += 1
    for t, cell in ((0.0, (12, 4)), (1.0, (21, 4))):
        measured = replace(sensors, t=t, cell=cell,
                           pose=(*cell_center(cell, DEFAULT.cell_m), 0.0))
        brain._v7_observe_passages(t, measured)
    expected = {} if mismatch is None else {0: (6, 4)}
    assert brain._v7_pending_corridors(task, 1.0) == expected


def _reference_heartbeat(task, cell, t, seq, reference):
    packet = _execution_heartbeat(task, cell, t, seq, legacy=True)
    return replace(packet, body={**packet.body, "tr": reference})


def test_full_declaration_and_compact_reference_prove_same_execution():
    brain, task, sensors = _observer()
    brain._ingest(0.0, [_authenticated(_execution_heartbeat(task, (12, 4), 0.0, 10))])
    brain._v7_observe_passages(0.0, sensors)
    brain._ingest(1.0, [_authenticated(_reference_heartbeat(task, (21, 4), 1.0, 11, 10))])
    brain._v7_observe_passages(1.0, sensors)
    assert brain._v7_pending_corridors(task, 1.0) == {}
    # An unchanged full refresh advances the reference, not task authority/history.
    brain._ingest(1.1, [_authenticated(_execution_heartbeat(task, (21, 4), 1.1, 12))])
    assert brain._v7_pending_corridors(task, 1.1) == {}
    brain._ingest(1.2, [_authenticated(_reference_heartbeat(task, (21, 4), 1.2, 13, 12))])
    assert brain._v7_pending_corridors(task, 1.2) == {}


@pytest.mark.parametrize("failure", ("missing", "wrong_reference", "old_reference", "new_session"))
def test_unknown_or_cross_session_reference_never_establishes_or_restores_passage(failure):
    brain, task, sensors = _observer()
    if failure != "missing":
        brain._ingest(0.0, [_authenticated(_execution_heartbeat(task, (12, 4), 0.0, 10))])
        brain._v7_observe_passages(0.0, sensors)
    if failure == "old_reference":
        brain._ingest(0.5, [_authenticated(_execution_heartbeat(task, (12, 4), 0.5, 11))])
    reference = 9 if failure == "wrong_reference" else 10
    brain._ingest(1.0, [_authenticated(_reference_heartbeat(task, (21, 4), 1.0, 12, reference),
        session="new-session" if failure == "new_session" else "owner-session")])
    brain._v7_observe_passages(1.0, sensors)
    assert brain._v7_pending_corridors(task, 1.0) == {0: (6, 4)}
    assert not brain._v7_passages_cleared


@pytest.mark.parametrize("expire_peer", (False, True))
def test_delayed_unseen_retired_session_packets_cannot_resurrect_release(expire_peer):
    from src.transport import ReplayWindow
    brain, task, sensors = _observer(session="old")
    windows = {}

    def receive(session, seq, sent, received, cell):
        packet = _authenticated(_execution_heartbeat(task, cell, sent, seq), session)
        assert windows.setdefault(session, ReplayWindow()).accept(seq)
        brain._ingest(received, [packet])
        brain._v7_observe_passages(received, sensors)

    receive("old", 10, 0.0, 0.0, (12, 4))
    receive("old", 11, 1.0, 1.0, (21, 4))
    assert brain._v7_pending_corridors(task, 1.0) == {}
    receive("new", 1, 2.0, 2.0, (12, 4))
    assert brain._v7_pending_corridors(task, 2.0) == {0: (6, 4)}
    now = 20.0 if expire_peer else 2.2
    if expire_peer:
        brain._expire_peers(now)
        assert "B" not in brain.peers
    # Real per-session anti-replay accepts these previously unseen old datagrams.
    receive("old", 12, 1.2, now, (12, 4))
    receive("old", 13, 1.6, now + 0.2, (21, 4))
    assert brain._v7_pending_corridors(task, now + 0.2) == {0: (6, 4)}
    assert brain._v7_passage_sessions["B"].active == "new"
    if not expire_peer:
        assert brain.peers["B"].cell == (12, 4)


def test_same_session_pose_sequence_high_watermark_survives_peer_expiry():
    brain, task, sensors = _observer()
    brain._ingest(0.0, [_authenticated(_execution_heartbeat(task, (12, 4), 0.0, 20))])
    brain._v7_observe_passages(0.0, sensors)
    brain._expire_peers(20.0)
    assert "B" not in brain.peers
    for seq, t, cell in ((18, 20.0, (12, 4)), (19, 20.2, (21, 4))):
        brain._ingest(t, [_authenticated(_execution_heartbeat(task, cell, 0.0, seq))])
        brain._v7_observe_passages(t, sensors)
    assert "B" not in brain.peers
    assert brain._v7_pending_corridors(task, 20.2) == {0: (6, 4)}


def test_session_history_and_roster_saturation_fail_closed_without_eviction(monkeypatch):
    import src.amr as amr
    monkeypatch.setattr(amr, "MAX_RETIRED_PASSAGE_SESSIONS", 2)
    monkeypatch.setattr(amr, "MAX_PASSAGE_SESSION_PEERS", 1)
    brain, task, sensors = _observer()
    for index, session in enumerate(("first", "second", "third", "fourth")):
        brain._ingest(float(index), [_authenticated(
            _execution_heartbeat(task, (12, 4), float(index), 1), session)])
    entry = brain._v7_passage_sessions["B"]
    assert entry.disabled and len(entry.retired) == 2
    brain._v7_observe_passages(3.0, sensors)
    brain._ingest(4.0, [_authenticated(_execution_heartbeat(task, (21, 4), 4.0, 2), "fourth")])
    brain._v7_observe_passages(4.0, sensors)
    assert brain._v7_pending_corridors(task, 4.0) == {0: (6, 4)}
    brain._task_claims[task.tid] = (3, 2.0, "C", 100.0)
    for seq, t, cell in ((1, 5.0, (12, 4)), (2, 6.0, (21, 4))):
        brain._ingest(t, [_authenticated(_execution_heartbeat(task, cell, t, seq, owner="C"))])
        brain._v7_observe_passages(t, sensors)
    assert "C" in brain.peers  # Telemetry remains available; evidence does not.
    assert set(brain._v7_passage_sessions) == {"B"}
    assert brain._v7_pending_corridors(task, 6.0) == {0: (6, 4)}


@pytest.mark.parametrize("owner", ("B", "A"))
def test_use_time_full_body_reentry_revokes_between_observation_samples(owner):
    brain, task, sensors = _observer()
    if owner == "A":
        brain.task, brain.goal = task, task.drop
        brain._task_claims[task.tid] = (3, 2.0, "A", 100.0)
    boundary = (20 * DEFAULT.cell_m + DEFAULT.robot.radius_m
                + DEFAULT.traffic.v7_passage_clearance_m)
    for seq, t, cell, x in ((10, 0.0, (12, 4), 17.5),
                            (11, 1.0, (20, 4), boundary + 0.001),
                            (12, 1.09, (20, 4), boundary - 0.001)):
        if owner == "B":
            packet = _execution_heartbeat(task, cell, t, seq)
            packet = replace(packet, body={**packet.body, "p": [x, 6.3, 0.0]})
            brain._ingest(t, [_authenticated(packet)])
            brain._v7_observe_passages(t, sensors)
        else:
            brain._v7_observe_passages(t, replace(sensors, t=t, cell=cell,
                pose=(x, 6.3, 0.0)))
        if t == 1.0:
            assert brain._v7_pending_corridors(task, t) == {}
    assert brain._v7_passage_observed_at == 1.0  # Last pose skipped the sampler.
    assert brain._v7_pending_corridors(task, 1.09) == {0: (6, 4)}


def test_previously_unseen_old_session_cannot_be_treated_as_a_newer_incarnation():
    from src.transport import ReplayWindow
    brain, task, sensors = _observer()
    brain.peers.clear()
    brain._v7_passage_sessions.clear()  # A newly started observer knows neither SID.
    original_claim = brain._task_claims[task.tid]
    windows = {}
    # Actual restarted owner is still inside at x=26.54. Older, delayed motion is
    # physically plausible and authenticated, but the opaque SID cannot order it.
    events = (
        ("new", 1, 0.9, 0.9, (18, 4), 26.54, None),
        ("unseen-old", 10, 0.0, 1.02, (18, 4), 26.59, None),
        ("unseen-old", 11, 0.4, 1.22, (19, 4), 27.06, 10),
    )
    for session, seq, sent, arrival, cell, x, reference in events:
        packet = (_execution_heartbeat(task, cell, sent, seq) if reference is None
                  else _reference_heartbeat(task, cell, sent, seq, reference))
        packet = replace(packet, body={**packet.body, "p": [x, 6.3, 0.0]})
        decoded = _authenticated(packet, session)
        assert windows.setdefault(session, ReplayWindow()).accept(seq)
        brain._ingest(arrival, [decoded])
        brain._v7_observe_passages(arrival, sensors)
        assert brain._v7_pending_corridors(task, arrival) == {0: (6, 4)}
    assert brain._v7_passage_sessions["B"].disabled
    assert brain.peers["B"].pose_seen_t == 1.22  # Advisory telemetry is not a proof.
    assert brain._task_claims[task.tid] == original_claim
    # Neither a fresh full declaration nor peer expiry re-enables this observer.
    brain._expire_peers(20.0)
    for seq, t, cell in ((12, 20.0, (12, 4)), (13, 21.0, (21, 4))):
        brain._ingest(t, [_authenticated(_execution_heartbeat(task, cell, t, seq), "unseen-old")])
        brain._v7_observe_passages(t, sensors)
        assert brain._v7_pending_corridors(task, t) == {0: (6, 4)}
