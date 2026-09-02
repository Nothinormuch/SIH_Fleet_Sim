# 05. COORDINATION POLICIES

> This document establishes what each of the thirteen route-coordination policies in this repository actually does, which of them are baselines, which one ships, and what evidence exists that any of them resolves a conflict, a deadlock or a chokepoint.

**Audience:** SIH judges and BEL evaluators reading the coordination claim for the first time, and teammates who have to defend a specific policy branch live.
**Reads best after:** [04. Path Planning](04-PATH-PLANNING.md) — the search algorithms (A*, space-time A*, PIBT) are defined there. This document covers what happens *after* a path exists and two robots want the same cell.

Everything below is measured at commit `4a8186e` (branch `main`) unless stated otherwise. Behavioural claims carry a `path/file.py:LINE` citation; where a claim could not be verified it says so.

## Requirements evidenced

| # | Requirement | Where | Evidence |
|---|---|---|---|
| 3 | Decentralized communication | [§4](#4-chokepoint-handling-end-to-end-req-11) | `src/amr.py:5021`, `src/amr.py:2366` |
| 5 | Intent sharing | [§4](#4-chokepoint-handling-end-to-end-req-11) | `src/amr.py:5084`, `src/messages.py:501` |
| 6 | No central coordination server | [§1.1](#11-how-set-membership-is-derived), [§2.4](#24-central-and-hierarchical--the-centralised-comparators) | `src/main.py:104`, `src/main.py:166` |
| 8 | Collision avoidance | [§2.1](#21-one-class-one-follower-one-safety-layer) | `src/amr.py:868`, `src/amr.py:603` |
| 9 | Real-time conflict resolution | [§3](#3-deadlock-resolution-req-10), [§6](#6-the-bios_pibt-lineage) | `src/amr.py:1126`, `src/amr.py:1827` |
| 10 | Deadlock resolution | [§3](#3-deadlock-resolution-req-10) | `src/amr.py:2227`, `src/amr.py:2143`, `src/amr.py:2494` |
| 11 | Narrow intersection / chokepoint handling | [§4](#4-chokepoint-handling-end-to-end-req-11) | `src/amr.py:1463`, `src/amr.py:2320`, `src/amr.py:1690` |
| 13 | Re-routing | [§3.5](#35-escalations-that-are-not-deadlock-breaking) | `src/amr.py:2693`, `src/amr.py:1367` |
| 15 | Edge / local execution | [§2.1](#21-one-class-one-follower-one-safety-layer) | `src/amr.py:547`, `src/priority.py:84` |
| 20 | ≥20% task-time reduction vs stop-and-wait | [§2.2](#22-stop_and_wait--the-comparator-the-success-criterion-names) | `src/amr.py:1151`, `src/metrics.py:289` |

Requirements 1, 2, 4, 7, 12, 14, 16, 17, 18 and 19 are carried by sibling documents; see [01. Requirements Traceability](01-REQUIREMENTS-TRACEABILITY.md).

---

## 1. The policy inventory

There are exactly thirteen route policies. The list is a tuple, `POLICIES`, at `src/amr.py:86-87`; both the brain (`src/amr.py:155`) and the headless runner (`src/main.py:116`) reject anything not in it, so the tuple is the authority and not a convention.

| Policy id | Mechanism, in one line | Coordination | Role | Defined |
|---|---|---|---|---|
| `stop_and_wait` | Is the next cell occupied by a lidar detection? Then stop. No intent, no priority, no negotiation. | Peer-to-peer (heartbeats only) | Baseline — the success criterion's comparator | `src/amr.py:63`, branch at `src/amr.py:1151` |
| `stop_and_wait_competition` | Same next-cell occupancy test on a bounding-box check, plus event-driven waiting and a purely local detour after 4 s of persistent block. | Peer-to-peer (heartbeats only) | Baseline — the strong non-cooperative comparator | `src/amr.py:64`, branch at `src/amr.py:1161` |
| `central` | Robot owns no autonomy above Layer 0: it asks the fleet manager for a route every tick and follows the schedule. Manager unreachable → it parks. | Managed | Baseline — the single-point-of-failure demonstration | `src/amr.py:65`, branch at `src/amr.py:1131` and `src/amr.py:2580` |
| `prioritized_space_time_astar` | Same as `central`, but the manager runs prioritised space-time A* and returns earliest-entry times per cell that the follower honours as a schedule. | Managed | Baseline — the centralised reference planner | `src/amr.py:66`, `src/fleet_manager.py:33`, `src/amr.py:1452` |
| `hierarchical` | Uses the manager's schedule when one is fresh; falls back to peer-intent yielding and block tokens when it is not. | Managed, degrades to peer-to-peer | Baseline — central-plus-peer, the architecture the PS criticises | `src/amr.py:67`, `src/main.py:104`, `src/amr.py:1231` |
| `BIOS_1.0.0` | Peer-intent yielding plus block tokens, with a hard liveness valve: held still longer than `bios_unstick_s` → step into any free adjacent cell. | Peer-to-peer | Experimental — the liveness-first ancestor | `src/amr.py:68`, valve at `src/amr.py:1325` |
| `decentralized` | Identical branch coverage to `BIOS_1.0.0` (every conditional naming one names the other) minus the Layer-0 creep permission. | Peer-to-peer | Baseline — "peer intent, no manager" control | `src/amr.py:74`; see the defect at [§3.1](#31-panic-on-stick-the-liveness-valve) |
| `BIOS_PIBT.1` | Adds replicated PIBT: every robot reconstructs the same fleet snapshot from heartbeats and runs the same deterministic priority-inheritance resolver. | Peer-to-peer | Experimental | `src/amr.py:69`, resolver at `src/priority.py:84`, call site `src/amr.py:1827` |
| `BIOS_PIBT.2` | Adds a directed one-way circulation graph and a two-phase lease on *every* destination cell, so head-on edges cannot exist on rack maps. | Peer-to-peer | Experimental | `src/amr.py:70`, `src/amr.py:1985`, `src/amr.py:1664` |
| `BIOS_PIBT.3` | Adds a replicated batch auction, drop-cell capacity and immutable directional corridor waves; never injects a physical reverse into live traffic. | Peer-to-peer | Experimental | `src/amr.py:71`, `src/amr.py:2019`, `src/amr.py:1367` |
| `BIOS_PIBT.5` | Adds an energy-feasibility admission gate to bidding and a cargo-aware task order. Motion layer unchanged from `.3`. | Peer-to-peer | Production (frozen comparison baseline) | `src/amr.py:72`, `src/amr.py:4114` |
| `BIOS_PIBT.6` | Adds event-triggered communication, a decaying per-edge congestion memory shared between peers, short-horizon occupancy forecasting, charger-aware dock choice and a bounded decision log. | Peer-to-peer | **Production — the shipped default** | `src/amr.py:73`; V6 branches at `src/amr.py:660-864` |
| `BIOS_4` | A 549-parameter MLP that chooses one of five verbs the fleet already implements — proceed, hold, yield, claim, reroute — at the 10 Hz traffic layer. It does not drive the wheels. | Peer-to-peer | Learned / research | `src/amr.py:77`, `src/bios4.py:126`, branch at `src/amr.py:1147` |

`_BIOS_FAMILY` at `src/amr.py:78` is defined and never referenced anywhere in `src/`. It is dead code, not a set with semantics.

### 1.1 How set membership is derived

Policy behaviour is not selected by thirteen independent implementations. It is selected by membership in six nested tuples, built transitively at `src/amr.py:78-87`:

```
ENERGY_AUCTION_POLICIES = (.5, .6)
V3_AUCTION_POLICIES     = (.3, *ENERGY_AUCTION_POLICIES)          -> (.3, .5, .6)
DIRECTED_POLICIES       = (.2, *V3_AUCTION_POLICIES)              -> (.2, .3, .5, .6)
PIBT_POLICIES           = (.1, *DIRECTED_POLICIES)                -> (.1, .2, .3, .5, .6)
DECENTRAL_POLICIES      = (BIOS_1.0.0, decentralized, *PIBT_POLICIES, BIOS_4)
CENTRAL_POLICIES        = (central, prioritized_space_time_astar)
STOP_WAIT_POLICIES      = (stop_and_wait, stop_and_wait_competition)
```

The consequence is that a feature added to `.3` is inherited by `.5` and `.6` without either being named. When `src/amr.py:1367` says "V3 never injects a physical reverse into live traffic", that rule binds `.5` and `.6` too. This is why the lineage in [§6](#6-the-bios_pibt-lineage) is cumulative rather than a set of alternatives, and it is the single most important thing to understand before reading any policy branch: **there is no `if policy == "BIOS_PIBT.5"` anywhere in the traffic layer.** `.5` differs from `.3` only in the allocation layer.

`MANAGED_POLICIES` lives in the runner rather than the brain, at `src/main.py:104`, and is `(*CENTRAL_POLICIES, POLICY_HIERARCHICAL)` — three policies. It is the answer to one question asked in two places: whether `run_scenario` builds a `FleetManager` at all (`src/main.py:166`), and whether the dashboard should treat a missing manager as a failure or as the design (`src/main.py:697`). **A policy absent from that tuple has no fleet manager in the process at all** — not a disabled one, not an idle one. That is the mechanical content of requirement 6 for the ten remaining policies.

### 1.2 What is actually running by default

| Surface | Default policy | Cited |
|---|---|---|
| CLI (`python -m src.main`) | `BIOS_PIBT.6` | `src/main.py:745` |
| Dashboard `POST /api/run` | `BIOS_PIBT.6` | `backend/server.py:174` |
| Dashboard policy dropdown preselect | `BIOS_PIBT.6` | `frontend/js/main.js:78` |
| Brain constructor, when nothing is passed | `hierarchical` | `src/amr.py:151` |

The constructor default is a historical artefact and is overridden by every caller in the repository (`src/main.py:153`). It is worth knowing because a unit test that constructs an `AMRBrain` without naming a policy is exercising the *hierarchical* branch, not the shipped one.

`archive/BIOS_PIBT_5_ENERGY_AUCTION.md` still opens with "`BIOS_PIBT.5` is the default software policy". **That statement is stale**; `.6` has been the default on all three surfaces since its promotion, which `archive/BIOS_PIBT_6_PREDICTIVE_INTELLIGENCE.md` records correctly.

All thirteen policies are offered to the dashboard — the server sends `sorted(POLICIES)` at `backend/server.py:612`. The frontend supplies friendly labels for only nine of them (`frontend/js/main.js:287-297`); `BIOS_1.0.0`, `BIOS_PIBT.1`, `BIOS_PIBT.2` and `BIOS_4` appear in the dropdown under their raw identifiers.

---

## 2. The baselines, and the experimental design that makes them mean anything

### 2.1 One class, one follower, one safety layer

Every one of the thirteen policies is a *field* on a single class, not a separate implementation. `AMRBrain.__init__` stores `self.policy` at `src/amr.py:161` and nothing else about the policy is configured. The reason is stated in the module docstring at `src/amr.py:25-30` and it is the load-bearing claim of the whole benchmark:

- Every policy runs the same trajectory follower, `_follow` at `src/amr.py:4822`.
- Every policy runs the same protective-stop layer, `_safety` at `src/amr.py:868`, and it runs **last**: `src/amr.py:602-603` calls `_follow` then `_safety`, in that order, on every 50 Hz tick. Layer 0 has final authority over the actuation regardless of what any coordination layer decided (requirement 8).
- Every policy runs the same 50 Hz world integration and the same swept-contact detector, because `run_scenario` steps them identically (`src/main.py:107`).
- Robots are stepped in sorted id order and all of them read sensors from the same pre-step world state (`src/main.py:9-20`), so no robot gets a physically impossible information advantage.

The methodological consequence is the only reason a throughput number is attributable: since the chassis, the follower, the safety envelope and the physics are byte-identical across policies, a makespan difference between two of them **cannot** be caused by a better-tuned controller. It is caused by coordination or by nothing.

The same discipline is applied to task allocation. The headline route-policy comparison uses pre-assigned round-robin queues (`src/amr.py:232-236`) precisely so that a makespan difference cannot be caused by *who got which job*. Allocation is a separately selectable axis — see [06. Task Allocation](06-TASK-ALLOCATION.md).

### 2.2 `stop_and_wait` — the comparator the success criterion names

Requirement 20 asks for a ≥20% reduction in total task completion time against traditional stop-and-wait. `stop_and_wait` is that comparator and it is implemented faithfully rather than as a straw man.

Its entire decision rule is `_traffic_ahead` at `src/amr.py:1414-1435`: take the next path cell, and hold if any lidar *detection* quantises into it. Two design choices make it an honest baseline rather than a rigged one:

- It reads **detections, not clearance** (`src/amr.py:1432-1434`). A version that halted for anything within two metres would stop in front of every shelf and lose to everything; that would prove nothing.
- It shares heartbeats but **not intent**. `_broadcast` short-circuits for `STOP_WAIT_POLICIES` and `CENTRAL_POLICIES` at `src/amr.py:5023-5035`, emitting pose and battery so the dashboard and the manager work, and deliberately withholding the `INTENT` message. Lending the baseline our own mechanism would flatter our result.

Its pathology is the real one and it is a reported result, not a rigged one: two robots meeting head-on in a single-file aisle each find the other in their next cell, both stop, and neither has any mechanism to break the tie. Measured on `crossing_chokepoint`, 4 robots, seed 0, 300 s: **0 of 8 tasks completed, 0 contacts.** The baseline is safe. It is also stationary.

Because the baseline does not finish, a naive makespan ratio is undefined. `compare_paired` at `src/metrics.py:289-299` handles this explicitly: when the candidate finishes and the baseline reaches the fixed cutoff, it reports `1 - candidate/cutoff` as a **conservative lower bound** and refuses to produce a percentage at all for mismatched workloads or a candidate timeout. The right-censoring argument belongs to [12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md); what matters here is that the comparator is a real algorithm with a real failure mode, and the failure is measured rather than asserted.

### 2.3 `stop_and_wait_competition` — the strong non-cooperative baseline

Beating a baseline that never moves is a weak result. `stop_and_wait_competition` (`src/amr.py:1161-1178`) is the harder comparator: it keeps the strictly non-cooperative decision rule — "is my next cell occupied?", evaluated by bounding box at `src/amr.py:1437-1450` — but adds two improvements that borrow nothing from BIOS:

1. **Event-driven waiting.** The block start time is latched when the held cell changes (`src/amr.py:1170-1172`) instead of re-deciding from scratch.
2. **A persistent-block local detour.** After `stop_wait_persistent_s` = 4.0 s (`src/settings.py:163`) on the same cell, and no more often than every 3.0 s, it penalises that cell and replans a purely local A* route (`src/amr.py:2652-2661`).

It uses no peer intent, no priority, no lease and no reservation. On the same pinned chokepoint run it also completes **0 of 8** — the detour does not help when the only route between the bays is the contested corridor.

### 2.4 `central` and `hierarchical` — the centralised comparators

The problem statement treats centralisation as the flaw. The repository's position, stated at `src/fleet_manager.py:1-22`, is that nobody actually runs stop-and-wait, and that every deployed fleet runs a reservation-based fleet manager on an on-prem box on the same LAN. Beating only the weak baseline proves nothing to an evaluator who knows the field, so the strong one is implemented and reported against.

- **`central`** never plans locally. `_route_loop` at `src/amr.py:2589-2595` emits a `PLAN_REQ` and returns; there is no local A* fallback. When the manager is unreachable, `src/amr.py:2580-2588` clears the path and sets the robot to `blocked`. That is the single-point-of-failure claim, demonstrated rather than argued: a purely centralised fleet with an unreachable manager does not degrade, it parks.
- **`prioritized_space_time_astar`** is the same policy with a stronger planner behind it — the manager returns earliest-entry timestamps per cell, and `_schedule_holds` at `src/amr.py:1452-1461` makes the follower honour them. Without those times the robot would collapse the planned waits and sail straight through the conflict they were inserted to avoid.
- Neither central policy layers peer negotiation on top. `_traffic_loop` returns immediately for `CENTRAL_POLICIES` at `src/amr.py:1131-1137`, with the comment that adding it "would quietly hand the baseline some of our own mechanism and flatter our result".
- **`hierarchical`** is the hybrid, and the interesting one. It requests a schedule every tick the manager is reachable (`src/amr.py:2676-2679`), and the flag `coordinated` at `src/amr.py:1231` — mode is `CENTRAL_OK` **and** a schedule is in hand — suppresses all local peer negotiation. The comment at `src/amr.py:1224-1230` records why: running both at once was measured at roughly *half* the throughput of the central baseline it was supposed to match, because every robot deferred to plans the optimiser had already deconflicted. That is what makes it a hierarchy rather than two coordination schemes fighting.

A judge asking "why keep the centralised baselines in a decentralisation submission?" gets a two-part answer: they are the honest benchmark, and `hierarchical`'s degraded path *is* Layer 2 of the shipped architecture.

---

## 3. Deadlock resolution (req 10)

There are four distinct mechanisms, and they break different classes of deadlock at different costs. None of them is a general solution and the repository does not claim one.

| Mechanism | Deadlock class it breaks | Cost | Where |
|---|---|---|---|
| Panic-on-stick (liveness valve) | Any configuration where a robot is held still and a free adjacent cell exists — including ones no protocol can see | Motion off the planned route; a replan; possible oscillation | `src/amr.py:2227` |
| Block token | Prevents rather than resolves: two robots committing to opposite mouths of a single-file aisle | One propagation round per entry, and same-direction convoys are serialised | `src/amr.py:2320`, `src/amr.py:1463` |
| Wait-for graph + cycle detection | A closed cycle of robots each blocked on the next | Needs global state approximated from broadcasts; fails exactly where partitions make deadlock likeliest | `src/amr.py:2494`, `src/amr.py:2143` |
| PIBT priority inheritance + backtracking | A high-priority robot blocked by a low-priority occupant that has somewhere else to go | O(depth) recursion per tick; proves cell endpoints, not swept trajectories | `src/priority.py:84` |
| Livelock escalation | Not deadlock — the *result* of breaking one badly | Throws away the plan and all learned penalties | `src/amr.py:2686-2691` |

### 3.1 Panic-on-stick: the liveness valve

This is `BIOS_1.0.0`'s defining safeguard and the only mechanism here that needs no agreement with anybody.

The trigger is in `_traffic_loop` at `src/amr.py:1325-1328`: for `BIOS_1.0.0` and `decentralized`, once `blocked_since` is older than `cfg.traffic.bios_unstick_s` — **2.0 s** by default (`src/settings.py:168`) — `_bios_unstick` fires. The threshold is deliberately short; the comment at `src/settings.py:165-167` says the point is a liveness guarantee, not polite traffic theory.

The mechanism (`src/amr.py:2227-2302`):

1. Clear `blocked_since` and the hold flag first, so the valve cannot re-fire inside the same tick (`src/amr.py:2244-2245`).
2. Build the occupied set from every peer's reported cell **plus every peer's first intended cell** (`src/amr.py:2248-2252`), so the step cannot walk into a head-on swap with a robot about to move.
3. Reject any neighbour inside a controlled block someone else holds the token for (`src/amr.py:2258-2264`) — creeping into a locked aisle is exactly the pile-up the token exists to prevent.
4. Rank the survivors: prefer a perpendicular pull-aside over a reverse, prefer a step that still reduces goal distance, then prefer distance from the contested cell (`src/amr.py:2279-2286`).
5. Install a two-cell path to the winner and arm `self._creep_until = t + 6.0` (`src/amr.py:2288-2302`).

The liveness argument is the last two steps together. The target cell is free *by construction at the moment of choice* and adjacent, so the step is always executable — which means **no robot can settle permanently.** If there is genuinely no free neighbour, the fallback at `src/amr.py:2267-2273` makes the contested cell expensive and replans rather than standing still.

Step 5 is where the mechanism meets Layer 0, and this is a genuine defect. `_safety` refuses all forward motion when the omnidirectional guard is violated (`src/amr.py:890`), but permits a bounded creep — capped at 0.20 m/s and still throttled by the forward-cone speed limit — while `_creep_until` is live. The condition at `src/amr.py:892-896` is:

```python
timed_creep = (
    self.policy in (POLICY_BIOS, POLICY_BIOS4, *PIBT_POLICIES)
    and sensors.t < self._creep_until
    and act.v > 0.0
)
```

`POLICY_DECENTRALIZED` is not in that tuple. So `decentralized` arms `_creep_until` in `_bios_unstick` (`src/amr.py:2302`) and Layer 0 never honours it: its liveness valve chooses a free cell, installs the path, and is then vetoed by the omni guard for as long as a peer sits inside 0.45 m.

**This is measurable, and it is the whole difference between the two policies.** On `crossing_chokepoint`, 4 robots, seed 0, 300 s, identical pre-assigned workload:

| Policy | Tasks | Retreats | Robot-robot contacts | Min separation |
|---|---:|---:|---:|---:|
| `stop_and_wait` | 0/8 | 0 | 0 | 0.963 m |
| `stop_and_wait_competition` | 0/8 | 0 | 0 | 0.963 m |
| `BIOS_1.0.0` | **3/8** | 174 | 0 | 0.845 m |
| `decentralized` | **0/8** | 160 | 0 | 0.883 m |

`decentralized` executes essentially the same number of unstick manoeuvres as `BIOS_1.0.0` (160 vs 174) and completes none of the tasks, because the manoeuvres never translate. Grep confirms the two policies are named together in every conditional in the traffic layer (`src/amr.py:1325`, `src/amr.py:1534`) and differ nowhere else. Whether the omission at `src/amr.py:893` is intentional — keeping `decentralized` as a pure "peer intent, no recovery creep" control — or an oversight is **not verified**; nothing in the code or docs says. Either way, the policy as it stands demonstrates that the valve without Layer-0 cooperation is inert, which is a useful thing to be able to say out loud.

For `BIOS_4` the same valve is wired **above the model and outside its control**, at `src/amr.py:2406-2410`. A network that learned to always hold cannot suppress it. That is the difference between "we trained a model and it seems not to deadlock" and a liveness argument.

### 3.2 Block tokens: a corridor as a single mutex

A per-cell rule cannot help two robots that have already met halfway down a one-lane aisle. The block token prevents that state instead of resolving it (requirement 11; the end-to-end exchange is [§4](#4-chokepoint-handling-end-to-end-req-11)).

`corridors(env)` (`src/amr.py:172`) decomposes the map into maximal degree-two runs. `_controlled_block` at `src/amr.py:1690-1717` then decides which of them are worth controlling, and the threshold is empirical rather than assumed: `min_controlled_block` = **6 cells** (`src/settings.py:152`). The comment at `src/amr.py:1691-1698` records why — the standard warehouse has 24 four-cell picking aisles and 35 two-cell rack gaps, and applying block control to all 59 turned the floor into a series of toll gates and made the fleet *worse than doing nothing*. Short gaps have passing room at both ends and ordinary per-cell yielding resolves them at a fraction of the cost.

Ownership is answered by `_bios_lock` at `src/amr.py:2304-2318`, and the ordering matters: **physical presence outranks any claim.** If any peer's reported cell is inside the block, that peer is the owner with effectively infinite expiry, regardless of the token table. Only if the block is physically empty does an unexpired broadcast claim reserve it.

`_bios_claim` at `src/amr.py:2320-2380` maintains the token. It is called on **every** control tick for every decentralised policy (`src/amr.py:597-600`), not on the 10 Hz traffic tick, so a rival learns of the reservation as early as possible. The token is taken the moment the robot is cleared to enter, re-broadcast every 0.5 s while inside (`src/amr.py:2361`) with a TTL of `bios_claim_ttl_s` = 4.0 s (`src/settings.py:171`), and released the instant the robot leaves (`src/amr.py:2372-2380`). Releases are an optimisation: a lost `RELEASE` is repaired by TTL expiry, which is why the wire format carries a receiver-local duration rather than an absolute time (`src/messages.py:530-542`).

Two details are worth defending live:

- **The rank is frozen for the whole lease attempt** (`src/amr.py:2354-2359`). Recomputing it on every keep-alive would let a waiting robot's accumulating age repeatedly steal the token from the current winner before it could cross the mouth.
- **A robot queued behind another at the same mouth may not claim** (`src/amr.py:2344-2348`). Only a *gate* hold counts as a legitimate two-phase claim attempt.

`_block_conflict` at `src/amr.py:1463-1584` is where the token becomes a decision, and its asymmetry is the point:

- Someone already **inside** heading the other way → wait, *regardless of priority*. Priority cannot create space the aisle does not have; outranking an oncoming robot buys you a head-on stand-off deeper in.
- Someone **outranks you and wants in** → wait, this time on priority, because both are still outside and either could go first.
- Someone travelling **your way** → follow through. A block is not a naive one-robot-at-a-time mutex; that would serialise every picking aisle. The exception at `src/amr.py:1534-1542` is `BIOS_1.0.0`/`decentralized`, which *do* admit strictly one at a time, because a one-lane tunnel cannot take a convoy at standstill clearance.
- A peer queued **behind you at the same mouth** is not a contender (`src/amr.py:1547-1566`). Yielding to it would be a textbook priority inversion — the robot in front stops for the robot it is itself blocking — and ageing makes that certain rather than unlikely, because the one stuck at the back accrues priority fastest. So *position* decides among robots entering by the same mouth, and *priority* only decides between robots arriving at different mouths.

### 3.3 The wait-for graph and cycle detection

`_track_block` at `src/amr.py:2120-2141` maintains two fields on every hold: `blocked_since` and `blocked_on` — the id of whoever we are waiting for. `blocked_on` is republished in the heartbeat (`src/amr.py:5059`), which is what makes the wait-for graph a *distributed* structure: each robot contributes one edge and every robot can walk the chain.

`_find_cycle` at `src/amr.py:2494-2508` walks that chain from self and reports the cycle only if it returns to self. If the chain closes on somebody else, it returns `None` — that is a cycle, but not one we are part of, and acting on it would be interference. The walk is bounded by the peer count.

`_break_deadlock` at `src/amr.py:2143-2225` fires after `deadlock_wait_s` = 4.0 s of blocking (`src/settings.py:145`) and does three things in order:

1. Restart the clock (`src/amr.py:2163`), or the breaker re-fires every reactive tick and the fleet thrashes on replans instead of recovering.
2. If a cycle exists, every member computes the **same** loser from the same broadcast keys — the minimum key in the cycle (`src/amr.py:2169-2181`). No agreement protocol is needed because everyone runs the same deterministic function on the same data. `PIBT_POLICIES` use the rich `PriorityKey`; the rest use the scalar legacy key.
3. If we are not the loser, return. If we are — or there was no detectable cycle — penalise the contested cell and replan (`src/amr.py:2186-2196`). Only if no alternative route exists (a genuine single-file aisle) does the robot physically give way by reversing into the nearest free side cell via `_passing_bay` (`src/amr.py:2510-2543`), which prefers a *sideways* step over a reverse because reversing merely relocates the obstruction one cell down the lane the other robot needs.

Both caveats are stated in the code and should be stated to a judge (`src/amr.py:2147-2159`):

- **Cycle detection needs global state.** It is approximated from broadcasts, so it works exactly where the radio works and fails where partitions make deadlock most likely. This is not a decentralisation success story.
- **Breaking a cycle needs a total order, and ours ends in `robot_id`** (`src/priority.py:38`) — a number handed out by a central authority at commissioning. Every practical distributed scheme needs one; "no central server" is never literally true.

The observed value of `deadlocks_detected` is **0** on every run reported in this document. That is not evidence the detector is broken — it is evidence that the mechanisms in §3.1, §3.2 and §3.4 dissolve the configuration before a closed cycle forms and persists for four seconds.

### 3.4 PIBT's inherent liveness, and what it is not

`pibt_step` at `src/priority.py:84-196` is a dependency-free implementation of priority inheritance with backtracking. For the algorithm see [04. Path Planning](04-PATH-PLANNING.md); what matters here is what it contributes to deadlock resolution and what it does not.

What it contributes: a low-priority robot that is in the way does not merely wait — it **inherits** the requester's priority (`src/priority.py:121-124`) and recursively looks for somewhere to go, and if the chain has no legal end, the tentative assignments are rolled back from an explicit snapshot and the requester tries its next candidate (`src/priority.py:140-162`). Head-on edge swaps are banned outright (`src/priority.py:134-135`) and the result is defensively re-checked for one before it is returned (`src/priority.py:181-185`). Rotations of length ≥ 3 remain legal, which is what lets a ring of blocked robots turn over instead of jamming.

What it is not:

- It **refuses to output a partial configuration** (`src/priority.py:178-179`) and **rejects duplicate current cells** (`src/priority.py:101-102`). A contradictory or stale snapshot is not a licence to move; the caller catches the exception at `src/amr.py:1883-1887` and holds. Stopping is safer than inventing occupancy.
- It proves collision-free **cell endpoints, not swept continuous trajectories.** `_bios_pibt_coordinate` spends most of its length on that gap: a turning leader can close the gap even when the discrete configuration is valid (`src/amr.py:1918-1936`), so the follower is staged at its own centre first; a side-step is refused above 0.25 m/s because a differential-drive chassis cannot rotate instantly (`src/amr.py:1974-1976`); an authorised convoy transition arms the recovery creep because both chassis may already sit inside the conservative omni field (`src/amr.py:1938-1956`).
- The published liveness theorem does **not** apply. `archive/DECENTRALIZED_PRIORITY.md` is explicit: PIBT's finite-time reachability requires every adjacent vertex pair to lie on a simple cycle of length ≥ 3, a warehouse violates that at shelf spurs and dead ends, and the exPIBT movement restrictions that repair it are not implemented — only the exit-priority signal (`src/priority.py:34`, `src/topology.py`). The doc says the theorem "must not be claimed for this simulator". This document repeats that.

### 3.5 Escalations that are not deadlock breaking

Three timers exist above the deadlock timer, and confusing them with it is the commonest way to misread the traffic loop.

- **`block_wait_s` = 30.0 s** (`src/settings.py:148`). Queueing behind a robot legitimately driving through an aisle is traffic, not deadlock. A 13-cell block takes about 11 s to clear, so the 4 s deadlock timer would fire mid-transit and send healthy robots into give-way manoeuvres that create the problem they exist to fix (`src/amr.py:1218-1222`). `PIBT_POLICIES` go further and return outright rather than run the generic breaker on a block queue (`src/amr.py:1361-1366`).
- **`yield_aside_s` = 2.0 s** (`src/settings.py:144`). Waiting at a mouth is fine unless you are waiting *on the way out*: a robot queued at the entrance stands exactly where the robot inside must drive to leave, so the two wait for each other with no cycle to detect and no rule violated. Stepping aside is the only thing that breaks it (`src/amr.py:1329-1357`). `V3_AUCTION_POLICIES` are excluded from this branch.
- **`livelock_progress_s` = 12.0 s** (`src/settings.py:154`). Breaking a cycle by backing off converts deadlock into livelock, so `_route_loop` carries a separate no-progress timer that clears all learned penalties and throws the plan away (`src/amr.py:2686-2691`). The problem statement sets no liveness criterion; this repository sets one and measures against it.

`V3_AUCTION_POLICIES` — and therefore `.5` and `.6` — replace the physical retreat entirely: on a block timeout they penalise the contested cell and compute a new legal A* route (`src/amr.py:1367-1375`, requirement 13), preserving the directed-flow safety invariant. `BIOS_PIBT.2` does the same on circulation maps (`src/amr.py:1376-1385`). This is the single largest behavioural difference between the early and late lineage: **the modern policies do not reverse into live traffic.**

---

## 4. Chokepoint handling end to end (req 11)

Take the concrete case the benchmark is built on: `crossing_chokepoint` (`src/scenarios.py:359`), a warehouse whose two bays are joined by one 13-cell single-file corridor, with tasks alternating direction so the corridor is contested from both ends at once (`src/scenarios.py:366-368`). No policy can dodge the conflict by taking another aisle. Here is what actually crosses the wire, for a `BIOS_PIBT.5`/`.6` robot approaching the mouth.

**Continuously, at 5 Hz — `HEARTBEAT` (`HB`).** `_broadcast` at `src/amr.py:5021` emits pose, cell, battery, mode, state, current task, the scalar priority, `blocked_on`, the goal, and the seven-element frozen `PriorityKey` (`src/amr.py:5054-5061`). The key is latched at the moment of publication (`src/amr.py:5037-5043`) and arbitration compares *published* keys on both sides — comparing a live ageing value against a stale peer value is a bug this codebase has already paid for once, in the form of symmetric yielding where both robots believed they lost (`src/priority.py:26-29`, `src/amr.py:400-402`). Under `.6` this heartbeat is event-triggered: it fires on any signature change, at 5 Hz while a conflict is active, and otherwise decays to 0.3 s cruising / 0.6 s idle (`src/amr.py:677-687`, `src/settings.py:229-231`).

**Continuously — `INTENT` (`IN`).** The next `intent_horizon` = 6 cells (`src/settings.py:140`) *with time windows*, as receiver-local offsets because independent edge nodes have unrelated clock epochs (`src/messages.py:501-513`). The windows are not decoration: `_peer_intends` at `src/amr.py:1799-1817` only treats a peer as a contender if its window overlaps a 2 s horizon. Without them a robot yields to any peer whose route merely passes through the cell at some point, which in a busy aisle means yielding permanently — the classic way a naive intent protocol underperforms plain stop-and-wait.

**On approach — the admission decision.** At 10 Hz, `_traffic_loop` (`src/amr.py:1126`) calls `_block_conflict` (`src/amr.py:1214-1217`). It resolves, in order: any V2 per-cell lease conflict (`src/amr.py:1482`); whether we are already committed inside this block, in which case outside peers have no standing however high their priority (`src/amr.py:1499-1500`); whether the token is held (`src/amr.py:1502-1510`); and finally the contender scan described in §3.2.

**The commit round.** If nobody is contesting the block *right now*, the robot still does not enter. `src/amr.py:1568-1584` opens a gate: hold at the mouth for `gate_commit_s` = **0.45 s** (`src/settings.py:159`) — two heartbeat periods, long enough for our own intent to reach every peer and for theirs to reach us — then re-check. Any contender that appears during the round is resolved by the total order. Once admission is granted it is **latched** in `_gate_committed` (`src/amr.py:1515-1526`), because without the latch a new gate starts every reactive tick and the robot never enters.

The honesty here is worth quoting to a judge, because it is at `src/amr.py:1574-1576`: *"This shrinks the race window; it does not close it. Over an asynchronous lossy channel no protocol can guarantee agreement (Fischer-Lynch-Paterson), which is precisely why the collision guarantee lives in Layer 0 and not here."*

**On entry — `CLAIM` (`CL`) with the block flag.** `_bios_claim` (`src/amr.py:2320`) broadcasts `block_claim` (`src/messages.py:530`) carrying the block id, a TTL, the frozen priority, the route epoch and the seven-element key. It is re-sent every 0.5 s for the whole transit, so a robot behind cannot mistake our intention for our presence.

**When we lose — `YIELD` (`YD`).** `src/amr.py:1300-1315` sets the hold, increments the yield counter and emits `yield_to(cell, winner)` (`src/messages.py:555`). The message changes no peer's behaviour; it exists so that deadlock breaking is *observable* in the telemetry rather than inferred.

**On exit — `RELEASE` (`RL`).** `src/amr.py:2374` releases the token immediately. TTL expiry is the fallback if the packet is lost.

**Underneath all of it, unconditionally.** `_safety` at `src/amr.py:868` runs after every decision above, on every 50 Hz tick, and reads one number — the distance to the nearest thing, of any kind, in the forward cone and the 360° guard. It does not care whether that thing broadcast anything. On real hardware this is a certified scanner wired to the motor contactors; modelling it in Python is a simulation convenience, but *placing it below the network in the architecture* is the engineering claim (`src/amr.py:878-880`).

Measured on the corridor, `showcase_chokepoint`, 4 robots, seed 7, 320 s, `auction_bundle`: **8/8 tasks in 254.0 s, 0 robot-robot contacts, minimum separation 1.352 m, 15 yields, 0 retreats, 0 detected deadlocks** — for both `.5` and `.6` (see [§6.2](#62-open-finding-bios_pibt6-is-motion-identical-to-5-on-the-chokepoint)). Two robots of 0.70 m diameter on a 1.4 m pitch have 0.70 m of nominal clearance, so 1.352 m of measured minimum separation means the corridor was serialised, not shared.

---

## 5. BIOS_4, the learned policy

### 5.1 What the network decides, and what it deliberately does not

`BIOS_4` does **not** drive the wheels. It selects one of five verbs the fleet already implements and has already been measured on (`src/bios4.py:55-62`):

| Verb | Constant | What executing it means | Implemented at |
|---|---|---|---|
| `proceed` | `ACT_PROCEED` = 0 | Clear the hold; the shared follower advances normally | `src/amr.py:2429-2432` |
| `hold` | `ACT_HOLD` = 1 | Wait, and name the contender so the wait-for graph gains an edge | `src/amr.py:2476-2481` |
| `yield` | `ACT_YIELD` = 2 | Physically pull aside into a passing bay | `src/amr.py:2448-2466` |
| `claim` | `ACT_CLAIM` = 3 | Respect the block token — wait at the mouth until it is ours | `src/amr.py:2434-2446` |
| `reroute` | `ACT_REROUTE` = 4 | Penalise the next cell and replan | `src/amr.py:2468-2474` |

Every one of these is existing `AMRBrain` machinery. The rejection of the obvious design — learn `(v, ω)` directly — is argued at `src/bios4.py:10-22` with three reasons specific to this codebase: `_safety` has unconditional final authority, so a model trained to emit velocities spends its capacity rediscovering an envelope Layer 0 will not let it leave and every good decision is indistinguishable from a veto; the braking-equation inversion in `_follow` was the single change that took the first task from never-completing to completing, and learning actuation throws that away; and it would make sim-to-real a question about *chassis dynamics*, the hardest thing there is to transfer.

The architectural split is the honest version of the liveness claim (`src/amr.py:2389-2399`). Everything that must be true regardless of what a network happened to learn stays in ordinary Python: the unstick valve above the model (`src/amr.py:2406-2410`), the legality mask, and Layer 0 underneath all of it. **A badly trained `BIOS_4` is slow. It is not unsafe, and it does not deadlock.**

### 5.2 The feature vector

28 features (`src/bios4.py:68-103`), in four groups. Every one is computable by a real robot from its own sensors plus the multicast heartbeats it already receives. Nothing reads world ground truth, because `AMRBrain` cannot see any — it is handed a `Sensors` struct and an inbox and nothing else (`src/bios4.py:26-32`). That architectural decision predates anyone thinking about learning, and it is exactly what makes a model trained in the simulator legitimate on hardware.

| Group | Features |
|---|---|
| Ego / safety (5) | `clear_fwd`, `clear_omni`, `clear_static`, `speed`, `turning` |
| Goal and path (5) | `has_path`, `dist_goal`, `goal_sin`, `goal_cos`, `path_left` |
| Stuckness (6) | `stall_s`, `blocked_s`, `no_progress_s`, `in_cycle`, `is_blocked`, `is_retreat` |
| Peers (8) | `peers_near`, `peer_dist`, `peer_sin`, `peer_cos`, `closing`, `peer_on_next`, `conflicts_ahead`, `i_lose` |
| Single-file blocks (4) | `next_in_block`, `block_taken`, `i_hold_block`, `committed` |

Four representation choices are load-bearing and each is defended in the source:

- **Bearings are robot-relative and split into sin/cos** (`src/bios4.py:303-311`). An absolute bearing ties the policy to this map's orientation; a raw angle has a discontinuity at ±π that a network reads as a huge input jump every time a robot crosses it.
- **Unbounded quantities are `tanh`-squashed, not clipped** (`src/bios4.py:109-116`). Three seconds stuck and thirty seconds stuck should not look the same, but neither should thirty and three hundred.
- **`closing` comes from detections, not the peer table** (`src/bios4.py:372-384`). That split is the whole point: a human or a dropped pallet closes on us without ever appearing in a heartbeat.
- **`i_lose` compares the same published arbitration key the rest of the fleet uses** (`src/bios4.py:400-408`), not a live one.

The vector is **self-describing**: the feature names travel inside the saved model and `model_from_dict` rejects any mismatch outright rather than silently reinterpreting a renumbered input (`src/bios4.py:243-249`). A model trained against a different observation layout is not a worse model — it is a different function.

### 5.3 The action space and the legality mask

`act` picks the highest-scoring **legal** action (`src/bios4.py:181-195`). Masking rather than penalising in the reward is deliberate: an action that cannot be executed carries no information about whether it was a good idea, so training against it is training against noise.

`legal_actions` at `src/bios4.py:424-451`:

- `hold` is **always** legal, so `act()` always has an answer and the fallback for a masked-out choice is the conservative one.
- With no next cell, only `hold` is offered.
- `yield` requires an actual passing bay to exist and the robot not to already be retreating.
- `claim` requires the next cell to be in a *controlled* block whose token is free or already ours.
- `reroute` is rate-limited to once per `BIOS4_REROUTE_COOLDOWN_S` = 3.0 s (`src/bios4.py:454`). The comment at `src/bios4.py:445-448` gives the reason: the hierarchical policy churns roughly 100 local replans against central's 20 and loses to it, and an unlimited reroute verb would let evolution rediscover that exact pathology and call it a strategy.

The network itself is a single hidden layer of 16 units with `tanh` activation, flattened into one weight vector because an evolution strategy perturbs a point in Rⁿ and the genome and the network have to be the same object (`src/bios4.py:126-179`). 28 × 16 + 16 + 16 × 5 + 5 = **549 parameters** (`src/bios4.py:152-154`). Pure stdlib, a few hundred multiply-adds at 10 Hz, so it drops onto a bare Pi image with no build step.

### 5.4 The training loop

`src/evolve.py`. A mirrored-sampling evolution strategy: `evolve()` at `src/evolve.py:300-415`.

| Parameter | Default | Cited |
|---|---|---|
| Scenario | `crossing_chokepoint`, 4 robots | `src/evolve.py:123-124` |
| Population | 24 (halved into ± mirrored pairs) | `src/evolve.py:126`, `src/evolve.py:335-339` |
| Generations | 30 | `src/evolve.py:127` |
| Perturbation σ | 0.30, decaying ×0.985 to a floor of 0.05 | `src/evolve.py:128-130` |
| Step size α | 0.25 | `src/evolve.py:131` |
| Episodes per genome | `(seed 0, 120 s), (seed 1, 120 s), (seed 2, 240 s)` | `src/evolve.py:147` |
| Train seeds | 0–7 | `src/evolve.py:64` |
| Eval seeds (withheld) | 8–11 | `src/evolve.py:65` |

Four properties are worth defending:

- **Evolution, not Q-learning, and the reason is a measurement not a preference** (`src/evolve.py:4-15`). On sample efficiency ES is the worse algorithm; it wins because an episode costs ~5 s, the machine has 16 cores, and 24 × 30 finishes inside half an hour. Sample efficiency matters when samples are expensive. What it buys in return: no credit-assignment problem, no discretisation of a continuous state, and a fitness function you can read.
- **Train and eval seeds are disjoint by construction**, asserted at import (`src/evolve.py:66`), and `TrainConfig.validate` *refuses to run* if any training episode uses a held-out seed, with an error message that says the result would be a memorisation score (`src/evolve.py:157-161`).
- **Mixed episode lengths are deliberate** (`src/evolve.py:143-146`): at 120 s the fleet is still dispersed and at 240 s it is saturated, so training on short episodes alone would teach a policy about a regime that is not the one it exists to fix.
- **The shipped model is the best genome ever *scored*, not the final iterate** (`src/evolve.py:370-377`). An ES iterate is a running estimate of a good direction and is not guaranteed to be the best point visited; shipping the last one is a way to hand over a worse model than you actually trained.

One documented weakness, left as a knob rather than silently fixed: `init_scale` defaults to 0.0 (`src/evolve.py:140`), and an all-zero network emits equal logits, so the first legal verb always wins and small updates never flip the argmax. That was **measured at thirteen generations before the iterate moved at all.** The comment says plainly that it is still the default because the shipped model comes from the sampled population anyway, and that an unvalidated fix is worse than a documented limitation.

Fitness shaping uses centred ranks in [−0.5, +0.5] (`src/evolve.py:247-261`) rather than raw scores, because raw fitness spans five orders of magnitude — a collision is −20,000 against a few hundred for a good episode — so an unshaped gradient would be one enormous vector pointing away from whichever genome happened to crash.

### 5.5 The fitness function, and why `progress_cells` had to exist

`FitnessWeights` at `src/evolve.py:72-87`; applied at `src/evolve.py:186-196`.

| Term | Weight | What it encodes |
|---|---:|---|
| `task` | +1000 per completed delivery | The actual objective |
| `progress` | +1 per cell of net approach | Partial credit, so generation 1 has a slope |
| `contact_rr` | −20,000 | Robot-robot contact: unsurvivable, not expensive |
| `contact_rh` | −50,000 | A human contact ends the run's usefulness entirely |
| `contact_rack` | −500 | Rack contact |
| `unstick` | −3 per valve firing | The backstop firing means the policy failed to act |
| `finish_bonus` | +5 per second saved | Only paid if the whole task set landed |

`progress_cells` is the term the whole exercise depends on, and it exists because `tasks_completed` is too coarse to train on. Over a 120 s episode the fleet completes 0–3 of 12 tasks, so almost every genome in the first generations scores exactly zero and evolution has nothing to climb (`src/evolve.py:19-24`).

The counter is accumulated in `AMRBrain.step` at `src/amr.py:570-583` and it is **monotone on purpose**: it credits only *net* approach, tracking the closest the robot has ever been to the current goal and paying only when that record improves. Crediting every step that happens to point goalwards would pay a robot to oscillate, and any fitness function built on it would be optimised by twitching in place (`src/amr.py:351-355`).

The contact weights are set to be unsurvivable rather than merely expensive (`src/evolve.py:26-28`): the success bar for this policy is "beat `BIOS_1.0.0`'s task count **at zero contacts**", and a fitness that would trade one collision for three extra deliveries is optimising for a different project.

### 5.6 The model file, and what each file in `models/` actually is

Format `bios4-mlp`, version 1 (`src/bios4.py:217-218`), max 4 MiB (`src/bios4.py:219`). `model_from_dict` at `src/bios4.py:226-274` is deliberately strict — it is the one place in the project that consumes a file a human chose — and rejects a wrong format, a wrong version, a mismatched feature list, a mismatched action set, a shape that disagrees with the build, an implausible hidden size, non-numeric weights and NaN/infinity.

Three files exist. Their provenance, read from their own `meta` blocks:

| File | Tracked | Fitness | Best tasks in training | Train seeds | Bytes | Weights |
|---|---|---:|---:|---|---:|---|
| `models/bios4.json` | yes | 2108.0 | 2 | 0, 1, 2 | 8684 | — |
| `models/bios4-legacy-2026-08-24.json` | yes | 3039.3 | 0 | 0, 1, 2 | 8673 | different |
| `models/bios4-v2.json` | **no — untracked** | 2108.0 | 2 | 0, 1, 2 | 8684 | **byte-identical to `bios4.json`** |

**`models/bios4-v2.json` is not a new model.** Its SHA-256 matches `models/bios4.json` exactly (`293fce680e42145d…` for both whole files). Its name promises a second version and it delivers a copy. It should be deleted or given real provenance before a judge opens the directory and asks which one is the result.

The legacy file's `meta` is the tell for the expiry story in §5.7: it records a *higher* fitness (3039.3) and a `best_tasks` of **0**. It was fitted to dynamics in which the fleet completed nothing, and it scored well on `progress_cells` alone.

**No code path loads any of these files by default.** The only reference to `models/bios4.json` in `src/` is the `--out` default of the training CLI (`src/evolve.py:549`). The server keeps models in memory only, under short ids, capped at 16 and evicted oldest-first (`backend/server.py:73-75`), and says so when one is missing: *"models are held in memory and are lost across restarts"* (`backend/server.py:628-631`). A model reaches a run either by being trained in-process or by being uploaded to `POST /api/model` (`backend/server.py:771-786`), and `run_scenario` receives it as an injected argument (`src/main.py:110`, `src/main.py:154`).

That injection has a defined null state: `policy_model=None` is legal and `BIOS_4` degrades to always-hold, with the liveness valve still moving the fleet (`src/amr.py:163-165`, `src/amr.py:2416-2419`). Precisely *because* it is legal, the dashboard refuses it: `parse_run_request` rejects a `BIOS_4` run with no model id (`backend/server.py:222-228`), on the reasoning that an untrained control rendered as a trained policy is worse than an error, because nothing on screen would say which of the two you were looking at. And a completed run carries the model's `meta` in its payload (`src/main.py:713-717`) so a `BIOS_4` result can always be traced to the weights that produced it.

### 5.7 The expiry risk, stated plainly

**A trained model is only valid against the simulator it was trained on.** This is not a hypothetical. `archive/FINDINGS.md:187-243` records what happened here:

1. A merge into `main` silently disabled `BIOS_4`. Every self-contained file survived — `src/bios4.py`, `src/evolve.py`, `models/bios4.json`, all 25 tests, the dashboard panel — and every call site did not: the import, `_bios4_traffic()`, the `self.policy_model` assignment, the `_traffic_loop` dispatch, six server endpoints and the JS controller. `POLICY_BIOS4` stayed in `POLICIES`, so the policy remained selectable and produced plausible numbers while discarding every uploaded model. **All 25 tests still passed, because each of them is also true when the model is ignored.**
2. With inference restored, the old weights had expired. On held-out seeds 8–11 at 420 s, 4 robots, the identical weights went **13/48 → 0/48**. Retraining against the current code recovered it to **6/48**.

The control that proves the simulator did not simply regress is `BIOS_1.0.0`, which scored 7/48 both before and after. Only the learned policy fell to zero, because it is the only one whose behaviour was *fitted* to the old Layer-1 machinery.

The structural cause is a gap in the model format. `to_dict` (`src/bios4.py:199-211`) records the feature list, the action set, the layer shape, the training seeds and the withheld seeds — and **nothing about the code version**. So a change to `_traffic_loop`, `_passing_bay`, `_bios_lock` or the follower expires every model on disk while every format check keeps passing. `model_from_dict` will catch a renamed feature; it cannot catch a changed `_bios_unstick`.

Three things follow, and a team defending this should say all three:

- **The safety claim survived the expiry intact.** Zero robot-robot, zero robot-human, zero rack contacts, and the *best* worst-separation of any policy measured (0.869 m). The guarantees live in ordinary Python below the model, so a stale `BIOS_4` is slow, not dangerous — which is exactly the claim the architecture was built to be able to make.
- **The honest reading of the retrained result is "matches the hand-written baseline at better separation", not "beats it"** — 6/48 against `BIOS_1.0.0`'s 7/48, from one training run on one scenario at one fleet size, with no variance estimate over training seeds.
- **The mitigation is procedural, and it is not implemented.** Nothing in the repository stamps a commit hash into a model file or fails a run when the two disagree. Until it does, the only safe operating rule is: *retrain before quoting a `BIOS_4` number, and quote the model's `meta` alongside it.*

A methodological trap from the same investigation is worth carrying into any live demo (`archive/FINDINGS.md:229-236`): the first re-measurement was run at 12 robots, where `stop_and_wait`, `central`, `hierarchical` and both `BIOS_4` models all score 0 and `BIOS_1.0.0` scores 2/144. At that fleet size the scenario is saturated and **the benchmark cannot discriminate between any two policies.** A configuration where the baselines all read zero is not a hard test, it is a broken instrument.

---

## 6. The BIOS_PIBT lineage

Cumulative, by transitive set membership (§1.1). Read down: everything in a row is also true of every row below it.

| Version | What it added | Mechanism | Cited |
|---|---|---|---|
| `.1` | Replicated PIBT | Every node reconstructs the same fleet snapshot from heartbeats and runs the same deterministic resolver. A low-priority robot receives an *inherited move out of the way* instead of merely waiting. No elected coordinator. | `src/amr.py:1827`, `src/priority.py:84` |
| `.2` | Directed circulation + per-cell leases | The rack map becomes alternating one-way lanes, so a reverse edge does not exist and a head-on meeting is structurally impossible. Every destination cell gets a two-phase lease. Block control drops to a 2-cell minimum because direction, not exclusion, now prevents opposing occupancy. | `src/amr.py:1985`, `src/amr.py:1664`, `src/amr.py:1703-1706` |
| `.3` | Batch auction + corridor waves + no reverse | Replicated bounded batch auction; drop-cell capacity 2; immutable two-task directional corridor waves; a one-cell-early staging check at the mouth; a duplicate-cell repair owner. Crucially: **never injects a physical reverse into live traffic** — a block timeout becomes a penalty plus a new legal route. | `src/amr.py:2019`, `src/amr.py:1586`, `src/amr.py:2067`, `src/amr.py:1367` |
| `.5` | Energy-feasible admission | A robot bids only if projected post-task energy clears a 15% reserve after the drop-to-charger leg, with cargo factors and a 10% uncertainty margin. **The motion layer is byte-identical to `.3`** — `.5` appears in no traffic-layer conditional except through `V3_AUCTION_POLICIES` and `ENERGY_AUCTION_POLICIES`. | `src/amr.py:4114`, `src/settings.py:206-224` |
| `.6` | Event-triggered comms, congestion memory, forecasting, charger choice, decision log | Five separable additions, below. | `src/amr.py:660-864` |

`.4` does not exist in this lineage. `BIOS_4` is the learned policy (§5) and is unrelated to the PIBT numbering.

### 6.1 What `.6` adds, mechanism by mechanism

1. **Event-triggered communication.** Heartbeats fire on signature change, at 5 Hz while a conflict is active, otherwise at 0.3 s cruising / 0.6 s idle (`src/amr.py:677-687`). Intent refreshes at 0.4 s unless the signature changed or a conflict is active (`src/amr.py:5076-5090`). Bid rebroadcasts are suppressed inside a cost-delta and time window (`src/amr.py:689-700`). Every suppression is *counted*, not silent (`src/amr.py:412-415`).
2. **Distributed congestion experience.** `_v6_track_wait` (`src/amr.py:724-733`) accumulates time genuinely held on one directed edge; `_v6_finish_wait_episode` (`src/amr.py:716-722`) closes the episode when the edge or the cell changes and records it via `_v6_observe_edge` (`src/amr.py:702-714`) as a decaying EWMA. `_v6_edge_costs` (`src/amr.py:735-758`) turns that into A* edge cost — but only for edges with at least `v6_experience_min_samples` = **8** samples (`src/settings.py:265`), capped, and *never* impassable. A remote report counts as exactly **one** bounded observation regardless of the sender's claimed counter (`src/amr.py:800-806`), so one forged packet cannot instantly poison route preference.
3. **Short-horizon occupancy prediction.** Anonymous moving detections are projected over 2.4 s into soft A* penalties that expire (`src/amr.py:809-824`, `src/settings.py:273-276`). Cooperating peers are deliberately excluded — an early ablation that added peer intent to A* cost double-counted the same conflict and caused route oscillation.
4. **Charger-aware dock choice.** Peer charging goals act as a soft decentralised queue signal; no robot owns a global dock schedule (`src/amr.py:826-864`).
5. **A bounded decision log.** `_record_decision` (`src/amr.py:643-658`) keeps the last 32 machine-derived explanations with reason codes (`CONGESTION_REROUTE`, `PREDICTIVE_REROUTE`, `CHARGER_SELECTION`, `CLEARANCE_UNSTICK`, …). These are emitted by the deterministic decision code itself, not narrated after the fact, and they change no decision. `.6` is the only policy that populates it (`src/amr.py:646`).

Both (2) and (3) are **disabled outright when the configured channel has packet loss or dead zones** (`src/amr.py:743-744`, `src/amr.py:817-819`, `src/amr.py:762-763`). Under degradation the policy falls back to proven `.5` routing, because divergent local experience maps amplify rather than relieve a partition. This has a consequence nobody should discover live: on `showcase_grand_challenge`, which carries 5% loss and a dead zone by construction (`src/scenarios.py:768-772`), **`.6`'s congestion and predictive routing are switched off by design.** What `.6` contributes there is the communication reduction and the decision log, not the learned routing.

`.6` also gains one recovery mechanism the earlier versions lack: `_v6_clearance_unstick` (`src/amr.py:984-1122`), a bounded lidar-verified step to a cell that is farther from *every* close return, taken only after Layer 0 has refused motion for a full deadlock interval and only on directed circulation maps.

### 6.2 OPEN FINDING: `BIOS_PIBT.6` is motion-identical to `.5` on the chokepoint

This is unresolved and is documented rather than hidden.

**Observation.** On `showcase_chokepoint` at `main` (`4a8186e`), `.6` and `.5` produce **identical trajectories and identical outcomes** in every configuration tested:

| Configuration | Policy | Tasks | Makespan | Min sep | R-R | Auction bids | Energy bids suppressed | Nonproductive wait ticks |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 4 robots, seed 0, 120 s, `auction_bundle` (dashboard defaults) | `.5` | 3/8 | 120.0 (timeout) | 1.309 m | 0 | 2058 | 8 | 1589 |
| 4 robots, seed 0, 120 s, `auction_bundle` | `.6` | 3/8 | 120.0 (timeout) | 1.309 m | 0 | 2058 | 8 | 1589 |
| 4 robots, seed 7, 320 s, `auction_bundle` (the scenario's own declared config) | `.5` | 8/8 | 254.0 | 1.352 m | 0 | — | — | 1404 |
| 4 robots, seed 7, 320 s, `auction_bundle` | `.6` | 8/8 | 254.0 | 1.352 m | 0 | — | — | 1404 |
| 4 robots, seed 0, 320 s, `auction` (the config `BIOS_PIBT_6` doc's own evidence used) | `.5` | 8/8 | 258.66 | 0.911 m | 0 | — | — | 3176 |
| 4 robots, seed 0, 320 s, `auction` | `.6` | 8/8 | 258.66 | 0.911 m | 0 | — | — | 3176 |

**Correction to the informal report of this finding.** The results are *not* byte-identical. On the 120 s default run, 19 of 100 summary fields differ — and **every one of them is a communication or CPU metric, not a motion metric.** `.6` sends 4,525 messages against `.5`'s 6,683 (a 32.3% reduction) and 851,708 bytes against 1,281,703 (33.5%), suppressing 1,000 heartbeats, 335 intents and 833 lease renewals. Tasks, makespan, minimum separation, contacts, yields, retreats, deadlocks, auction bids sent and nonproductive wait ticks are identical to the last digit. So **`.6`'s communication layer is doing exactly what it claims; its routing layer is not engaging at all.**

**Diagnosis.** The proposed explanation — that `POST /api/run` truncates the scenario — is *partly* right and is not sufficient.

- The truncation is real. `parse_run_request` defaults to `duration` = 120 s, `robots` = 4 and `seed` = 0 regardless of scenario (`backend/server.py:192-197`), while `showcase_chokepoint` declares 320 s and seed 7 in its own registry entry (`src/scenarios.py:805`) and its underlying builder declares 420 s (`src/scenarios.py:383`). A judge who picks "Chokepoint" in the dashboard and presses Run gets a 120 s run of a 320 s scenario. (The figure of 800 s belongs to `showcase_grand_challenge`, `src/scenarios.py:823` — not to the chokepoint.)
- But duration is not the binding constraint. At the full 320 s with all 8 tasks completed, `.6` accumulated only **5** congestion samples fleet-wide (seed 7) and **27** (seed 0). The threshold is 8 samples **per directed edge** (`src/amr.py:749-752`).
- The actual mechanism is the sample *generator*. `congestion_samples` counts completed **wait episodes**, not ticks: one increment per `_v6_observe_edge` call (`src/amr.py:714`), reached only from `_v6_finish_wait_episode` when an episode of ≥ 0.1 s closes because the robot changed cell or changed edge (`src/amr.py:716-722`, called from `src/amr.py:560-561` and `src/amr.py:729-731`). A single-corridor map produces a *few very long* waits — 1,404 nonproductive wait ticks at seed 7 is ~28 s of waiting spread over about 5 episodes — so no edge ever reaches 8. A dense rack map produces *many short* ones.

**The control that confirms the diagnosis.** On `dense_aisles`, 8 robots, seed 0, 600 s, `auction_bundle`, the same two policies diverge sharply:

| Policy | Tasks | Makespan | Congestion samples | Experience-guided replans | Experience updates received | Msgs/robot/s |
|---|---:|---:|---:|---:|---:|---:|
| `.5` | 30/32 | 600.0 (timeout) | 0 | 0 | 0 | 14.39 |
| `.6` | **32/32** | **424.16 s** | 1350 | 2 | 9086 | 11.53 |

So `.6`'s congestion machinery is not broken — it is topology-dependent, and the chokepoint is the topology where it has nothing to learn. That is arguably correct behaviour (there is only one route; a congestion penalty cannot help), but it means **the chokepoint demo cannot show any difference between `.5` and `.6` beyond message count**, and nothing in the UI says so.

**A contradiction with the existing documentation, which is the more serious half.** `archive/BIOS_PIBT_6_PREDICTIVE_INTELLIGENCE.md` states under Release evidence: *"**Chokepoint:** V6 completes all 24 task instances; V5 completes 22… Persistent peer nomination resolves seed 0 at 274.5 s, while V5 remains at 6/8 at the 320 s cutoff."* Re-run at `main` in that document's own configuration — `showcase_chokepoint`, seed 0, 4 robots, 320 s, `--allocation-policy auction` — **`.5` completes 8/8 at 258.66 s, identical to `.6`.** The doc pins its evidence to commit `80aebdb`; `archive/BIOS6_THREE_WAY_COMPARISON.md` pins to `027d1445…`; `main` is at `4a8186e`. The chokepoint result that justified `.6`'s promotion over `.5` no longer reproduces, because `.5` improved. The message-reduction claim from the same paragraph *does* reproduce (30.9% measured against 29.4% claimed).

**What is not yet known.** Which commit between `80aebdb` and `4a8186e` closed the gap, and whether `.6` retains a throughput advantage over `.5` on the *other* four showcase scenarios at `main`, are open. Re-running the full paired showcase campaign is the fix; that is [12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md)'s territory and is recorded as an open thread in [14. Findings](14-FINDINGS.md) and [15. Limitations](15-LIMITATIONS.md).

**How to state it to a judge.** *"On a single-corridor map `.6` and `.5` route identically, and that is expected — there is one route and nothing to learn. What `.6` buys there is a third of the radio traffic. Where `.6` earns its keep is a dense rack map, and we can show you that run."*

---

## 7. Selection guidance

**Run `BIOS_PIBT.6`.** It is the default on the CLI (`src/main.py:745`), the server (`backend/server.py:174`) and the dashboard (`frontend/js/main.js:78`), it is the only policy that populates the Decision Trace the dashboard renders (`src/amr.py:646`), and it is the policy the acceptance campaign in `archive/BIOS6_THREE_WAY_COMPARISON.md` was run against. Pair it with `auction_bundle` allocation, which is what both defaults already select (`src/main.py:569`).

Scenario choice matters more than most teams expect, and §6.2 is the reason:

| If the question is… | Run | Because |
|---|---|---|
| "Show me a narrow chokepoint being coordinated" (req 9, 11) | `showcase_chokepoint`, **override duration to 320 s and seed to 7** | At the 120 s default the fleet completes 3/8 and times out. At its declared settings it completes 8/8 in 254 s with zero contacts. |
| "Show me `.6` beating `.5`" | `dense_aisles`, 8 robots, 600 s | 32/32 at 424 s against 30/32 timing out. The chokepoint will show no difference at all. |
| "Show me the 20% claim" (req 20) | The paired campaign, not a single run | `stop_and_wait` completes 0/8 on the chokepoint, so the number is a censored lower bound and only `compare_paired` (`src/metrics.py:289`) computes it correctly. |
| "Show me what happens without coordination" (req 8, 10) | `crossing_chokepoint` with `stop_and_wait`, then the same seed with `BIOS_PIBT.6` | 0/8 against 8/8, on the identical workload, follower and safety layer. |
| "Show me the single point of failure" (req 6) | `manager_dies` with `central`, then `hierarchical` | `central` parks (`src/amr.py:2580-2588`); `hierarchical` drops to `DEGRADED_P2P` and keeps working at reduced plan quality. |

Policies to avoid on stage:

- **`BIOS_4`** unless you have retrained a model against the current tree *today* and can show its `meta`. §5.7 is the reason. The server will refuse to run it without a model, which is the correct behaviour and looks like a bug to anyone who does not know why.
- **`decentralized`**, which completes 0/8 on the chokepoint for the Layer-0 reason in §3.1, and whose dropdown label ("Peer intent baseline") does not warn you.
- **`BIOS_PIBT.1` and `.2`**, which appear in the dropdown under raw identifiers with no label (`frontend/js/main.js:287-297`) and are superseded by `.3`/`.5`/`.6` in every respect.

Keep `stop_and_wait` and `prioritized_space_time_astar` one click away. The first is the requirement's comparator; the second is the answer to the evaluator who says "a central planner would do better", and the honest answer is that on the acceptance campaign it did not finish either.

---

**Siblings:** [02. Architecture](02-ARCHITECTURE.md) · [03. Decentralized Protocol](03-DECENTRALIZED-PROTOCOL.md) · [04. Path Planning](04-PATH-PLANNING.md) · [06. Task Allocation](06-TASK-ALLOCATION.md) · [07. Safety](07-SAFETY.md) · [11. Scenarios](11-SCENARIOS.md) · [12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md) · [13. Testing](13-TESTING.md) · [14. Findings](14-FINDINGS.md) · [15. Limitations](15-LIMITATIONS.md) · [16. Demo Runbook](16-DEMO-RUNBOOK.md)
