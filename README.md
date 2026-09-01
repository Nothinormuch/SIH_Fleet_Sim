# SIH_Fleet_Priority — SIH26123

**Edge-AI Based Distributed Fleet Coordination for Autonomous Mobile Robots (AMRs) in
Smart Warehouses** · Bharat Electronics Limited · Software · Robotics and Drones

A multi-robot warehouse simulation, a peer-to-peer coordination protocol, and a
benchmark harness for decentralized AMR priority and path-conflict resolution.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest -q
python backend/server.py                       # http://127.0.0.1:8000
python edge_demo.py --robots 3 --duration 5    # real processes + signed UDP
python fault_campaign.py --seeds 30 --jobs 8  # loss, partition, crash recovery
python benchmark.py --seeds 30 --jobs 8        # strict SIH acceptance gate
```

The simulation core and the benchmark have **no third-party dependencies** — stdlib
only, so a robot node drops onto a bare Raspberry Pi image with no build step.

## Decentralized priority algorithm

The default policy is `BIOS_PIBT.6` with peer `auction` allocation. It combines V5's
battery-aware priority/deadline cargo allocation with event-triggered communication,
bounded predictive hints and measured recovery escalation. Every AMR broadcasts a
frozen lexicographic priority plus its next-cell intent. On grid-like rack maps it
plans on a strongly connected one-way circulation graph and takes an expiring,
two-phase peer lease on every destination cell. This prevents head-on entry and restores
one-robot-per-cell ownership before a queue forms. Merge contenders use the same frozen
total order; no process assigns moves and the dashboard is a passive observer.

BIOS 6 adds short-horizon occupancy prediction, distributed congestion experience,
charger-aware dock choice, load-aware idle-lane clearing, bounded auction-churn backoff,
peer winner nomination after persistent split views and machine-derived decision
explanations. BIOS 5 remains selectable as the frozen release baseline. The BIOS 6
multi-seed safety, liveness, performance and multi-process gates are documented in
[`docs/BIOS_PIBT_6_PREDICTIVE_INTELLIGENCE.md`](docs/BIOS_PIBT_6_PREDICTIVE_INTELLIGENCE.md).

Maps that cannot be oriented without losing reachability use bounded directional task
waves, block leases, early mouth staging and PIBT. Local 50 Hz protective stopping
remains authoritative for every policy.

See [`docs/BIOS_PIBT_3_PROTOCOL.md`](docs/BIOS_PIBT_3_PROTOCOL.md) for the complete
allocation/traffic relationship, state machine, conditional liveness argument and
current benchmark evidence. V2 remains documented in
[`docs/BIOS_PIBT_2_PROTOCOL.md`](docs/BIOS_PIBT_2_PROTOCOL.md).
Version 1 remains documented in
[`docs/DECENTRALIZED_PRIORITY.md`](docs/DECENTRALIZED_PRIORITY.md).

---

## The position this project takes

The problem statement asks for a fully decentralised fleet and treats centralisation as
the flaw. That framing does not survive contact with how AMR fleets are actually built,
and a submission that repeats it back is arguing from a false model. The specific errors
are catalogued in [`docs/CRITIQUE.md`](docs/CRITIQUE.md); the short version:

- **"Centralised" is not "cloud."** Real fleets (Amazon Robotics, Locus, Geek+, 6 River,
  OTTO) run an on-prem fleet manager on the LAN at 1–5 ms. The latency argument only
  works against an architecture nobody deploys.
- **The latency numbers do not survive arithmetic.** At 1.2 m/s, 50 ms is 6 cm. Global
  routing runs at 0.1–1 Hz anyway. Localisation error causes warehouse collisions;
  network round-trip does not.
- **Peer-to-peer does not fix Wi-Fi dead zones.** In infrastructure mode the access
  point relays peer frames — same radio, same hole. `tests/test_core.py` tests exactly
  this. The fix is a different link layer (802.11s, Wi-Fi Direct, UWB), which the
  statement never mentions.
- **"Zero inter-robot collisions" is not a testable claim.** Absence over finitely many
  runs bounds a rate; it does not establish zero. And over an asynchronous lossy channel
  no protocol can guarantee agreement at all (Fischer–Lynch–Paterson).
- **N ≥ 3 cannot test the hypothesis.** The justification for decentralising is scaling;
  congestion and cascading deadlock appear north of 20 robots.

So this repository implements a **hierarchy**, and treats full decentralisation as a
*degraded mode* rather than a superior architecture:

| Layer | Rate | Where it runs | What it does |
| --- | --- | --- | --- |
| **0 — Safety** | 50 Hz | Onboard, certified, **never network-dependent** | Protective stop. Sized by own speed *and* closing speed. Sees anything, including things that do not broadcast. |
| **1 — Local traffic** | 10 Hz | Onboard | Peer intents, block-level exclusion, deadlock breaking, give-way manoeuvres. |
| **2 — Global route** | 1 Hz | Fleet manager when reachable, P2P when not | Prioritised space-time A*. Optimal when the network is healthy. |

Under ISO 3691-4 / EN ISO 13849, protective stopping must be local, independent and
certified — it may not wait on a radio packet. **Messaging buys efficiency; it never
buys safety.** Layer 0 does not import the protocol module.

---

## Layout

```
src/
  settings.py      tunables; the physical envelope of a real AMR, and the braking equations
  geometry.py      vectors, angles, swept segment distance
  environment.py   grid map, warehouse generators, single-file block decomposition
  planner.py       A*, space-time A* with reservations, prioritised fleet planning
  priority.py      deterministic next-cell PIBT, inheritance and backtracking
  topology.py      2-core/tree decomposition for temporary exit priority
  messages.py      validated, authenticated P2P wire protocol with relative TTLs
  transport.py     seeded network model + replay-safe real UDP multicast transport
  world.py         ground truth: kinematics, 360° sensing, swept collision detection
  amr.py           the agent: three control loops. Pure — no I/O, no clock, no globals
  fleet_manager.py the optional central optimiser, and the strong baseline
  assignment.py    dependency-free Hungarian assignment implementation
  task_allocation.py allocation policy contracts, separate from route coordination
  metrics.py       Poisson rate intervals, honest policy comparison
  scenarios.py     pinned, seeded benchmark scenarios including a negative control
  benchmark.py     strict paired SIH acceptance gate and JSON/CSV evidence writer
  main.py          the headless runner and CLI
  edge_runtime.py  50 Hz fail-safe node loop + UDP sensor/actuator adapter
  distributed_demo.py independent process launcher with physics-only referee
  fault_campaign.py packet-loss, partition-heal and crashed-winner release gate
backend/
  server.py        stdlib HTTP server: serves the frontend, runs sims on request
frontend/
  index.html       professional digital-twin control-room shell
  css/digital-twin.css responsive desktop, jury and mobile presentation layer
  css/style.css    palette shared with the generated asset set
  js/digital-twin.js Three.js warehouse, AMRs, humans, paths, leases and mesh links
  js/environment.js  asset loading, world->screen transform, static warehouse layer
  js/amr.js        robots, status halos, payload, the human worker
  js/network.js    the coordination layer: intent, peer links, wait-for arrows
  js/main.js       fetch, interpolated playback, panel binding
  assets/          generated sprite set (256 px per cell)
tests/             core, priority, benchmark-integrity and dashboard regression tests
docs/              acceptance evidence, V3/V2 protocols, V1 design, critique and findings
deploy/            hardened systemd service for one process per AMR
config/            non-secret example edge-node environment
artifacts/benchmarks/ checked-in raw and summarized acceptance evidence
reference/         asset prompt pack and loader spec
```

### The one design decision everything rests on

`AMRBrain.step(t, sensors, inbox) -> (actuation, outbox)` — **the agent does no I/O.**
Transport and world are injected. That single constraint buys three things at once:

1. The same brain runs as a real UDP process, so decentralisation is something a judge
   can packet-capture (`tcpdump -i any port 26123`), not something they have to take on
   trust.
2. The same brain runs headless against a seeded network model at hundreds of times
   realtime, which is the only way to get a collision *rate with a confidence interval*
   instead of an anecdote.
3. The same brain drops onto a Pi unmodified — the direct answer to the statement's
   contradiction between "must run on constrained edge hardware" and "deliver a
   simulation".

---

## Coordination policies

All policies are fields on one class, sharing one trajectory follower, one safety layer and
one physics interface — so any difference between them is caused by coordination and
nothing else. Separately tuned controllers would make the comparison meaningless.

| Policy | What it is | Why it is here |
| --- | --- | --- |
| `stop_and_wait` | Textbook: follow your own shortest path, stop when the next cell is occupied. | The weak baseline the statement names. Implemented faithfully, not as a straw man. |
| `central` | Fleet manager plans everything with prioritised space-time A*. Robots follow the schedule; no peer negotiation. | **The strong baseline the statement omits** — what every deployed fleet actually runs. Beating only stop-and-wait proves nothing. |
| `hierarchical` | Central plans when reachable, P2P negotiation when not, Layer 0 always. | The proposal. Full decentralisation as a fallback, not an ideal. |
| `BIOS_1.0.0` | Decentralized block leases plus an aggressive local unstick manoeuvre. | Existing experimental liveness policy retained for comparison. |
| `BIOS_PIBT.1` | Replicated PIBT next-cell resolution, rich priorities and corridor leases. | Retained regression baseline; it gridlocks under the 24-AMR stress seed. |
| `BIOS_PIBT.2` | Strongly connected directed routes, two-phase destination-cell leases, merge priority and route-discontinuity repair. | V3 traffic foundation and retained benchmark. |
| `BIOS_PIBT.3` | V2 traffic plus replicated batch auction, drop admission, bounded directional waves, completion gossip and invariant repair. | Retained decentralized comparison policy. |
| `BIOS_PIBT.5` | V3 invariants plus full-commitment energy admission, payload/cargo factors, priority/deadline ordering, a live three-robot candidate set, bounded bid bundles and charging re-entry. | Frozen decentralized release baseline. |
| `BIOS_PIBT.6` | V5 plus event-triggered traffic, decaying peer congestion experience, soft anonymous-moving-object forecasts, charger contention avoidance, load-aware idle clearing, churn recovery and decision traces. | Default fully decentralized route + allocation policy. |

Task ownership is selected independently of the route policy. `auction` lets peers
broadcast bids and converge on deterministic leased awards; this is the fully
decentralized V3 mode. `hungarian` lets the optional fleet manager minimise the
robot-to-task cost matrix and exists only as a comparison baseline. Keeping allocation
and traffic separate makes their performance effects measurable rather than conflated.

The BIOS 5 energy and cargo model is specified in
[`docs/BIOS_PIBT_5_ENERGY_AUCTION.md`](docs/BIOS_PIBT_5_ENERGY_AUCTION.md). Its refined
eight-seed energy-stress result improves completion from 7/8 to 8/8 while reducing
aggregate auction bids by 47.20% and total messages by 24.64%. Its separate SIH release
gate passes 90/90 candidate runs across 4-, 6-, and 8-robot fleets.

`--seeds N` pools runs so the safety statistics have enough exposure to mean something.

---

### `BIOS_4` — the learned one

A 549-parameter network chooses among five verbs the fleet already implements — proceed,
hold, yield to a passing bay, respect the block token, replan — at the 10 Hz traffic
layer. **It does not drive the wheels**, and that is deliberate: `_safety()` has final
authority over actuation, so a model trained to emit velocities would spend its capacity
rediscovering an envelope it is not allowed to leave, and sim-to-real would become a
question about chassis dynamics instead of about decisions.

Everything that must hold regardless of what the network learned stays in ordinary Python:
panic-on-stick fires above the model on its own timer, Layer 0 sits below it, and
unexecutable verbs are masked. A badly trained BIOS_4 is slow, not unsafe.

```bash
# train (about 45 minutes on 12 cores, writes models/bios4.json)
python -m src.evolve --population 24 --generations 30 --workers 12

# report it against every baseline on the HELD-OUT seeds
python -m src.evolve --evaluate models/bios4.json --workers 12
```

Or do both from the dashboard: pick `BIOS_4` and the Train / Upload buttons appear.
Training runs as a background job you can watch and cancel; the model downloads as a
`.json` you can re-upload here or flash onto a robot.

Training seeds (0–7) and evaluation seeds (8–11) are disjoint and the trainer refuses to
cross the line — training and reporting on the same seed turns the headline into a
memorisation score. Result and caveats: `docs/FINDINGS.md`.

## Status — read this before quoting any number

The strict SIH acceptance benchmark now passes all 90 paired seeds across 4-, 6- and
8-robot fleets. `BIOS_PIBT.5` completes 30/30 runs at every fleet size; stop-and-wait
completes 0/30 before the fixed 1200 s cutoff. The minimum conservative per-seed
completion-time reduction bounds are **63.64%**, **51.17%** and **34.16%** respectively,
all above the required 20%. All 1,620 candidate tasks complete with zero observed
robot/robot, robot/human or robot/rack contacts across 88.6512 robot-hours.

These are right-censored lower bounds, not exact speedups: the baseline makespans are
unknown because the baseline never finishes. A candidate result at time `C` and an
unfinished baseline at cutoff `D` establish only that the true reduction is greater
than `1 - C/D`. The release gate uses the minimum bound across seeds, not a favorable
average, and refuses candidate timeouts or mismatched workload fingerprints.

See [`docs/SIH_ACCEPTANCE_BENCHMARK.md`](docs/SIH_ACCEPTANCE_BENCHMARK.md) for the exact
method, limitations and commands. Raw evidence is checked in as
[`artifacts/benchmarks/sih-acceptance.json`](artifacts/benchmarks/sih-acceptance.json)
and [`artifacts/benchmarks/sih-acceptance.csv`](artifacts/benchmarks/sih-acceptance.csv).
The complete Python regression suite, lint, Python compilation and frontend JavaScript
syntax checks are release gates; run them again before quoting a new commit as evidence.

## The dashboard

`python -m backend.server` then open <http://127.0.0.1:8000>. The default experience is
an interactive 3D warehouse digital twin with orbit, tactical, selected-AMR follow and
robot-POV cameras. Click an AMR to inspect battery reserve, cargo, task, deadline and P2P
neighbours; use **Jury Mode** for a clean, automatically narrated full-screen playback.
A dedicated operations dock below the scene holds camera, playback and selected-AMR
telemetry, so no robot card or transport control covers the warehouse. A 2D diagnostic
view remains available for protocol inspection.

When `BIOS_PIBT.6` is selected, the side rail adds a **Collective Intelligence** panel.
It reports measured packet suppressions, forecasts and reroutes, then replays bounded
decision records produced by the controller itself. The text is not generated after the
run and does not claim that the AMRs use an LLM.

The public demo library is deliberately limited to five explainable stories: Open Floor,
Chokepoint, Human Interaction, Dead-Zone Mesh and Grand Challenge. The benchmark and
regression scenarios still exist in the simulation core, but are not mixed into the jury
UI. Every showcase defaults to the decentralized `auction` allocator, heterogeneous
battery state and cargo-aware energy admission. A WMS injects tasks; it never selects a
winner. Each AMR admits a bid only when the task, cargo factor and post-task charger return
remain above the protected reserve.

The previous checked-in acceptance campaign remains BIOS 5 versus stop-and-wait evidence;
it must not be relabelled as BIOS 6 evidence. The final BIOS 6 three-seed showcase matrix
records zero observed robot/robot, robot/human and robot/rack contacts in 10.019 candidate
robot-hours. Open Floor and Human Interaction retain exact per-seed makespan parity while
cutting messages by 38.4% and 32.7%. BIOS 6 completes all 24 Chokepoint tasks versus
V5's 22, and all 18 Dead-Zone tasks versus V5's 16. In Grand Challenge fixed windows it
completes 47 tasks versus 27 (+74.1%), cuts waits by 58.4% and messages by 33.8%, with no
per-seed task-count regression. These are simulation observations, not physical safety
certification or a universal speedup claim. Exact evidence and limitations are in
[`docs/BIOS_PIBT_6_PREDICTIVE_INTELLIGENCE.md`](docs/BIOS_PIBT_6_PREDICTIVE_INTELLIGENCE.md).

Pick a showcase, route policy, task-allocation policy, fleet size and seed; the server
runs the simulation and returns the map, every telemetry frame and the result summary,
and the page plays it back with a scrubber. Three.js and OrbitControls are vendored under
`frontend/vendor/`, so the demo does not depend on venue Wi-Fi or a CDN.

It draws the things that are otherwise invisible, because a warehouse of moving robots
looks the same whether it is coordinating or getting lucky:

- **broadcast intent** — the cell horizon each robot publishes in its INTENT message,
  fading along the horizon as the time windows do
- **peer links** — who can currently hear whom, straight from each robot's peer table
- **wait-for arrows** — who is blocked on whom. Two arrows pointing at each other *is*
  the cycle the distributed deadlock detector searches for
- **single-file blocks** — the runs of aisle the traffic layer applies block control to
- **task allocation** — `TASK_NEW`, `BID`, `AWARD` and `TASK_DONE` messages for the
  peer auction, or directed manager awards for Hungarian allocation
- **mapped human workers** — three in Human Interaction and five in Grand Challenge,
  with rack-safe pedestrian routes, high-visibility 3D PPE models and yield state.
  They publish nothing; only the onboard safety layer sees them

The run endpoint is POST-only, size- and workload-bounded, and protected by strict
request validation and browser security headers. Playback rather than a live socket:
the sim runs far faster than realtime, so streaming
would mean throttling it back to wall-clock for no benefit, and a recorded run can be
paused on the frame where two robots negotiate a chokepoint and replayed against a
different policy on the same seed.

## Real edge processes and failure campaigns

`python edge_demo.py --robots 3 --duration 5` starts a distinct operating-system process,
brain, clock epoch, replay window and authenticated multicast socket for every AMR. The
parent is a physics/lidar referee only; it never forwards peer traffic or chooses routes,
bids, priorities or motor commands. `edge_node.py` replaces that referee pipe with a
validated UDP sensor/actuator bridge and fails safe when sensor frames are invalid or
stale.

`python fault_campaign.py --seeds 30 --jobs 8` is a separate release gate covering
0/5/10/20% packet loss, partition and healing, and crash of the current auction winner.
The lease expiry reopens work for surviving robots; a failed chassis remains a sensed
physical obstacle. Dynamic anonymous obstacles are promoted to expiring local blocked
cells only after persistent observations, then routes are recalculated.

See [`docs/EDGE_DEPLOYMENT.md`](docs/EDGE_DEPLOYMENT.md) for clean-clone/venv commands,
[`docs/WIRE_PROTOCOL.md`](docs/WIRE_PROTOCOL.md) for the validated packet contract and
[`docs/DEMO_AND_JUDGING.md`](docs/DEMO_AND_JUDGING.md) for the live judging sequence.
The deployment runbook also covers the Raspberry Pi service.
Actual Pi/Jetson CPU and memory claims still require running the included harness on that
named device; local Mac timing is intentionally not presented as Pi evidence.
