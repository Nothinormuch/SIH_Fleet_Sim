# 02. ARCHITECTURE

> This document establishes the shape of the system: one agent class with a single
> pure entry point, a control stack split across three timescales, and the injection
> boundaries that let the same code run as a benchmark, as separate OS processes, and
> on an edge board.

**Audience:** SIH judges and BEL evaluators reading this codebase for the first time, and
teammates who must defend the layering claim under questioning.
**Reads best after:** [00. Problem Statement](00-PROBLEM-STATEMENT.md)

Every behavioural claim below carries a `file:LINE` citation against commit `07337e0`
(plus one uncommitted frontend change that touches no file discussed here). Where a claim
in an existing document does not survive reading the code, this document says so instead
of repeating it — see [§9](#9-architectural-gaps-found-while-writing-this).

---

## Requirements evidenced

| # | Requirement | Where | Evidence |
|---|---|---|---|
| 1 | At least 3 AMRs | [§4](#4-one-tick-end-to-end), [§6](#6-process-and-deployment-topologies) | `src/main.py:146` builds one `AMRBrain` per start cell; `src/distributed_demo.py:164` refuses fewer than three processes |
| 3 | Decentralized communication | [§1](#1-the-signature-everything-rests-on), [§5](#5-the-worldagent-boundary) | `src/amr.py:547` returns an outbox instead of sending; `src/world.py:1` states the referee never forwards a packet; the two transports are `src/transport.py:75` and `src/transport.py:194` |
| 6 | No central coordination server | [§6](#6-process-and-deployment-topologies) | `src/main.py:104` lists the only policies that get a manager; `src/main.py:166` is the sole construction site, so every other policy runs with `manager = None` (`src/main.py:165`) |
| 8 | Collision avoidance — layer placement only | [§2](#2-the-layered-control-stack) | `src/amr.py:603` applies `_safety` after every policy's output on every tick; `_safety` (`src/amr.py:868`) reads only `Sensors` |
| 15 | Edge / local execution | [§1](#1-the-signature-everything-rests-on), [§6](#6-process-and-deployment-topologies) | `src/edge_runtime.py:266` runs one brain against real hardware I/O at `safety_hz`; `pyproject.toml:11` declares zero runtime dependencies and no module in `src/` imports anything outside the standard library |
| 16 | Fleet dashboard | [§6](#6-process-and-deployment-topologies) | `backend/server.py:616` runs a simulation on request and returns map + frames + summary from `src/main.py:567` |

Requirements 2, 4, 5, 7, 9–14, 17–20 are exercised by this architecture but are evidenced
in the sibling documents that own them: [Protocol](03-DECENTRALIZED-PROTOCOL.md),
[Path Planning](04-PATH-PLANNING.md), [Coordination Policies](05-COORDINATION-POLICIES.md),
[Task Allocation](06-TASK-ALLOCATION.md), [Dashboard](09-DASHBOARD.md),
[Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md).

---

## 1. The signature everything rests on

```python
# src/amr.py:547-548
def step(self, t: float, sensors: Sensors,
         inbox: list[msg.Message]) -> tuple[Actuation, list[msg.Message]]:
```

The agent is a function of `(time, sensor frame, received messages)` returning
`(wheel command, messages to send)`. It opens no socket, reads no clock, touches no file,
and holds no module-level mutable state (`src/amr.py:148`).

### What the signature forbids, mechanically

| Capability an agent might want | How it is denied | Consequence |
|---|---|---|
| Send a packet | `step` has no transport reference; it appends to a local `outbox` list (`src/amr.py:549`) that the caller drains | The caller chooses the wire. Nothing in the agent knows whether it is UDP or a heap |
| Receive a packet | Messages arrive only as the `inbox` argument, consumed at `src/amr.py:550` | A test can hand the agent an arbitrary message history with no network |
| Read wall-clock time | `t` is a parameter. The only `time` calls are `time.perf_counter()` around the planner for CPU accounting (`src/amr.py:2697`, `src/amr.py:2753`) — they influence a statistic, never a decision | Simulated time can advance 300× faster than wall clock |
| Read another robot's true state | The agent imports two type names from the world and nothing else: `from .world import Actuation, Sensors` (`src/amr.py:59`) | There is no handle through which ground truth could arrive |
| Persist state | `export_terminal_records()` (`src/amr.py:472`) returns a list; the runtime decides whether to write it (`src/edge_runtime.py:121`) | Disk latency can never delay a control tick |

The one deliberate exception to "no clock" is `time.perf_counter()` in `_replan`. It is
worth naming because a judge will find it: it measures the planner and feeds
`plan_cpu_max_ms` in the report. It is the only place the agent's behaviour differs
between two runs of the same seed (see [§7](#7-determinism-and-seeding)).

### Why this one signature serves three deployments

The same `AMRBrain` object is constructed identically in all three, and the only thing
that changes is who calls `step` and what they pass it.

| Deployment | Constructed at | `step` called from | Inbox source | Outbox sink | Sensor source | Status |
|---|---|---|---|---|---|---|
| Headless benchmark (in-process) | `src/main.py:153` | `src/main.py:338` | `SimNetwork.poll` (`src/transport.py:182`) | `SimNetwork.send` (`src/transport.py:144`) | `World.sense` (`src/world.py:739`) | Implemented and tested |
| Multi-process UDP demo | `src/distributed_demo.py:47` (inside a child process) | `src/edge_runtime.py:109`, reached via `src/distributed_demo.py:84` | `UdpMulticastTransport.poll` (`src/transport.py:263`) | `UdpMulticastTransport.send` (`src/transport.py:251`) | Parent referee pipes a `Sensors` object (`src/distributed_demo.py:242`) | Implemented and tested (`tests/test_edge_runtime.py`) |
| Single edge node on real hardware | `src/edge_runtime.py:366` | `src/edge_runtime.py:109`, reached via `src/edge_runtime.py:297` | same UDP transport | same UDP transport | `UdpJsonHardwareIO.read_sensors` (`src/edge_runtime.py:175`) | Implemented; **never executed on a Raspberry Pi or Jetson** — see [Edge Deployment](08-EDGE-DEPLOYMENT.md) and [Limitations](15-LIMITATIONS.md) |

`EdgeRuntime.tick` is the whole adapter, and it is eleven lines
(`src/edge_runtime.py:105-119`): poll the transport, rewrite the frame's timestamp to the
node's own monotonic epoch (`src/edge_runtime.py:107`), call `brain.step`, send every
outbox message, record the loop duration against the period. There is no second
implementation of the agent anywhere in the repository.

Two properties fall out of this that are worth stating to a jury:

* **Clock epochs are unrelated across nodes and nothing breaks.** The demo hands each
  child a deliberately absurd offset — `10_000.0 * (index + 1)` seconds
  (`src/distributed_demo.py:192`) — precisely so that any latent comparison of a
  sender's absolute timestamp against a receiver's clock would fail loudly. The wire
  therefore carries relative TTLs, not absolute deadlines: intent windows are rebased on
  receipt (`src/amr.py:5297-5300`), and task deadlines arrive as a remaining-time TTL
  (`src/amr.py:5332-5340`).
* **Zero third-party dependencies.** `pyproject.toml:11` declares `dependencies = []`,
  and an import audit of every file in `src/` and `backend/` finds only standard-library
  modules. `src/geometry.py:1` states the reason: the agent must drop onto a bare Pi
  image with no build step. (`requirements.txt:4-5` declares `websockets` and
  `matplotlib`, but no Python file in the repository imports either — see
  [§9](#9-architectural-gaps-found-while-writing-this).)

### What the signature does *not* prove

It does not prove the agent fits a Pi's CPU budget. `plan_cpu_max_ms` in every checked-in
result was measured on a developer host, and `archive/CRITIQUE.md:95-99` already says so.
The architecture makes the Pi measurement *possible without a code change*; it does not
substitute for it.

---

## 2. The layered control stack

```
                       inbox (peer datagrams)          sensors (lidar, pose, battery)
                              |                                    |
                              v                                    |
        +-------------------------------------------+              |
        | ingest / peer table / task claims  50 Hz  |              |
        +-------------------------------------------+              |
                              |                                    |
   Layer 2 GLOBAL ROUTE  1 Hz | _route_loop      A*, replan, mode   |
        (route_hz)            v                                    |
   Layer 1 LOCAL TRAFFIC 10 Hz| _traffic_loop    leases, PIBT,      |
        (reactive_hz)         v                  yields, deadlock   |
        - - - - - - - - - - - | - - - - - - - - - - - - - - - - -  |
                              |  self._hold, self.path, self.pidx   |
                              v                                    |
        +-------------------------------------------+              |
        | _follow   waypoint follower, braking      |<-------------+
        +-------------------------------------------+              |
                              | Actuation(v, omega)                 |
                              v                                    |
   Layer 0 SAFETY       50 Hz | _safety   READS SENSORS ONLY <------+
        (per tick)            v
                       Actuation (possibly v=0, safety_stop=True)
```

### The real constants

Every rate lives in one frozen dataclass, `Rates` (`src/settings.py:99-112`):

| Field | Default | Read at | Effect |
|---|---|---|---|
| `world_hz` | 50.0 | `src/main.py:123`, `src/distributed_demo.py:231` | Fixed physics timestep `dt = 1/50 s`, and therefore the rate at which `step` is called in simulation |
| `safety_hz` | 50.0 | `src/edge_runtime.py:281`, `src/edge_runtime.py:117` | Real-time loop period on an edge node, and the deadline each tick is scored against |
| `reactive_hz` | 10.0 | `src/amr.py:593` | Layer 1 gate: `_traffic_loop` runs when `t - _t_reactive >= 0.1 s` |
| `route_hz` | 1.0 | `src/amr.py:589`, `src/fleet_manager.py:116` | Layer 2 gate for the robot and for the optional manager's planning pass |
| `heartbeat_hz` | 5.0 | `src/amr.py:624` | Pose/intent broadcast gate |
| `telemetry_hz` | 10.0 | `src/main.py:350-351` | Trace decimation: one frame kept every `50/10 = 5` physics ticks |

Two precision notes that a careful judge will otherwise catch:

1. **`safety_hz` is never read by the agent.** In simulation, Layer 0 runs once per
   `step`, and `step` is driven at `world_hz` (`src/main.py:123`). The two constants
   happen to be equal at 50.0, so the numbers agree — but the constant that *sets* the
   simulated safety rate is `world_hz`. `safety_hz` governs only the real-time edge loop.
2. **Not everything is rate-gated.** `_ingest` (`src/amr.py:550`), `_expire_peers`
   (`src/amr.py:551`), `_observe_dynamic_obstacles` (`src/amr.py:585`), `_task_loop`
   (`src/amr.py:587`), the decentralized block-token refresh `_bios_claim`
   (`src/amr.py:600`), `_follow` (`src/amr.py:602`) and `_safety` (`src/amr.py:603`) all
   run on **every** tick. "Three rates" describes the three *coordination* loops, not the
   whole tick. The token refresh in particular is deliberate: the comment at
   `src/amr.py:598-599` explains that a chokepoint claim is renewed every control tick so
   a rival learns as early as possible.

### Why the rates are separated

The separation is an argument, not an optimisation, and `src/settings.py:101-105` states
it in the source: *only the slowest loop was ever a candidate for a central server, and it
is the one least hurt by latency.*

* **Layer 2 at 1 Hz** owns global route choice — A\* over the grid, penalties, dynamic
  obstacles, and the `CENTRAL_OK` / `DEGRADED_P2P` mode decision (`src/amr.py:2571-2573`).
  At 1 Hz a 4 ms LAN round trip (`src/settings.py:124`) is 0.4 % of a period, and at
  1.2 m/s (`src/settings.py:19`) it is under 5 mm of robot travel. This is the only loop a
  real fleet manager has ever owned, and killing the manager degrades it rather than
  stopping the robot — unless the policy is `central`, which parks by design
  (`src/amr.py:2580-2588`), demonstrating the single point of failure instead of arguing
  about it.
* **Layer 1 at 10 Hz** owns the discrete question "may I enter the next cell?"
  (`src/amr.py:1126-1128`). It consumes peer intent, leases and PIBT priorities. Running
  it at 50 Hz would cost 5× the CPU to re-answer a question whose inputs (5 Hz
  heartbeats) have not changed; running it at 1 Hz would let a robot commit to a cell a
  second after the information that forbids it arrived.
* **Layer 0 every tick** owns the continuous question "is this velocity safe?" It must be
  faster than everything else because it is the only layer whose failure is a collision
  rather than a delay.

### Layer 0 is not on the network path — audited, not asserted

This is the load-bearing claim of the whole submission, so here is the exact result of
reading the code rather than the comments.

**What Layer 0 reads.** `_safety` (`src/amr.py:868-954`) and its two helpers,
`_escape_motion_increases_clearance` (`src/amr.py:956-982`) and
`_speed_limit_from_traffic` (`src/amr.py:1389-1412`), read exactly:

* `sensors.clearance_omni_m`, `sensors.clearance_static_m`, `sensors.detections`,
  `sensors.pose`, `sensors.t`
* `self.cfg.robot` — the frozen `RobotSpec` (`src/settings.py:14`)
* `self.policy` — a string fixed at construction
* `self._creep_until` — a float set by Layer 1 (discussed below)
* `act` — the velocity Layer 1/the follower proposed

They read **no** `self.peers` entry, **no** `inbox`, and construct **no** message.
Verified by inspection of the whole block. The naming is a trap worth pre-empting:
`_speed_limit_from_traffic` sounds like a coordination function and is not — "traffic" there
means physical objects in the forward cone, and its only inputs are `sensors.detections`
and `sensors.clearance_static_m`.

**How the stop is computed.** Not by a tuned constant. `stop_field_m(v)`
(`src/settings.py:46-55`) is `v²/(2a) + v·τ + margin`, and
`max_speed_for_clearance(clearance, v_closing)` (`src/settings.py:61-96`) is that equation
solved for `v`, with a `v_closing` term because braking only slows *us* while an oncoming
robot keeps approaching. Layer 0 therefore caps the commanded speed at whatever the
measured gap can still absorb (`src/amr.py:930-937`), and it stops outright when anything
is inside the 0.45 m omnidirectional guard (`src/amr.py:890`, `src/settings.py:32`).

**Where the isolation is imperfect, stated plainly.** Layer 0 consults one field that
Layer 1 writes: `self._creep_until`. Fifteen sites in the traffic and recovery code push
it forward (`src/amr.py:1274`, `:1287`, `:1312`, `:1355`, `:1858`, `:1907`, `:1935`,
`:1950`, `:1982`, `:2115`, `:2195`, `:2224`, `:2302`, `:2629`, `:3447`), and several of
those decisions were reached using peer messages. So message-derived state *can* relax
the omnidirectional standstill guard. The relaxation is bounded three ways:

| Guard | Where | Effect |
|---|---|---|
| Speed ceiling | `src/amr.py:926` | A creep is capped at 0.20 m/s (0.12 m/s in the separating case), and further capped by `_speed_limit_from_traffic` on the same line |
| Non-zero clearance still required | `src/amr.py:923-925` | If the forward-cone limit is ≤ 0.02 m/s the creep is refused and the stop stands |
| Geometry overrides the timer for the release policies | `src/amr.py:909-915` | For `BIOS_PIBT.3/.5/.6` (`V3_AUCTION_POLICIES`, `src/amr.py:80`) a time-based creep is **rejected** unless `_escape_motion_increases_clearance` (`src/amr.py:956`) independently proves that the commanded velocity is opening every gap under 0.50 m |

The honest summary: for the default policy `BIOS_PIBT.6` (`src/main.py:745`), motion
inside the omni guard requires a purely geometric, sensor-only proof, and a message can
only *enable the attempt*, never authorise the motion. For the older `BIOS_1.0.0`,
`BIOS_4`, `BIOS_PIBT.1` and `BIOS_PIBT.2` policies the timed creep alone suffices
(`src/amr.py:892-896`, `:916-927`), which is a weaker property. Both behaviours are
pinned by tests: `tests/test_core.py:247`
(`test_decentralized_safety_guard_does_not_creep_inside_contact_envelope`),
`tests/test_core.py:262` (`test_v3_recovery_motion_must_increase_close_peer_clearance`)
and `tests/test_core.py:279`
(`test_v6_can_creep_out_of_omni_field_only_while_separating`).

**One more filing error to know about.** `_v6_clearance_unstick` (`src/amr.py:984-1122`)
sits under the `# Layer 0` banner at `src/amr.py:866`, and it *does* read the peer table
(`src/amr.py:1014`). It is not a Layer 0 function: it is called from `_traffic_loop`
(`src/amr.py:1272`), it selects a *target cell* rather than a velocity, and Layer 0
independently revalidates the resulting motion on every subsequent tick — its own
docstring says exactly that (`src/amr.py:993-995`). The section banner is misplaced; the
behaviour is not. If a judge greps for `self.peers` between lines 866 and 1124 they will
find this one hit, and the answer is above.

**The literal claim about imports.** `src/messages.py:8` says "Layer 0 in amr.py ignores
this module." At function granularity that is true. At file granularity it cannot be:
`src/amr.py:46` imports `messages` because Layers 1 and 2 live in the same file. On real
hardware the argument is stronger than any Python arrangement — `src/amr.py:877-880`
states that Layer 0 is a certified PLd/SIL2 scanner wired to the motor contactors, and
that modelling it in Python is a simulation convenience while *placing it below the
network* is the engineering claim. That claim is the one this codebase can and does
demonstrate. See [Safety](07-SAFETY.md) for the field geometry and the measured
separation distributions.

---

## 3. Module map

`src/` is 14,794 lines across 27 files. `backend/server.py` adds 828, `tests/` adds 4,044.

| File | Lines | Responsibility | The one thing to know |
|---|---:|---|---|
| `src/amr.py` | 5,685 | The agent. All three layers, every policy, the follower, the auction, the wire handling | Policies are **fields on one class** (`src/amr.py:25-30`), sharing one follower and one safety layer, so a throughput difference between them can only be coordination |
| `src/world.py` | 906 | Ground truth: kinematics, lidar synthesis, swept collision detection, humans, obstacles | The referee is deliberately dumb and **never forwards a packet between robots** (`src/world.py:1-7`) — that is what makes "decentralized" checkable rather than self-certified |
| `src/scenarios.py` | 843 | Pinned, seeded scenarios and the workload fingerprint | `workload_fingerprint` (`src/scenarios.py:100`) SHA-256s the map, starts, tasks, faults, radio model and **every controller constant**, so a comparator cannot silently pair two different experiments |
| `src/main.py` | 818 | The headless runner, the CLI, and the dashboard payload builder | The tick ordering at `src/main.py:9-20` is load-bearing: every robot senses the *same* world state before any of them acts |
| `src/messages.py` | 661 | The wire protocol: encode, decode, HMAC, validation | Every message is advisory, self-contained and idempotent (`src/messages.py:5-13`) — a lost INTENT cannot leave a receiver in a wrong state |
| `src/evolve.py` | 600 | Neuroevolution trainer for the `BIOS_4` policy | Train seeds 0–7 and eval seeds 8–11 are disjoint and the trainer refuses to cross the line |
| `src/baseline_comparison.py` | 467 | Three-way campaign: BIOS 6 vs competition stop-and-wait vs central | Pairs by a task-catalog digest because allocator choice is part of the independent variable |
| `src/bios4.py` | 454 | The learned policy's network, feature vector and model file format | The model picks among five verbs the fleet already implements; it never drives the wheels (`src/bios4.py:5-10`) |
| `src/metrics.py` | 439 | `PolicyResult`, Poisson rate intervals, paired comparison | Refuses to emit a speedup when a policy did not complete; reports separation *distributions*, not a collision count |
| `src/edge_runtime.py` | 401 | The deployment boundary: real-time loop, hardware I/O protocol, JSON/UDP bridge | Stale or malformed sensor frames command a stop and never reach the planner (`src/edge_runtime.py:292-295`) |
| `src/auction_v2_campaign.py` | 395 | Auction V2 release gate | Re-runs the campaign under `PYTHONHASHSEED` 0/1/42 and compares digests |
| `src/distributed_demo.py` | 366 | Multi-process referee: one OS process, socket and clock per AMR | The parent never forwards peer traffic and never chooses a move (`src/distributed_demo.py:4-7`) |
| `src/benchmark.py` | 320 | Strict paired SIH acceptance gate | Treats a stop-and-wait timeout as a right-censored lower bound, not a fabricated makespan |
| `src/settings.py` | 316 | Every tunable constant, and the braking equations | The **only** place cells and metres meet (`src/settings.py:4-5`); one grid cell is 1.4 m (`src/settings.py:305`) |
| `src/transport.py` | 312 | `SimNetwork` (seeded model) and `UdpMulticastTransport` (real sockets) | `peer_traffic_via_ap=True` by default (`src/settings.py:133`), because infrastructure Wi-Fi relays peer frames through the AP — so P2P inherits the dead zone |
| `src/environment.py` | 238 | The grid map, warehouse generators, single-file block decomposition | `corridors()` (`src/environment.py:164`) turns chokepoints into *blocks* acquired whole, like single-track railway signalling |
| `src/planner.py` | 209 | A\*, space-time A\* with reservations, prioritised fleet planning | Heap ties break on an explicit counter (`src/planner.py:92`), never on object identity, so two runs with one seed produce identical paths |
| `src/task_protocol.py` | 204 | Task identity hashes and completion certificates | Separates transient *auction epoch* from durable *generation*, so a valid completion cannot be resurrected by a retry |
| `src/topology.py` | 202 | 2-core/tree decomposition, and the one-way circulation graph | `directed_circulation` (`src/topology.py:165`) is what removes head-on edges on rack maps; it returns `enabled=False` when orienting the map would break reachability |
| `src/priority.py` | 196 | `PriorityKey` and the deterministic PIBT next-cell resolver | No I/O, no clock, no singleton — the identical resolver runs headless and on an edge node (`src/priority.py:3-6`) |
| `src/fault_campaign.py` | 171 | Loss / partition / crashed-winner release gate | — |
| `src/terminal_journal.py` | 153 | Atomic, bounded on-disk persistence of completion certificates | Written *after* the actuation is sent (`src/edge_runtime.py:299-302`) so disk latency never delays a control command |
| `src/assignment.py` | 84 | Dependency-free Hungarian assignment | Used only by the optional manager; ties break on input order so results are stable |
| `src/geometry.py` | 77 | Vectors, angles, swept segment distance | `segments_min_distance` (`src/geometry.py:68`) is what catches two robots exchanging cells in one tick — the case an endpoint check misses |
| `src/task_allocation.py` | 24 | The four allocation policy names and their validator | Contains no planning or traffic code by design, so allocation and routing stay independently selectable |
| `src/__init__.py` | 0 | Package marker | — |
| `backend/server.py` | 828 | Stdlib HTTP server: serves the frontend, runs a simulation per POST | Playback, not streaming (`backend/server.py:7-14`); one `_SIM_LOCK` serialises CPU-bound runs (`backend/server.py:58`) |
| `run.py` | 7 | CLI entry point → `src.main:main` | The `python main.py …` invocation in `README.md:188` refers to a file that does not exist |

---

## 4. One tick, end to end

### The runner's ordering, and why it is that order

`src/main.py:9-20` documents the five phases and the reason for two of them:

```
for k in range(steps):                                   # src/main.py:223
    t = k * dt                                           # dt = 1/50 s  (src/main.py:123)

  1. scripted world events    manager kill, partition, heal, robot fail/restart,
                              obstacle appear/clear                  src/main.py:226-270
  2. WMS announcement         idempotent TASK_NEW multicast, repeated every 4 s
                                                                     src/main.py:295-317
  3. fleet manager tick       advice only; built for 3 of the 13 policies
                                                                     src/main.py:319-324
  4. every robot, in sorted id order                                 src/main.py:327
        net.set_position(rid, cell coords)      -> radio dead-zone model  :329
        sensors = world.sense(rid, pose_noise)                            :337
        act, outbox = brains[rid].step(t, sensors, net.poll(t, rid))      :338
        for m in outbox: net.send(t, rid, m)                              :339-340
  5. world.step(dt, cmds)     integrate physics, then swept contact checks
                                                                     src/main.py:348
     trace frame every 5th tick (10 Hz)                              src/main.py:350-352
```

Two ordering decisions are correctness properties, not style:

* **Sorted id order, not dict order** (`src/main.py:327`) so a run is reproducible across
  Python versions.
* **All robots sense before any robot moves.** Sensing happens inside phase 4 but physics
  integration is deferred to phase 5, so `world.sense` returns the same world state to
  every robot. `src/main.py:18-20` names the alternative and why it is wrong: stepping
  robot A into a new position before robot B senses would give A a physically impossible
  information advantage.

### Inside one `step`

```
 step(t, sensors, inbox)                                        src/amr.py:547
  |
  |-- _ingest(t, inbox)                             every tick   :550   peers, bids,
  |                                                                     awards, plans
  |-- _expire_peers(t)                              every tick   :551   silence => drop
  |                                                                     stale intent
  |-- _expire_task_claims(t)                        every tick   :552   lease expiry
  |
  |-- goal-progress accounting                      every tick   :573-583
  |-- _observe_dynamic_obstacles(t, sensors)        every tick   :585   anonymous blobs
  |                                                                     -> expiring
  |                                                                     local map layer
  |-- _task_loop(t, sensors, outbox)                every tick   :587   charge? accept?
  |                                                                     bid? complete?
  |
  |-- if t - _t_route  >= 1.0 s :  _route_loop      LAYER 2      :589-591
  |        mode = CENTRAL_OK if manager heard <1.5 s ago else DEGRADED_P2P  :2571
  |        decay contested-cell penalties                                   :2575
  |        A* replan when path exhausted or livelock                        :2684-2691
  |
  |-- if t - _t_reactive >= 0.1 s : _traffic_loop   LAYER 1      :593-595
  |        block/cell lease conflict, PIBT, yields, deadlock timer   :1214-1387
  |        sets self._hold                                           :1129, :1301
  |
  |-- _bios_claim(...)   (decentralized policies)   every tick   :600   renew block token
  |
  |-- act = _follow(t, sensors)                     every tick   :602   waypoint pursuit,
  |                                                                     brake for turns
  |-- act = _safety(sensors, act)                   LAYER 0      :603   FINAL AUTHORITY
  |
  |-- wait/stall accounting                         every tick   :605-622
  |-- if t - _t_hb >= 0.2 s : _broadcast(...)                    :624-626  heartbeat +
  |                                                                        intent + leases
  |-- meter every outbox message: count and encoded bytes        :628-640
  |
  '-> return (act, outbox)                                       :641
```

Note the direction of authority: `_traffic_loop` cannot command motion. It can only set
`self._hold` and rewrite `self.path`/`self.pidx`; `_follow` turns that into a velocity;
`_safety` gets the last word on every tick, unconditionally, on every policy — there is no
branch in `step` that returns before line 603.

The message meter at `src/amr.py:628-640` is worth pointing out: bytes are counted by
actually encoding each message (`len(msg.encode(m))`), so "messages per robot per second"
and "bytes per second" in the report are measured, not estimated. O(N²) chatter is a real
cost of decentralisation and this is where it is priced.

---

## 5. The world/agent boundary

The honesty test for "decentralized" is whether a robot's decisions use only what it
could actually sense or receive. Here is the complete inventory of what crosses the
boundary.

### What the simulator hands the robot

`World.sense(rid, pose_noise_m)` (`src/world.py:739-789`) returns a `Sensors`
(`src/world.py:80-104`):

| Field | Source | Would a real AMR have it? |
|---|---|---|
| `pose` | True pose plus optional Gaussian noise (`src/world.py:750-752`) | Yes — this is the localisation estimate |
| `v`, `omega` | Wheel odometry | Yes |
| `battery_frac` | `battery_wh / battery_full_wh` | Yes |
| `cell` | Quantised from the **noisy** pose (`src/world.py:782`) | Yes |
| `clearance_static_m` | 3-ray Amanatides–Woo cast against mapped racks (`src/world.py:818-823`) | Yes — mapped geometry the nav stack plans around |
| `clearance_dynamic_m` | Nearest detection inside the ±60° cone (`src/world.py:825-828`) | Yes |
| `clearance_omni_m` | Nearest detection in any direction (`src/world.py:775`) | Yes — real AMRs carry 360° protective coverage |
| `detections` | Every robot, human and obstacle within 4 m (`src/world.py:754-772`) | Yes, as anonymous returns |
| `on_dock` | Whether the current cell is a dock (`src/world.py:688`) | Yes — a charge contact is a physical signal |

`Detection` (`src/world.py:59-77`) carries `x, y, r, range_m, vx, vy` and **no identity
field**. That is the architectural point, stated in the class docstring: a robot cannot
tell whether a blob is a peer, a human, a forklift or a dropped pallet, so any protocol
that resolves conflict purely by exchanging intent is structurally blind to everything
that does not broadcast. `tests/test_core.py:632` asserts the absence of an id attribute.

### What the robot never gets

* No peer identity, task, plan or priority except what arrives in the inbox as a message.
* No global task table — every robot's `open_tasks` is built from `TASK_NEW` packets
  (`src/amr.py:5306`) it actually received, and repaired by peer gossip
  (`src/amr.py:5145`) when it missed one.
* No handle to `World`, `SimNetwork`, other `AMRBrain` objects, or the `FleetManager`.
  The agent's only import from the world module is two type names (`src/amr.py:59`).
* No ground-truth positions of anything outside the 4 m sense radius
  (`src/settings.py:37`).

### Where the boundary leaks, stated plainly

| Leak | Where | Severity |
|---|---|---|
| **Detections are exact.** Positions, radii and velocities come straight from ground truth (`src/world.py:754-772`). A real 2D safety lidar plus tracker gives noisy, partially occluded estimates | `src/world.py:754-772` | Real. This flatters the reactive layer. `Detection` explicitly models range rate as available (`src/world.py:72-75`), which is defensible; the *exactness* is not |
| **Pose noise is applied inconsistently.** `pose` and `cell` are perturbed (`src/world.py:750-752`), but `det.range_m` is computed from the robot's **true** position (`src/world.py:758`), and detections are placed at other bodies' true coordinates | `src/world.py:750-772` | Real but small. `_speed_limit_from_traffic` mixes a noisy self-pose with true obstacle coordinates. Default `pose_noise_m` is 0.02 m (`src/scenarios.py:82`) and several scenarios set it to 0.0 |
| **No occlusion.** A rack between two robots does not hide either from the other's detection list | `src/world.py:754-772` | Real. Mapped clearance *is* ray-cast (`src/world.py:818-823`); dynamic detections are not |
| **The shared static map.** Every brain is constructed with the same `Warehouse` (`src/main.py:153`) and derives `corridors`, `analyse_topology` and `directed_circulation` from it (`src/amr.py:172-174`) | `src/amr.py:172-174` | Not a leak. A warehouse AMR ships with a map. It is worth saying out loud that the *agreement* between nodes on block ids and circulation direction comes from this shared map, not from a protocol |
| **The runner writes `st.carrying` from brain state** (`src/main.py:346`) | `src/main.py:346` | Cosmetic only. `carrying` is declared at `src/world.py:115` and read only by `snapshot()` (`src/world.py:882`); no physics or contact check uses it |
| **The radio model uses true positions** (`src/main.py:329` → `src/transport.py:106`) | `src/transport.py:116-123` | Not a leak. That is the network deciding whether a packet is delivered, not the robot deciding anything |
| **The WMS injector observes `TASK_DONE`** (`src/main.py:277-293`) | `src/main.py:272-276` | Bounded and documented in the source: the injector reads completion certificates only to stop repeating finished jobs. It never evaluates a bid, chooses a winner, or sends an award |

Status labels for this section: the sensor model is **simulated only**; the fact that the
agent consumes nothing else is **implemented and tested** (`tests/test_core.py:632`,
`tests/test_core.py:421` — received protocol times are derived from the receiver's clock).

---

## 6. Process and deployment topologies

| Topology | Processes | Transport | Coordinator present? | Status |
|---|---|---|---|---|
| **A. In-process headless** (`src/main.py:107`) | 1 | `SimNetwork` — seeded loss, latency, dead zones, partitions (`src/transport.py:75`) | Only for `central`, `prioritized_space_time_astar`, `hierarchical` (`src/main.py:104`), or when `--allocation-policy hungarian` is chosen (`src/main.py:167`) | Implemented and tested |
| **B. Multi-process real UDP** (`src/distributed_demo.py:154`) | 1 referee + N children, `spawn` context (`src/distributed_demo.py:176`) | Real IPv4 multicast, `239.26.1.23:26123` (`src/transport.py:44-45`), HMAC-authenticated, replay-windowed (`src/transport.py:263-288`) | None. The parent is physics and lidar only | Implemented and tested (`artifacts/benchmarks/bios6-distributed-demo.json`) |
| **C. Single edge node on hardware** (`src/edge_runtime.py:266`) | 1 per robot | Same real multicast transport; sensors/actuators over validated JSON/UDP (`src/edge_runtime.py:151`) | None | Implemented, **not executed on target hardware** |
| **D. Dashboard** (`backend/server.py:810`) | 1 HTTP server | HTTP POST `/api/run` (`backend/server.py:616`); playback of a completed run | Passive. It is downstream of a finished simulation | Implemented and tested (`tests/test_dashboard.py`, `tests/test_server.py`) |

### On "no central coordination server" (requirement 6)

`MANAGED_POLICIES` (`src/main.py:104`) is a single tuple, kept in one place precisely so
that the runner and the dashboard payload give the same answer. A policy absent from it
is peer-to-peer by intent, and `manager` stays `None` (`src/main.py:165`). The default
policy `BIOS_PIBT.6` (`src/main.py:745`) is absent from it. Three tests pin this:
`tests/test_core.py:465` and `tests/test_core.py:478` assert that no frame of a
decentralized dashboard run reports a live manager, and `tests/test_core.py:235` asserts
that an allocation-only manager never controls routes.

Topology B is the strongest form of the claim available without hardware: N distinct OS
processes with distinct PIDs, distinct sockets, distinct replay windows and deliberately
unrelated clock epochs, coordinating over datagrams a judge can capture with
`tcpdump -i any port 26123`. The verdict fields the demo returns
(`src/distributed_demo.py:288-305`) include `separate_processes`,
`peer_messages_observed`, `authenticated_transport` and `control_deadlines_met`, all
computed from the child reports rather than asserted.

### The dashboard's playback model

The dashboard is **playback, not a live stream**, and both `src/main.py:572-577` and
`backend/server.py:7-14` give the same reason: the simulation runs far faster than
realtime, so streaming it would mean throttling it back to wall-clock for no benefit,
while a recorded run can be scrubbed, paused on the interesting frame and replayed against
a different policy on the same seed. `run_for_dashboard` (`src/main.py:567`) returns
`{map, meta, frames, summary}`; frames are captured at 10 Hz (`src/main.py:350-351`) with
a guaranteed final completion frame (`src/main.py:366-368`) so the summary and the last
visible frame cannot disagree.

**What is not implemented:** `backend/server.py:20` states that "in the distributed runner
the dashboard is a passive multicast listener: it joins the group and reads the same
datagrams the robots send each other." No such listener exists. No file outside
`src/transport.py` calls `IP_ADD_MEMBERSHIP`, and `src/distributed_demo.py` writes a JSON
report rather than feeding the dashboard. The dashboard today serves only topology A.
Treated as a design intention this is fine; presented to a jury as a feature it would be
false. See [§9](#9-architectural-gaps-found-while-writing-this) and
[Limitations](15-LIMITATIONS.md).

---

## 7. Determinism and seeding

A benchmark whose loss pattern changes between runs cannot support "policy A beat policy
B", because the difference could be the dice (`src/transport.py:77-83`). Four mechanisms
make a run reproduce:

1. **One seed reaches every stochastic component.** `run_scenario` stamps it into the
   config (`src/main.py:121`) and passes it to both `World` (`src/main.py:125`) and
   `SimNetwork` (`src/main.py:126`). `World` owns a private `random.Random(seed)`
   (`src/world.py:235`) used only for pose noise.
2. **Per-packet seeded draws, not a shared stream.** `SimNetwork.send`
   (`src/transport.py:167-176`) hashes `[seed, src, dst, type, time, semantic body]` with
   BLAKE2b and seeds a fresh `random.Random` from the digest. The sequence number is
   deliberately excluded, because event-triggered policies allocate fewer preceding
   sequence numbers. The consequence is *counterfactual fairness*: a policy that
   suppresses one redundant heartbeat cannot shift the loss and latency of every later
   packet in its paired comparison. `tests/test_core.py:697` pins this
   (`test_unrelated_packet_does_not_shift_later_loss_draws`).
3. **Every iteration order is explicit.** Robots step in `sorted(brains)`
   (`src/main.py:327`); delivery fans out over `sorted(self.nodes)`
   (`src/transport.py:155`); A\* heap ties break on an integer counter, never object
   identity (`src/planner.py:92`, `src/planner.py:10-11`); the topology 2-core peel uses a
   sorted queue for stability (`src/topology.py:52`); the corridor decomposition iterates
   `sorted(corridor_cells)` (`src/environment.py:190`).
4. **The workload is fingerprinted.** `workload_fingerprint` (`src/scenarios.py:100`)
   SHA-256s the map, starts, ordered tasks, humans, faults, radio model, pose noise, seed
   and `asdict(cfg)` — every controller constant. The route policy is excluded because it
   is the independent variable. `benchmark.py` refuses to pair two runs whose fingerprints
   differ.

### Measured result

Running `crossing_chokepoint`, 4 robots, seed 3, `BIOS_PIBT.6` + `auction_bundle` for 40
simulated seconds, twice in one process, the two `PolicyResult` dictionaries are
**identical in every field except three**:

| Field | Run 1 | Run 2 |
|---|---|---|
| `plan_cpu_total_s` | 0.0653 | 0.0747 |
| `plan_cpu_mean_ms` | 0.129 | 0.147 |
| `plan_cpu_max_ms` | 0.75 | 1.096 |

Those three are host wall-clock measurements taken with `time.perf_counter()` around the
planner (`src/amr.py:2697`, `src/amr.py:2753`) — they measure the machine, not the fleet.
Every trajectory, contact count, separation, message count, task time and makespan
reproduces exactly. The stronger check the release campaign runs is cross-process:
`src/auction_v2_campaign.py:189` re-executes under `PYTHONHASHSEED` 0, 1 and 42 and
compares semantic digests.

**Why it matters.** The 20 % success criterion (requirement 20) is a *paired* claim: the
same map, the same starts, the same task stream, the same radio, the same seed, one
variable changed. Without bit-reproducibility the pairing is decorative. See
[Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md) for what is actually claimed and
how the censored bounds are computed.

---

## 8. Configuration surface

Everything tunable is a frozen dataclass in `src/settings.py`, composed into one `Config`
(`src/settings.py:298-313`) with a module-level `DEFAULT` (`src/settings.py:316`). Frozen
means a live change is made with `dataclasses.replace`, which is exactly how the runner
applies a scenario's radio model and seed (`src/main.py:121`) and how `--loss` is applied
(`src/main.py:779`).

### `RobotSpec` — the physical envelope (`src/settings.py:14-96`)

| Field | Default | Meaning |
|---|---:|---|
| `radius_m` | 0.35 | Footprint; two robots touch at 0.70 m |
| `v_max` | 1.2 m/s | Commercial AMRs run 1.0–2.0 in aisles |
| `a_max` | 0.8 m/s² | Sets the braking distance, and therefore the protective field |
| `omega_max` | 1.6 rad/s | ~92°/s turn in place |
| `reaction_s` | 0.10 | Sense-to-brake latency allowance |
| `safety_margin_m` | 0.15 | Standstill clearance never given up |
| `omni_stop_m` | 0.45 | 360° guard — the number a judge will ask you to change |
| `safety_cone_rad` | 1.05 | ±60° for unexpected objects |
| `static_cone_rad` | 0.35 | ±20° for mapped shelving |
| `sense_radius_m` | 4.0 | Lidar range for unlabelled obstacles |
| `battery_full_wh` | 480 | With `draw_move_w` 210, `draw_idle_w` 45, `charge_w` 900 |

`stop_field_m` and `max_speed_for_clearance` are methods on this dataclass
(`src/settings.py:46`, `src/settings.py:61`), so changing `a_max` changes the safety
envelope consistently everywhere — there is no second copy of the braking constant.

### `Rates` (`src/settings.py:99-112`)

See the table in [§2](#2-the-layered-control-stack). A live demo of the layering claim:
set `reactive_hz` to 1.0 and the fleet gets clumsy but never collides, because Layer 0 is
unaffected.

### `NetSpec` — the radio (`src/settings.py:115-133`)

| Field | Default | Meaning |
|---|---:|---|
| `latency_mean_s` / `latency_jitter_s` | 0.004 / 0.003 | On-prem LAN, not cloud. Deliberately small — saying so is half the critique |
| `loss` | 0.0 | Uniform packet loss; the sweeps move this. `--loss` overrides it (`src/main.py:759`) |
| `mtu_bytes` | 1400 | Oversized datagrams are dropped loudly (`src/transport.py:149-153`) |
| `dead_zones` | `()` | `(cx, cy, radius)` in cells |
| `peer_traffic_via_ap` | `True` | The honest default: infrastructure Wi-Fi relays peer frames through the AP, so a robot in a hole loses peers too |

### `TrafficSpec` — Layer 1 and Layer 2 coordination (`src/settings.py:136-296`)

The largest surface, 60+ fields. The ones a judge is most likely to probe live:

| Field | Default | What it controls |
|---|---:|---|
| `intent_horizon` | 6 cells | How far ahead an INTENT message publishes |
| `peer_stale_s` | 1.0 s | Drop a peer's intent after this silence (`v6_peer_stale_s` for BIOS 6) |
| `deadlock_wait_s` | 4.0 s | Blocked this long → run cycle detection |
| `block_wait_s` | 30.0 s | Waiting for a single-file block to clear is traffic, not deadlock; must exceed the longest aisle transit |
| `gate_commit_s` | 0.45 s | Two heartbeat periods — the commit round that shrinks the two-mouth race |
| `min_controlled_block` | 6 cells | Below this, block control costs more than it saves (`src/amr.py:1691-1698` records the measurement) |
| `livelock_progress_s` | 12.0 s | No net progress this long → throw the plan away |
| `central_timeout_s` | 1.5 s | No manager beacon for this long → `DEGRADED_P2P` (`src/amr.py:2571`) |
| `replan_penalty` | 6.0 | Cost added to a contested cell when detouring — never impassable |
| `auction_bid_window_s` | 0.6 s | Bid deadline published by the injector so every robot closes the same auction |
| `auction_lease_s` | 20.0 s | Award lease; expiry is what reclaims work from a crashed owner |
| `energy_reserve_frac` | 0.15 | Hard admission constraint on bidding |
| `priority_age_quantum_s` | 1.0 s | Discrete ageing step — continuous ageing makes two waiters swap rank several times a second |

### `Config` (`src/settings.py:298-316`)

| Field | Default | Note |
|---|---:|---|
| `cell_m` | 1.4 | Lane pitch. The comment at `src/settings.py:299-304` records why 1.00 m failed: with a 0.70 m footprint and a 0.30 m standstill guard there was no positive clearance budget |
| `robot` / `rates` / `net` / `traffic` | the four specs above | |
| `seed` | 0 | Overwritten per run at `src/main.py:121` |

### Scenario-level knobs (`src/scenarios.py:62-89`)

`Scenario` is the mutable outer layer: `starts`, `assignments`, `humans`, `duration_s`,
`net`, `kill_manager_at`, `partition_at` / `heal_at` / `partition_groups`, `robot_fail_at`
/ `robot_restart_at`, `obstacles`, `pose_noise_m`, `initial_battery_fracs`, `seed`. These
are the fault-injection surface; see [Scenarios](11-SCENARIOS.md).

### CLI surface (`src/main.py:739-764`)

`--scenario --policy --allocation-policy --robots --duration --seed --seeds --loss --json -v`.
Entry point is `run.py` (or `python -m src.main`), **not** `main.py`.

---

## 9. Architectural gaps found while writing this

Listed because a jury will find them and it is cheaper to name them first.

1. **The dashboard has no multicast listener.** `backend/server.py:20` describes one.
   None exists anywhere in the repository. The dashboard serves only completed batch
   runs.
2. **`src/main.py:635` references an undefined name `Cell`.** It is harmless at runtime
   (PEP 526 local annotations are never evaluated), but `python -m ruff check src/ backend/`
   fails on it with F821, and `README.md` lists lint as a release gate.
3. **`README.md:188` documents `python main.py …`.** There is no `main.py` at the
   repository root; the entry point is `run.py`.
4. **`requirements.txt:4-5` and `pyproject.toml:14-17` declare `websockets` and
   `matplotlib`.** No Python file in the repository imports either. The "stdlib only"
   claim is *stronger* than advertised, and the declarations are dead.
5. **`_v6_clearance_unstick` is filed under the `# Layer 0` banner** (`src/amr.py:866`,
   `src/amr.py:984`) but reads the peer table (`src/amr.py:1014`) and is called from
   Layer 1 (`src/amr.py:1272`). The behaviour is correct; the section header is wrong.
6. **Layer 0's `_creep_until` channel.** For `BIOS_1.0.0`, `BIOS_4`, `BIOS_PIBT.1` and
   `BIOS_PIBT.2`, a Layer-1 timer alone can permit ≤0.20 m/s motion inside the 0.45 m
   omni guard (`src/amr.py:892-896`, `src/amr.py:916-927`). Only the `BIOS_PIBT.3/.5/.6`
   family requires the independent geometric proof (`src/amr.py:909-915`). The default
   policy is in the stricter family; the older policies remain selectable and are weaker.
7. **`safety_hz` is not what sets the simulated safety rate.** It is read only by
   `src/edge_runtime.py`. In simulation the rate is `world_hz`. They are equal by default,
   so no result is affected — but the constant a reader would grep for is not the one in
   effect.
8. **No Pi or Jetson measurement exists.** Every CPU number in the repository was taken on
   a developer host. `archive/IMPLEMENTATION_STATUS.md:31` and `archive/CRITIQUE.md:95-99`
   already say this; it is repeated here because it is the single largest gap between the
   architecture and the problem statement's hardware requirement.

---

## Where to go next

* Wire format, gossip, leases and the dead-zone argument → [03. Decentralized Protocol](03-DECENTRALIZED-PROTOCOL.md)
* A\*, space-time A\*, circulation graphs and PIBT → [04. Path Planning](04-PATH-PLANNING.md)
* What each of the thirteen policies actually does → [05. Coordination Policies](05-COORDINATION-POLICIES.md)
* Auctions, leases, completion certificates → [06. Task Allocation](06-TASK-ALLOCATION.md)
* Field geometry, braking equations, separation statistics → [07. Safety](07-SAFETY.md)
* The Pi story and what is still external → [08. Edge Deployment](08-EDGE-DEPLOYMENT.md)
* What the dashboard draws and why → [09. Dashboard](09-DASHBOARD.md)
* Every public function and message type → [10. API Reference](10-API-REFERENCE.md)
* Pinned maps, workloads and fault injections → [11. Scenarios](11-SCENARIOS.md)
* The 20 % claim, its bounds and its caveats → [12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md)
* What the 4,044 lines of tests actually pin → [13. Testing](13-TESTING.md)
* Traps that cost time, and what they taught → [14. Findings](14-FINDINGS.md)
* Everything this submission does not claim → [15. Limitations](15-LIMITATIONS.md)
