"""Bounded static memoization must preserve the pre-cache energy model exactly."""
from dataclasses import replace
import random
from types import SimpleNamespace

import pytest

import src.amr as amr
from src.amr import AMRBrain, POLICY_BIOS_PIBT_V7, Task
from src.environment import RACK, open_floor
from src.planner import astar
from src.settings import DEFAULT
from src.task_allocation import ALLOCATION_AUCTION_BUNDLE


def legacy_estimate(self, task, start, extra_cost=None, edge_cost=None):
    """Exact pre-optimization method, including its prior cache and dock fallback."""
    cache_key = (start, task.tid, task.pick, task.drop,
                 task.cargo_type, float(task.cargo_weight))
    cacheable = extra_cost is None and not edge_cost
    if cacheable and cache_key in self._energy_required_cache:
        return self._energy_required_cache[cache_key]
    cargo_factor = self._cargo_factor(task.cargo_type)
    if cargo_factor is None or task.cargo_weight < 0.0:
        if cacheable:
            self._energy_required_cache[cache_key] = None
        return None
    to_pick = amr.astar(self.env, start, task.pick, extra_cost=extra_cost,
                       edge_cost=edge_cost)
    to_drop = amr.astar(self.env, task.pick, task.drop, extra_cost=extra_cost,
                       edge_cost=edge_cost)
    if not to_pick or not to_drop:
        if cacheable:
            self._energy_required_cache[cache_key] = None
        return None
    charger_cells = []
    for dock in self.env.docks:
        path = amr.astar(self.env, task.drop, dock)
        if path:
            charger_cells.append(max(0, len(path) - 1))
    charger_steps = min(charger_cells) if charger_cells else 0
    loaded_steps = max(0, len(to_drop) - 1)
    spec, traffic = self.cfg.robot, self.cfg.traffic
    cruise_mps = max(0.1, 0.65 * spec.v_max)
    approach_steps = max(0, len(to_pick) - 1)
    approach_s = approach_steps * self.cfg.cell_m / cruise_mps
    dock_s = charger_steps * self.cfg.cell_m / cruise_mps
    loaded_s = loaded_steps * self.cfg.cell_m / cruise_mps
    handling_s = traffic.energy_service_s * cargo_factor
    weight_factor = 1.0 + traffic.cargo_full_payload_energy_premium * (
        task.cargo_weight / max(1e-9, spec.max_payload_kg))
    energy_wh = (
        spec.draw_move_w
        * (approach_s + traffic.energy_loaded_multiplier * loaded_s)
        * cargo_factor * weight_factor / 3600.0
        + spec.draw_move_w * dock_s / 3600.0
        + spec.draw_idle_w * handling_s / 3600.0)
    required = energy_wh / spec.battery_full_wh
    required *= 1.0 + traffic.energy_uncertainty_frac
    result = (required, approach_s + loaded_s + handling_s)
    if cacheable:
        self._energy_required_cache[cache_key] = result
    return result


def brain(*, divided=False, docks=((13, 1), (13, 13))):
    env = open_floor(15, 15)
    if divided:
        grid = [list(row) for row in env.grid]
        for row in grid:
            row[7] = RACK
        env = replace(env, grid=tuple(tuple(row) for row in grid))
    env = replace(env, docks=docks)
    return AMRBrain("AMR01", env, DEFAULT, policy=POLICY_BIOS_PIBT_V7,
                    allocation_policy=ALLOCATION_AUCTION_BUNDLE)


def count_paths(monkeypatch):
    calls = []

    def counted(env, start, goal, **kwargs):
        calls.append((start, goal, kwargs))
        return astar(env, start, goal, **kwargs)

    monkeypatch.setattr(amr, "astar", counted)
    return calls


@pytest.mark.parametrize("seed", range(100))
def test_real_path_cargo_cost_and_cache_eviction_match_legacy(seed):
    rng = random.Random(seed)
    # Both components have a charger: the separate unreachable-charger safety fix
    # intentionally differs from the old reference and is tested independently.
    kwargs = {"divided": bool(seed % 3 == 0),
              "docks": () if seed % 7 == 0 else ((1, 1), (13, 13))}
    current, original = brain(**kwargs), brain(**kwargs)
    current._energy_cache_capacity = 3
    current._dock_distance_cache_capacity = 2
    free = list(current.env.free_cells())
    cases = []
    for index in range(12):
        start, pick, drop = (rng.choice(free) for _ in range(3))
        task = Task(f"T{index}", pick, drop,
            cargo_type=rng.choice(("normal", "fragile", "heavy", "hazardous", "invalid")),
            cargo_weight=rng.choice((-1, 0, 20, 80)))
        extra = rng.choice((None, {}, {rng.choice(free): 20.0}))
        edge = rng.choice((None, {}, {(pick, drop): 10.0}))
        cases.append((task, start, extra, edge))
    for task, start, extra, edge in cases + cases[::-1]:
        expected = legacy_estimate(original, task, start, extra, edge)
        assert current._task_estimate(task, start, extra, edge) == expected
        assert len(current._energy_required_cache) <= 3
        assert len(current._dock_distance_cache) <= 2


def test_empty_cost_maps_share_exact_static_estimate_without_more_paths(monkeypatch):
    current = brain()
    calls = count_paths(monkeypatch)
    task = Task("T", (2, 1), (6, 1))
    result = current._task_estimate(task, (1, 1), extra_cost={})
    assert len(calls) == 2 + len(current.env.docks)
    calls.clear()
    for extra, edge in ((None, None), ({}, None), (None, {}), ({}, {})):
        assert current._task_estimate(task, (1, 1), extra, edge) == result
    assert calls == []


def test_nonempty_cost_maps_bypass_static_cache_then_clearing_restores_it(monkeypatch):
    current, original = brain(), brain()
    calls = count_paths(monkeypatch)
    task = Task("T", (2, 1), (6, 1))
    initial = current._task_estimate(task, (1, 1), extra_cost={})
    penalty = {(4, 1): 100.0}
    learned = {((3, 1), (4, 1)): 100.0}
    for extra, edge in ((penalty, None), (None, learned), ({(4, 1): 0.0}, None)):
        expected = legacy_estimate(original, task, (1, 1), extra, edge)
        calls.clear()
        assert current._task_estimate(task, (1, 1), extra, edge) == expected
        assert len(calls) == 2  # Dynamic approach/drop recomputed; static dock leg reused.
    penalty.clear()
    learned.clear()
    calls.clear()
    assert current._task_estimate(task, (1, 1), penalty, learned) == initial
    assert calls == []


def test_same_drop_across_starts_tasks_and_cargo_reuses_only_dock_paths(monkeypatch):
    current = brain()
    calls = count_paths(monkeypatch)
    first = Task("A", (2, 1), (6, 1))
    current._task_estimate(first, (1, 1))
    calls.clear()
    second = Task("B", (3, 2), first.drop, cargo_type="heavy", cargo_weight=80)
    current._task_estimate(second, (1, 3), extra_cost={(4, 2): 8.0})
    assert [(start, goal) for start, goal, _ in calls] == [((1, 3), (3, 2)), ((3, 2), (6, 1))]


def test_no_dock_legacy_map_preserves_prior_zero_fallback():
    current, original = brain(divided=True, docks=()), brain(divided=True, docks=())
    task = Task("T", (2, 1), (5, 3))
    assert current._task_estimate(task, (1, 1)) == legacy_estimate(original, task, (1, 1))
    assert current._nearest_dock_steps(task.drop) == 0


def test_configured_unreachable_dock_rejection_is_cached_without_repeated_search(monkeypatch):
    current = brain(divided=True)
    calls = count_paths(monkeypatch)
    task = Task("T", (2, 1), (5, 3))
    assert current._task_estimate(task, (1, 1), extra_cost={}) is None
    assert current._dock_distance_cache[task.drop] is None
    calls.clear()
    assert current._task_estimate(task, (1, 1), extra_cost={}) is None
    assert calls == []
    assert current._task_estimate(task, (1, 2), extra_cost={(2, 2): 10.0}) is None
    assert len(calls) == 2  # Live task legs rechecked, known static disconnection reused.


def test_unreachable_task_none_is_cached_and_bounded(monkeypatch):
    current = brain(divided=True)
    current._energy_cache_capacity = 2
    calls = count_paths(monkeypatch)
    tasks = [Task(f"T{i}", (2, 1), (12, 3)) for i in range(3)]
    for task in tasks:
        assert current._task_estimate(task, (1, 1), extra_cost={}) is None
    assert len(current._energy_required_cache) == 2
    assert all(key[1] != "T0" for key in current._energy_required_cache)
    calls.clear()
    assert current._task_estimate(tasks[-1], (1, 1), extra_cost={}) is None
    assert calls == []
    assert current._task_estimate(tasks[0], (1, 1), extra_cost={}) is None
    assert len(calls) == 2


def test_fifo_energy_and_dock_eviction_recomputes_exactly(monkeypatch):
    current, original = brain(), brain()
    assert current._energy_cache_capacity == current._dock_distance_cache_capacity == 2048
    current._energy_cache_capacity = current._dock_distance_cache_capacity = 2
    calls = count_paths(monkeypatch)
    tasks = [Task(f"T{i}", (2, 1), (4+i, 3)) for i in range(3)]
    expected = [legacy_estimate(original, task, (1, 1)) for task in tasks]
    for index in (0, 1, 0, 2):
        assert current._task_estimate(tasks[index], (1, 1)) == expected[index]
    # A hit does not refresh insertion order: explicit bounded FIFO, not LRU.
    assert [key[1] for key in current._energy_required_cache] == ["T1", "T2"]
    assert list(current._dock_distance_cache) == [tasks[1].drop, tasks[2].drop]
    calls.clear()
    assert current._task_estimate(tasks[0], (1, 1)) == expected[0]
    assert len(calls) == 2 + len(current.env.docks)
    assert len(current._energy_required_cache) == len(current._dock_distance_cache) == 2


def test_battery_deadline_and_task_descriptor_changes_are_not_cached(monkeypatch):
    current = brain()
    task = Task("T", (2, 1), (6, 1))
    sensors = SimpleNamespace(cell=(1, 1), battery_frac=1.0)
    assert current._energy_feasible(task, sensors, t=1)[0]
    required, duration = current._task_estimate(task, sensors.cell)
    calls = count_paths(monkeypatch)
    sensors.battery_frac = required + current.cfg.traffic.energy_reserve_frac - .01
    assert not current._energy_feasible(task, sensors, t=1)[0]
    sensors.battery_frac = 1.0
    task.deadline = 1 + duration
    assert current._energy_feasible(task, sensors, t=1)[0]
    assert not current._energy_feasible(task, sensors, t=1.01)[0]
    assert calls == []  # Only static estimate reused, not any eligibility verdict.
    task.cargo_type = "heavy"
    task.cargo_weight = 80
    current._task_estimate(task, sensors.cell)
    assert len(calls) == 2  # New cargo descriptor, same static docking distance.
