"""Exact candidate-membership parity and bounded predecessor work."""
from dataclasses import replace
import random
from types import SimpleNamespace

import pytest

from src.amr import (AMRBrain, Peer, POLICY_BIOS_PIBT_V7, ST_BLOCKED, ST_CHARGING,
                     ST_IDLE, ST_TO_PICK, Task)
from src.environment import open_floor
from src.geometry import manhattan
from src.settings import DEFAULT
from src.task_allocation import ALLOCATION_AUCTION_BUNDLE


def legacy_candidate(self, task, t, sensors):
    """Unchanged pre-pruning implementation retained as the equivalence oracle."""
    candidates = [(manhattan(sensors.cell, task.pick), self.rid)]
    for peer in self.peers.values():
        if (t - peer.last_seen > self._peer_stale_after_s()
                or peer.state != ST_IDLE or peer.goal is not None
                or peer.battery_frac < self.cfg.traffic.energy_charge_trigger_frac):
            continue
        required = self._energy_required(task, peer.cell)
        if (required is None
                or peer.battery_frac - required < self.cfg.traffic.energy_reserve_frac):
            continue
        estimate = self._task_estimate(task, peer.cell)
        if (estimate is None
                or (task.deadline is not None and t + estimate[1] > task.deadline)):
            continue
        candidates.append((manhattan(peer.cell, task.pick), peer.rid))
    candidates.sort()
    count = max(1, self.cfg.traffic.energy_candidate_bids)
    return self.rid in {rid for _distance, rid in candidates[:count]}


def brain(count=3, rid="AMR50"):
    cfg = replace(DEFAULT, traffic=replace(DEFAULT.traffic, energy_candidate_bids=count))
    return AMRBrain(rid, open_floor(16, 16), cfg, policy=POLICY_BIOS_PIBT_V7,
                    allocation_policy=ALLOCATION_AUCTION_BUNDLE)


def estimated(brain, estimates):
    calls = []

    def estimate(_task, cell):
        calls.append(("estimate", cell))
        return estimates.get(cell)

    def required(_task, cell):
        calls.append(("required", cell))
        value = estimates.get(cell)
        return value[0] if value is not None else None

    brain._task_estimate = estimate
    brain._energy_required = required
    return calls


@pytest.mark.parametrize("seed", range(200))
def test_randomized_membership_matches_full_sort_for_valid_peer_views(seed):
    rng = random.Random(seed)
    b = brain(count=rng.choice((0, 1, 2, 3, 5, 50)), rid=f"AMR{rng.randrange(50):02d}")
    t = 100.0
    task = Task("TASK", (rng.randrange(16), rng.randrange(16)), (15, 15),
                deadline=rng.choice((None, 105.0, 110.0, 125.0)))
    sensors = SimpleNamespace(cell=(rng.randrange(16), rng.randrange(16)), battery_frac=1.0)
    estimates = {}
    ids = [f"AMR{i:02d}" for i in range(50) if f"AMR{i:02d}" != b.rid]
    rng.shuffle(ids)
    for rid in ids[:rng.randrange(40)]:
        cell = (rng.randrange(16), rng.randrange(16))
        estimates[cell] = (rng.uniform(0.0, 0.8), rng.uniform(0.0, 30.0)) if rng.random() > .15 else None
        b.peers[rid] = Peer(rid, cell=cell,
            state=rng.choice((ST_IDLE, ST_IDLE, ST_IDLE, ST_CHARGING, ST_TO_PICK, ST_BLOCKED)),
            goal=None if rng.random() > .2 else (15, 15),
            last_seen=t-rng.choice((0.0, .1, b._peer_stale_after_s(), b._peer_stale_after_s()+.01)),
            battery_frac=rng.uniform(0.0, 1.0))
    calls = estimated(b, estimates)
    expected = legacy_candidate(b, task, t, sensors)
    calls.clear()
    assert b._energy_candidate(task, t, sensors) is expected
    own_rank = (manhattan(sensors.cell, task.pick), b.rid)
    preceding_cells = {p.cell for p in b.peers.values()
                       if (manhattan(p.cell, task.pick), p.rid) < own_rank}
    assert all(cell in preceding_cells for _, cell in calls)


def test_farther_peers_never_trigger_energy_work():
    b = brain()
    b.peers = {"AMR01": Peer("AMR01", cell=(8, 8), last_seen=1)}
    calls = estimated(b, {(8, 8): (.1, 1)})
    assert b._energy_candidate(Task("T", (1, 1), (3, 3)), 1,
                               SimpleNamespace(cell=(1, 1)))
    assert calls == []


def test_stops_after_exactly_k_feasible_predecessors_in_rank_order():
    b = brain(3)
    # Insertion order is deliberately opposite to deterministic distance/ID rank.
    b.peers = {f"AMR{i:02d}": Peer(f"AMR{i:02d}", cell=(i, 1), last_seen=1)
               for i in reversed(range(1, 9))}
    calls = estimated(b, {(i, 1): (.1, 1) for i in range(1, 9)})
    assert not b._energy_candidate(Task("T", (0, 1), (10, 10)), 1,
                                   SimpleNamespace(cell=(15, 1)))
    assert [cell for kind, cell in calls if kind == "required"] == [(1, 1), (2, 1), (3, 1)]


@pytest.mark.parametrize("override,estimate", [
    ({"last_seen": -100}, (.1, 1)),
    ({"state": ST_CHARGING}, (.1, 1)),
    ({"state": ST_TO_PICK}, (.1, 1)),
    ({"goal": (1, 1)}, (.1, 1)),
    ({"battery_frac": .05}, (.1, 1)),
    ({"battery_frac": .2}, (.1, 1)),  # Below reserve after work, despite charge threshold.
    ({}, None),
    ({}, (.1, 100)),  # Can afford work, but cannot finish by hard deadline.
])
def test_ineligible_nearer_peer_never_consumes_candidate_slot(override, estimate):
    b = brain(1)
    b.peers = {"AMR01": Peer("AMR01", **{"cell": (1, 1), "last_seen": 1, **override})}
    estimated(b, {(1, 1): estimate})
    task = Task("T", (1, 1), (10, 10), deadline=20)
    sensors = SimpleNamespace(cell=(12, 1))
    assert legacy_candidate(b, task, 1, sensors)
    assert b._energy_candidate(task, 1, sensors)


def test_equal_distance_robot_id_tie_and_deadline_equality_are_preserved():
    b = brain(1, "AMR02")
    b.peers = {"AMR01": Peer("AMR01", cell=(2, 1), last_seen=1),
               "AMR03": Peer("AMR03", cell=(1, 2), last_seen=1)}
    calls = estimated(b, {(2, 1): (.1, 9), (1, 2): (.1, 9)})
    task = Task("T", (1, 1), (10, 10), deadline=10)
    assert not b._energy_candidate(task, 1, SimpleNamespace(cell=(0, 1)))
    assert calls == [("required", (2, 1)), ("estimate", (2, 1))]


def test_peer_battery_and_deadline_are_revalidated_between_calls():
    b = brain(1)
    peer = Peer("AMR01", cell=(1, 1), last_seen=1, battery_frac=1)
    b.peers = {peer.rid: peer}
    estimated(b, {(1, 1): (.1, 9)})
    task = Task("T", (1, 1), (10, 10), deadline=10)
    sensors = SimpleNamespace(cell=(12, 1))
    assert not b._energy_candidate(task, 1, sensors)
    peer.battery_frac = .2
    assert b._energy_candidate(task, 1, sensors)
    peer.battery_frac = 1
    assert b._energy_candidate(task, 1.1, sensors)


@pytest.mark.parametrize("seed", range(12))
def test_real_path_energy_estimates_keep_legacy_membership(seed):
    rng = random.Random(seed)
    b = brain(rng.choice((1, 3, 5)))
    for index in range(8):
        rid = f"AMR{index:02d}"
        b.peers[rid] = Peer(rid, cell=(rng.randrange(16), rng.randrange(16)),
            last_seen=1, battery_frac=rng.uniform(.15, 1))
    task = Task("T", (2, 2), (12, 12), cargo_type=rng.choice(("normal", "heavy")),
                cargo_weight=rng.choice((0, 30, 80)), deadline=rng.choice((None, 10, 100)))
    sensors = SimpleNamespace(cell=(rng.randrange(16), rng.randrange(16)))
    assert b._energy_candidate(task, 1, sensors) == legacy_candidate(b, task, 1, sensors)
