"""Pinned benchmark scenarios.

WHY THESE ARE FIXED AND SEEDED
==============================
"A minimum 20% reduction in total task completion time compared to traditional
stop-and-wait" is not a measurable claim on its own. The speedup of any coordination
policy swings from roughly 5% to over 300% depending on map topology, robot density and
the task mix - so a number quoted without a pinned scenario means nothing, and can be
manufactured to order by choosing a friendly map.

Every scenario here therefore fixes the map, the start cells, the exact task stream and
the RNG seed. The same task list is handed to every policy, pre-assigned identically, so
the only variable between runs is how the fleet handles conflict.

`open_floor_control` exists to keep us honest: a map with no chokepoints where every
policy should tie. If our policy "wins" there too, the harness is measuring something
other than coordination and the headline number is wrong.

FLEET SIZE
==========
The statement asks for "at least 3 AMRs". Three robots cannot test its own hypothesis:
the entire justification for decentralising is *scaling*, and congestion, cascading
deadlock and O(N^2) message load only appear somewhere north of 20. At N=3 a central
planner wins trivially, and three robots in three disjoint aisles satisfy both stated
success criteria while solving nothing. So the default is larger and `bench` sweeps
N upward until the curves separate - that sweep is the actual result.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .amr import Task
from .environment import RACK, Warehouse, chokepoint_warehouse, classic_warehouse, open_floor
from .geometry import Cell, manhattan
from .settings import Config, NetSpec


@dataclass
class Scenario:
    name: str
    env: Warehouse
    starts: list[Cell]
    # rid index -> ordered task queue. Pre-assigned so allocation is not a variable.
    assignments: list[list[Task]]
    humans: list[list[Cell]] = field(default_factory=list)
    duration_s: float = 300.0
    net: NetSpec = field(default_factory=NetSpec)
    kill_manager_at: float | None = None
    partition_at: float | None = None
    heal_at: float | None = None
    partition_groups: list[list[str]] = field(default_factory=list)
    pose_noise_m: float = 0.02
    use_auction: bool = False
    # Tasks the WMS announces over multicast instead of pre-assigning. Only the
    # auction scenario uses these; everywhere else allocation is held constant.
    unassigned: list[Task] = field(default_factory=list)
    seed: int = 0

    @property
    def n_robots(self) -> int:
        return len(self.starts)

    @property
    def n_tasks(self) -> int:
        return sum(len(q) for q in self.assignments)


# ---------------------------------------------------------------- helpers


def _aisle_cells(env: Warehouse) -> list[Cell]:
    """Free cells that touch shelving - i.e. where a pick actually happens."""
    out = []
    for c in env.free_cells():
        x, y = c
        for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if env.in_bounds(n) and env.grid[n[1]][n[0]] == RACK:
                out.append(c)
                break
    return out


def _spread_starts(env: Warehouse, n: int, rng: random.Random) -> list[Cell]:
    """Start cells that are far apart, so run 1 is not decided by the initial jam."""
    candidates = [c for c in env.free_cells() if env.degree(c) >= 2]
    rng.shuffle(candidates)
    chosen: list[Cell] = []
    for c in candidates:
        if all(manhattan(c, o) >= 3 for o in chosen):
            chosen.append(c)
        if len(chosen) == n:
            break
    while len(chosen) < n and candidates:          # cramped map: relax the spacing
        c = candidates.pop()
        if c not in chosen:
            chosen.append(c)
    return chosen[:n]


def _round_robin(tasks: list[Task], n: int) -> list[list[Task]]:
    out: list[list[Task]] = [[] for _ in range(n)]
    for i, t in enumerate(tasks):
        out[i % n].append(t)
    return out


# ---------------------------------------------------------------- scenarios


def crossing_chokepoint(n_robots: int = 4, tasks_per_robot: int = 3,
                        seed: int = 0) -> Scenario:
    """The headline stress test: every task must cross one single-file corridor.

    This is the map the speedup number is quoted against, and it is deliberately the
    hardest honest case rather than a friendly one: with only one route between the
    bays, no policy can dodge the conflict by taking a different aisle. Coordination is
    the only thing that can help, which is what makes the comparison meaningful.
    """
    env = chokepoint_warehouse(length=13)
    rng = random.Random(seed)
    left = [c for c in env.free_cells() if c[0] < 6]
    right = [c for c in env.free_cells() if c[0] > env.width - 7]

    tasks: list[Task] = []
    for i in range(n_robots * tasks_per_robot):
        # Alternate direction so the corridor is contested from both ends at once.
        a, b = (left, right) if i % 2 == 0 else (right, left)
        tasks.append(Task(f"T{i:03d}", rng.choice(a), rng.choice(b), 0.0))

    # Sample start cells WITHOUT replacement and keep them apart. Sampling with
    # replacement puts two robots in one cell, where they overlap for the entire run
    # and the contact counter reports hundreds of "collisions" that are really one
    # broken initial condition. A benchmark that starts in an impossible state cannot
    # measure anything.
    starts: list[Cell] = []
    for i in range(n_robots):
        pool = [c for c in (left if i % 2 == 0 else right)
                if all(manhattan(c, o) >= 2 for o in starts)]
        if not pool:
            pool = [c for c in env.free_cells() if c not in starts]
        starts.append(rng.choice(sorted(pool)))
    return Scenario("crossing_chokepoint", env, starts,
                    _round_robin(tasks, n_robots), duration_s=420.0, seed=seed)


def dense_aisles(n_robots: int = 8, tasks_per_robot: int = 4,
                 seed: int = 0) -> Scenario:
    """A realistic warehouse under load: many robots, narrow aisles, mixed routes."""
    # Capacity is part of the scenario, not something a priority rule can invent.
    # The original fixed 31x21 floor gives 399 free cells and is the pinned <=24 AMR
    # benchmark.  A requested 100-AMR demo now receives roughly four times the floor
    # area instead of silently cramming one quarter of all cells with chassis.
    env = (classic_warehouse() if n_robots <= 24
           else classic_warehouse(width=61, height=41, name="classic_large"))
    rng = random.Random(seed)
    picks = _aisle_cells(env)
    drops = list(env.stations) + list(env.docks)

    tasks = [Task(f"T{i:03d}", rng.choice(picks), rng.choice(drops), 0.0)
             for i in range(n_robots * tasks_per_robot)]
    return Scenario("dense_aisles", env, _spread_starts(env, n_robots, rng),
                    _round_robin(tasks, n_robots), duration_s=600.0, seed=seed)


def human_in_aisle(n_robots: int = 6, tasks_per_robot: int = 3,
                   seed: int = 0) -> Scenario:
    """The case the problem statement forgot: an agent that does not broadcast.

    A worker walks a main cross-aisle for the whole run. They publish no intent, honour
    no priority and cannot be negotiated with, so every protocol built on shared intent
    is structurally blind to them. Only the onboard safety layer sees them at all - and
    this scenario is the one that proves that layer is real rather than decorative.
    """
    base = dense_aisles(n_robots, tasks_per_robot, seed)
    env = base.env
    aisle_y = 9                                    # a free cross-aisle in the classic map
    walk = [(2, aisle_y), (env.width - 3, aisle_y)]
    return Scenario("human_in_aisle", env, base.starts, base.assignments,
                    humans=[walk], duration_s=600.0, seed=seed)


def manager_dies(n_robots: int = 8, tasks_per_robot: int = 4,
                 seed: int = 0) -> Scenario:
    """Kill the fleet manager mid-run. The single-point-of-failure demo.

    `central` parks; `hierarchical` drops to DEGRADED_P2P and keeps working at reduced
    plan quality. The number worth quoting is not "we survived" but *how much
    throughput the fallback costs* - which is the price of decentralisation, stated.
    """
    base = dense_aisles(n_robots, tasks_per_robot, seed)
    return Scenario("manager_dies", base.env, base.starts, base.assignments,
                    duration_s=600.0, kill_manager_at=60.0, seed=seed)


def dead_zone(n_robots: int = 8, tasks_per_robot: int = 4, seed: int = 0,
              mesh_radio: bool = False) -> Scenario:
    """A Wi-Fi hole in the middle of the floor.

    Run it twice. With `mesh_radio=False` (infrastructure-mode 802.11, the default and
    the realistic case) peer traffic is relayed by the access point, so a robot in the
    hole loses its peers exactly as it loses the server - and P2P buys nothing at all.
    With `mesh_radio=True` the link is genuinely different (802.11s / Wi-Fi Direct /
    UWB) and the advantage appears.

    The pair of runs is the finding: the fix for dead zones is a different radio, not a
    different software topology. The problem statement claims otherwise and never names
    a link layer.
    """
    base = dense_aisles(n_robots, tasks_per_robot, seed)
    env = base.env
    net = NetSpec(dead_zones=((env.width / 2, env.height / 2, 5.0),),
                  peer_traffic_via_ap=not mesh_radio)
    name = "dead_zone_mesh" if mesh_radio else "dead_zone_infra"
    return Scenario(name, env, base.starts, base.assignments, duration_s=600.0,
                    net=net, seed=seed)


def open_floor_control(n_robots: int = 8, tasks_per_robot: int = 4,
                       seed: int = 0) -> Scenario:
    """Negative control: no chokepoints, so every policy should tie.

    If the hierarchical policy shows a large win here, the benchmark is measuring an
    implementation artefact rather than coordination, and the headline number on
    `crossing_chokepoint` cannot be trusted. Publishing a scenario designed to show no
    effect is what separates a measurement from a demo.
    """
    env = open_floor(24, 24)
    rng = random.Random(seed)
    free = list(env.free_cells())
    tasks = [Task(f"T{i:03d}", rng.choice(free), rng.choice(free), 0.0)
             for i in range(n_robots * tasks_per_robot)]
    return Scenario("open_floor_control", env, _spread_starts(env, n_robots, rng),
                    _round_robin(tasks, n_robots), duration_s=600.0, seed=seed)


def auction_test(n_robots: int = 8, n_tasks: int = 32, seed: int = 0) -> Scenario:
    """Task allocation under test: nothing pre-assigned, robots bid over multicast.

    Pairs with `manager_dies` to cover requirement 3 of the statement - automatic
    re-assignment when a robot is blocked or the manager is gone.
    """
    env = classic_warehouse()
    rng = random.Random(seed)
    picks = _aisle_cells(env)
    drops = list(env.stations) + list(env.docks)
    tasks = [Task(f"T{i:03d}", rng.choice(picks), rng.choice(drops), 0.0)
             for i in range(n_tasks)]
    sc = Scenario("auction_test", env, _spread_starts(env, n_robots, rng),
                  [[] for _ in range(n_robots)], duration_s=600.0,
                  kill_manager_at=45.0, use_auction=True, seed=seed)
    sc.unassigned = tasks                          # announced by the WMS at t=0
    return sc


SCENARIOS = {
    "crossing_chokepoint": crossing_chokepoint,
    "dense_aisles": dense_aisles,
    "human_in_aisle": human_in_aisle,
    "manager_dies": manager_dies,
    "dead_zone_infra": lambda **kw: dead_zone(mesh_radio=False, **kw),
    "dead_zone_mesh": lambda **kw: dead_zone(mesh_radio=True, **kw),
    "open_floor_control": open_floor_control,
    "auction_test": auction_test,
}
