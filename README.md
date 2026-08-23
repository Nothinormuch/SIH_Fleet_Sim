# SIH_Fleet_Sim — SIH26123

**Edge-AI Based Distributed Fleet Coordination for Autonomous Mobile Robots (AMRs) in
Smart Warehouses** · Bharat Electronics Limited · Software · Robotics and Drones

A multi-robot warehouse simulation, a peer-to-peer coordination protocol, and a
benchmark harness that measures the three coordination policies against each other on
fixed, seeded scenarios.

```bash
python backend/server.py                 # dashboard -> http://127.0.0.1:8000
python -m pytest tests -q                # 19 tests
python run.py --scenario crossing_chokepoint --policy all --robots 4 --seeds 3
python run.py --scenario dense_aisles --policy hierarchical --json out/run.json
```

The simulation core and the benchmark have **no third-party dependencies** — stdlib
only, so a robot node drops onto a bare Raspberry Pi image with no build step.

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
  messages.py      the P2P wire protocol (JSON over UDP multicast)
  transport.py     seeded network model + real UDP multicast socket, one interface
  world.py         ground truth: kinematics, 360° sensing, swept collision detection
  amr.py           the agent: three control loops. Pure — no I/O, no clock, no globals
  fleet_manager.py the optional central optimiser, and the strong baseline
  metrics.py       Poisson rate intervals, honest policy comparison
  scenarios.py     pinned, seeded benchmark scenarios including a negative control
  main.py          the headless runner and CLI
backend/
  server.py        stdlib HTTP server: serves the frontend, runs sims on request
frontend/
  index.html       dashboard shell
  css/style.css    palette shared with the generated asset set
  js/environment.js  asset loading, world->screen transform, static warehouse layer
  js/amr.js        robots, status halos, payload, the human worker
  js/network.js    the coordination layer: intent, peer links, wait-for arrows
  js/main.js       fetch, interpolated playback, panel binding
  assets/          generated sprite set (256 px per cell)
tests/             19 regression tests
docs/              CRITIQUE.md, FINDINGS.md
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

## The three policies

All three are fields on one class, sharing one trajectory follower, one safety layer and
one physics interface — so any difference between them is caused by coordination and
nothing else. Three separately tuned controllers would make the comparison meaningless.

| Policy | What it is | Why it is here |
| --- | --- | --- |
| `stop_and_wait` | Textbook: follow your own shortest path, stop when the next cell is occupied. | The weak baseline the statement names. Implemented faithfully, not as a straw man. |
| `central` | Fleet manager plans everything with prioritised space-time A*. Robots follow the schedule; no peer negotiation. | **The strong baseline the statement omits** — what every deployed fleet actually runs. Beating only stop-and-wait proves nothing. |
| `hierarchical` | Central plans when reachable, P2P negotiation when not, Layer 0 always. | The proposal. Full decentralisation as a fallback, not an ideal. |

`--seeds N` pools runs so the safety statistics have enough exposure to mean something.

---

## Status — read this before quoting any number

**Working and covered by tests:** the map and block decomposition, A* and space-time A*
(verified to resolve a head-on corridor with zero space-time clashes), the physics and
swept collision detection, the two-channel speed-scaled protective field, the network
model including the dead-zone result, the Poisson statistics, and the end-to-end
single-robot path.

**Not yet converged: multi-robot throughput.** Runs are collision-free across all three
policies (0 contacts, worst separation ≈ 0.87 m over 1.4 robot-hours), but no policy
completes a full task set within the scenario duration, and `hierarchical` currently
performs **worse** than `central` rather than matching it. The remaining cause is
identified and written up in [`docs/FINDINGS.md`](docs/FINDINGS.md) §7: robots that
finish work still obstruct cells other robots need, and the hierarchical policy churns
between central schedules and local replanning.

No speedup figure is published here, because there is not yet an honest one to publish.
`metrics.compare()` deliberately returns `"incomparable"` rather than a ratio when a
policy fails to complete — a policy that does not finish is a different kind of result,
not an infinitely slow one, and folding it into a percentage would be the exact
dishonesty this project is arguing against.

## The dashboard

`python backend/server.py` then open <http://127.0.0.1:8000>. Pick a scenario, policy,
fleet size and seed; the server runs the simulation and returns the map, every telemetry
frame and the result summary, and the page plays it back with a scrubber.

It draws the things that are otherwise invisible, because a warehouse of moving robots
looks the same whether it is coordinating or getting lucky:

- **broadcast intent** — the cell horizon each robot publishes in its INTENT message,
  fading along the horizon as the time windows do
- **peer links** — who can currently hear whom, straight from each robot's peer table
- **wait-for arrows** — who is blocked on whom. Two arrows pointing at each other *is*
  the cycle the distributed deadlock detector searches for
- **single-file blocks** — the runs of aisle the traffic layer applies block control to
- **the human worker** — dashed ring, because they publish nothing and cannot be
  negotiated with. Only the onboard safety layer sees them at all

Playback rather than a live socket: the sim runs far faster than realtime, so streaming
would mean throttling it back to wall-clock for no benefit, and a recorded run can be
paused on the frame where two robots negotiate a chokepoint and replayed against a
different policy on the same seed.

**Not built yet:** the distributed multi-process runner (the UDP transport class exists
and is unit-tested; the process launcher is not written), and the packet-loss / partition
sweep plots.
