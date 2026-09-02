# 15. LIMITATIONS AND KNOWN GAPS

> This document establishes what the submission does not prove, does not model, and does
> not yet do — so a judge finds it here first, from us, rather than in the demo.

**Audience:** SIH judges and BEL evaluators stress-testing the claims in this submission;
teammates who must defend it live and need to know which questions have a rehearsed,
honest answer.
**Reads best after:** [12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md)

---

Every other document in this suite states what is built and what evidence supports it.
This one inverts the exercise: for each limitation, what it is, why it exists, what
closing it would take, and whether it touches a success-criterion claim (requirement 19,
zero inter-robot collisions; requirement 20, ≥20% task-time reduction). Two items that
resemble limitations but are not are called out separately in [§3](#3-two-things-that-are-not-limitations)
so they are not miscounted against the submission. A repo-wide search for `TODO`,
`FIXME`, `XXX`, and `HACK` across every `.py`, `.js`, `.html`, `.css`, and `.md` file
returned zero matches — there is no marker-based backlog to report.

---

## 1. Structural limits

### 1.1 This is a simulation. Nothing has run on physical hardware.

No part of this system has executed on a Raspberry Pi, a Jetson, or any device other
than the development machine. The hardware capability table in
[08. Edge Deployment](08-EDGE-DEPLOYMENT.md) is explicitly labelled ESTIMATE for exactly
this reason: every millisecond and every watt figure in it comes from timing the same
code on different hardware classes analytically, not from a board on a bench.

What `src/world.py` models, as ground truth, per tick:

| Modeled | Not modeled |
|---|---|
| Unicycle kinematics with bounded acceleration and angular acceleration (`a_max=0.8 m/s^2`, `alpha_max=3.2 rad/s^2`, `omega_max=1.6 rad/s`, `v_max=1.2 m/s` — `src/settings.py:18-22`) so a planner cannot assume instant velocity change (`src/world.py:415-421`) | Wheel slip, traction loss, wheel-ground friction variance |
| Swept (not endpoint) contact detection against racks, other robots, and humans, so two robots exchanging cells in one tick still register a hit (`src/world.py:14-16`, `:406-434`) | Real actuator noise or motor-controller error — commanded `v`/`omega` are followed exactly, subject only to the rate limits above |
| A single scalar, `pose_noise_m`, added as fresh independent Gaussian noise to the *reported* pose each time a robot senses (`src/world.py:739-752`) as a stand-in for localization error | Accumulating odometry drift — `pose_noise_m` is resampled per call, not integrated over time, so it cannot produce the growing, correlated position error a real dead-reckoning stack accumulates between localization fixes |
| Battery draw as a function of motion state (`draw_move_w` vs `draw_idle_w`) and charging on dock contact (`src/world.py:434-438`) | Battery chemistry, temperature effects, or charge-cycle degradation |
| A configurable global default of `pose_noise_m = 0.02` (`src/scenarios.py:82`), overridable per scenario | Sensor noise on anything other than pose — no simulated LIDAR returns, no false negatives/positives in obstacle detection, no occlusion |
| Deterministic, in-process message delivery with configurable loss and dead zones (see [03. Decentralized Protocol](03-DECENTRALIZED-PROTOCOL.md)) | Real radio behavior: multipath, interference, Wi-Fi/mesh retransmission timing, real UDP multicast on a physical LAN |
| CPU-cost accounting for planning calls (see item 2.9 below, which shows this undercounts) | Real CPU contention, thermal throttling, OS scheduling jitter, or power draw on an SBC under load |

This table is an honest reading of `src/world.py` and `src/settings.py`, not an argument
that the fidelity is insufficient — swept collision checking and bounded kinematics are
more rigorous than many hobbyist sims. But the gap between "the physics referee did not
let a robot teleport through a rack" and "a robot ran this stack on a Jetson and did not
hit a rack" is exactly the gap between simulation and hardware evidence, and the
submission has none of the latter. This affects requirement 15 (edge/local execution):
the codebase-structure argument (Layer 0 reads only onboard sensor data — see
[07. Safety §1.2](07-SAFETY.md#12-what-layer-0-is-allowed-to-read)) is verifiable by
reading `src/amr.py`, but the performance argument (fits in a Pi's CPU/RAM budget at the
required rate) is an estimate, not a measurement. It does not affect requirement 19 or 20
directly, since both are defined and measured entirely in-simulation.

**What it would take to close:** port `edge_node.py`/`edge_runtime.py` to run unmodified
on a Pi 4 and a Jetson Orin Nano, replace the in-process transport with the real UDP
multicast socket path (`src/transport.py:241`) across an actual LAN or ad-hoc mesh, and
re-run the acceptance benchmark measuring wall-clock tick time, packet loss, and CPU/RAM
against the ESTIMATE table in [08. Edge Deployment](08-EDGE-DEPLOYMENT.md).

### 1.2 Zero observed contacts is not a proven zero rate

`artifacts/benchmarks/sih-acceptance.json` records zero contacts of every kind over 90
candidate runs. Zero events in a finite sample is right-censored evidence, not proof of a
zero rate: the correct statistical statement is a one-sided upper confidence bound on the
rate, not "the rate is zero." [07. Safety §9](07-SAFETY.md#9-the-statistical-limit-of-the-claim)
derives this in full, including a defect in the bound calculation itself worth
restating here because it affects every published number: `_chi2_quantile`, the
Wilson–Hilferty approximation used to build the bound, returns 5.936870 for
χ²₀.₉₅(2) against an exact value of 5.991465 (`docs/07-SAFETY.md:889-890`, verified
against a scientific reference implementation). Every published upper bound in the repo
is therefore about 0.9% more optimistic than it should be. The corrected 95% upper bounds
per 1000 robot-hours are 233.941 (was 231.809), 109.061 (was 108.067), and 61.924 (was
61.360) at the three exposure levels reported in [07. Safety](07-SAFETY.md#9-the-statistical-limit-of-the-claim).
The correction does not change which side of any threshold the result falls on — it
moves each bound up by under 1%, and 07-SAFETY.md already carries the corrected table —
but a team that quotes the original 231.809/108.067/61.360 without the caveat is quoting
a number the code's own approximation slightly underestimates.

### 1.3 The headline task-time reduction is a censored lower bound, not a speedup

[12. Benchmark and Evidence §4](12-BENCHMARK-AND-EVIDENCE.md#4-why-the-result-is-a-lower-bound-not-a-speedup)
derives this; it is restated here because it is the single most consequential number in
the submission and the one most likely to be mis-stated live. The stop-and-wait baseline
does not complete within the benchmark's timeout on the pinned chokepoint workload — it
right-censors into a permanent or near-permanent head-on wait. Let `C` be the candidate
policy's measured (completed) makespan, `D` the timeout duration, and `B` the baseline's
true — unobserved — makespan. Because the baseline was cut off before finishing,
`B > D`. Substituting into the reduction formula:

```
true reduction = 1 - C/B
since B > D:      1 - C/B  >  1 - C/D
```

The artifact reports `100 × (1 − C/D)` and calls it a lower bound, which is what it is:
whatever the baseline's true makespan actually is, the real reduction is *at least*
`1 − C/D`, because a larger, unobserved `B` in the denominator can only make `1 − C/B`
larger still. This is why no exact baseline mean, median, or p95 makespan appears
anywhere in the repo — none was ever measured, because the baseline never finished — and
why no exact speedup ratio can be quoted, only the lower bound. The bound is still
sufficient to establish the ≥20% claim (requirement 20): 12-BENCHMARK-AND-EVIDENCE.md
reports minimum per-seed bounds of 64.07% / 50.63% / 33.38% at 4/6/8 robots, each
comfortably above 20%, and the release gate accepts the minimum bound across seeds, not a
favorable average. What cannot be claimed is a precise percentage improvement — only that
the true improvement is at least the reported number.

### 1.4 Zero robot-human contacts is a property of both parties, not just the robots'

Pedestrians in the simulation are not passive obstacles: they path with A* and refuse to
walk through racks, pallets, AMRs, or each other (`src/world.py:446-448`). Their
look-ahead margin — the distance at which a worker reacts and turns away — is
deliberately larger than the AMR's own protective field:

```
protective_separation = h.radius + spec.radius_m + spec.omni_stop_m + 0.16
                       = 0.30    + 0.35          + 0.45              + 0.16
                       = 1.26 m                                    (src/world.py:459-461)
```

against the AMR's own 360° omni guard of `omni_stop_m = 0.45 m`
(`src/settings.py:32`). The comment at `src/world.py:450-454` states the reasoning
directly: the worker's margin is set larger than the robot's stop field so that a person
who notices a robot only after entering that field has *already* made the robot stop —
the human's evasive behavior is engineered to bind before the robot's independent safety
layer would need to. The enforcement point is `src/world.py:498`, where a candidate human
move is rejected if it would bring the human-robot clearance under `protective_separation`.
This means "zero robot-human contacts" over the benchmarked runs is not solely a
demonstration of robot-side collision avoidance — it is also a demonstration that the
pedestrian model was built to avoid the robots. A judge asking "would this hold if the
human didn't cooperate" is asking a fair question the current benchmark cannot answer,
because no scenario runs a pedestrian model with a smaller or zero avoidance margin.
This does not invalidate requirement 19 as measured (contacts are counted honestly and
swept, not endpoint-only), but it bounds what "zero" is evidence of.

---

## 2. Verified defects

Each item below was independently confirmed against the repository at HEAD `7740efb`
this session.

| # | Defect | Location | Blocks a release gate? | Touches req. 19/20? |
|---|---|---|---|---|
| 2.1 | `ruff` fails at HEAD | `src/main.py:635` | Yes — README lists lint as a gate | No |
| 2.2 | Dashboard docstring overstates a live capability that doesn't exist in the checked-in code | `backend/server.py:20` | No | No |
| 2.3 | `preassigned` allocator announces zero tasks on every `showcase_*` scenario | `src/scenarios.py:717-718` | No | No (showcase family only) |
| 2.4 | The acceptance benchmark has no pedestrians | `src/scenarios.py:350-351`, `:577-578` | No | Yes — human-robot bound is vacuous |
| 2.5 | Block-based chokepoint control never fires on the standard map | `src/settings.py:152`, corridor histogram below | No | No (mechanism unused, not unsafe) |
| 2.6 | `manager_dies` fault scenario is inert under the default policy | `src/main.py:165-172`, `:226` | No | No |
| 2.7 | The systemd unit crash-loops after the first completed task | `deploy/systemd/sih-edge-node@.service` | No | No (deployment-only) |
| 2.8 | A cost-optimal plan can still reverse out of a chokepoint it entered | `src/environment.py:171` | No | No (efficiency, not safety) |
| 2.9 | `plan_calls` undercounts real A* search work | `src/amr.py:4045-4050` | No | No, but weakens the edge-feasibility argument |
| 2.10 | `requirements.txt` is wrong in both directions | `requirements.txt` | No | No |
| 2.11 | Dashboard coverage gap: no test loads the dashboard page | `tests/` (see [13. Testing](13-TESTING.md#52-no-test-loads-the-dashboard-page)) | No | No |
| 2.12 | `POST /api/run` silently truncates long scenarios to defaults | `backend/server.py:174-197` | No | No |
| 2.13 | Stale documentation: commit hash, default policy claims, priority-vs-deadline ordering claim, non-existent `main.py` | `archive/SIH_ACCEPTANCE_BENCHMARK.md:95`, `archive/BIOS6_EXPERIMENTAL_BOUNDED_FUTURE_AUCTION.md:3,213-218`, `archive/BIOS_PIBT_5_ENERGY_AUCTION.md:5,39,64`, `README.md:188` | No | No |
| 2.14 | `bios6-distributed-demo.json` does not demonstrate sustained wall-clock 50 Hz | `artifacts/benchmarks/bios6-distributed-demo.json:15,335` | No | No |
| 2.15 | Deliberate behavioral limits worth stating plainly | `src/amr.py:2986`, `:4700-4709` | No | No |

### 2.1 `ruff` fails at HEAD

`ruff check .` reports one error:

```
F821 Undefined name `Cell`
   --> src\main.py:635:33
635 |         human_routes: list[list[Cell]] = []
```

`Cell` is used only as a type annotation on a local variable; it is not imported in
`src/main.py` (it is defined in `src/geometry.py` and imported there, and elsewhere, but
not in `main.py`). The annotation is never evaluated at runtime because the module uses
`from __future__ import annotations`, so this has zero runtime effect — the program runs
correctly. But `README.md:267` lists "the complete Python regression suite, lint, Python
compilation and frontend JavaScript syntax checks" as release gates, and the lint gate is
currently red. **Fix:** add `Cell` to the existing `from .geometry import (...)` block in
`src/main.py`, or change the annotation to `list[list[tuple[int, int]]]`. One line, no
behavior change.

### 2.2 The dashboard docstring overstates a capability that does not exist in the checked-in code

`backend/server.py:20` reads: "In the distributed runner the dashboard is a **passive
multicast listener**: it joins the group and reads the same datagrams the robots send
each other." Searching `backend/` and `frontend/` for `socket`, `IP_ADD_MEMBERSHIP`, or
`multicast` turns up no socket code and no multicast join anywhere in either directory —
the only three non-comment matches in `backend/server.py` refer to closing an HTTP
connection socket, unrelated to fleet transport. `IP_ADD_MEMBERSHIP` exists exactly once
in the repository, at `src/transport.py:241`, inside the simulation's own UDP transport
layer used by `edge_node.py`/`edge_runtime.py`, not by anything under `backend/`. The
docstring's own next sentence is accurate and is what the checked-in code actually does:
"Here in the batch runner it is downstream of a completed simulation, which is even
further from being a coordinator." The described "distributed runner" listener mode is
either aspirational or lives in code not present at this HEAD. **Fix:** either build the
multicast listener the docstring describes, or rewrite lines 15-23 to describe only the
batch-runner mode that exists.

### 2.3 `preassigned` announces zero tasks on every `showcase_*` scenario

`_showcase_profile` (`src/scenarios.py:717-718`) sets `sc.unassigned = profiled` and then
`sc.assignments = [[] for _ in range(sc.n_robots)]` unconditionally, on every showcase
scenario, regardless of which allocation policy will run it. This clears the
pre-assigned work queue that the `preassigned` allocator reads from. Running
`showcase_chokepoint` with `--allocation-policy preassigned` therefore produces 0/0 tasks
completed, 0 bids, and a reported minimum separation of 9.899 m — four robots that never
left their start cells, which reads as a flawless collision-free result but is actually
zero robots doing zero work. The invariant that a scenario always populates either
`assignments` or `unassigned` (never neither) holds for custom scenarios
(`src/main.py:622-627`, where both round-robin `assignments` and a duplicate
`unassigned` list are set together) and for classic scenarios; it is broken specifically
by `_showcase_profile`. **Fix:** in `_showcase_profile`, only clear `assignments` when
`sc.uses_allocation` will be `True` for the policy that is about to run — or reject
`preassigned` for showcase scenarios explicitly, with an error rather than a silent
zero-task run.

### 2.4 The acceptance benchmark has no pedestrians

`sih_acceptance_overlap` (`src/scenarios.py:567-593`) — the scenario the ≥20%
task-time-reduction figure and the primary safety bound are drawn from — builds on
`crossing_chokepoint` (`src/scenarios.py:577-578`). `crossing_chokepoint`'s signature
(`src/scenarios.py:350-351`) takes no `humans` argument and never adds any, so no
pedestrian is ever present in this workload. Consequently, the published robot/human
separation bounds attached to the acceptance benchmark are computed over zero
human-involving ticks — they are vacuous duplicates of the robot/robot bounds, not
independent evidence. The only real human-safety evidence in the repository comes from
the mixed-traffic `showcase_*` scenarios, which total 7.83 robot-hours of combined
exposure and yield the 95% upper bound of 379.1 contacts per 1000 robot-hours cited in
[07. Safety §9](07-SAFETY.md#9-the-statistical-limit-of-the-claim) — an order of
magnitude less exposure than the 90-run, 88.65 robot-hour robot/robot benchmark. **Fix:**
either add a `humans=` parameter to `crossing_chokepoint` and pass a fixed pedestrian
route into `sih_acceptance_overlap`, or stop reporting a human-safety bound alongside
this benchmark and rely solely on the showcase-derived figure, labelled as such.

### 2.5 Block-based chokepoint control never fires on the standard map

`min_controlled_block = 6` cells (`src/settings.py:152`) is the threshold above which the
block-locking mechanism described in [04. Path Planning](04-PATH-PLANNING.md) and
[05. Coordination Policies](05-COORDINATION-POLICIES.md) engages. Running
`corridors(classic_warehouse())` — the standard demo map — produces 59 single-file
components with the length histogram `{2: 35, 4: 24}` (confirmed by direct execution this
session): every corridor on the classic warehouse is 2 or 4 cells long, and zero reach
the length-6 threshold. The mechanism the documentation and demo narration describe
("a robot acquires the whole block before entering," `src/environment.py:174-175`) is
real code, exercised by tests, and does fire on other maps (e.g. the dedicated chokepoint
map, whose single 13-cell block is well above the threshold) — but on the map most judges
will actually watch run, it is present and inert. **Fix:** either lower
`min_controlled_block` for the classic map's corridor lengths, or narrate the demo
against a map where the mechanism visibly engages (the chokepoint or dense-aisles maps),
and say so.

### 2.6 `manager_dies` is inert under the default policy

The `manager_dies` fault scenario kills the fleet manager at `t=60s`
(`src/scenarios.py:421-430`), but the kill is guarded: `if sc.kill_manager_at is not None
and manager is not None and manager.alive and t >= sc.kill_manager_at` (`src/main.py:226-227`).
`manager` is only constructed (`src/main.py:165-172`) when the selected route policy is
in `MANAGED_POLICIES` or the allocation policy is Hungarian. The default policy,
`BIOS_PIBT.6` (`src/main.py:745`, `backend/server.py:174`), is decentralized and is not a
managed policy, so `manager` stays `None` for the entire run, the guard never fires, and
the run is structurally identical to `dense_aisles` with no fault injected at all.
Running this scenario meaningfully requires explicitly passing `--policy hierarchical` or
`--policy central`. **Fix:** either have `main.py` warn or refuse when `manager_dies` is
selected under a policy that builds no manager, or extend the guard's intent
(demonstrating resilience to a central point of failure) to also cover a decentralized
policy's own single points of failure, if any exist.

### 2.7 The systemd unit crash-loops on the first completed task

`deploy/systemd/sih-edge-node@.service` invokes `edge_node.py` without
`--terminal-journal`. `src/edge_runtime.py:349-358` falls back, in that case, to
`$XDG_STATE_HOME` or `~/.local/state/sih-fleet-priority/<robot-id>-terminal.json`. The
unit sets `ProtectSystem=strict` and `ProtectHome=true`, which make both the real home
directory and most of the filesystem read-only to the service — `~/.local/state` is not
in the unit's `ReadWritePaths=/var/lib/sih-fleet`. Startup succeeds silently because
`TerminalJournal.load()` returns `[]` when the path is unreachable at read time in the
observed code path; the failure surfaces only when the first task completes and
`flush_terminal_records` (`src/edge_runtime.py:121`) attempts to persist a record through
an uncaught `TerminalJournalError`, which crashes the process. `Restart=on-failure` then
loops it indefinitely against the same unwritable path. **Fix:** add `--terminal-journal
/var/lib/sih-fleet/%i-terminal.json` (inside the already-writable `ReadWritePaths`) to the
unit's `ExecStart`.

### 2.8 A cost-optimal plan can reverse out of the chokepoint it just entered

`corridors()`'s own docstring states the intended design plainly: "two robots that meet
halfway down a one-lane aisle have both already committed, and no amount of yielding
creates space that the map does not have. One of them has to reverse out, which is a
**failure, not a plan**" (`src/environment.py:168-171`). On the pinned head-on chokepoint
scenario, `prioritized_plan` — under the cost model that permits it — returns a plan
matching exactly that failure mode: it drives 11 cells into the 15-cell corridor,
reverses back out, side-steps, and re-enters, and the plan is accepted because it is
cost-optimal under the objective function, even though the module's own commentary
identifies this maneuver as the thing the block-locking mechanism exists to prevent. This
is an efficiency defect (wasted motion, wasted time), not a safety defect — no unsafe
proximity results from it, so it does not affect requirement 19. **Fix:** either extend
block-acquisition logic to cover the paths `prioritized_plan` produces directly, or add a
penalty term that makes a reversal-through-a-committed-block strictly dominated in the
cost model, so an optimizer never selects it.

### 2.9 `plan_calls` undercounts real A* search work

`plan_calls` is incremented at only three call sites in the codebase. The two A* calls
inside `_bid_cost` (`src/amr.py:4045-4050`, one for the pickup leg and one for the drop
leg, per candidate task per robot per auction round) are not among them, nor is the
`_energy_required` path, which can issue up to `2 + |docks|` further A* calls. Across a
12-task auction round this is roughly 24 uncounted A* calls, estimated at approximately
4.4 ms of real search time not reflected in `plan_calls`. Every `plan_cpu_mean_ms` figure
published anywhere in the repository is therefore a **lower bound** on the true search
CPU cost, not an exact figure. This matters specifically because `plan_cpu_mean_ms` is
the number [08. Edge Deployment](08-EDGE-DEPLOYMENT.md) leans on to argue the planner
fits an edge CPU budget — the argument is directionally sound but the margin it claims is
smaller than stated. **Fix:** route `_bid_cost` and `_energy_required`'s A* calls through
the same counted entry point as route planning, or maintain a separate counter for
bid-evaluation search and publish both.

### 2.10 `requirements.txt` is wrong in both directions

```
websockets>=12,<16
matplotlib>=3.8,<4
```

Neither package is imported anywhere in the repository (searched by both grep and AST
import inspection). Conversely, `numpy` and `PIL` are imported and used by
`tools/bake_brand_assets.py` and `tools/bake_twin_textures.py` but are absent from
`requirements.txt`. The stdlib-only claim — that `src/`, `backend/`, and the root
scripts run on a bare Python install with no dependencies, which matters because the
edge node must drop onto a Raspberry Pi image without a build step (`requirements.txt`'s
own header comment states this intent) — is true for those directories and is AST-verified,
not just grepped. The file is simply stale for the asset-baking tools it was never scoped
to describe. **Fix:** move `numpy`/`PIL` into a `requirements-dev.txt`-style optional
group (a `requirements-dev.txt` already exists in the repo and is the natural home), and
drop `websockets`/`matplotlib` or gate them behind a comment explaining what still needs
them, if anything does.

### 2.11 Dashboard test coverage gap

[13. Testing §5.2](13-TESTING.md#52-no-test-loads-the-dashboard-page) documents this in
full and is the authoritative source; it is listed here because it is a genuine gap a
judge could probe live. No test in `tests/` opens `frontend/index.html`, constructs a
DOM, or executes `frontend/js/main.js` (1,825 of the frontend's 5,553 JS lines). The three
Node-based tests import two of eight JS modules in isolation with a stubbed
`global.window` and never call `boot()`. 13-TESTING.md demonstrates the consequence
directly: it identifies two IDs (`progressTasks`, `progressTime`) read on every frame by
`updateSummaryProgress` that have no matching element in `frontend/index.html`, and a
constructed scenario (a panel deleted from `index.html` that `main.js` still wires in
`boot()`) that would break the dashboard on first paint while every one of 148 tests
still passes. **Fix:** add at least one test that loads the real page (via `node` with
`jsdom`, or a headless browser) and calls `boot()`, so a broken wiring between HTML and
JS fails CI instead of only a live demo.

### 2.12 `POST /api/run` silently truncates long scenarios to defaults

`backend/server.py:174-197` sets `robots` to 4, `seed` to 0, and `duration` to 120
seconds whenever a request omits them, regardless of which scenario is requested. A
showcase scenario built around a longer run, more robots, or a specific seed, requested
without every parameter spelled out explicitly, silently runs a shorter/smaller version
of itself with no error or warning. This has already produced at least one false
comparison conclusion attributed to a run that was actually truncated rather than
representative. **Fix:** derive the default `duration`/`robots` from the requested
scenario's own declared defaults (`Scenario.duration_s`, `Scenario.n_robots`) instead of
one fixed literal for every scenario.

### 2.13 Stale documentation

Four cases, independently confirmed:

- `archive/SIH_ACCEPTANCE_BENCHMARK.md:94-95` cites `git_commit`
  `b1d3c82445cc32a8cbbf78331dfef462999a4e8a`, but the checked-in
  `artifacts/benchmarks/sih-acceptance.json:4` records `git_commit`
  `781a4dfc2b3ae09e68768bd0453ad3443d56b520`. The document and the evidence it describes
  were committed at different points and never reconciled.
- `archive/BIOS6_EXPERIMENTAL_BOUNDED_FUTURE_AUCTION.md:3` and `:213-218` state plainly that
  `auction_bundle` "is not a BIOS 6 default" and instructs "do not make it the default" —
  while `auction_bundle` is in fact the default allocation policy in three places at this
  HEAD: `src/main.py:749` (`ALLOCATION_AUCTION_BUNDLE`), `backend/server.py:179`
  (`scalar("allocation_policy", "auction_bundle")`), and `frontend/js/main.js:79` (the
  dashboard's own dropdown default).
- `archive/BIOS_PIBT_5_ENERGY_AUCTION.md:39` ("Declared priority orders tasks first, then
  earliest hard deadline") and `:64` ("task order: priority, hard deadline, bid cost,
  stable IDs") both state priority is evaluated before deadline. `_task_urgency`
  (`src/amr.py:4084-4087`) returns `(task.deadline is None, task.deadline or inf,
  -priority)` — a sort key that ranks by deadline-presence and value first and priority
  only as a tie-breaker, the reverse of what the document says. The same document's line
  5 additionally calls `BIOS_PIBT.5` "the default software policy," while the actual
  default at this HEAD, in both `src/main.py:745` and `backend/server.py:174`, is
  `BIOS_PIBT.6`.
- `README.md:188` documents running `python main.py --scenario ...`; no `main.py` exists
  at the repository root at this HEAD (confirmed absent). The entry point is
  `src/main.py`, invoked as a module.

None of these are behavioral defects in the running system — the code does what the
verified line numbers show, independent of what the prose claims. They are a
documentation-drift risk specifically because a judge who reads the docs before watching
the demo will be told the opposite of what actually runs. **Fix:** a single pass
reconciling each of the four documents against the current default values and the
current git hash, ideally driven by a small script that reads the defaults out of
`src/main.py`/`backend/server.py` rather than hand-copying them into prose that can drift
again.

### 2.14 `bios6-distributed-demo.json` is not evidence of sustained 50 Hz

`artifacts/benchmarks/bios6-distributed-demo.json` records `duration_s: 120.0` (line 15)
against `wall_time_s: 1.5197662090067752` (line 335) — the simulated 120 seconds of fleet
time executed in 1.52 seconds of real time, which is only possible with `--no-realtime`
set. This is a legitimate and useful artifact for what it actually measures — per-tick
computational cost, message counts, and outcome quality, all independent of wall clock —
but it cannot be cited as evidence that the stack sustains 50 Hz control-loop timing
against a real clock, because it was never run against one. **Fix:** either re-run this
specific artifact with `--realtime` and record the wall-clock figure separately, or
rename the field/document to make clear `wall_time_s` here means "harness execution time,"
not "simulated wall-clock duration."

### 2.15 Deliberate behavioral limits worth stating plainly

Two behaviors are intentional design choices, not bugs, but are exactly the kind of
question a judge should be able to ask and get a rehearsed answer to rather than a
surprised one:

- A robot already holding a task never aborts that task for low battery. The charging
  trigger in `_task_loop` is gated `if self.task is None and sensors.battery_frac <
  charge_trigger` (`src/amr.py:2985-2986`) — the `self.task is None` clause means a robot
  mid-delivery will not divert to charge, however low its battery falls, until the task
  completes.
- BIOS 6's lease-renewal logic extends a task lease for an owner that is alive but
  stuck, not just one that is alive and progressing: the renewal condition checks that
  the owner was heard from recently and is not `ST_IDLE`/`ST_CHARGING`
  (`src/amr.py:4700-4709`), with no check on whether the owner's task is actually
  advancing. Re-assignment (`src/amr.py:4711-4728`) therefore triggers reliably on node
  failure or silence, but not on a live node that has stalled mid-task (e.g., wedged
  against an obstacle it cannot resolve). This is a real gap between "fault-tolerant to
  node death" and "fault-tolerant to node hangs," and it is not exercised by any fault
  scenario in the repository at this HEAD.

---

## 3. Two things that are NOT limitations

**`auction_bundle` matching `auction` exactly on `showcase_chokepoint` is correct
behavior, not a bug.** `_future_network_healthy` (`src/amr.py:2890`, condition at `:2900`) returns `False`
whenever `self.blocks.members` is non-empty, and `showcase_chokepoint`'s map has exactly
one 13-cell block. Every bundle-reservation path is therefore gated off by design on this
map, and the policy correctly reduces to plain `auction`. On `showcase_open_floor`, which
has no single-file blocks, `auction` completes 7/8 tasks and times out while
`auction_bundle` completes 8/8 at 115.66 s under the same defaults — the feature does
work where it is supposed to. The real, smaller defect worth listing is item 2.12's
sibling concern: the dashboard's default scenario (`showcase_chokepoint`, per common
demo scripts) is one where its own headline allocator is structurally inert, which is a
demo-narration risk, not an allocator defect.

**The "`BIOS_1.0.0` makes 15 robot-rack contacts" figure is refuted and must not be
repeated.** It appears only inside a section of `archive/FINDINGS.md` that the document
itself marks, in its own text: "**CORRECTION, 2026-08-25. The table below is
superseded — do not quote it.**" (`archive/FINDINGS.md:189`). The corrected figure is zero,
and `contacts_robot_rack` summed across every run in all 13 checked-in benchmark
artifacts (1,030 runs total) is zero everywhere, confirmed this session by direct
inspection of the artifact files. Citing the "15" figure without the correction
misrepresents the repository's own documented history.

---

## What we would build next, in order

Ordered by how much each closes the weakest currently-standing claim, not by
implementation effort:

1. **Add pedestrians to the acceptance benchmark (§2.4).** The human-safety claim rests
   on 7.83 robot-hours from showcase scenarios never designed as a statistical evidence
   base, while the flagship 88.65 robot-hour benchmark that everything else cites has no
   humans in it at all. This is the single biggest gap between what requirement 8 claims
   and what the acceptance benchmark actually measures, and it is a scenario-construction
   change, not a new mechanism.
2. **Fix the `_chi2_quantile` approximation (§1.2).** A one-line numerical fix
   (use an exact chi-squared quantile, or a tighter approximation) that changes every
   published safety bound by under 1% and removes a "the bound itself has a known
   inaccuracy" objection a statistically literate judge could raise.
3. **Fix `preassigned` on showcase scenarios (§2.3).** A scenario family currently
   capable of silently reporting a flawless collision-free result from four robots that
   never moved is a credibility risk if triggered live by an unexpected `--allocation-policy`
   flag during Q&A.
4. **Reconcile the four stale documents and the lint failure (§2.1, §2.13).** Cheap,
   mechanical, and each one is a specific, checkable claim a prepared judge can catch —
   exactly the kind of small miss that costs credibility disproportionate to its size.
5. **Fix `plan_calls` undercounting (§2.9).** Strengthens the edge-feasibility argument
   (requirement 15) with its own numbers rather than a caveat; needed before quoting a
   tighter CPU-budget margin in front of BEL evaluators who will ask about edge
   headroom directly.
6. **Add a dashboard-loading test (§2.11).** The largest coverage gap in the repository
   by line count, and the one most likely to fail exactly when a live demo needs the
   dashboard to work — a broken wire between `index.html` and `main.js` currently has a
   nonzero chance of surfacing for the first time in front of judges instead of in CI.
7. **Run on real hardware (§1.1).** The highest-value item and the most expensive: a
   single successful run on a Raspberry Pi or Jetson, even a short one, converts the
   entire edge-deployment story from an ESTIMATE-labelled analysis to a measurement, and
   is the one limitation on this page no software fix can substitute for.
