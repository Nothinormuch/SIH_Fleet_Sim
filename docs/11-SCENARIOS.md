# 11. SCENARIOS

> This document is the evidence index: for each of the twenty requirements it names the scenario that demonstrates it, the exact command that runs it, and — where no scenario demonstrates it — says so.

**Audience:** SIH judges and BEL evaluators who want to reproduce a claim without reading the codebase, and teammates who must answer "which run shows that?" live.
**Reads best after:** [05. Coordination Policies](05-COORDINATION-POLICIES.md)

## Requirements evidenced

| # | Requirement | Where | Evidence |
|---|---|---|---|
| 1 | At least 3 AMRs | [§3](#3-requirement-to-scenario-evidence-map) | `src/scenarios.py:498`, `src/scenarios.py:520` |
| 2 | Dynamic warehouse environment | [§4](#4-the-dynamic-warehouse-environment-mechanism-by-mechanism) | `src/main.py:226-270` |
| 3 | Decentralized communication | [§3](#3-requirement-to-scenario-evidence-map) | `src/main.py:326-347`, `src/distributed_demo.py:154` |
| 4 | Position sharing | [§3](#3-requirement-to-scenario-evidence-map) | `src/main.py:435` |
| 5 | Intent sharing | [§3](#3-requirement-to-scenario-evidence-map) | `src/main.py:436`, `src/settings.py:141` |
| 6 | No central coordination server | [§2.11](#211-manager_dies) | `src/main.py:104`, `src/main.py:166-173` |
| 7 | Multi-agent path planning | [§2.5](#25-dense_aisles) | `src/scenarios.py:386-402` |
| 8 | Collision avoidance | [§2.7](#27-human_in_aisle) | `src/world.py:691-720` |
| 9 | Real-time conflict resolution | [§2.2](#22-crossing_chokepoint) | `src/scenarios.py:350-383` |
| 10 | Deadlock resolution | [§7](#7-seed-99) | `src/scenarios.py:609-665` |
| 11 | Narrow intersection / chokepoint | [§8](#8-the-acceptance-scenario) | `src/environment.py:135-153` |
| 12 | Blocked aisle | [§2.8](#28-blocked_aisle) | `src/scenarios.py:498-517` |
| 13 | Re-routing | [§2.8](#28-blocked_aisle) | `tests/test_resilience.py:22` |
| 14 | Task re-assignment | [§2.9](#29-robot_failure_reassignment) | `tests/test_resilience.py:35` |
| 15 | Edge / local execution | [§9](#9-coverage-gaps-and-cautions) | `src/distributed_demo.py:321-322`, `src/edge_runtime.py:321` |
| 16 | Fleet dashboard | [§9](#9-coverage-gaps-and-cautions) | no scenario — `backend/server.py:616-647` |
| 17 | Real-time positions on dashboard | [§9](#9-coverage-gaps-and-cautions) | no scenario — `src/world.py:875-884` |
| 18 | Battery status | [§2.4](#24-energy_acceptance) | `src/scenarios.py:598-606`, `src/world.py:881` |
| 19 | Zero inter-robot collisions | [§8](#8-the-acceptance-scenario) | `src/main.py:416-420` |
| 20 | ≥20% task-time reduction | [§8](#8-the-acceptance-scenario) | `src/benchmark.py:269-281` |

Section [§3](#3-requirement-to-scenario-evidence-map) restates every row with the command and the metric that carries it.

---

## 1. How to read a scenario

### 1.1 The Scenario record

A scenario is a frozen experiment description, not a script. The dataclass is `src/scenarios.py:62-97`; every field below is an input to one run and nothing in it is chosen at runtime.

| Field | Line | Meaning |
|---|---|---|
| `name` | `src/scenarios.py:64` | Identity used in results, artifacts and the fingerprint |
| `env` | `src/scenarios.py:65` | The `Warehouse` grid — dimensions, racks, stations, docks |
| `starts` | `src/scenarios.py:66` | One start cell per AMR; `n_robots` is `len(starts)` (`src/scenarios.py:91-93`) |
| `assignments` | `src/scenarios.py:69` | Per-robot ordered task queue, used by route-only comparisons |
| `unassigned` | `src/scenarios.py:86` | Unallocated catalogue, used by the allocation policies |
| `humans` | `src/scenarios.py:70` | One list of work cells per worker; expanded to a rack-safe loop by A\* |
| `duration_s` | `src/scenarios.py:71` | Evidence window in simulated seconds |
| `net` | `src/scenarios.py:72` | Radio model: loss, latency, dead zones, AP relaying (`src/settings.py:115-133`) |
| `kill_manager_at` | `src/scenarios.py:73` | Fleet-manager process kill time |
| `partition_at` / `heal_at` / `partition_groups` | `src/scenarios.py:74-76` | Network split and repair |
| `robot_fail_at` / `robot_restart_at` | `src/scenarios.py:79-80` | Per-robot process death and cold restart |
| `obstacles` | `src/scenarios.py:81` | Timed `ObstacleEvent` records (`src/scenarios.py:53-59`) |
| `pose_noise_m` | `src/scenarios.py:82` | Gaussian localisation error injected into every sensor read |
| `initial_battery_fracs` | `src/scenarios.py:88` | Per-robot starting state of charge |
| `seed` | `src/scenarios.py:89` | Builder seed recorded for the fingerprint |

Grid cells are 1.4 m square (`src/settings.py:305`); an AMR is 0.70 m across (`src/settings.py:18`). Every cell dimension quoted below converts to metres by multiplying by 1.4. Tile codes are `FREE`/`RACK`/`STATION`/`DOCK` = 0/1/2/3 (`src/environment.py:17-20`), and only `RACK` is impassable (`src/environment.py:51-54`).

### 1.2 What the seed randomises, and what is fixed

The seed appears twice and does two different jobs.

**Builder seed** — passed to the scenario function. It drives a local `random.Random` that picks task pick/drop cells and start cells in the two randomised builders only:

| Builder | What the seed moves | What stays fixed |
|---|---|---|
| `crossing_chokepoint` (`src/scenarios.py:360-381`) | Which left-bay and right-bay cells each task uses; which start cell each AMR gets | Map, corridor length, robot count, task count, the alternating direction pattern, 420 s window |
| `dense_aisles` (`src/scenarios.py:396-401`) | `_spread_starts` shuffle order; pick cell from the aisle set, drop from stations/docks | Map, robot count, task count, 600 s window |
| `showcase_*` | The above, plus which direction each worker walks their inspection loop (`src/scenarios.py:285`, `src/scenarios.py:341-342`) | Worker count, zone assignment, cargo mix, battery ladder |

Every other builder is fully deterministic in its structure: `open_floor_control`, `blocked_aisle`, `robot_failure_reassignment`, `partition_recovery` and `seed_99_congestion` compute their starts and tasks arithmetically and use no RNG at all (`src/scenarios.py:457-495`, `498-517`, `520-540`, `543-564`, `609-665`).

**Run seed** — passed to `run_scenario`. It seeds the physics world and the network model (`src/main.py:121-126`), so it controls pose noise draws (`src/main.py:337`) and per-packet loss and latency. The CLI ties both to the same number (`src/main.py:771-780`), but they are separate arguments and the benchmark pairs on both.

### 1.3 How a scenario becomes a run

`run_scenario` (`src/main.py:107-373`) builds a `World`, a `SimNetwork` and one `AMRBrain` per start cell, then integrates at 50 Hz. Inside each tick the order is fixed (`src/main.py:223-348`): scripted world events, then WMS announcements, then the fleet manager (if the policy has one), then every robot in sorted id order, then physics. A run ends when the duration elapses or every announced task is complete (`src/main.py:358-369`); a run that did not finish reports the cutoff and `completed_all: false` rather than pretending the cutoff was a makespan (`src/main.py:410-413`).

Allocation mode decides which workload the run uses. With an active allocation policy the announced catalogue is `unassigned`, or the flattened queues if `unassigned` is empty (`src/main.py:78-81`); with `preassigned` the per-robot queues are used directly (`src/main.py:155-156`). This is why `sih_acceptance_overlap` carries both.

### 1.4 Workload fingerprint

`workload_fingerprint` (`src/scenarios.py:100-164`) hashes the map, starts, ordered tasks, humans, duration, every failure and network setting, the pose noise, the seed and the full controller config into one SHA-256. The route policy is deliberately excluded because it is the independent variable (`src/scenarios.py:105-107`). The acceptance comparator refuses any pair whose fingerprints differ, which is what stops two different experiments being reported as one comparison.

---

## 2. The complete catalogue

Eighteen scenarios are registered in `SCENARIOS` (`src/scenarios.py:828-843`) — thirteen benchmark and fault scenarios plus five jury showcases. Every name below is a valid `--scenario` argument (`src/main.py:743-744`).

All commands assume the repository root and an activated virtual environment. `--robots` maps to the builder's `n_robots` (`src/main.py:773-774`); there is no CLI flag for `tasks_per_robot`, so changing the fleet size scales the task count by the builder's fixed ratio.

### 2.1 Map primitives

Three map builders serve every scenario (`src/environment.py:234-238`).

| Map | Builder | Default geometry | Chokepoint structure |
|---|---|---|---|
| `classic` | `src/environment.py:112-132` | 31×21 cells (43.4 m × 29.4 m), 399 free cells, 2×4 rack blocks, single-cell aisles, free perimeter ring, 5 stations, 4 docks | 59 corridor blocks, longest 4 cells |
| `chokepoint` | `src/environment.py:135-153` | 25×9 cells (35.0 m × 12.6 m), 121 free cells, two 6×9 bays joined by one corridor, 3 stations, 3 docks | 1 corridor block, 13 cells (18.2 m), single file |
| `open` | `src/environment.py:156-161` | No racks at all; stations on the left edge, docks on the right | 0 corridor blocks |

A corridor block is a maximal connected run of passable cells with at most two exits, length ≥ 2 (`src/environment.py:164-214`). Blocks are the traffic-control primitive: a robot acquires the whole block before entering and waits at a junction if it cannot. Block control is only applied to blocks of at least 6 cells (`src/settings.py:152`), so the 13-cell chokepoint is controlled and the classic warehouse's 4-cell aisle segments are not.

`dense_aisles` swaps to a `classic_large` 61×41 floor above 24 robots so a 100-AMR request gets roughly four times the area instead of one quarter of all cells filled with chassis (`src/scenarios.py:392-394`).

### 2.2 crossing_chokepoint

`src/scenarios.py:350-383`.

| Property | Value |
|---|---|
| Map | `chokepoint`, 25×9, one 13-cell single-file corridor at row y=4 |
| Robots | 4 (default), alternating bays, start cells ≥ 2 Manhattan apart |
| Tasks | 12 pre-assigned round-robin; every task crosses the corridor, directions alternate (`src/scenarios.py:365-368`) |
| Duration | 420 s |
| Dynamic elements | Pose noise 0.02 m only |
| Allocation | Pre-assigned queues |

**Designed to prove:** that the reported speedup is not an artefact of a friendly map. With one route between the bays, no policy can dodge the conflict by taking a different aisle, so coordination is the only variable that can help. Start cells are sampled without replacement and kept apart, because two robots sharing one cell at t=0 produce hundreds of spurious contacts from a broken initial condition (`src/scenarios.py:370-374`).

**Requirements:** 1, 7, 8, 9, 10, 11, 19.

```bash
python -m src.main --scenario crossing_chokepoint --policy BIOS_PIBT.6 \
  --allocation-policy preassigned --robots 4 --seed 0
```

### 2.3 sih_acceptance_overlap

`src/scenarios.py:567-595`. Covered in detail in [§8](#8-the-acceptance-scenario).

| Property | Value |
|---|---|
| Map | Same `chokepoint` map and same starts as `crossing_chokepoint` (`src/scenarios.py:577-578`) |
| Robots | 4 (default); the benchmark sweeps 4, 6, 8 |
| Tasks | 12; supplied **both** as round-robin queues and as an announced catalogue (`src/scenarios.py:579-591`) |
| Duration | 1200 s |
| Allocation | Auction by default (`use_auction=True`) |

**Designed to prove:** the problem statement's measurable success criterion — ≥20% task-completion-time reduction versus stop-and-wait on overlapping paths, with zero inter-robot contacts.

**Requirements:** 1, 7, 8, 9, 10, 11, 19, 20.

```bash
python -m src.main --scenario sih_acceptance_overlap --policy BIOS_PIBT.5 \
  --allocation-policy auction --robots 4 --seed 0
python benchmark.py --seeds 30 --jobs 8
```

### 2.4 energy_acceptance

`src/scenarios.py:598-606`.

| Property | Value |
|---|---|
| Map | `chokepoint`, 25×9 — built from `sih_acceptance_overlap` and renamed |
| Robots | 8 (default) |
| Tasks | 16 (2 per robot), announced catalogue |
| Duration | 1200 s |
| Dynamic elements | Heterogeneous starting charge: 0.12, 0.18, 0.28, 0.42, 0.58, 0.72, 0.86, 0.96 (`src/scenarios.py:604-605`) |

**Designed to prove:** that battery state is a hard admission constraint on task allocation, not a display field. Two of the eight AMRs start below the 0.15 charge trigger (`src/settings.py:216`), so an allocator that ignores energy will award work to a robot that cannot finish it. The run reports `energy_bids_suppressed` and `energy_no_eligible_rounds` (`src/main.py:432-433`).

**Requirements:** 1, 14, 18.

```bash
python -m src.main --scenario energy_acceptance --policy BIOS_PIBT.6 \
  --allocation-policy auction_bundle --robots 8 --seed 0
```

**Caution:** no checked-in test or campaign exercises this scenario (see [§9](#9-coverage-gaps-and-cautions)).

### 2.5 dense_aisles

`src/scenarios.py:386-402`.

| Property | Value |
|---|---|
| Map | `classic`, 31×21, 399 free cells, single-cell aisles, 59 corridor blocks |
| Robots | 8 (default) |
| Tasks | 32; picks are cells that touch shelving (`src/scenarios.py:170-179`), drops are stations and docks |
| Duration | 600 s |
| Dynamic elements | Pose noise 0.02 m only |

**Designed to prove:** that the coordination layer holds up on a realistic floor plan with many routes, many crossings and mixed traffic rather than one contrived corridor. It is also the base map for `human_in_aisle`, `manager_dies`, `dead_zone_*` and `showcase_grand_challenge`.

**Requirements:** 1, 7, 8, 9, 11, 19.

```bash
python -m src.main --scenario dense_aisles --policy BIOS_PIBT.6 \
  --allocation-policy preassigned --robots 8 --seed 0
```

### 2.6 open_floor_control

`src/scenarios.py:457-495`.

| Property | Value |
|---|---|
| Map | `isolated_lanes_control`, 18×17, 144 free cells; one 18-cell lane per robot, lanes physically disconnected by rack rows |
| Robots | 8 (default); the map height is `2n+1` |
| Tasks | 32; each task's pick and drop lie in the robot's own lane and alternate direction |
| Duration | 600 s |
| Dynamic elements | None; pose noise 0 |

**Designed to prove:** the negative result. With every robot in a private, physically isolated lane, no coordination policy can legitimately gain a traffic advantage. If the harness shows the candidate policy "winning" here, it is measuring something other than coordination and the headline number is wrong. The regression gate requires the candidate and the baseline to be within 10% of each other (`tests/test_resilience.py:82-98`).

This is the honest-control scenario; the earlier random open floor still produced shared destinations and idle blockers, so it was a contention workload claiming to measure no contention (`src/scenarios.py:461-465`).

**Requirements:** 1, 20 (as the falsification control).

```bash
python -m src.main --scenario open_floor_control --policy all \
  --allocation-policy preassigned --robots 4 --seed 0
```

### 2.7 human_in_aisle

`src/scenarios.py:405-418`.

| Property | Value |
|---|---|
| Map | `classic`, 31×21 (inherits `dense_aisles`) |
| Robots | 6 (default) |
| Tasks | 18 pre-assigned round-robin |
| Duration | 600 s |
| Dynamic elements | 1 warehouse worker on a principal cross-aisle (`src/scenarios.py:206-245`), walking at 1.15 m/s (`src/world.py:132`) |

**Designed to prove:** that the safety layer is real rather than decorative. A worker publishes no intent, honours no priority and cannot be negotiated with, so every protocol built on shared intent is structurally blind to them (`src/scenarios.py:407-412`). The robot sees them only as an anonymous lidar `Detection` with no identity (`src/world.py:59-77`). The gate requires zero robot-human contacts and a minimum separation of at least 0.90 m (`tests/test_resilience.py:69-79`).

**Requirements:** 1, 2, 8, 19.

```bash
python -m src.main --scenario human_in_aisle --policy BIOS_PIBT.6 \
  --allocation-policy auction --robots 6 --seed 0
```

### 2.8 blocked_aisle

`src/scenarios.py:498-517`.

| Property | Value |
|---|---|
| Map | `open`, 14×9, 126 free cells, no racks |
| Robots | 3 (floored at 3 by `max(3, n_robots)`) |
| Starts | (1,2), (1,4), (1,6) |
| Tasks | 3; each robot drives its own row from its start cell to (12, y) |
| Duration | 180 s |
| Dynamic elements | `dropped-pallet`, radius 0.40 m, appears at cell (5,4) at t=1.0 s and is never cleared (`src/scenarios.py:512-513`) |

**Designed to prove:** requirement 12 and requirement 13 together. The pallet lands on the middle robot's straight-line route one second into the run, broadcasts nothing, and must be discovered by onboard sensing and routed around. Because the floor is open, an alternate route always exists — the scenario tests re-routing, not survival of a partitioned graph. The gate asserts at least one dynamic obstacle detected and at least one dynamic reroute, with zero robot-robot and robot-rack contacts (`tests/test_resilience.py:13-24`).

The pallet is placed only when the target footprint is genuinely unoccupied; otherwise the runner retries on a later physics tick rather than materialising matter inside a chassis (`src/world.py:373-399`, `src/main.py:257-266`).

**Requirements:** 1, 2, 8, 12, 13, 19.

```bash
python -m src.main --scenario blocked_aisle --policy BIOS_PIBT.6 \
  --allocation-policy preassigned --robots 3 --seed 0
```

### 2.9 robot_failure_reassignment

`src/scenarios.py:520-540`.

| Property | Value |
|---|---|
| Map | `open`, 16×10, 160 free cells |
| Robots | 3 (floored at 3); starts (1,2), (1,5), (1,8) |
| Tasks | 1 announced task, T000: pick (3,2) → drop (14,2) |
| Duration | 180 s |
| Dynamic elements | `AMR01` fails at t=2.0 s (`src/scenarios.py:538`) |
| Allocation | Auction |

**Designed to prove:** requirement 14. `AMR01` is deliberately closest to T000 and wins the first auction; two seconds later its brain and radio go silent while its chassis remains a stopped physical obstacle (`src/main.py:331-335`). Its 20 s task lease (`src/settings.py:179`) then expires and a surviving peer re-bids and completes the job — with no dispatcher deciding the reassignment. The gate asserts exactly one recorded robot failure, at least one task reassignment, 1/1 tasks completed and zero robot-robot contacts (`tests/test_resilience.py:27-38`).

**Requirements:** 1, 2, 3, 14, 19.

```bash
python -m src.main --scenario robot_failure_reassignment --policy BIOS_PIBT.5 \
  --allocation-policy auction --robots 3 --seed 0
```

### 2.10 partition_recovery

`src/scenarios.py:543-564`.

| Property | Value |
|---|---|
| Map | `open`, 18×12, 216 free cells |
| Robots | 4 (floored at 4); starts (1,1), (1,3), (1,5), (1,7) |
| Tasks | 4 announced; each crosses its own row from (3,y) to (16,y) |
| Duration | 240 s |
| Dynamic elements | Network splits into `{AMR01, AMR02}` and `{AMR03, AMR04}` at t=2.0 s, heals at t=12.0 s (`src/scenarios.py:560-562`) |
| Allocation | Auction |

**Designed to prove:** that two islands of robots each keep working on their own catalogue, and that when the radio comes back the two catalogues converge to one consistent assignment without duplicate execution. The gate asserts all four announced tasks complete exactly once, with zero robot-robot contacts (`tests/test_resilience.py:41-50`).

**Requirements:** 1, 2, 3, 14, 19.

```bash
python -m src.main --scenario partition_recovery --policy BIOS_PIBT.5 \
  --allocation-policy auction --robots 4 --seed 0
```

### 2.11 manager_dies

`src/scenarios.py:421-431`.

| Property | Value |
|---|---|
| Map | `classic`, 31×21 (inherits `dense_aisles`) |
| Robots | 8 (default) |
| Tasks | 32 pre-assigned |
| Duration | 600 s |
| Dynamic elements | `kill_manager_at = 60.0` |

**Designed to prove:** requirement 6 by contradiction — what a fleet with a central coordinator does when that coordinator dies. Under `central` the fleet parks; under `hierarchical` it falls back to peer-to-peer and keeps working at reduced plan quality. The number worth quoting is not "we survived" but how much throughput the fallback costs, which is the stated price of decentralisation.

**This scenario is inert under the default policy.** A fleet manager is only constructed when the route policy is one of `central`, `prioritized_space_time_astar` or `hierarchical`, or when the allocator is `hungarian` (`src/main.py:104`, `src/main.py:166-173`). The kill event is guarded by `manager is not None` (`src/main.py:226-227`). The CLI defaults are `BIOS_PIBT.6` and `auction_bundle` (`src/main.py:745-751`), neither of which builds a manager, so a default run of `manager_dies` is byte-identical in structure to `dense_aisles`. The policy must be named explicitly:

```bash
python -m src.main --scenario manager_dies --policy hierarchical \
  --allocation-policy preassigned --robots 8 --seed 0 --verbose
python -m src.main --scenario manager_dies --policy central \
  --allocation-policy preassigned --robots 8 --seed 0 --verbose
```

`--verbose` prints the kill at t=60.0 (`src/main.py:229-230`), which is how a judge confirms the event actually fired.

**Requirements:** 1, 6.

### 2.12 dead_zone_infra

`src/scenarios.py:434-454`, registered at `src/scenarios.py:833`.

| Property | Value |
|---|---|
| Map | `classic`, 31×21 (inherits `dense_aisles`) |
| Robots | 8 (default) |
| Tasks | 32 pre-assigned |
| Duration | 600 s |
| Dynamic elements | One geometric radio hole centred at cell (15.5, 10.5) with radius 5 cells (7 m); `peer_traffic_via_ap = True` |

**Designed to prove:** the honest half of the dead-zone finding. In infrastructure-mode 802.11 — the realistic default — peer frames are relayed by the access point, so a robot inside the hole loses its peers exactly as it loses the server, and a peer-to-peer topology buys nothing (`src/settings.py:129-133`).

**Requirements:** 1, 2, 3.

```bash
python -m src.main --scenario dead_zone_infra --policy BIOS_PIBT.6 \
  --allocation-policy preassigned --robots 8 --seed 0
```

### 2.13 dead_zone_mesh

Same builder with `mesh_radio=True`, registered at `src/scenarios.py:834`. Identical map, robots, tasks and duration; the only difference is `peer_traffic_via_ap = False` (`src/scenarios.py:450-451`).

**Designed to prove:** the other half. With a genuine peer link (802.11s, Wi-Fi Direct, UWB) the advantage of decentralisation appears. **The pair of runs is the finding, not either run alone:** the fix for dead zones is a different radio, not a different software topology, and the problem statement never names a link layer.

**Requirements:** 1, 2, 3, 6.

```bash
python -m src.main --scenario dead_zone_infra --policy BIOS_PIBT.6 \
  --allocation-policy preassigned --robots 8 --seed 0
python -m src.main --scenario dead_zone_mesh --policy BIOS_PIBT.6 \
  --allocation-policy preassigned --robots 8 --seed 0
```

**Caution:** neither dead-zone scenario is covered by any checked-in campaign or end-to-end test (see [§9](#9-coverage-gaps-and-cautions)). The paired run above must be executed by hand.

### 2.14 seed_99_congestion

`src/scenarios.py:609-665`. Covered in detail in [§7](#7-seed-99).

| Property | Value |
|---|---|
| Map | `classic`, 31×21, renamed `seed_99_congestion` |
| Robots | Fixed at 6; construction raises `ValueError` for any other count or seed (`src/scenarios.py:623-628`) |
| Starts | (16,11) junction centre, (15,11), (17,11), (16,10), (16,12), (14,11) |
| Tasks | 6 announced, each picked up under one chassis, dropped on the opposite side of the cluster |
| Duration | 180 s |
| Dynamic elements | None injected; the congestion is the initial condition. Pose noise 0, all batteries 0.90 |

**Requirements:** 1, 9, 10, 14, 19.

```bash
python -m src.main --scenario seed_99_congestion --policy BIOS_PIBT.6 \
  --allocation-policy auction_bundle --robots 6 --seed 99 --duration 180
```

`--robots 6 --seed 99` are mandatory; omitting either raises immediately (`src/scenarios.py:623-628`).

### 2.15 showcase_open_floor

`src/scenarios.py:722-737`, profile at `src/scenarios.py:795-800`.

| Property | Value |
|---|---|
| Map | `open`, 22×15, 330 free cells, no racks, 0 corridor blocks |
| Robots | 4; builder seed 4 |
| Tasks | 8 announced; alternating left-edge ↔ right-edge, energy-aware cargo mix |
| Duration | 240 s in the builder; the profile advertises 180 s |
| Dynamic elements | Battery ladder 0.48 / 0.62 / 0.74 / 0.86 (`src/scenarios.py:670`) |

**Designed to prove:** the energy admission gate visually — identical AMRs rejecting jobs they cannot finish and self-selecting the best battery-feasible task, on a map with no traffic conflict to confound the picture.

**Requirements:** 1, 14, 18.

```bash
python -m src.main --scenario showcase_open_floor --policy BIOS_PIBT.6 \
  --allocation-policy auction_bundle --robots 4 --seed 4 --duration 180
```

### 2.16 showcase_chokepoint

`src/scenarios.py:740-743`, profile at `src/scenarios.py:801-806`. `crossing_chokepoint` with the showcase cargo and battery profile applied.

| Property | Value |
|---|---|
| Map | `chokepoint`, 25×9, one 13-cell single-file corridor |
| Robots | 4; builder seed 7 |
| Tasks | 8 announced (2 per robot) |
| Duration | 420 s in the builder; the profile advertises 320 s |

**Designed to prove:** priority negotiation at a narrow intersection — opposing robots ordering a single-file aisle with priority, yielding and expiring leases, visible frame by frame.

**Requirements:** 1, 9, 10, 11, 19.

```bash
python -m src.main --scenario showcase_chokepoint --policy BIOS_PIBT.6 \
  --allocation-policy auction_bundle --robots 4 --seed 7 --duration 320
```

### 2.17 showcase_human

`src/scenarios.py:746-751`, profile at `src/scenarios.py:807-812`.

| Property | Value |
|---|---|
| Map | `classic`, 31×21 |
| Robots | 5; builder seed 7 |
| Tasks | 10 announced |
| Duration | 600 s in the builder; the profile advertises 520 s |
| Dynamic elements | 3 workers on seeded shelf-inspection orders through the same rack aisles the AMRs use (`src/scenarios.py:276-344`) |

**Designed to prove:** cooperative mixed traffic. The worker routes are derived from rack geometry rather than baked screen coordinates, expanded to rack-safe loops by the same A\* the AMRs plan with (`src/world.py:272-281`), and each worker dwells at inspection points (`src/world.py:200-207`). The workers still broadcast nothing.

**Requirements:** 1, 2, 8, 19.

```bash
python -m src.main --scenario showcase_human --policy BIOS_PIBT.6 \
  --allocation-policy auction_bundle --robots 5 --seed 7 --duration 520
```

### 2.18 showcase_dead_zone

`src/scenarios.py:754-758`, profile at `src/scenarios.py:813-818`. `dead_zone(mesh_radio=True)` with the showcase profile.

| Property | Value |
|---|---|
| Map | `classic`, 31×21 |
| Robots | 6; builder seed 4 |
| Tasks | 6 announced (1 per robot) |
| Duration | 600 s in the builder; the profile advertises 650 s |
| Dynamic elements | Radio hole at cell (15.5, 10.5), radius 5 cells; genuine peer path (`peer_traffic_via_ap = False`) |

**Designed to prove:** degraded links, stale-lease expiry and recovery on a peer radio — the favourable half of the dead-zone pair, shown visually. Judges should be told that the infrastructure-mode counterpart exists and behaves differently ([§2.12](#212-dead_zone_infra)).

**Requirements:** 1, 2, 3, 6.

```bash
python -m src.main --scenario showcase_dead_zone --policy BIOS_PIBT.6 \
  --allocation-policy auction_bundle --robots 6 --seed 4 --duration 650
```

### 2.19 showcase_grand_challenge

`src/scenarios.py:761-791`, profile at `src/scenarios.py:819-824`. The only scenario that combines every dynamic mechanism.

| Property | Value |
|---|---|
| Map | `classic`, 31×21 |
| Robots | 10; builder seed 1 |
| Tasks | 20 announced, four-way cargo mix (normal / fragile / heavy / hazardous) with deadlines on every third task (`src/scenarios.py:671-676`, `700-716`) |
| Duration | 480 s in the builder; the profile advertises 800 s |
| Humans | 5 workers on non-overlapping two-aisle zones (`src/scenarios.py:310-316`) |
| Blocked aisle | `fallen-pallet` at cell (16,11), appears t=24.0 s, **cleared** t=78.0 s |
| Robot failure | `AMR03` fails at t=52.0 s, cold-restarts at t=96.0 s |
| Radio | 5% uniform packet loss plus a 4-cell dead zone at (19.22, 10.08), mesh path |

**Designed to prove:** that the mechanisms compose. The obstacle cell is deliberately chosen as a redundant junction of degree ≥ 3 rather than a degree-two articulation point, because physically partitioning the one-way circulation graph measures a different problem and can leave every policy gridlocked after the pallet has already cleared (`src/scenarios.py:773-780`). The restart builds a fresh brain with no shared process state, which must reconstruct its catalogue from peer gossip (`src/main.py:241-256`).

**Requirements:** 1, 2, 3, 7, 8, 9, 11, 12, 13, 14, 18, 19.

```bash
python -m src.main --scenario showcase_grand_challenge --policy BIOS_PIBT.6 \
  --allocation-policy auction_bundle --robots 10 --seed 1 --duration 800
```

---

## 3. Requirement to scenario evidence map

One row per requirement. "Metric" names the field in `PolicyResult` (`src/main.py:405-510`) that carries the evidence, so a judge can check the claim in the JSON rather than on a slide.

| # | Requirement | Scenario(s) | Command | Metric / evidence |
|---|---|---|---|---|
| 1 | At least 3 AMRs | Every scenario. Smallest fleets: `blocked_aisle` and `robot_failure_reassignment`, both floored at 3 (`src/scenarios.py:501`, `523`) | any command in [§2](#2-the-complete-catalogue) | `robots` (`src/main.py:409`) |
| 2 | Dynamic warehouse environment | `showcase_grand_challenge` (all four mechanisms); `blocked_aisle`, `human_in_aisle`, `robot_failure_reassignment`, `partition_recovery`, `dead_zone_*` singly | [§2.19](#219-showcase_grand_challenge) | `dynamic_obstacles_detected`, `seconds_degraded`, `robot_failures` (`src/main.py:428`, `493`, `509`) |
| 3 | Decentralized communication | Every scenario in the batch runner; real UDP multicast in `edge_demo.py`, which accepts any scenario id (`src/distributed_demo.py:321-322`) | `python edge_demo.py --scenario open_floor_control --robots 3 --duration 5 --port 26231` | `msgs_sent`, `bytes_sent`, `msgs_per_robot_s` (`src/main.py:494-496`) |
| 4 | Position sharing | Every scenario; heartbeats carry pose | any command in [§2](#2-the-complete-catalogue) | `heartbeat_messages_sent` (`src/main.py:435`) |
| 5 | Intent sharing | Every scenario; 6 cells of path published per INTENT (`src/settings.py:141`) | any command in [§2](#2-the-complete-catalogue) | `intent_messages_sent` (`src/main.py:436`) |
| 6 | No central coordination server | `manager_dies` (the single-point-of-failure demo); `dead_zone_mesh` | [§2.11](#211-manager_dies) — **requires `--policy hierarchical` or `central`** | `manager_killed_at` (`src/main.py:508`), `has_manager` in dashboard meta (`src/main.py:697`) |
| 7 | Multi-agent path planning | `dense_aisles`, `crossing_chokepoint`, `showcase_grand_challenge` | [§2.5](#25-dense_aisles) | `plan_calls`, `plan_cpu_mean_ms`, `replans` (`src/main.py:499-500`, `427`) |
| 8 | Collision avoidance | Every scenario; `human_in_aisle` is the hardest case because the hazard does not broadcast | [§2.7](#27-human_in_aisle) | `contacts_robot_robot`, `contacts_robot_human`, `min_separation_m`, `safety_stop_ticks` (`src/main.py:416-419`, `488`) |
| 9 | Real-time conflict resolution | `crossing_chokepoint`, `seed_99_congestion`, `showcase_chokepoint` | [§2.2](#22-crossing_chokepoint) | `yields`, `priority_decisions`, `priority_waits` (`src/main.py:426`, `502`, `506`) |
| 10 | Deadlock resolution | `seed_99_congestion` (six-way launch gridlock), `crossing_chokepoint` | [§7](#7-seed-99) | `deadlocks_detected`, `retreats`, `bios4_unstick` (`src/main.py:424-425`, `423`) — read the caveat in [§7](#7-seed-99) |
| 11 | Narrow intersection / chokepoint | `crossing_chokepoint`, `sih_acceptance_overlap`, `showcase_chokepoint`, `energy_acceptance` — all on the 13-cell single-file corridor | [§8](#8-the-acceptance-scenario) | `priority_waits`, `nonproductive_wait_ticks` (`src/main.py:506`, `434`) |
| 12 | Blocked aisle | `blocked_aisle` (pallet never cleared), `showcase_grand_challenge` (pallet cleared at t=78 s) | [§2.8](#28-blocked_aisle) | `dynamic_obstacles_detected` (`src/main.py:428`) |
| 13 | Re-routing | `blocked_aisle` — gate asserts `dynamic_reroutes >= 1` (`tests/test_resilience.py:22`) | [§2.8](#28-blocked_aisle) | `dynamic_reroutes`, `replans`, `predictive_reroutes` (`src/main.py:429`, `427`, `449`) |
| 14 | Task re-assignment | `robot_failure_reassignment` — gate asserts `task_reassignments >= 1` (`tests/test_resilience.py:35`); also `partition_recovery`, `showcase_grand_challenge` | [§2.9](#29-robot_failure_reassignment) | `task_reassignments`, `future_lease_expiries` (`src/main.py:430`, `459`) |
| 15 | Edge / local execution | **No scenario.** Two runners consume scenarios: `edge_demo.py` (one OS process per AMR, real HMAC-authenticated UDP multicast, `src/distributed_demo.py:154-168`) and `edge_node.py` (one node per machine, `src/edge_runtime.py:317-341`) | `python edge_demo.py --robots 3 --duration 5 --port 26231` | per-child PID, clock offset, session id, CPU time and max RSS in the report (`src/distributed_demo.py:63-73`) |
| 16 | Fleet dashboard | **No scenario.** The dashboard is `backend/server.py` plus `frontend/`; any scenario replayed through `POST /api/run` exercises it | `python backend/server.py`, then run any showcase | HTTP 200 payload with `map`, `frames`, `summary` (`src/main.py:681-722`) |
| 17 | Real-time positions on dashboard | **No scenario.** Positions are emitted per frame at 10 Hz (`src/settings.py:112`, `src/main.py:350-352`) | as row 16 | `frames[].robots[].x/y/th` (`src/world.py:878-884`); rendered at `frontend/js/main.js:1064-1092` |
| 18 | Battery status | `energy_acceptance` (starting charge 0.12→0.96), `showcase_open_floor` (energy gate), all showcases (battery ladder) | [§2.4](#24-energy_acceptance) | `frames[].robots[].batt` (`src/world.py:881`); `energy_bids_suppressed` (`src/main.py:432`); rendered at `frontend/js/main.js:1092`, `frontend/js/shell.js:183-190` |
| 19 | Zero inter-robot collisions | `sih_acceptance_overlap` is the pinned gate; every scenario reports the same counters | `python benchmark.py --seeds 30 --jobs 8` | `contacts_robot_robot`, `min_separation_m`, `p05_separation_m` (`src/main.py:416`, `419-420`); one-sided rate bound via `safety_report` (`src/main.py:788-794`) |
| 20 | ≥20% task-time reduction | `sih_acceptance_overlap` against `stop_and_wait`; `open_floor_control` as the falsification control | `python benchmark.py --seeds 30 --jobs 8` | `makespan_s`, `completed_all`, per-seed bound (`src/main.py:412-413`); acceptance rules in [12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md) |

Requirements **15, 16 and 17 have no dedicated scenario**. They are properties of the runners and of the dashboard, not of any warehouse layout, and the traceability matrix should record them that way rather than pointing at a scenario that does not exist. See [§9](#9-coverage-gaps-and-cautions).

---

## 4. The dynamic warehouse environment, mechanism by mechanism

Requirement 2 is easy to hand-wave. Here is exactly what changes during a run, and where each change is applied.

| Mechanism | Applied at | Scenario field | Which scenarios use it |
|---|---|---|---|
| A blocked aisle appears | `src/main.py:257-266` → `src/world.py:373-399` | `obstacles: list[ObstacleEvent]` (`src/scenarios.py:81`) | `blocked_aisle` (t=1.0 s, permanent), `showcase_grand_challenge` (t=24.0 s) |
| A blocked aisle clears | `src/main.py:267-270` → `src/world.py:401-402` | `ObstacleEvent.clear_at` (`src/scenarios.py:58`) | `showcase_grand_challenge` (t=78.0 s) |
| People walk the floor | `src/main.py:160-161` → `src/world.py:256-371`; stepped every tick at `src/world.py:455-631` | `humans: list[list[Cell]]` (`src/scenarios.py:70`) | `human_in_aisle` (1), `showcase_human` (3), `showcase_grand_challenge` (5) |
| A robot dies mid-task | `src/main.py:235-240`; the chassis stays as a stopped obstacle while its radio goes silent (`src/main.py:331-335`) | `robot_fail_at` (`src/scenarios.py:79`) | `robot_failure_reassignment` (AMR01 @ 2.0 s), `showcase_grand_challenge` (AMR03 @ 52.0 s) |
| A robot cold-restarts | `src/main.py:241-256`; a fresh `AMRBrain` with no shared process state | `robot_restart_at` (`src/scenarios.py:80`) | `showcase_grand_challenge` (AMR03 @ 96.0 s) |
| The network splits and heals | `src/main.py:231-234` | `partition_at`, `heal_at`, `partition_groups` (`src/scenarios.py:74-76`) | `partition_recovery` (split 2.0 s, heal 12.0 s) |
| The radio degrades uniformly | `src/main.py:121` applies `sc.net` to the run config | `net.loss` (`src/settings.py:126`) | `showcase_grand_challenge` (5%); the fault campaign sweeps 0/5/10/20% onto `sih_acceptance_overlap` (`src/fault_campaign.py:37-39`) |
| A geometric radio hole | `src/settings.py:129-133` | `net.dead_zones` | `dead_zone_infra`, `dead_zone_mesh`, `showcase_dead_zone`, `showcase_grand_challenge` |
| The coordinator dies | `src/main.py:226-230` | `kill_manager_at` (`src/scenarios.py:73`) | `manager_dies` — **only with a managed policy** ([§2.11](#211-manager_dies)) |
| Battery drains and recharges | `src/world.py:438-444`, every tick, at 210 W moving / 45 W idle / 900 W charging (`src/settings.py:39-41`) | `initial_battery_fracs` (`src/scenarios.py:88`) | `energy_acceptance`, `seed_99_congestion`, all five showcases |
| Localisation drifts | `src/main.py:337` → `src/world.py:749-752` | `pose_noise_m` (`src/scenarios.py:82`) | 0.02 m in `crossing_chokepoint`, `dense_aisles`, `sih_acceptance_overlap` and every showcase; 0 in the deterministic fault scenarios |

Two points a judge will press on:

**The obstacle is genuinely anonymous.** A robot's lidar returns a `Detection` with a position and a radius and no identity (`src/world.py:59-77`). It cannot tell a pallet from a peer from a person, so a protocol that resolves conflicts purely by exchanging intent is structurally blind to all three. That is why the reactive layer works off detections rather than the peer table.

**The people are physically real, not ghosts.** Workers are constrained to passable cells, refuse to walk through racks, pallets, AMRs or one another, and evaluate forward, reverse and bounded side-steps each tick before committing (`src/world.py:485-628`). Side-steps are bounded to 0.42 m from the mapped route (`src/world.py:44`, `src/world.py:572`) so accumulated avoidance drift cannot silently invent a new pedestrian path through an AMR lane.

---

## 5. Showcase profiles versus benchmark scenarios

The two families exist for different purposes and must not be confused in a results table.

| | Benchmark scenarios | Showcase profiles |
|---|---|---|
| Purpose | Measurement | Demonstration |
| Members | `crossing_chokepoint`, `dense_aisles`, `open_floor_control`, `sih_acceptance_overlap`, `energy_acceptance`, `blocked_aisle`, `robot_failure_reassignment`, `partition_recovery`, `manager_dies`, `dead_zone_infra`, `dead_zone_mesh`, `human_in_aisle`, `seed_99_congestion` | `showcase_open_floor`, `showcase_chokepoint`, `showcase_human`, `showcase_dead_zone`, `showcase_grand_challenge` |
| Workload | Whatever the builder pins | Rewritten by `_showcase_profile` (`src/scenarios.py:679-719`): auction forced on, a battery ladder applied, a four-way cargo mix applied, deadlines added to every third task |
| Registered | `src/scenarios.py:829-841` | `src/scenarios.py:794-825`, merged into `SCENARIOS` at `src/scenarios.py:842` |
| Surfaced by | CLI only | `GET /api/scenarios` (`backend/server.py:588-614`) and the dashboard gallery |

`_showcase_profile` explicitly leaves the pinned scientific scenarios alone and applies only to the public digital-twin experience (`src/scenarios.py:682-684`). A showcase result is therefore **not** a substitute for a benchmark result: the workload has been rewritten.

### The truncation warning

The profile dictionary carries `robots`, `seed` and `duration` (`src/scenarios.py:794-825`), but those values are **metadata for the gallery**, not defaults on the server. `parse_run_request` supplies its own defaults when the field is absent from the request body: **scenario `open_floor_control`, policy `BIOS_PIBT.6`, allocation `auction_bundle`, robots 4, seed 0, duration 120 s** (`backend/server.py:173-197`). `run_for_dashboard` then passes `robots` into the builder and unconditionally overwrites the builder's duration for any non-custom scenario (`src/main.py:657-662`).

So a bare `POST /api/run {"scenario": "showcase_grand_challenge"}` runs 4 robots for 120 s at seed 0 — not the 10 robots, seed 1 and 800 s the gallery advertises — and the response reports the truncated values in `meta.robots` and `meta.duration_s` without flagging the substitution.

| Showcase | Gallery profile | Builder default | Bare `POST /api/run` |
|---|---|---|---|
| `showcase_open_floor` | 4 robots · seed 4 · 180 s | 4 · 4 · 240 s | 4 · 0 · **120 s** |
| `showcase_chokepoint` | 4 · 7 · 320 s | 4 · 7 · 420 s | 4 · 0 · **120 s** |
| `showcase_human` | 5 · 7 · 520 s | 5 · 7 · 600 s | **4** · 0 · **120 s** |
| `showcase_dead_zone` | 6 · 4 · 650 s | 6 · 4 · 600 s | **4** · 0 · **120 s** |
| `showcase_grand_challenge` | 10 · 1 · 800 s | 10 · 1 · 480 s | **4** · 0 · **120 s** |

Robot count and seed agree between the profile and the builder; only the duration differs, and for three of the five showcases the gallery window is *shorter* than the builder's declared window. The browser UI copies all three profile fields into the form before submitting (`frontend/js/main.js:234-239`, `503-505`), so clicking a gallery card is safe; scripting the API directly is not. Always send `robots`, `seed` and `duration` explicitly. Endpoint details and validation bounds are in [10. HTTP API Reference](10-API-REFERENCE.md).

The checked-in showcase artifacts were produced with the gallery durations, which confirms the gallery value is what the evidence used: `duration_override_s` is 180.0, 320.0, 520.0 and 650.0 in `artifacts/benchmarks/bios6-showcase-open.json`, `-chokepoint.json`, `-human.json` and `-dead-zone.json` respectively. Note that those artifacts used seeds 0–2 (`first_seed: 0`, `seeds_per_fleet: 3`), **not** the profile seeds, so an artifact row and a gallery run are not the same workload.

### Seed 99 substitutes any scenario

One more silent substitution. `run_for_dashboard` treats seed 99 as a reserved presentation seed: *any* requested scenario submitted with `seed: 99` is replaced by `seed_99_congestion` (`src/main.py:580-586`). The response reports both names — `meta.scenario` and `meta.requested_scenario` — so the substitution is visible rather than hidden, and `tests/test_seed_99.py:44-55` asserts exactly this behaviour by requesting `showcase_grand_challenge` with 10 robots and receiving `seed_99_congestion` with 6.

---

## 6. The fault campaign

`python fault_campaign.py --seeds 30 --jobs 8` (`src/fault_campaign.py:132-167`). Every condition runs `BIOS_PIBT.5` under the `auction` allocator (`src/fault_campaign.py:46-48`), so the failure is the only variable.

| Condition | Scenario built | What is injected | Runs |
|---|---|---|---|
| Packet loss | `sih_acceptance_overlap(n_robots=4, seed=s)` with `net.loss` overridden (`src/fault_campaign.py:37-39`) | Uniform 0%, 5%, 10%, 20% loss on every datagram | 30 seeds × 4 loss levels |
| Partition then heal | `partition_recovery(n_robots=4, seed=s)` (`src/fault_campaign.py:40-41`) | Two isolated islands from t=2.0 s, healed at t=12.0 s | 30 seeds |
| Auction winner crashes | `robot_failure_reassignment(n_robots=3, seed=s)` (`src/fault_campaign.py:42-43`) | `AMR01` — the winner — dies at t=2.0 s; its lease expires and a peer takes over | 30 seeds |

**How recovery is measured.** Each condition is summarised by completion rate, median and p95 makespan, the three contact totals, and `task_reassignment_observations` (`src/fault_campaign.py:52-69`). Recovery is not a label; it is the completion count under the fault.

**Pass condition** (`src/fault_campaign.py:109-115`): every condition must complete every run, all three contact totals must be zero across all six summaries, **and** the robot-failure condition must record at least `seeds` task reassignments. The CLI exits 0 on pass and 2 on a completed failing gate (`src/fault_campaign.py:167`).

The checked-in result `artifacts/benchmarks/fault-campaign.json` records `verdict: pass` over 180 runs, with 60 reassignment observations in the robot-failure condition and zero contacts everywhere. Median makespans: 384.03 / 385.81 / 392.12 / 388.12 s across the loss sweep, 19.38 s for partition-heal and 47.28 s for the crashed winner. These match `archive/FAULT_CAMPAIGN.md`.

**`dead_zone_infra` and `dead_zone_mesh` are not part of this campaign.** `src/fault_campaign.py:17-18` imports only `partition_recovery`, `robot_failure_reassignment` and `sih_acceptance_overlap`. The dead-zone pair is a manual two-command experiment ([§2.13](#213-dead_zone_mesh)) with no automated pass condition and no checked-in result; `dead_zone_infra` is referenced nowhere in the repository outside its own registration. Do not describe it as campaign-gated.

A separate three-way campaign, `python baseline_comparison.py --seeds 30 --fault-seeds 10 --jobs 8` (`src/baseline_comparison.py:426-440`), compares `BIOS_PIBT.6`+`auction_bundle`, `stop_and_wait_competition`+`preassigned` and `prioritized_space_time_astar`+`hungarian` across `sih_acceptance_overlap` at 4/6/8 robots plus five supporting cases (`src/baseline_comparison.py:324-338`). Note that its `open_floor` case builds `showcase_open_floor`, not `open_floor_control` (`src/baseline_comparison.py:55-57`).

---

## 7. Seed 99

`seed_99_congestion` (`src/scenarios.py:609-665`) is a six-AMR launch gridlock, fixed in every dimension.

**What it is for.** Six AMRs start on distinct, collision-free cells packed around one rack junction at (16,11). Each announced task has its pickup *under one chassis* and its drop on the opposite side of the cluster, so the decentralized auction naturally gives every AMR its local job without a dispatcher choosing the winners (`src/scenarios.py:613-616`). The moment the awards close, all six robots request occupied cells around the same junction and the traffic layer — not a scripted animation — has to establish an order, yield, and drain the jam.

**Why that seed specifically.** Seed 99 is reserved and kept out of the ordinary showcase defaults and benchmark seed ranges (`src/scenarios.py:45-50`). It is a deliberately adversarial fixed workload used to explain traffic coordination, *not* a favourable sample. Keeping it out of the aggregate is what stops a "Seed 99" number from being quoted as a performance claim. The builder enforces its own identity: any other robot count or seed raises `ValueError` (`src/scenarios.py:623-628`), so a replay for a judge cannot silently change meaning.

**How the evidence is derived.** `_seed_99_demo_evidence` (`src/main.py:513-564`) reads the recorded 10 Hz telemetry and counts an AMR as blocked when its traffic state is `blocked` or `retreat`, or when it names a non-empty wait-for owner (`src/main.py:527-533`). It reports peak simultaneous blockage, the timestamp of the measured 6/6 standstill, and the first frame afterwards in which fewer than six remain blocked. A scripted "deadlock resolved" label would prove nothing; this is the same state the Fleet panel displays.

**Pass condition** (`tests/test_seed_99.py:44-69`): full 6/6 gridlock observed, first release strictly after detection with latency ≤ 1.0 s, 6/6 tasks completed, makespan under 120 s, at least one yield, and zero contacts of all three kinds. `tests/test_seed_99.py:72-88` additionally requires that untouched `stop_and_wait` completes **zero** tasks on the identical workload with strictly more non-productive wait ticks.

**The counter caveat.** `deadlocks_detected` stays at zero by design. That counter records the later stale wait-for-cycle breaker; in this workload priority arbitration and cell gates release the standstill before it ages into that fallback. The correct line for a judge is "BIOS prevented the opening gridlock from becoming a persistent deadlock", not "the deadlock detector fired once" (`archive/SEED_99_CONGESTION_DEMO.md:38-42`).

Measured result from `archive/SEED_99_CONGESTION_DEMO.md:24-32`: 6/6 blocked at 0.72 s, first release at 1.22 s (0.50 s later), 6/6 tasks at 106.22 s, zero contacts of all three kinds, closest separation 1.188 m, 228 yield decisions — against 0/6 at the 180 s cutoff for both stop-and-wait baselines.

```bash
python -m src.main --scenario seed_99_congestion --policy BIOS_PIBT.6 \
  --allocation-policy auction_bundle --robots 6 --seed 99 --duration 180
python -m src.main --scenario seed_99_congestion --policy stop_and_wait \
  --allocation-policy auction_bundle --robots 6 --seed 99 --duration 180
python -m pytest -q tests/test_seed_99.py
```

---

## 8. The acceptance scenario

`sih_acceptance_overlap` (`src/scenarios.py:567-595`) is the scenario the success criteria are measured on.

**The map is the argument.** `chokepoint_warehouse(length=13)` (`src/environment.py:135-153`) produces a 25×9 grid: a 6×9 left bay, a 6×9 right bay, and exactly one connection between them — a 13-cell single-file corridor along row y=4, 18.2 m long and 1.4 m wide. `corridors()` confirms one controlled block of 13 cells, and 13 exceeds the 6-cell threshold at which block control engages (`src/settings.py:152`).

**Why this is the right test of "overlapping paths".** The problem statement asks for a ≥20% reduction in total task completion time "compared to traditional stop-and-wait on overlapping paths". Overlap has to be *forced*, not hoped for:

- Every task's pick is in one bay and its drop is in the other (`src/scenarios.py:361-368` via `crossing_chokepoint`), so every route without exception traverses the same 13 cells. There is no second aisle to escape into.
- Directions alternate task by task (`src/scenarios.py:366-367`), so the corridor is contested from both ends simultaneously rather than degenerating into a one-way queue.
- Start cells alternate between bays (`src/scenarios.py:376-381`), so the contest begins immediately instead of after a warm-up transient.
- Three tasks per robot means each robot must make six corridor transits, so the result reflects sustained contention rather than one lucky crossing.

On a map with an alternate route, a policy can "win" by dispersing traffic, and the number measures the map. Here it cannot; the only remaining variable is coordination. That is the whole reason the scenario looks artificial: an easier map would make the 20% claim vacuous.

**Both allocation modes carry the same work.** The scenario populates `assignments` (round-robin queues) *and* `unassigned` (the same twelve tasks as an announced catalogue) from identical `Task` objects (`src/scenarios.py:579-591`). A route-policy comparison uses the queues; an allocation-policy run uses the catalogue; the workload fingerprint covers whichever one the run actually consumed (`src/scenarios.py:129-139`). This is what lets baseline and candidate be paired without the allocator becoming a hidden second variable.

**The censoring caveat is built into the scenario.** Pure stop-and-wait can settle into a permanent head-on wait and never finish, so the 1200 s window is a cutoff, not a makespan. `run_scenario` records `completed_all: false` and reports the cutoff rather than substituting it for a makespan (`src/main.py:410-413`), and the benchmark converts this into a conservative right-censored lower bound instead of an exact speedup (`src/scenarios.py:572-575`).

**Gate:** `python benchmark.py --seeds 30 --jobs 8` sweeps fleets 4, 6 and 8 with 30 seeds each, `stop_and_wait` versus `BIOS_PIBT.5` under `auction`, requiring 20% (`src/benchmark.py:269-281`). Every fleet must satisfy all four acceptance rules or the run exits 2. Full acceptance rules, the censoring arithmetic and the measured bounds are in [12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md).

```bash
python -m src.main --scenario sih_acceptance_overlap --policy stop_and_wait \
  --allocation-policy auction --robots 4 --seed 0
python -m src.main --scenario sih_acceptance_overlap --policy BIOS_PIBT.5 \
  --allocation-policy auction --robots 4 --seed 0
python benchmark.py --seeds 30 --jobs 8
```

---

## 9. Coverage gaps and cautions

Stated plainly, because the traceability matrix depends on it.

### Requirements with no scenario evidence

| # | Requirement | Why there is no scenario | What to cite instead |
|---|---|---|---|
| 15 | Edge / local execution | This is a property of the *runner*, not of a warehouse layout. Both edge runners take `--scenario` as an input | `edge_demo.py` (`src/distributed_demo.py:154-168`, `321-322`) and `edge_node.py` (`src/edge_runtime.py:317-341`); measured CPU time, max RSS and platform per process (`src/distributed_demo.py:63-73`); [08. Edge Deployment](08-EDGE-DEPLOYMENT.md) |
| 16 | Fleet dashboard | The dashboard is a service, not a workload | `backend/server.py:616-647`, `frontend/`; [09. Dashboard](09-DASHBOARD.md) |
| 17 | Real-time positions on dashboard | Telemetry is emitted by every run at 10 Hz regardless of scenario | `src/world.py:875-884`, `src/main.py:186-221`, `350-352`; [09. Dashboard](09-DASHBOARD.md) |

Requirements 3, 4, 5 and 6 are similar in kind but do have scenario-level evidence: no scenario *proves* decentralisation on its own, but `manager_dies` demonstrates the single point of failure ([§2.11](#211-manager_dies)), `dead_zone_mesh` demonstrates a genuine peer path ([§2.13](#213-dead_zone_mesh)), and every scenario reports the peer message counters. The structural argument — that a decentralized policy never constructs a `FleetManager` at all (`src/main.py:166-173`) — belongs to [03. Decentralized Protocol](03-DECENTRALIZED-PROTOCOL.md).

### Scenarios with no automated gate

| Scenario | Status |
|---|---|
| `manager_dies` | No test, no campaign. Also inert under the default policy ([§2.11](#211-manager_dies)) |
| `energy_acceptance` | No test, no campaign; runnable from the CLI only |
| `dead_zone_infra` | Referenced nowhere outside its own registration at `src/scenarios.py:833` |
| `dead_zone_mesh` | One dashboard smoke test (`tests/test_dashboard.py:398`) and one comparison in `archive/BIOS_PIBT_2_PROTOCOL.md:140`; no pass condition |
| `crossing_chokepoint` | Used heavily as a fixture in `tests/test_bios4.py`, but has no acceptance gate of its own — the gate lives on `sih_acceptance_overlap` |
| `dense_aisles` | Used as a dashboard/core fixture (`tests/test_core.py:468-516`); no acceptance gate |

Gated scenarios: `blocked_aisle`, `robot_failure_reassignment`, `partition_recovery`, `human_in_aisle`, `open_floor_control` (`tests/test_resilience.py`), `sih_acceptance_overlap` (`tests/test_resilience.py:53-66` plus `benchmark.py`), `seed_99_congestion` (`tests/test_seed_99.py`), and the five showcases (`tests/test_dashboard.py`).

### Two documentation defects found while writing this

1. `archive/SIH_ACCEPTANCE_BENCHMARK.md:95` states that the checked-in JSON records `git_commit` as `b1d3c82445cc32a8cbbf78331dfef462999a4e8a`. The file `artifacts/benchmarks/sih-acceptance.json` actually records `781a4dfc2b3ae09e68768bd0453ad3443d56b520` (`Promote BIOS 5 cargo-aware auction`); the string `b1d3c82…` does not appear in the artifact at all. Both are real commits in this repository, so one of the two is stale.
2. `artifacts/benchmarks/bios6-grand-challenge.json` records an **8**-robot Grand Challenge campaign in which seed 0 completed 15/16 tasks and was right-censored at the 800 s cutoff. `archive/HUMAN_FLOW_AUDIT.md:92-105` reports a later **10**-robot campaign at 20/20 for seeds 0–4. Quote the 10-AMR campaign; the 8-AMR JSON is superseded and should not be shown as the current Grand Challenge result.

---

**Siblings:** [README](README.md) · [00. Problem Statement](00-PROBLEM-STATEMENT.md) · [01. Requirements Traceability](01-REQUIREMENTS-TRACEABILITY.md) · [02. Architecture](02-ARCHITECTURE.md) · [03. Decentralized Protocol](03-DECENTRALIZED-PROTOCOL.md) · [04. Path Planning](04-PATH-PLANNING.md) · [05. Coordination Policies](05-COORDINATION-POLICIES.md) · [06. Task Allocation](06-TASK-ALLOCATION.md) · [07. Safety](07-SAFETY.md) · [08. Edge Deployment](08-EDGE-DEPLOYMENT.md) · [09. Dashboard](09-DASHBOARD.md) · [10. HTTP API Reference](10-API-REFERENCE.md) · [12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md) · [13. Testing](13-TESTING.md) · [14. Findings](14-FINDINGS.md) · [15. Limitations](15-LIMITATIONS.md) · [16. Demo Runbook](16-DEMO-RUNBOOK.md)
