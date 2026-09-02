# 04. PATH PLANNING

> This document establishes what the search layer actually computes: the map it searches, the three planning algorithms implemented (A\*, space-time A\*, PIBT), what each costs on a CPU, and every condition that makes a robot throw a route away and compute a new one.

**Audience:** SIH judges and BEL evaluators assessing requirement 7 (multi-agent path planning), requirements 12-13 (blocked aisles and re-routing) and requirement 15 (edge execution); and teammates who must answer a follow-up question about any line of it.
**Reads best after:** [02. Architecture](02-ARCHITECTURE.md)

Scope boundary: this document covers the **algorithms** and their cost. Which coordination policy calls which algorithm, and how the policies compare against each other, is [05. Coordination Policies](05-COORDINATION-POLICIES.md). Where the guarantee of non-collision actually lives is [07. Safety](07-SAFETY.md).

## Requirements evidenced

| # | Requirement | Where | Evidence |
|---|---|---|---|
| 2 | Dynamic warehouse environment | [§6.2](#62-blocked-aisle-detection-requirement-12) | `src/amr.py:2774` promotes stationary anonymous lidar returns into an expiring map layer |
| 7 | Multi-agent path planning | [§4](#4-space-time-a), [§5](#5-pibt--priority-inheritance-with-backtracking) | `src/planner.py:127` (space-time A\*), `src/priority.py:84` (PIBT) |
| 8 | Collision avoidance (planning contribution) | [§4.2](#42-the-reservation-table), [§5.3](#53-what-pibt-guarantees-and-what-it-does-not) | `src/planner.py:50` edge reservations; `src/priority.py:134` edge-swap ban |
| 9 | Real-time conflict resolution | [§5](#5-pibt--priority-inheritance-with-backtracking) | `src/amr.py:1880` runs PIBT inside the 10 Hz reactive loop |
| 10 | Deadlock resolution | [§2](#2-single-file-block-decomposition), [§6.1](#61-the-complete-trigger-list) | `src/environment.py:165` block decomposition; `src/amr.py:2143` cycle-break replan |
| 11 | Narrow intersection / chokepoint handling | [§1.3](#13-chokepoints-and-degree), [§2](#2-single-file-block-decomposition) | `src/environment.py:70`, `src/amr.py:1690` |
| 12 | Blocked aisle handling | [§6.2](#62-blocked-aisle-detection-requirement-12) | `src/amr.py:2863` writes an expiring block into the local map |
| 13 | Re-routing | [§6](#6-re-planning-triggers-requirements-12-and-13) | `src/amr.py:2693` `_replan`, 22 call sites |
| 15 | Edge / local execution | [§7](#7-the-edge-hardware-cost-argument-requirement-15) | Measured 0.182 ms per A\* call; `src/geometry.py:1` stdlib-only |

---

## 1. The map model

### 1.1 Grid and tile encoding

The warehouse is a 4-connected grid of square cells held in a frozen dataclass, `Warehouse` (`src/environment.py:36`). `grid` is indexed `grid[y][x]` (`src/environment.py:40`) — row-major, Y first. Getting this backwards is the single easiest way to build a mirrored warehouse, so every consumer goes through the accessor methods rather than indexing directly.

Tiles are plain integers. **These four values are load-bearing across the planner, the physics, the dashboard and the benchmark; renumbering them silently builds a different warehouse:**

| Constant | Value | Meaning | Passable | Citation |
|---|---:|---|---|---|
| `FREE` | `0` | Empty floor | yes | `src/environment.py:17` |
| `RACK` | `1` | Static shelving | **no** | `src/environment.py:18` |
| `STATION` | `2` | Pick / drop station; tasks target these | yes | `src/environment.py:19` |
| `DOCK` | `3` | Charge pad | yes | `src/environment.py:20` |

Passability is defined by exactly one test — `grid[y][x] != RACK` (`src/environment.py:51`) — so `STATION` and `DOCK` are ordinary floor to the planner and special only to the task and battery layers. There is no separate obstacle list; the tile value *is* the obstacle model.

Neighbour generation is 4-connected and memoised (`src/environment.py:23`, `src/environment.py:56`). Diagonals are not offered, and the reason is stated in the code: a differential-drive chassis cutting a diagonal past a rack corner needs a clearance check the grid cannot express (`src/environment.py:57`). The memo is an `lru_cache(maxsize=8192)` keyed on the immutable grid tuple, which is why `Warehouse` is `frozen=True` — the cache would be unsound on a mutable map.

### 1.2 Continuous pose to discrete cell

The cell pitch is **1.4 m centre-to-centre** (`src/settings.py:305`). That number is a physical admissibility condition, not a tuning knob: with a 0.35 m footprint radius (`src/settings.py:18`), a 0.30 m standstill guard and 0.02 m pose noise, a 1.00 m pitch leaves *zero* clearance between two correctly centred neighbours, so the one-robot-per-cell invariant would be unexecutable however good the plan is. The reasoning is written out at `src/settings.py:299-304` and repeated in [`archive/BIOS_PIBT_2_PROTOCOL.md`](archive/BIOS_PIBT_2_PROTOCOL.md) §6.

Two functions relate metres to cells, and only these two:

- `cell_center(c, cell_m) -> ((x+0.5)*cell_m, (y+0.5)*cell_m)` (`src/geometry.py:33`) — the metric waypoint the follower drives to.
- `to_cell(p, cell_m) -> (floor(x/cell_m), floor(y/cell_m))` (`src/geometry.py:38`) — the discrete cell a pose currently occupies.

`to_cell` uses `floor`, so the reported cell changes the instant the chassis crosses a boundary, *before* it reaches the new centre. This asymmetry is a real source of bugs and the code handles it explicitly in two places: PIBT ignores a "request" for the cell the robot is already measured to be in (`src/amr.py:1845`), and the directed policies repair a route whose next waypoint is no longer adjacent to the measured cell rather than executing the implied diagonal (`src/amr.py:1188-1200`).

### 1.3 Chokepoints and degree

`degree(c)` is the count of passable 4-neighbours (`src/environment.py:61`). `chokepoints()` returns every passable cell with `degree <= 2` (`src/environment.py:70`) — a cell where a stopped robot blocks the aisle outright. On the standard 31x21 racking map that is **170 of 399 free cells** (measured by running `Warehouse.chokepoints()` on `classic_warehouse()`). Requirement 11 is therefore not a corner case on this map; it is most of the map.

### 1.4 The three pinned maps

| Map | Builder | Size | Free cells | Single-file blocks | Purpose |
|---|---|---|---:|---|---|
| `classic` | `src/environment.py:112` | 31x21 (default) | 399 | 59 (35 of length 2, 24 of length 4) | Rack blocks separated by **single-cell** aisles. Wider aisles would make every policy a winner and the 20% claim vacuous (`src/environment.py:118-120`). |
| `chokepoint` | `src/environment.py:135` | 27x9 | 46 | 1, of length 15 | Two open bays joined by one single-file corridor. Every crossing pair must negotiate the same cells. |
| `open` | `src/environment.py:156` | 20x20 | 400 | none of length ≥ 6 | Negative control. With no chokepoints every policy ties, and any speedup claim would be coming from the map, not the algorithm. |

The block counts above are measured, not quoted: running `corridors(classic_warehouse())` yields 59 components with a length histogram of `{2: 35, 4: 24}`. The `open` map's absence of long blocks is asserted as a regression test (`tests/test_core.py:571`), and the chokepoint map's single 15-cell corridor with exactly two mouths at `tests/test_core.py:562`.

---

## 2. Single-file block decomposition

### 2.1 What a block is and how it is computed

A chokepoint is not a cell; it is a **run** of cells with no room to pass. The distinction is the whole point, and `src/environment.py:168-172` states why: two robots that meet halfway down a one-lane aisle have both already committed, and no amount of cell-by-cell yielding creates space the map does not have. One of them has to reverse out, which is a failure, not a plan.

`corridors(env)` (`src/environment.py:165`) computes the decomposition in one linear pass:

1. Collect `corridor_cells = {c : degree(c) <= 2}` (`src/environment.py:183`).
2. Iterate starts in **sorted** order and flood-fill connected components with an explicit stack (`src/environment.py:190-201`). Sorting is what makes block IDs identical on every robot and across runs — the IDs are broadcast in lease messages, so a non-deterministic numbering would be a protocol bug, not a cosmetic one.
3. Drop components of length 1 (`src/environment.py:202`): a single cell is just a cell, and locking it would serialise ordinary corners for nothing.
4. Record the **ends** — block cells with at least one neighbour outside the block (`src/environment.py:207`). An end is the mouth a robot enters and leaves by, and by construction a mouth abuts a junction, i.e. somewhere with room to pass. This is why a robot that must wait waits *at the mouth* and does not itself become the next obstruction.

The result is a `CorridorMap` (`src/environment.py:217`) with `id_of(cell)` and `nearest_end(cid, cell)`; the latter is how a robot infers a peer's direction of travel from its published intent without any extra protocol field (`src/amr.py:1786`). The whole decomposition is `lru_cache`d per immutable `Warehouse` (`src/environment.py:164`), so it is computed once per map, not once per plan.

A second, independent decomposition — the graph 2-core, separating cycle-rich core from tree-shaped loading spurs — lives in `src/topology.py:44`. It exists because PIBT's finite-reachability results assume biconnectivity, which a dead-end spur violates; a robot leaving a spur gets a priority bump (`exiting_branch`, `src/priority.py:34`) because admitting traffic *into* a dead end first is the classic way to turn a local conflict into a permanent one (`src/topology.py:6-8`).

### 2.2 Why only runs of ≥ 6 cells get block control

Block control is a mutex. Acquiring it costs a commit round — the robot holds at the mouth for `gate_commit_s = 0.45 s` while its own claim propagates, then re-checks (`src/amr.py:1568-1584`, `src/settings.py:159`). That is backpressure, and backpressure is only worth paying where per-cell yielding genuinely cannot recover.

`_controlled_block(cell)` (`src/amr.py:1690`) returns a block ID only if the block is at least `min_controlled_block = 6` cells long (`src/settings.py:152`, applied at `src/amr.py:1713-1716`).

**The measured negative result.** The threshold is not a guess. From [`archive/FINDINGS.md`](archive/FINDINGS.md) line 114:

> **Block control everywhere is worse than none.** Applying full block exclusion to all 59 short gaps in a racking layout turned the warehouse into a series of toll gates and made throughput *worse than doing nothing*. Block control is now scoped to runs of ≥ 6 cells, where per-cell yielding genuinely cannot recover.

The same conclusion is restated in the function's own docstring (`src/amr.py:1693-1698`). This is worth pausing on as evidence of method rather than of cleverness: the intuitive engineering move — "chokepoints cause deadlock, so lock every chokepoint" — was implemented, measured, and found to be *net negative*. 59 blocks, each costing a commit round to enter, on a map where the median trip crosses several of them. The short gaps have passing room at both ends, so ordinary per-cell yielding resolves them at a fraction of the cost.

The consequence on the standard map is stark and should be stated plainly rather than hidden: **on `classic`, the longest block is 4 cells, so a `min_controlled_block` of 6 disables block control entirely there.** Block control is live only on maps that actually contain a long single-file run — principally `chokepoint`, whose single corridor is 15 cells. That is the intended behaviour (the mechanism is scoped to where it pays) but it means a demo on the racking map is *not* demonstrating block leases; it is demonstrating per-cell arbitration and, for the directed policies, one-way circulation.

A second, stricter threshold governs the **exit apron**: keeping the doorstep of a block clear for a robot driving out is enforced only for blocks of at least `apron_block_len = 8` cells (`src/settings.py:153`, applied at `src/amr.py:1735-1738`). On a four-cell gap it just adds another way to be stuck (`src/amr.py:1722-1724`).

### 2.3 A contradiction inside `_controlled_block`, stated rather than smoothed over

The same function carries two comment blocks that argue opposite things, and a reader should know which applies when.

- The **docstring** (`src/amr.py:1691-1698`) defends the ≥ 6 threshold with the measured negative result above.
- The **inline comment** (`src/amr.py:1707-1712`) says: "V1 protected only long runs. The standard warehouse has 24 four-cell picking aisles and 35 two-cell rack gaps, so that threshold protected precisely zero of its 59 non-passing segments... **V2 treats every maximal degree-two run as a traffic zone.** The extra lease round is intentional backpressure, not planner latency."

Both are in force, for different policies, and the code says so at `src/amr.py:1713-1714`: the minimum is `2` for the directed policies (`BIOS_PIBT.2/.3/.5/.6`) and `min_controlled_block` (6) otherwise.

But the inline comment is misleading about the case it names. For a directed policy on the standard map, `_controlled_block` returns `None` **before** it ever reaches that threshold line, because of the early return at `src/amr.py:1703-1706` ("Direction already makes opposing occupancy impossible"). So on `classic`, V2 does *not* treat every degree-two run as a traffic zone — it turns block control off completely and relies on one-way circulation (`src/topology.py:99`) plus per-cell leases (`src/amr.py:1664`). The `minimum = 2` branch is reachable only for a directed policy on a map where `directed_circulation` gave up, which is precisely the single-bidirectional-chokepoint case (`src/topology.py:166-173` returns a disabled `CirculationMap` when the map lacks two parallel lanes in each axis). The comment describes an intent the code does not implement on the map it names. The **behaviour** matches [`archive/BIOS_PIBT_2_PROTOCOL.md`](archive/BIOS_PIBT_2_PROTOCOL.md) §2 (circulation is V2's answer on rack maps); only the comment is wrong.

Which policy uses which of these mechanisms is [05. Coordination Policies](05-COORDINATION-POLICIES.md).

---

## 3. A\*

`astar(env, start, goal, extra_cost, edge_cost, blocked, edge_allowed)` (`src/planner.py:73`). This is the route-level workhorse: every decentralised policy plans with it, and it is called from 14 sites in `src/amr.py` plus `src/world.py:275` (human patrol routes).

### 3.1 Heuristic and cost model

- **Heuristic:** Manhattan distance (`src/geometry.py:29`, applied at `src/planner.py:94` and `src/planner.py:122`). On a 4-connected unit grid this is the exact free-space distance, so it is admissible and consistent.
- **Base step cost:** `1.0` per move (`src/planner.py:117`).
- **Soft cell cost:** `extra_cost[nxt]`, added to the step (`src/planner.py:117`). This is how the traffic layer says "this cell is contested, route around it if that is nearly free" without ever declaring it impassable — and the docstring says why that matters: declaring a contested cell impassable is how a traffic jam becomes an unsolvable map (`src/planner.py:80-82`).
- **Soft edge cost:** `edge_cost[(cur, nxt)]` (`src/planner.py:118`), used by the BIOS 6 shared-experience layer to penalise directed edges peers have measured as slow (`src/amr.py:735`).
- **Hard exclusion:** `blocked` (a set of cells) and `edge_allowed` (a predicate). `edge_allowed` is how the one-way circulation graph is enforced *inside the search* rather than rejected afterwards (`src/amr.py:2711`).

Because every added cost is non-negative and the base cost is 1, Manhattan distance remains admissible **and consistent** under the soft-cost layers: `h(n) - h(n') <= 1 <= c(n, n')` across any edge. That is what makes the closed-set skip at `src/planner.py:109` and `src/planner.py:115` sound — a node is never re-expanded, and no re-opening logic is needed.

### 3.2 Tie-breaking and determinism

The open-heap entry is a 4-tuple `(f, h, tie, cell)` (`src/planner.py:93`, `src/planner.py:123`):

1. `f = g + h` — standard.
2. `h` — among equal-`f` nodes, prefer the one closer to the goal (equivalently, larger `g`). This is the classic A\* tie-break that drives the search along the goal-ward frontier instead of fanning out across a plateau of equal-cost cells, which on an open warehouse floor is most of the map.
3. `tie` — a monotonic counter from `itertools.count()` (`src/planner.py:92`). This exists so the heap **never compares cells or objects** to break a tie. Without it, two runs with the same seed can produce different paths, and the module docstring makes determinism an explicit contract: "two runs with the same seed produce byte-identical paths" (`src/planner.py:10-11`).

Determinism here is not tidiness. Every robot in the fleet independently computes paths from a replicated view; if A\* were nondeterministic, two robots reasoning about the same peer would disagree about where it is going.

### 3.3 Early exits and failure

`start == goal` returns `[start]` (`src/planner.py:84`). An unreachable or blocked goal returns `[]` (`src/planner.py:87`), as does an exhausted open list (`src/planner.py:124`). Callers treat `[]` as "no route now" and retry, rather than as an error — see [§6](#6-re-planning-triggers-requirements-12-and-13).

### 3.4 Complexity and measured cost

With `V` free cells and `E = 4V` edges, binary-heap A\* is `O(E log V)` time and `O(V)` space. The practical figure that matters is the constant.

**Measured on this repository** (Intel Core, Windows, CPython 3.13.14; 400 random start/goal pairs on `classic_warehouse()`, 399 free cells, mean path 20.7 cells):

| Metric | Value |
|---|---:|
| Mean time per `astar` call | **0.182 ms** |
| p95 | 0.387 ms |
| Max | 0.641 ms |
| Peak allocation, corner-to-corner search | 24.4 KiB |

Verified as a functional property by `tests/test_core.py:523`: every returned path avoids `RACK` tiles and every consecutive pair is a 4-connected unit step. The soft-cost layer is verified at `tests/test_core.py:71` (an expensive directed edge is genuinely avoided, and the resulting path is longer).

---

## 4. Space-time A\*

`space_time_astar(env, start, goal, res, owner, t0, max_steps, wait_cost)` (`src/planner.py:127`). This is the algorithm that makes requirement 7 a multi-agent claim rather than a single-agent one, because it is the only search here that can express *when* a cell is occupied.

### 4.1 The time dimension

The search state is `(cell, timestep)` (`src/planner.py:141`), not `cell`. Three consequences follow:

- **Waiting is a move.** The successor list is `env.neighbors(cell) + [cell]` (`src/planner.py:167`) — staying put is an ordinary edge, charged `wait_cost` (default `1.0`, `src/planner.py:173`). A plain A\* cannot express "stand here for three ticks and then proceed"; that is the entire class of solution this planner adds.
- **The goal test is time-aware.** The search terminates only at `cell == goal and t >= settle_t`, where `settle_t = res.horizon + 1` (`src/planner.py:149`, `src/planner.py:156`). Reaching the goal *early* is not acceptance: past the last reservation nothing can conflict, so the plan is only complete once the robot is parked beyond every other agent's reserved horizon.
- **A step budget bounds the search.** `t - t0 > max_steps` (default 512) prunes the branch (`src/planner.py:163`). The docstring is explicit that this makes the algorithm complete only up to `max_steps`, and that the caller falls back to plain A\* plus reactive yielding on failure — "exactly the degradation story the report has to be honest about" (`src/planner.py:133-135`).

A planning timestep is defined as "the time to traverse one cell"; the planner is deliberately unitless and the executor converts to seconds (`src/planner.py:23-24`). `src/fleet_manager.py:207` does that conversion at `cell_m / v_max` — the *fastest* the robot can drive, because these timestamps are an earliest-entry bound whose only job is to preserve the waits the planner inserted.

### 4.2 The reservation table

`Reservations` (`src/planner.py:29`) holds two dictionaries and a horizon:

| Field | Key | Purpose |
|---|---|---|
| `vertex` | `(cell, t) -> owner` | Who occupies which cell at which timestep (`src/planner.py:40`) |
| `edge` | `(cell, prev, t) -> owner` | The **reverse** traversal, banned over the same interval (`src/planner.py:41`, written at `src/planner.py:50`) |
| `horizon` | int | Last reserved timestep; defines `settle_t` |

The edge table is the non-obvious half and the class docstring says why: two robots can exchange cells in one step without ever sharing one. That passes a vertex check and is a head-on collision in the world (`src/planner.py:31-34`). This exact property is a regression test (`tests/test_core.py:546`).

`reserve_path` also writes a **tail**: after the path ends, the goal cell stays reserved for `hold_after = 8` further timesteps (`src/planner.py:44`, `src/planner.py:54-58`). A robot that has arrived still occupies its goal; without the tail, later agents plan straight through a parked robot and "the plan is a lie" (`src/planner.py:52-53`).

### 4.3 Resolving a head-on corridor conflict — worked example

`prioritized_plan` (`src/planner.py:184`) plans a fleet in priority order against one shared table: plan agent, reserve its path, plan the next agent against those reservations. `src/fleet_manager.py:194` is the only production caller.

Running the pinned head-on case — `chokepoint_warehouse()` (27x9, one 15-cell corridor at y=4, x∈[6,20]), robot A from `(1,4)` to `(25,4)`, robot B the exact reverse — produces:

| | Plan length | Behaviour |
|---|---:|---|
| A (planned first) | 25 steps | Drives straight through: `(1,4)@t0` → `(25,4)@t24`. |
| B (planned second) | 42 steps | Cannot use the corridor while A is in it; arrives `(1,4)@t41`. |

**Measured space-time clashes between the two plans: 0.** No `(cell, t)` is claimed by both owners. This is asserted as a regression test at `tests/test_core.py:532` — "the case a time-independent planner cannot express at all."

**An honest observation about B's plan.** B does not simply wait at the mouth. Its plan drives 11 cells *into* the corridor (to `(14,4)` at t=11), waits one step, reverses back out to `(21,4)` by t=19, steps sideways to `(21,5)` at t=20, and only then re-enters and drives through. The cost is optimal — 41 steps, the same as waiting 17 ticks in the bay and then driving 24 cells — but among the many optimal plans the tie-break picked one that performs a manoeuvre `src/environment.py:171-172` explicitly calls "a failure, not a plan". The planner is correct; it is indifferent between equal-cost plans, and nothing in the cost model expresses "do not enter a corridor you will have to reverse out of". This is a real limitation of the reservation-table formulation, and it is one reason the shipped decentralised policies do **not** use this planner online (see below). Recorded in [15. Limitations](15-LIMITATIONS.md).

`prioritized_plan` is also **incomplete**: a later agent can be walled in by an earlier agent's reservations. The code returns `[]` for that agent and the manager counts it as `unsolved` rather than hiding it (`src/planner.py:192-194`, `src/fleet_manager.py:209-214`), and there is a test that the failure is reported as a value rather than an exception (`tests/test_core.py:553`).

### 4.4 Cost relative to plain A\*

Measured on the same host, same map, same start/goal pair (`chokepoint_warehouse()`, `(1,4)` ↔ `(25,4)`):

| Planner | Mean time | Peak allocation |
|---|---:|---:|
| `astar`, one agent | 0.167 ms | — |
| `space_time_astar`, second agent against a populated table | **9.31 ms** (max 14.5 ms) | 209 KiB |

That is a **~56x** cost multiplier for one agent on a 46-free-cell map. The reason is structural: the state space is `V x T` rather than `V`, and with `max_steps = 512` the worst-case bound on the standard map is 399 x 512 ≈ 204,000 states. Plain A\* is bounded by 399.

This is exactly why space-time A\* runs on the **fleet manager** and not on the robot. `grep` confirms it: `space_time_astar` is called only from `prioritized_plan` (`src/planner.py:199`), which is called only from `src/fleet_manager.py:194`. `src/amr.py` imports `astar` alone (`src/amr.py:52`). A robot under the `hierarchical` policy consumes the manager's space-time *schedule* over the wire (`src/amr.py:2677`) and honours its timestamps (`src/amr.py:1452`), but never computes one.

Note a docstring imprecision: `src/planner.py:132-133` describes this function as the workhorse "of the centralised reservation baseline **and of the hierarchical policy Layer 2**". The second half is true only indirectly — the hierarchical policy receives the output, it does not run the search.

---

## 5. PIBT — Priority Inheritance with Backtracking

This is the decentralised MAPF algorithm, and the one a judge is most likely to know from the literature (Okumura et al.). The implementation is `src/priority.py:84`, deliberately kept small and dependency-free so it is auditable; the caller is `src/amr.py:1827`.

PIBT is a **one-step-at-a-time** algorithm. It does not produce a path. It takes the current configuration of the whole fleet and returns one collision-free *next* configuration — a single grid move for every robot. The long-range route stays with A\*; PIBT decides only who gets to execute the next cell of it (`src/priority.py:5-7`).

### 5.1 The priority scheme

`PriorityKey` (`src/priority.py:23`) is a frozen, ordered 7-tuple, larger moves first:

| Field | Meaning |
|---|---|
| `emergency` | Emergency battery state |
| `exiting_branch` | Leaving a tree appendage (see `src/topology.py:36`) |
| `waiting_age` | How long this robot has been waiting — the anti-starvation term |
| `service_age` | Task service age |
| `loaded` | Carrying cargo |
| `distance_bias` | Negative distance to goal |
| `robot_id` | Unique final tiebreaker only |

Two properties matter for correctness. First, the key is a **total order** — `robot_id` guarantees no two robots ever compare equal, so every edge node resolves the same configuration. Second, the key is **frozen when broadcast** (`src/priority.py:26-29`), and both sides compare published tokens. Comparing a live, ageing local value against a stale peer value causes symmetric yielding — both robots conclude they lose — and the docstring records that this codebase has already paid for that bug once. It is called out again at `src/bios4.py:400-402`. The wire format is a 7-element list with a defensive parser that falls back to `PriorityKey(robot_id=...)` on anything malformed (`src/priority.py:40-51`).

### 5.2 The algorithm

`pibt_step` first rejects an impossible input: if two robots are recorded at the same cell, there is no valid configuration to resolve from, and it raises `ValueError` rather than manufacture a plan (`src/priority.py:101`).

Robots are then processed in **descending base priority** (`src/priority.py:172`). For each unassigned robot, `assign(rid, inherited, parent, depth)` (`src/priority.py:113`) runs:

1. **Inherit.** If an inherited key exceeds this robot's own effective key, adopt it and record the parent (`src/priority.py:121-124`). Inheritance is transitive: a pushed robot pushes with the priority it was pushed *with*, not its own, so a chain A→B→C all moves on A's authority. Verified at `tests/test_priority.py:34`, which asserts `effective_priorities["C"] == priorities["A"]`.
2. **Order candidates.** `_candidates` (`src/priority.py:65`) returns `[current] + neighbours`, sorted by: the robot's existing A\* waypoint first, then Manhattan distance to its goal, then *preferring to move over staying*, then a deterministic `(y, x)` fallback. Putting the A\* waypoint first is what couples PIBT to the route layer; making waiting lose ties is what makes a pushed robot actively look for room to vacate instead of freezing (`src/priority.py:74-75`).
3. **Skip taken cells.** A cell already reserved by another robot this round is not a candidate (`src/priority.py:128-130`).
4. **Ban the edge swap.** If the target's current occupant has already been assigned to move into *my* current cell, skip the target (`src/priority.py:132-136`). Note the comment: rotations of length ≥ 3 remain legal — a ring of robots may all rotate one cell, which is a valid discrete configuration. Only the 2-cycle is forbidden.
5. **Tentatively assign, then push.** Reserve the target, and if it is occupied by an unassigned robot, recursively `assign` that occupant with my effective priority (`src/priority.py:145-149`). Because I have already reserved the target, the occupant cannot choose to stay there — which is precisely the mechanism that forces it to move.
6. **Backtrack.** Before each attempt the four mutable maps are snapshotted; if the recursive push fails, they are restored exactly and `backtracks` is incremented (`src/priority.py:140-162`). The comment explains the choice: recursion depth for warehouse conflicts is tiny, so explicit snapshots are cheaper to reason about than subtle mutation ordering.
7. **Wait as the terminal case.** If no candidate works, the robot stays put *only if* its own cell has not already been reserved by someone else; either way it returns `False`, so a requester that pushed it will backtrack (`src/priority.py:164-170`).

Recursion is bounded by `max_depth`, defaulting to 64 and configured as `priority_max_depth = 64` (`src/settings.py:175`). The comment at `src/settings.py:172-174` is honest that this is defensive: a physical conflict chain cannot exceed the fleet size.

Finally, two **post-conditions are checked, not assumed**: every robot must be assigned exactly one distinct cell (`src/priority.py:178`), and no pair may have swapped (`src/priority.py:182-185`). Either failure raises `RuntimeError`. "Refuse to output a partial configuration; stopping is safer than inventing occupancy."

The decision returned (`src/priority.py:54`) carries `next_cells`, `effective_priorities`, `inherited_from`, `blocked_by` and the `backtracks` count.

### 5.3 What PIBT guarantees, and what it does not

Verified by test:

| Property | Test |
|---|---|
| Three-robot inheritance chain pushes correctly, with transitive priority | `tests/test_priority.py:34` |
| A two-robot head-on swap is rejected and produces backtracks | `tests/test_priority.py:48` |
| A four-agent rotation is legal *and* deterministic across repeated calls | `tests/test_priority.py:60` |
| Randomised configurations produce no vertex conflict, no edge swap, and only legal single steps | `tests/test_priority.py:88` |

What it does **not** guarantee is that the configuration is physically executable. [`archive/FINDINGS.md`](archive/FINDINGS.md) §7 records the failure directly: PIBT returned a collision-free next-cell configuration and two robots stayed safety-stopped forever, because a leader turning west while its follower entered from the south *initially reduced* the chassis gap. Discrete occupancy is not a proof about swept continuous trajectories. The fix is in the caller, not the algorithm: `src/amr.py:1912-1936` detects a turning leader or a sub-standstill gap and stages the follower at its own cell centre first, and `src/amr.py:1949-1956` grants a bounded, speed-limited creep window so a legal convoy transition can actually execute. Layer 0 still vetoes anything that closes a gap ([07. Safety](07-SAFETY.md)).

### 5.4 How the robot calls it

`_bios_pibt_coordinate` (`src/amr.py:1827`) is invoked from the 10 Hz reactive loop (`src/amr.py:1241`, `src/amr.py:1244`; loop rate `reactive_hz = 10.0` at `src/settings.py:109`). Each robot **reconstructs the fleet configuration locally** from its own peer table and runs the identical deterministic resolver — no robot commands another and there is no elected coordinator (`src/amr.py:1831-1836`). See [03. Decentralized Protocol](03-DECENTRALIZED-PROTOCOL.md) for how that peer table is maintained.

Snapshot construction (`src/amr.py:1861-1876`):

- `positions[self]` = measured cell; `goals[self]` = current goal; `preferred[self]` = the requested next cell from the A\* route.
- For each peer: `positions` from its heartbeat pose, `goals` from its published goal (falling back to the last cell of its intent), `preferred` from the first cell of its intent, priority from its published key.
- A peer whose intent has expired is still **physical occupancy**, so it is included but asked to stay put (`src/amr.py:1869-1870`).
- Duplicate cells are dropped before the call (`src/amr.py:1871-1872`), because `pibt_step` rejects them.

Three guards precede the call:

1. If the requested cell *is* the measured cell, return immediately (`src/amr.py:1845`). This is the `to_cell` boundary asymmetry from [§1.2](#12-continuous-pose-to-discrete-cell): treating it as a discrete "stay" strands every robot half a cell into its route.
2. If this robot is inside a controlled block and is leaving it, PIBT is skipped entirely (`src/amr.py:1848-1859`). The block owner must be allowed to clear its exit; feeding outside peers' intents into PIBT would make the inside robot yield back into the single-file lane.
3. A `ValueError`/`RuntimeError` from a contradictory or stale snapshot is caught and converted into a wait, not a move (`src/amr.py:1883-1887`) — "a contradictory/stale snapshot is not a licence to move."

Outcomes are then instrumented (`src/amr.py:1889-1897`): `plan_cpu_s`, `plan_calls`, `plan_cpu_max_s`, `priority_decisions`, `priority_backtracks`, `priority_inheritances`, `priority_waits`, `priority_forced_moves`. If PIBT displaces this robot to a cell that is *not* its requested one, it executes exactly one cell and lets the ordinary route loop replan (`src/amr.py:1977-1982`) — and refuses to do so at speed above 0.25 m/s, because a differential-drive chassis cannot rotate instantly (`src/amr.py:1974-1976`).

### 5.5 Measured cost

`pibt_step` on a 61x41 map (2501 free cells), 200 calls per fleet size, randomised positions and goals:

| Fleet | Mean | Max |
|---:|---:|---:|
| 3 robots | 0.084 ms | 0.298 ms |
| 8 robots | 0.293 ms | 0.716 ms |
| 24 robots | 0.799 ms | 1.235 ms |
| 100 robots | 6.81 ms | 11.5 ms |

The cost is driven by fleet size, not map size: the search is over the fleet's candidate moves (at most 5 per robot), not over the grid. At the 24-robot benchmark density a robot spends under 1 ms per reactive tick on coordination, against a 100 ms tick budget.

---

## 6. Re-planning triggers (requirements 12 and 13)

`_replan(t, start, reuse_identical=False)` (`src/amr.py:2693`) is the single entry point for discarding a route and computing a new one. It:

1. Builds the hard-blocked set from `_dynamic_blocked_until`, **excluding the current cell and the goal** (`src/amr.py:2698-2701`) — so a dynamic obstacle sitting on the destination can never make the problem unsolvable.
2. Merges the soft-cost layers: the decaying `penalty` map, the BIOS 6 shared-experience edge costs (`src/amr.py:735`), and the BIOS 6 moving-obstacle prediction costs (`src/amr.py:809`), taking the max where they overlap (`src/amr.py:2702-2706`).
3. Calls `astar` with `edge_allowed` bound to the circulation graph for directed policies (`src/amr.py:2707-2712`).
4. For directed policies, **never installs an undirected fallback**: an empty temporary route is more live than a non-empty route the traffic gate can never admit (`src/amr.py:2713-2718`).
5. Optionally computes a second, unguided A\* purely to quantify and log what the soft costs bought (`src/amr.py:2719-2752`) — this is where the `CONGESTION_REROUTE` and `PREDICTIVE_REROUTE` jury-telemetry records come from.
6. Records CPU, bumps `epoch`, installs the path, and **drops `path_times`** (`src/amr.py:2768-2772`): a locally computed route carries no schedule, and honouring a timetable that belongs to a discarded plan is worse than having none.

The `penalty` map decays multiplicatively by 0.75 per route tick and is deleted below 0.1 (`src/amr.py:2575-2578`), so an avoidance is temporary by construction — a cell made expensive by a one-off conflict becomes attractive again within a few seconds.

### 6.1 The complete trigger list

Every call site of `_replan`, with its condition:

| Condition | Citation |
|---|---|
| Route's next waypoint is no longer adjacent to the measured cell (directed policies) | `src/amr.py:1194` |
| Blocked longer than `block_wait_s`/`deadlock_wait_s` under a V3 auction policy — penalise the cell, take a new legal route (never reverse) | `src/amr.py:1374` |
| Same, under `BIOS_PIBT.2` with circulation enabled | `src/amr.py:1384` |
| Deadlock breaker: a wait-for cycle was *not* found, so penalise the contested cell and detour | `src/amr.py:2190` |
| BIOS unstick: walled in with no free adjacent step — make the contested cell expensive and retry | `src/amr.py:2272` |
| BIOS_4 learned policy selects the `reroute` verb (rate-limited to one per 3 s) | `src/amr.py:2471`, `src/bios4.py:449` |
| A give-way retreat completes or times out after 6 s | `src/amr.py:2638` |
| Stop-and-wait-competition baseline: path exhausted | `src/amr.py:2646` |
| Stop-and-wait-competition baseline: blocked ≥ `stop_wait_persistent_s` (4 s), retried at most every `stop_wait_replan_period_s` (3 s) | `src/amr.py:2661`, `src/settings.py:163-164` |
| Stop-and-wait-competition baseline: no progress for `livelock_progress_s` (12 s) | `src/amr.py:2667` |
| **No path, or the path is exhausted** — the ordinary bootstrap case, at the 1 Hz route loop | `src/amr.py:2685` |
| **Liveness escalation:** no net progress for 12 s and no cycle detected, so the world model is wrong — clear all penalties and start over | `src/amr.py:2691` |
| **A dynamic obstacle appeared on the remaining path** | `src/amr.py:2869` |
| A predicted moving-obstacle hazard crosses the remaining path (BIOS 6, rate-limited to `v6_prediction_replan_s` = 1.5 s) | `src/amr.py:2876`, `src/settings.py:276` |
| Idle robot's repositioning target changed | `src/amr.py:3086` |
| **Task state transition:** arrived at pickup, goal becomes the drop cell | `src/amr.py:3162` |
| Duplicate-cell invariant repair: vacate to a distinct cell | `src/amr.py:3241` |
| Idle-vacate: clearing one cell for an active peer | `src/amr.py:3311` |
| Idle-vacate: moving to a clear parking dock | `src/amr.py:3345` |
| Idle-vacate: clearing a lane for an active peer | `src/amr.py:3385` |
| Auction repositioning toward a task this robot expects to win | `src/amr.py:4036` |
| **New task accepted** — goal set to the pick cell | `src/amr.py:4779` |

Route-level replanning is gated at `route_hz = 1.0` (`src/settings.py:110`, gate at `src/amr.py:589`), so the ordinary path-exhausted and liveness paths fire at most once per second. The obstacle-driven paths are *not* gated by that loop — `_observe_dynamic_obstacles` runs every world tick (`src/amr.py:585`, `world_hz = 50.0`).

`reuse_identical=True` (used only by the stop-and-wait baseline) suppresses the `replans` counter and the epoch bump when the recomputed suffix is byte-identical to the current one (`src/amr.py:2758-2765`), so the baseline is not charged for churn it did not cause.

### 6.2 Blocked aisle detection (requirement 12)

**How a robot learns an aisle is blocked.** It does not receive a message. Blocked aisles are discovered from anonymous lidar returns in `_observe_dynamic_obstacles` (`src/amr.py:2774`), whose docstring states the job precisely: "Promote stationary anonymous lidar blobs into an expiring local map layer."

The sequence, per detection, per world tick:

1. **Range gate.** A detection exists only within `sense_radius_m = 4.0 m` (`src/settings.py:37`, applied at `src/world.py:759`). At a 1.4 m pitch that is **≈2.9 cells** — a robot sees a blocked aisle roughly two cells ahead, not down the length of the corridor. Detections carry a position, a radius and a velocity, and **no identity** (`src/world.py:61`).
2. **Moving vs stationary.** Speed above 0.08 m/s is treated as traffic, not map state (`src/amr.py:2797`). For BIOS 6 with a healthy radio, a moving unmatched blob is *forward-projected* over a 2.4 s horizon in 0.6 s steps and each predicted cell gets a decaying soft cost (`src/amr.py:2812-2841`, `src/settings.py:273-275`) — a prediction, never a wall.
3. **Peer correlation.** A blob within 0.35 m of a known peer pose is a stopped robot, which is traffic to negotiate with, not a map mutation (`src/amr.py:2794`, `src/amr.py:2845-2848`). Moving blobs use a looser 0.75 m gate because a 5 Hz heartbeat pose can lag a moving chassis (`src/amr.py:2805-2807`).
4. **Persistence.** An unmatched stationary blob accumulates in `_dynamic_candidates`. Promotion to map state requires **at least 3 sightings AND at least 0.3 s since first sighting** (`src/amr.py:2859`). Candidates that go unseen for 0.5 s are dropped (`src/amr.py:2780`). The 0.3 s window is deliberately "at least one heartbeat interval" — a peer will identify itself in that time; a dropped pallet will not.

**How long until it reacts.** At `world_hz = 50`, three sightings accumulate in 0.06 s, so **the 0.3 s persistence wall dominates: ~0.3 s from first return to map mutation.** The replan then happens on the same tick.

**What it does.** The cell is written to `_dynamic_blocked_until[cell] = t + 2.0` (`src/amr.py:2863`) — a **2-second expiring** hard block, refreshed while the object is still seen. If and only if that cell lies on the *remaining* path (`self.path[self.pidx:]`) and is not the goal, `route_blocked` is set (`src/amr.py:2864`) and `_replan` runs immediately (`src/amr.py:2867-2871`), incrementing `dynamic_reroutes` if the route actually changed. A robot does not replan for an obstacle that is not in its way.

The expiry is the important design choice. A blocked aisle is a belief with a 2-second half-life, not a permanent map edit; a pallet that is moved out of the way is forgotten within two seconds without any explicit clearing protocol, and a robot cannot poison its own map with a stale observation. The interaction between the blocked layer and the directed route graph is tested at `tests/test_core.py:158`: every neighbour of the start cell is marked dynamically blocked, `_replan` runs, and the assertion is that the resulting route is either empty or entirely legal on the circulation graph — never a reverse edge the traffic gate could not admit.

**Limitation, stated:** the blocked map layer is strictly *receiver-local*. There is no message that shares "aisle N is blocked" with the fleet; each robot must see the obstacle itself, within 4 m. The only shared route knowledge is the BIOS 6 experience layer (`src/amr.py:760`), which shares measured *edge delays*, not obstacles, and is disabled entirely under packet loss or radio dead zones (`src/amr.py:743`).

---

## 7. The edge-hardware cost argument (requirement 15)

### 7.1 What was measured

All numbers below were produced on the development host (Intel Core, Windows, CPython 3.13.14). **No Raspberry Pi measurement exists in this repository**, and [`archive/CRITIQUE.md`](archive/CRITIQUE.md) lines 95-99 say so explicitly: the harness reports `plan_cpu_mean_ms` / `plan_cpu_max_ms` from the host, "and the report must say so rather than implying a Pi measurement." Treat every figure here as a host figure. A Pi 4 running CPython is commonly 5-15x slower on this kind of pure-Python integer work; even at 15x the budget below holds.

**Microbenchmarks** (this session, method described in the relevant sections above):

| Operation | Mean | Max | Peak memory |
|---|---:|---:|---:|
| `astar`, 31x21 map, mean path 20.7 cells | 0.182 ms | 0.641 ms | 24.4 KiB |
| `pibt_step`, 3 robots, 61x41 map | 0.084 ms | 0.298 ms | — |
| `pibt_step`, 24 robots, 61x41 map | 0.799 ms | 1.235 ms | — |
| `pibt_step`, 100 robots, 61x41 map | 6.81 ms | 11.5 ms | — |
| `space_time_astar`, one agent against a populated table | 9.31 ms | 14.5 ms | 209 KiB |

**In-simulation aggregates** from the committed benchmark artifacts, which sum `_replan` and `_bios_pibt_coordinate` CPU across a whole run (`src/main.py:393-396` / `src/main.py:498-501`, counters at `src/amr.py:1889` and `src/amr.py:2754`):

| Artifact | Policy | Plan calls | Mean per call | Max per call |
|---|---|---:|---:|---:|
| `artifacts/benchmarks/bios5-grand-challenge-baseline.json` | `BIOS_PIBT.5` | 302-446 | 0.115-0.151 ms | 0.74-0.79 ms |
| `artifacts/benchmarks/bios6-auction-v2-acceptance.json` (open, burst) | `BIOS_PIBT.6` | 5,023-7,103 | 0.076-0.080 ms | 4.5-7.9 ms |
| `artifacts/benchmarks/bios6-auction-v2-acceptance.json` (0% loss) | `BIOS_PIBT.6` | 4,839-5,582 | 0.066-0.070 ms | 3.9-4.0 ms |

**A measurement caveat that should not be glossed.** `plan_calls` is incremented at exactly three sites — `src/amr.py:1890` (PIBT), `src/amr.py:2755` (`_replan`) and `src/fleet_manager.py:196` (central). The A\* calls made while *bidding* are not counted: `_bid_cost` runs two A\* searches per candidate task (`src/amr.py:4046`, `src/amr.py:4049`) and `_energy_required` runs two more plus one per dock (`src/amr.py:4161`, `src/amr.py:4164`, `src/amr.py:4173`). With `energy_bid_bundle = 12` (`src/settings.py:215`), one auction round can cost ~24 uncounted A\* calls ≈ 4.4 ms. The reported `plan_cpu_mean_ms` therefore **understates** total search CPU. See [12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md).

### 7.2 The budget arithmetic

The three control loops run at different rates and only the slowest was ever a candidate for a server (`src/settings.py:100-112`):

| Loop | Rate | Budget/tick | Search work in it |
|---|---:|---:|---|
| Layer 0 safety | 50 Hz | 20 ms | none |
| Layer 1 reactive | 10 Hz | 100 ms | one `pibt_step` (0.08-0.8 ms at 3-24 robots) |
| Layer 2 route | 1 Hz | 1000 ms | at most one `astar` (0.18 ms) |

Steady-state search load on one robot at the pinned fleet sizes is therefore **on the order of 0.2-8 ms per second of wall clock**, i.e. well under 1% of one core. Even a 15x slowdown on a Pi-class CPU leaves >85% headroom.

### 7.3 Memory

| Structure | Size |
|---|---:|
| `Warehouse.grid`, 31x21 | 6.3 KB |
| `Warehouse.grid`, 61x41 | 22 KB |
| Peak A\* working set, corner-to-corner | 24.4 KiB |
| Peak space-time A\* working set | 209 KiB |
| Neighbour memo | `lru_cache(maxsize=8192)` entries (`src/environment.py:23`) |
| BIOS_4 learned policy | 549 floats — a 28-16-5 MLP (`src/bios4.py:126`, `src/amr.py:75-76`) |
| Block/topology/circulation decompositions | computed once per map, `lru_cache`d (`src/environment.py:164`, `src/topology.py:43`, `src/topology.py:165`) |

The whole planning layer fits in a few hundred kilobytes. There is **no third-party numerical dependency anywhere in it** — `src/geometry.py:1` and `src/bios4.py:39-43` say the constraint out loud ("stdlib-only so the agent runs on a bare Pi image", "no build step"). The heaviest module, `src/planner.py`, imports only `heapq` and `itertools`.

### 7.4 Why these algorithms and not an optimal MAPF solver

Optimal MAPF is NP-hard, and the module docstring makes the argument rather than assuming the reader agrees (`src/planner.py:3-8`). Three concrete reasons the search layer is A\* + PIBT and not CBS:

1. **Anytime behaviour beats optimality on a 100 ms tick.** CBS has no useful intermediate answer — it either finishes its constraint-tree search or it does not. PIBT returns a valid, collision-free configuration in bounded time *every* tick, and its worst measured case at 100 robots (11.5 ms) is a tenth of the reactive budget. A planner that is optimal 90% of the time and late 10% of the time is a planner that stops the fleet 10% of the time.
2. **CBS needs a joint problem, which is exactly what "decentralised" removes.** CBS reasons over a shared constraint tree across all agents. There is no coordinator here to hold one (requirement 6), and replicating a constraint tree across a lossy 5 GHz link at 10 Hz is a strictly harder distributed-systems problem than the one being solved. PIBT is *local* by construction: each robot reconstructs a small configuration from heartbeats and runs the same deterministic function (`src/amr.py:1831-1834`).
3. **The environment invalidates plans faster than an optimal plan can be computed.** A dynamic obstacle is detected and acted on in ~0.3 s ([§6.2](#62-blocked-aisle-detection-requirement-12)) with a 2 s belief lifetime. A joint plan optimal at t=0 is stale well before it is executed. Cheap replanning at 1 Hz dominates expensive planning at 0.1 Hz.

The counter-argument a judge should push on, and the honest answer: the space-time planner *is* implemented and *is* used, as the strong centralised baseline (`src/planner.py:186-190`). The project's position is that beating naive stop-and-wait is a low bar and that "prioritised space-time planning on a fleet manager" is what a real on-prem system does, so that is the number worth reporting. See [12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md).

### 7.5 The learned policy does not plan

`BIOS_4` (`src/bios4.py`) is a 28-feature, 16-hidden-unit, 5-output MLP — 549 parameters — that runs at the Layer 1 arbitration slot. It **does not compute paths and does not emit velocities.** It selects among five verbs the fleet already implements and has already been measured on: `proceed`, `hold`, `yield`, `claim`, `reroute` (`src/bios4.py:55-61`). Only `reroute` touches the planner, and it is rate-limited to one call per 3 s by the legality mask (`src/bios4.py:449`, `src/bios4.py:454`) — explicitly to stop evolution rediscovering replan churn and calling it a strategy.

Four of the 28 features describe the block layer, and one of them names this document's threshold directly: `next_in_block` is "next cell is inside a controlled (>=6 cell) block" (`src/bios4.py:98`), computed through `_controlled_block` (`src/bios4.py:413`). Inference is a few hundred multiply-adds in pure Python at 10 Hz (`src/bios4.py:43`). The liveness backstop sits *above* the model and cannot be suppressed by it (`src/amr.py:2403-2410`). Full treatment in [05. Coordination Policies](05-COORDINATION-POLICIES.md).

---

## 8. What is not implemented

Named deliberately, with the reason. Everything in this table is **not implemented**.

| Technique | Why not |
|---|---|
| **CBS / ECBS / CBSH** (optimal and bounded-suboptimal conflict-based search) | Needs a joint constraint tree and therefore a coordinator (violates requirement 6); no anytime answer within a 100 ms tick. See [§7.4](#74-why-these-algorithms-and-not-an-optimal-mapf-solver). |
| **ICTS, SAT/ILP/branch-and-cut MAPF** | Same objection, worse constants. Optimal MAPF is NP-hard (`src/planner.py:6`). |
| **LNS2 / MAPF-LNS** (large-neighbourhood repair) | Anytime, but still a centralised joint search over a shared solution. Would be the natural upgrade *if* a coordinator were permitted. |
| **RHCR / windowed lifelong MAPF** | The closest fit to the actual problem, and the honest gap in this design. Not attempted; PIBT plus 1 Hz individual replanning is the cheaper approximation. |
| **SIPP** (safe-interval path planning) | Would compress the space-time state space from `V x T` to `V x intervals` and largely remove the 56x penalty measured in [§4.4](#44-cost-relative-to-plain-a). Not implemented; the space-time planner runs on the manager, where the cost is affordable. This is the single clearest performance improvement available to the search layer. |
| **D\* Lite / LPA\*** (incremental replanning) | A\* from scratch costs 0.182 ms and runs at most 1 Hz; incremental machinery would save microseconds and add a correctness surface that must stay consistent with an expiring obstacle layer. |
| **JPS** (jump point search) | A symmetry-breaking optimisation for large open uniform grids. These maps are 399-2501 cells and dominated by racks; there is no symmetry plateau worth jumping over. |
| **Any-angle planning (Theta\*, Field D\*)** and **diagonal moves** | The grid cannot express the clearance check a diagonal past a rack corner needs on a differential-drive chassis (`src/environment.py:57`). |
| **Kinodynamic / trajectory optimisation (TEB, MPC)** | The follower is a hand-tuned pure-pursuit-style controller whose braking equation is recorded as the single change that made tasks complete at all ([`archive/FINDINGS.md`](archive/FINDINGS.md), cited at `src/bios4.py:17-19`). Replacing it was judged a worse bet than keeping it. |
| **Learned path planning (PRIMAL and similar)** | Deliberately rejected, with the reasoning written into `src/bios4.py:1-22`: Layer 0 has unconditional final authority, so a network trained to emit velocities spends its capacity rediscovering an envelope it will not be allowed to leave, and sim-to-real becomes a question about chassis dynamics. The learned policy chooses among *verbs*, not trajectories. |
| **Continuous-time / MAPF-POST post-processing** | The schedule is emitted as earliest-entry timestamps (`src/fleet_manager.py:202-207`) with no formal temporal-plan-graph safety argument. |

---

## Cross-references

- [01. Requirements Traceability](01-REQUIREMENTS-TRACEABILITY.md) — the full requirement-to-evidence matrix
- [02. Architecture](02-ARCHITECTURE.md) — the three-layer split these algorithms sit in
- [03. Decentralized Protocol](03-DECENTRALIZED-PROTOCOL.md) — how the peer snapshot PIBT consumes is built
- [05. Coordination Policies](05-COORDINATION-POLICIES.md) — which policy calls which algorithm, and how they compare
- [06. Task Allocation](06-TASK-ALLOCATION.md) — the auction that calls A\* for bid costs
- [07. Safety](07-SAFETY.md) — Layer 0, which vetoes any plan these algorithms produce
- [12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md) — the artifacts the CPU figures come from
- [15. Limitations](15-LIMITATIONS.md) — the reversing space-time plan, the host-not-Pi measurement, the receiver-local obstacle map
