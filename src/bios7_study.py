"""Explicit experiment definitions for BIOS 7; no release defaults are changed."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import platform
import random
import subprocess
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from .amr import (Task, POLICY_BIOS_PIBT_V6, POLICY_STOP_WAIT,
                  POLICY_STOP_WAIT_COMPETITION, POLICY_PRIORITIZED_SPACE_TIME)
from .environment import DOCK, FREE, STATION, Warehouse, classic_warehouse
from .planner import astar
from .scenarios import (SCENARIOS, Scenario, _spread_starts,
                        scenario_catalog_fingerprint)
from .settings import DEFAULT

CONTROL_COMMIT = "8e4ab51727e4fcd7f372de8b55b99c38cb213512"
POLICY_CONFIGS = {
    "bios6": (POLICY_BIOS_PIBT_V6, "auction_bundle"),
    "bios7": ("BIOS_PIBT.7", "auction_bundle"),
    "bios7_no_release": ("BIOS_PIBT.7", "auction_bundle"),
    "stop_wait": (POLICY_STOP_WAIT, "auction_bundle"),
    "competition": (POLICY_STOP_WAIT_COMPETITION, "preassigned"),
    "centralized": (POLICY_PRIORITIZED_SPACE_TIME, "hungarian"),
}
TRAFFIC_OVERRIDES = {"bios7_no_release": {"v7_passage_release": False}}
STRESS_CASES = {
    "crossflow": "edge_overlap", "doorway": "edge_chokepoint",
    "blocked": "blocked_aisle", "failure": "robot_failure_reassignment",
    "humans": "edge_human_crossing", "open": "open_floor_control",
}


def _hash(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def source_manifest(root: Path) -> dict:
    """Hash tracked executable sources; ignored/private user assets are not read."""
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=root).decode().split("\0")
    selected = [name for name in tracked if name and (
        (name.endswith((".py", ".js", ".mjs", ".json", ".toml", ".lock"))
         or Path(name).name.startswith("requirements"))
        and not name.startswith(("artifacts/", "tests/")))]
    # Candidate experiment files are deliberately included before their first commit.
    selected += [str(p.relative_to(root)) for p in root.glob("bios7*.py")]
    selected += [str(p.relative_to(root)) for p in (root / "src").glob("*.py")]
    hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
              for name in sorted(set(selected)) if (root / name).is_file()}
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root,
                                     text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--",
                                     *hashes], cwd=root, text=True).strip()
    dependencies = sorted({f"{dist.metadata['Name']}=={dist.version}"
                           for dist in importlib.metadata.distributions()
                           if dist.metadata.get("Name")})
    return {"commit": commit, "tracked_sources_dirty": bool(dirty),
            "source_sha256": hashes, "source_manifest_sha256": _hash(hashes),
            "python": sys.version, "python_executable": sys.executable,
            "dependencies": dependencies,
            "host": {"system": platform.system(), "release": platform.release(),
                     "machine": platform.machine(), "processor": platform.processor()},
            "hardware_scope": "current_machine_only", "raspberry_pi_measured": False}


def capacity_scenario(n_robots: int, seed: int, *, scaled: bool,
                      tasks_per_robot: int = 2, total_tasks: int | None = None) -> Scenario:
    """Explicit floor capacity, modest local work and 20% adjacent-module work.

    Fixed-floor saturation and scaled-floor capacity are intentionally separate.
    Neither silently replaces the pinned SIH map. A module is 18x15 cells; real free
    cell count, station count and distance distributions are recorded in diagnostics.
    """
    if not 3 <= n_robots <= 100 or tasks_per_robot < 1:
        raise ValueError("capacity screens require 3-100 robots and positive work")
    modules = math.ceil(n_robots / 10) if scaled else 6
    columns = math.ceil(math.sqrt(modules))
    rows = math.ceil(modules / columns)
    env = classic_warehouse(width=18 * columns + 1, height=15 * rows + 1,
                            name="capacity_scaled" if scaled else "capacity_fixed")
    # Service capacity scales with modules as well as floor. Merely expanding empty
    # space while leaving one set of chargers/stations would be a different study.
    stations = tuple((18*x + 1, 15*y + 1)
                     for y in range(rows) for x in range(columns))
    docks = tuple((18*x + 17, 15*y + 1)
                  for y in range(rows) for x in range(columns))
    grid = [list(row) for row in env.grid]
    for cell in env.stations + env.docks:
        grid[cell[1]][cell[0]] = FREE
    for kind, cells in ((STATION, stations), (DOCK, docks)):
        for x, y in cells:
            grid[y][x] = kind
    env = Warehouse(env.width, env.height, tuple(tuple(row) for row in grid),
                    stations, docks, env.name)
    rng = random.Random(seed)
    starts = _spread_starts(env, n_robots, rng)
    if len(starts) != n_robots:
        raise ValueError("floor cannot place the requested fleet without overlap")
    buckets = {}
    for cell in env.free_cells():
        key = (min(cell[0] // 18, columns - 1), min(cell[1] // 15, rows - 1))
        if env.degree(cell) >= 2:
            buckets.setdefault(key, []).append(cell)
    keys = sorted(buckets)
    count = n_robots * tasks_per_robot if total_tasks is None else total_tasks
    if count < 1:
        raise ValueError("task catalog must be nonempty")
    assignments = [[] for _ in starts]
    for i in range(count):
        key = keys[i % len(keys)]
        neighbors = [other for other in keys
                     if abs(other[0]-key[0]) + abs(other[1]-key[1]) == 1]
        other = rng.choice(neighbors) if neighbors and i % 5 == 0 else key
        pick = rng.choice(buckets[key])
        destinations = [cell for cell in buckets[other] if cell != pick]
        drop = rng.choice(destinations)
        assignments[i % n_robots].append(Task(f"CAP-{i:04d}", pick, drop))
    return Scenario(env.name, env, starts, assignments, duration_s=1200.0,
                    pose_noise_m=0.02, seed=seed)


def build_case(name: str, robots: int, seed: int,
               total_tasks: int | None = None) -> Scenario:
    if name == "sih":
        if robots not in (3, 4, 6, 8, 10):
            raise ValueError("pinned SIH study is explicitly scoped to 3/4/6/8/10 AMRs")
        if total_tasks is not None:
            raise ValueError("the pinned SIH task catalog cannot be overridden")
        return SCENARIOS["sih_acceptance_overlap"](n_robots=robots, seed=seed)
    if name in ("fixed_floor", "scaled_floor"):
        return capacity_scenario(robots, seed, scaled=name == "scaled_floor",
                                 total_tasks=total_tasks)
    if name in STRESS_CASES:
        if robots > 10:
            raise ValueError("compact live-style fixtures are scoped to at most 10 AMRs")
        return SCENARIOS[STRESS_CASES[name]](n_robots=robots, seed=seed)
    raise ValueError(f"unknown study case {name!r}")


def scenario_diagnostics(sc: Scenario) -> dict:
    tasks = {task.tid: task for queue in sc.assignments for task in queue}
    tasks.update({task.tid: task for task in sc.unassigned})
    counts: Counter = Counter()
    edges: Counter = Counter()
    lengths = []
    invalid = []
    for task in tasks.values():
        route = astar(sc.env, task.pick, task.drop)
        if not route:
            invalid.append(task.tid)
        else:
            lengths.append(len(route) - 1)
            counts.update(set(route))
            edges.update(set(zip(route, route[1:])))
    free = sum(1 for _ in sc.env.free_cells())
    shared = sum(count > 1 for count in counts.values())
    opposing = sum((b, a) in edges for a, b in edges) // 2
    valid_starts = (len(set(sc.starts)) == len(sc.starts)
                    and all(sc.env.passable(c) for c in sc.starts))
    return {"world_fingerprint": scenario_catalog_fingerprint(sc),
            "width_cells": sc.env.width, "height_cells": sc.env.height,
            "free_cells": free, "robot_density_per_free_cell": sc.n_robots / free,
            "stations": len(sc.env.stations), "docks": len(sc.env.docks),
            "robots": sc.n_robots, "tasks": len(tasks), "humans": len(sc.humans),
            "tasks_per_robot": len(tasks)/sc.n_robots,
            "cutoff_s": sc.duration_s, "shortest_task_lengths_cells": sorted(lengths),
            "shared_nominal_route_cells": shared,
            "opposing_nominal_route_edges": opposing,
            "overlap_fixture_valid": shared > 0,
            "valid_nonoverlapping_starts": valid_starts,
            "unreachable_task_ids": invalid,
            "feasibility_scope": "static reachability and initial placement only; "
                                 "not proof of dynamic schedulability before cutoff"}


def scenario_wire(sc: Scenario) -> dict:
    """Serializable complete scenario; the same bytes go to both source trees."""
    return json.loads(json.dumps(asdict(sc), allow_nan=False))


def physical_config_manifest() -> dict:
    cfg = asdict(DEFAULT)
    return {key: cfg[key] for key in ("robot", "rates", "cell_m")}
