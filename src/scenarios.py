"""Pinned benchmark scenarios.

WHY THESE ARE FIXED AND SEEDED
==============================
"A minimum 20% reduction in total task completion time compared to traditional
stop-and-wait" is not a measurable claim on its own. The speedup of any coordination
policy swings from roughly 5% to over 300% depending on map topology, robot density and
the task mix - so a number quoted without a pinned scenario means nothing, and can be
manufactured to order by choosing a friendly map.

Every scenario here therefore fixes the map, the start cells, the exact task stream and
the RNG seed. Route-policy comparisons use identical pre-assigned queues; when one of
the task-allocation policies is selected, the same queues are flattened and announced
so the selected allocator becomes the only allocation variable.

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

import hashlib
import json
import random
from dataclasses import asdict, dataclass, field

from .amr import Task
from .environment import (DOCK, FREE, RACK, STATION, Warehouse,
                          chokepoint_warehouse, classic_warehouse, open_floor)
from .geometry import Cell, manhattan
from .planner import astar
from .settings import Config, NetSpec
from .task_allocation import ACTIVE_ALLOCATION_POLICIES, ALLOCATION_PREASSIGNED


@dataclass(frozen=True)
class ObstacleEvent:
    oid: str
    cell: Cell
    appear_at: float
    clear_at: float | None = None
    radius_m: float = 0.40


@dataclass
class Scenario:
    name: str
    env: Warehouse
    starts: list[Cell]
    # rid index -> ordered task queue for route-only comparisons. Allocation policies
    # flatten these queues and announce the tasks instead.
    assignments: list[list[Task]]
    humans: list[list[Cell]] = field(default_factory=list)
    duration_s: float = 300.0
    net: NetSpec = field(default_factory=NetSpec)
    kill_manager_at: float | None = None
    partition_at: float | None = None
    heal_at: float | None = None
    partition_groups: list[list[str]] = field(default_factory=list)
    # A failed robot remains a physical stopped obstacle but its brain and radio are
    # silent. Optional restart creates a fresh brain with no shared process state.
    robot_fail_at: dict[str, float] = field(default_factory=dict)
    robot_restart_at: dict[str, float] = field(default_factory=dict)
    obstacles: list[ObstacleEvent] = field(default_factory=list)
    pose_noise_m: float = 0.02
    use_auction: bool = False
    # Optional unassigned workload. Allocation policies can also flatten the normal
    # round-robin queues, so task allocation is selected by policy rather than map.
    unassigned: list[Task] = field(default_factory=list)
    # Optional per-robot starting state of charge for energy-allocation experiments.
    initial_battery_fracs: list[float] = field(default_factory=list)
    seed: int = 0

    @property
    def n_robots(self) -> int:
        return len(self.starts)

    @property
    def n_tasks(self) -> int:
        return sum(len(q) for q in self.assignments)


def workload_fingerprint(sc: Scenario, cfg: Config,
                         allocation_policy: str | None) -> str:
    """Stable identity for every input that may affect a paired policy run.

    The route policy is deliberately excluded: it is the independent variable.  Map,
    starts, ordered tasks, failures, radio model, seed and every controller constant are
    included, so a comparator cannot silently call two different experiments a pair.
    """
    allocation = allocation_policy or ALLOCATION_PREASSIGNED

    def task_row(task: Task) -> dict:
        return {
            "id": task.tid,
            "pick": list(task.pick),
            "drop": list(task.drop),
            "announced_t": task.announced_t,
            "auction_epoch": task.auction_epoch,
            "bid_deadline": task.bid_deadline,
            "cargo_type": task.cargo_type,
            "cargo_weight": task.cargo_weight,
            "priority": task.priority,
            "deadline": task.deadline,
            "lease_owner": task.lease_owner,
            "lease_until": task.lease_until,
        }

    if allocation in ACTIVE_ALLOCATION_POLICIES:
        announced = (list(sc.unassigned) if sc.unassigned else
                     [task for queue in sc.assignments for task in queue])
        workload: dict = {"announced": [task_row(task) for task in announced]}
    else:
        workload = {
            "queues": [
                [task_row(task) for task in queue]
                for queue in sc.assignments
            ]
        }

    payload = {
        "schema": 2,
        "scenario": sc.name,
        "environment": sc.env.to_json(),
        "starts": [list(cell) for cell in sc.starts],
        "workload": workload,
        "allocation_policy": allocation,
        "humans": [[list(cell) for cell in route] for route in sc.humans],
        "duration_s": sc.duration_s,
        "kill_manager_at": sc.kill_manager_at,
        "partition_at": sc.partition_at,
        "heal_at": sc.heal_at,
        "partition_groups": sc.partition_groups,
        "robot_fail_at": sc.robot_fail_at,
        "robot_restart_at": sc.robot_restart_at,
        "initial_battery_fracs": sc.initial_battery_fracs,
        "obstacles": [asdict(event) for event in sc.obstacles],
        "pose_noise_m": sc.pose_noise_m,
        "seed": sc.seed,
        "config": asdict(cfg),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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


def _closed_human_walk(env: Warehouse, start: Cell, goal: Cell) -> list[Cell]:
    """A contiguous out-and-back pedestrian route that never crosses shelving."""
    path = astar(env, start, goal)
    if len(path) < 2:
        raise ValueError(f"invalid human walkway from {start!r} to {goal!r}")
    return path + path[-2:0:-1]


def _pedestrian_walks(env: Warehouse, robot_starts: list[Cell], count: int,
                      mixed: bool = False) -> list[list[Cell]]:
    """Build rack-safe walks and place their starts away from parked AMRs.

    The focused human showcase uses the principal cross aisles. The combined stress
    scene uses two interior routes plus perimeter walkways, so adding more people does
    not unrealistically turn every AMR lane into a permanently occupied footpath.
    """
    cross_rows = [
        y for y in range(2, env.height - 2)
        if all(env.passable((x, y)) for x in range(1, env.width - 1))
    ]
    through_cols = [
        x for x in range(2, env.width - 2)
        if all(env.passable((x, y)) for y in range(1, env.height - 1))
    ]
    central: list[tuple[Cell, Cell]] = [
        ((1, y), (env.width - 2, y)) for y in cross_rows
    ]
    if through_cols:
        indexes = sorted({len(through_cols) // 3, (2 * len(through_cols)) // 3})
        central.extend(
            ((through_cols[index], 1), (through_cols[index], env.height - 2))
            for index in indexes
        )
    if mixed:
        centre_row = central[len(cross_rows) // 2:len(cross_rows) // 2 + 1]
        centre_col = central[len(cross_rows):len(cross_rows) + 1]
        perimeter = [
            ((1, 1), (env.width - 2, 1)),
            ((1, env.height - 2), (env.width - 2, env.height - 2)),
            ((env.width - 2, 1), (env.width - 2, env.height - 2)),
        ]
        candidates = centre_row + centre_col + perimeter
    else:
        candidates = central
    if len(candidates) < count:
        raise ValueError(f"warehouse exposes only {len(candidates)} safe pedestrian routes")

    return _place_pedestrian_walks(env, robot_starts, candidates[:count])


def _place_pedestrian_walks(env: Warehouse, robot_starts: list[Cell],
                            specs: list[tuple[Cell, Cell]]) -> list[list[Cell]]:
    """Turn walkway endpoints into loops with non-overlapping initial positions."""
    walks: list[list[Cell]] = []
    occupied_starts = list(robot_starts)
    for start, goal in specs:
        route = _closed_human_walk(env, start, goal)
        offset = max(
            range(len(route)),
            key=lambda index: (
                min((manhattan(route[index], cell) for cell in occupied_starts),
                    default=env.width + env.height),
                -index,
            ),
        )
        route = route[offset:] + route[:offset]
        walks.append(route)
        occupied_starts.append(route[0])
    return walks


def _showcase_pedestrian_walks(env: Warehouse, robot_starts: list[Cell],
                               grand: bool = False) -> list[list[Cell]]:
    """Short, visible patrols that create crossings without occupying whole aisles."""
    cross_rows = [
        y for y in range(2, env.height - 2)
        if all(env.passable((x, y)) for x in range(1, env.width - 1))
    ]
    if len(cross_rows) < 3:
        raise ValueError("showcase warehouse requires three pedestrian cross aisles")
    if grand:
        specs = [
            ((2, 1), (7, 1)),
            ((11, 1), (16, 1)),
            ((env.width - 11, 1), (env.width - 6, 1)),
            ((5, env.height - 2), (10, env.height - 2)),
            ((env.width - 14, env.height - 2),
             (env.width - 9, env.height - 2)),
        ]
    else:
        specs = [
            # Marked pedestrian patrols run beside the perimeter AMR centreline.
            # Robots still encounter workers near entry/exit aprons, while people do
            # not unrealistically pace back and forth across every logistics aisle.
            ((2, 1), (7, 1)),
            ((14, 1), (19, 1)),
            ((env.width - 11, env.height - 2),
             (env.width - 6, env.height - 2)),
        ]
    return _place_pedestrian_walks(env, robot_starts, specs)


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
    return Scenario("human_in_aisle", env, base.starts, base.assignments,
                    humans=_pedestrian_walks(env, base.starts, 1),
                    duration_s=600.0, seed=seed)


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
    """True negative control: each robot has a physically isolated private lane.

    The previous random open floor still created shared destinations, crossings, idle
    blockers, and auction-allocation differences; it was a contention workload while
    claiming to measure no contention. Here lanes are disconnected by rack rows and
    tasks never leave their lane. Route policies may pay small protocol overhead, but
    no coordination policy can legitimately gain a large traffic advantage.
    """
    width = 18
    height = 2 * n_robots + 1
    grid = [[RACK] * width for _ in range(height)]
    stations = []
    docks = []
    starts = []
    assignments: list[list[Task]] = []
    for robot_index in range(n_robots):
        y = 2 * robot_index + 1
        grid[y] = [FREE] * width
        grid[y][1] = STATION
        grid[y][width - 2] = DOCK
        stations.append((1, y))
        docks.append((width - 2, y))
        starts.append((2, y))
        queue = []
        for task_index in range(tasks_per_robot):
            if task_index % 2 == 0:
                pick, drop = (3, y), (width - 3, y)
            else:
                pick, drop = (width - 3, y), (3, y)
            queue.append(Task(
                f"T{robot_index:02d}_{task_index:02d}", pick, drop, 0.0))
        assignments.append(queue)
    env = Warehouse(
        width, height, tuple(tuple(row) for row in grid),
        tuple(stations), tuple(docks), "isolated_lanes_control")
    return Scenario("open_floor_control", env, starts, assignments,
                    duration_s=600.0, pose_noise_m=0.0, seed=seed)


def blocked_aisle(n_robots: int = 3, tasks_per_robot: int = 1,
                  seed: int = 0) -> Scenario:
    """A dropped pallet appears on one planned route; an alternate route remains."""
    n_robots = max(3, n_robots)
    env = open_floor(14, max(9, 2 * n_robots + 3), name="blocked_aisle")
    starts = [(1, 2 * index + 2) for index in range(n_robots)]
    assignments = []
    for index, start in enumerate(starts):
        tasks = [Task(
            f"T{index:02d}_{task_index:02d}", start,
            (env.width - 2, start[1]), 0.0)
            for task_index in range(tasks_per_robot)]
        assignments.append(tasks)
    middle = n_robots // 2
    obstacle = ObstacleEvent(
        "dropped-pallet", (5, starts[middle][1]), appear_at=1.0)
    return Scenario(
        "blocked_aisle", env, starts, assignments,
        duration_s=180.0, pose_noise_m=0.0,
        obstacles=[obstacle], seed=seed)


def robot_failure_reassignment(n_robots: int = 3, tasks_per_robot: int = 1,
                               seed: int = 0) -> Scenario:
    """Crash an auction winner; its lease expires and a surviving peer finishes."""
    n_robots = max(3, n_robots)
    env = open_floor(16, 10, name="robot_failure_reassignment")
    starts = [(1, 2), (1, 5), (1, 8)]
    while len(starts) < n_robots:
        starts.append((2 + len(starts), 1))
    tasks = [Task(
        f"T{i:03d}", (3 + i, 2 + (i % 3) * 3),
        (env.width - 2, 2 + (i % 3) * 3), 0.0)
        for i in range(max(1, tasks_per_robot))]
    return Scenario(
        "robot_failure_reassignment", env, starts[:n_robots],
        [[] for _ in range(n_robots)],
        duration_s=180.0, pose_noise_m=0.0, use_auction=True,
        unassigned=tasks,
        # AMR01 is deliberately closest to T000 and wins the first auction.
        robot_fail_at={"AMR01": 2.0},
        seed=seed,
    )


def partition_recovery(n_robots: int = 4, tasks_per_robot: int = 1,
                       seed: int = 0) -> Scenario:
    """Split the peer network into two islands, then heal and require convergence."""
    n_robots = max(4, n_robots)
    env = open_floor(18, max(12, n_robots + 6), name="partition_recovery")
    starts = [(1, 1 + 2 * (index % ((env.height - 2) // 2)))
              for index in range(n_robots)]
    tasks = [Task(
        f"T{i:03d}", (3, starts[i % n_robots][1]),
        (env.width - 2, starts[i % n_robots][1]), 0.0)
        for i in range(max(n_robots, n_robots * tasks_per_robot))]
    ids = [f"AMR{i + 1:02d}" for i in range(n_robots)]
    split = n_robots // 2
    return Scenario(
        "partition_recovery", env, starts,
        [[] for _ in range(n_robots)],
        duration_s=240.0, pose_noise_m=0.0, use_auction=True,
        unassigned=tasks,
        partition_at=2.0, heal_at=12.0,
        partition_groups=[ids[:split], ids[split:]],
        seed=seed,
    )


def sih_acceptance_overlap(n_robots: int = 4, tasks_per_robot: int = 3,
                           seed: int = 0) -> Scenario:
    """Pinned SIH success-criterion workload with overlapping chokepoint paths.

    Every job crosses the same single-file block and directions alternate, which is
    exactly the case named by the problem statement.  Both policies receive the same
    decentralized-auction catalog.  Pure stop-and-wait can right-censor by settling
    into a permanent head-on wait; the benchmark reports a conservative completion-
    time reduction lower bound instead of pretending the timeout is a makespan.
    """
    base = crossing_chokepoint(n_robots=n_robots,
                               tasks_per_robot=tasks_per_robot, seed=seed)
    announced = [
        Task(**task.__dict__)
        for queue in base.assignments
        for task in queue
    ]
    return Scenario(
        "sih_acceptance_overlap",
        base.env,
        base.starts,
        base.assignments,
        duration_s=1200.0,
        net=base.net,
        pose_noise_m=base.pose_noise_m,
        use_auction=True,
        unassigned=announced,
        seed=seed,
    )


def energy_acceptance(n_robots: int = 8, tasks_per_robot: int = 2,
                      seed: int = 0) -> Scenario:
    """Pinned completion workload with heterogeneous starting battery state."""
    sc = sih_acceptance_overlap(
        n_robots=n_robots, tasks_per_robot=tasks_per_robot, seed=seed)
    sc.name = "energy_acceptance"
    levels = (0.12, 0.18, 0.28, 0.42, 0.58, 0.72, 0.86, 0.96)
    sc.initial_battery_fracs = [levels[i % len(levels)] for i in range(n_robots)]
    return sc


# ---------------------------------------------------------------- jury showcase profiles

_SHOWCASE_BATTERIES = (0.48, 0.62, 0.74, 0.86, 0.95, 0.56, 0.79, 0.91)
_SHOWCASE_CARGO = (
    ("normal", 8.0, 1),
    ("fragile", 18.0, 2),
    ("heavy", 72.0, 2),
    ("hazardous", 36.0, 3),
)


def _showcase_profile(sc: Scenario, name: str) -> Scenario:
    """Apply the same energy-aware task mix to every public jury scenario.

    Pinned scientific scenarios remain unchanged. These wrappers are for the public
    digital-twin experience, where every run should demonstrate the BIOS 5 energy gate.
    """
    sc.name = name
    sc.use_auction = True
    sc.initial_battery_fracs = [
        _SHOWCASE_BATTERIES[i % len(_SHOWCASE_BATTERIES)]
        for i in range(sc.n_robots)
    ]
    tasks = sc.unassigned or [task for queue in sc.assignments for task in queue]
    deadline_base = {
        "showcase_open_floor": 240.0,
        "showcase_chokepoint": 420.0,
        "showcase_human": 720.0,
        "showcase_dead_zone": 720.0,
        "showcase_grand_challenge": 720.0,
    }.get(name, 720.0)
    profiled: list[Task] = []
    for index, task in enumerate(tasks):
        cargo_type, cargo_weight, priority = _SHOWCASE_CARGO[index % len(_SHOWCASE_CARGO)]
        profiled.append(Task(
            tid=task.tid,
            pick=task.pick,
            drop=task.drop,
            announced_t=task.announced_t,
            auction_epoch=task.auction_epoch,
            bid_deadline=task.bid_deadline,
            cargo_type=cargo_type,
            cargo_weight=cargo_weight,
            priority=priority,
            deadline=(deadline_base + 15.0 * index) if index % 3 == 1 else None,
        ))
    sc.unassigned = profiled
    sc.assignments = [[] for _ in range(sc.n_robots)]
    return sc


def showcase_open_floor(n_robots: int = 4, tasks_per_robot: int = 2,
                        seed: int = 4) -> Scenario:
    env = open_floor(22, 15, name="showcase_open_floor")
    rng = random.Random(seed)
    starts = _spread_starts(env, n_robots, rng)
    left = [(2, y) for y in range(2, env.height - 2, 3)]
    right = [(env.width - 3, y) for y in range(2, env.height - 2, 3)]
    tasks = []
    for index in range(n_robots * tasks_per_robot):
        source, destination = (left, right) if index % 2 == 0 else (right, left)
        tasks.append(Task(f"TOO_{index:02d}", rng.choice(source),
                          rng.choice(destination), 0.0))
    return _showcase_profile(
        Scenario("showcase_open_floor", env, starts,
                 _round_robin(tasks, n_robots), duration_s=240.0, seed=seed),
        "showcase_open_floor")


def showcase_chokepoint(n_robots: int = 4, tasks_per_robot: int = 2,
                        seed: int = 7) -> Scenario:
    return _showcase_profile(
        crossing_chokepoint(n_robots, tasks_per_robot, seed), "showcase_chokepoint")


def showcase_human(n_robots: int = 5, tasks_per_robot: int = 2,
                   seed: int = 7) -> Scenario:
    scenario = human_in_aisle(n_robots, tasks_per_robot, seed)
    scenario.humans = _showcase_pedestrian_walks(
        scenario.env, scenario.starts)
    return _showcase_profile(scenario, "showcase_human")


def showcase_dead_zone(n_robots: int = 6, tasks_per_robot: int = 1,
                       seed: int = 4) -> Scenario:
    return _showcase_profile(
        dead_zone(n_robots, tasks_per_robot, seed, mesh_radio=True),
        "showcase_dead_zone")


def showcase_grand_challenge(n_robots: int = 8, tasks_per_robot: int = 2,
                             seed: int = 1) -> Scenario:
    """A deterministic jury story: traffic, humans, radio degradation and blockage."""
    base = dense_aisles(n_robots, tasks_per_robot, seed)
    env = base.env
    cross_aisle = min(11, env.height - 3)
    humans = _showcase_pedestrian_walks(env, base.starts, grand=True)
    net = NetSpec(
        loss=0.05,
        dead_zones=((env.width * 0.62, env.height * 0.48, 4.0),),
        peer_traffic_via_ap=False,
    )
    obstacle_cell = min(
        # Block a redundant junction, not a degree-two articulation of the nominal
        # circulation lane. The combined scenario is meant to test dynamic rerouting;
        # physically partitioning the one-way graph measures a different problem and
        # can leave every policy gridlocked after the pallet has already cleared.
        (cell for cell in env.free_cells() if env.degree(cell) >= 3),
        key=lambda cell: manhattan(cell, (env.width // 2, cross_aisle)),
    )
    combined = Scenario(
        "showcase_grand_challenge", env, base.starts, base.assignments,
        humans=humans, duration_s=480.0, net=net,
        obstacles=[ObstacleEvent(
            "fallen-pallet", obstacle_cell,
            appear_at=24.0, clear_at=78.0)],
        robot_fail_at={"AMR03": 52.0},
        robot_restart_at={"AMR03": 96.0},
        seed=seed,
    )
    return _showcase_profile(combined, "showcase_grand_challenge")


SHOWCASE_SCENARIOS = {
    "showcase_open_floor": {
        "builder": showcase_open_floor, "title": "Open Floor",
        "eyebrow": "Energy-aware allocation",
        "description": "Watch identical AMRs reject unsafe jobs and self-select the best battery-feasible task.",
        "robots": 4, "humans": 0, "seed": 4, "duration": 180, "accent": "cyan",
    },
    "showcase_chokepoint": {
        "builder": showcase_chokepoint, "title": "Chokepoint",
        "eyebrow": "Priority negotiation",
        "description": "Opposing robots coordinate a single-file aisle with priority, yielding and expiring leases.",
        "robots": 4, "humans": 0, "seed": 7, "duration": 320, "accent": "amber",
    },
    "showcase_human": {
        "builder": showcase_human, "title": "Human Interaction",
        "eyebrow": "Local perception",
        "description": "Three non-broadcasting workers patrol mapped lanes while robots stop and replan locally.",
        "robots": 5, "humans": 3, "seed": 7, "duration": 520, "accent": "violet",
    },
    "showcase_dead_zone": {
        "builder": showcase_dead_zone, "title": "Dead-Zone Mesh",
        "eyebrow": "Network resilience",
        "description": "Visualise degraded links, stale-lease expiry and recovery on a genuine peer radio path.",
        "robots": 6, "humans": 0, "seed": 4, "duration": 650, "accent": "rose",
    },
    "showcase_grand_challenge": {
        "builder": showcase_grand_challenge, "title": "Grand Challenge",
        "eyebrow": "The full BIOS story",
        "description": "Open traffic, humans, chokepoints, a blocked aisle, mixed cargo, a dead zone and robot recovery.",
        "robots": 8, "humans": 5, "seed": 1, "duration": 800, "accent": "lime",
    },
}


SCENARIOS = {
    "crossing_chokepoint": crossing_chokepoint,
    "dense_aisles": dense_aisles,
    "human_in_aisle": human_in_aisle,
    "manager_dies": manager_dies,
    "dead_zone_infra": lambda **kw: dead_zone(mesh_radio=False, **kw),
    "dead_zone_mesh": lambda **kw: dead_zone(mesh_radio=True, **kw),
    "open_floor_control": open_floor_control,
    "blocked_aisle": blocked_aisle,
    "robot_failure_reassignment": robot_failure_reassignment,
    "partition_recovery": partition_recovery,
    "sih_acceptance_overlap": sih_acceptance_overlap,
    "energy_acceptance": energy_acceptance,
    **{name: profile["builder"] for name, profile in SHOWCASE_SCENARIOS.items()},
}
