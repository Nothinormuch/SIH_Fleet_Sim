# BIOS 6 predictive collective intelligence

## Status

`BIOS_PIBT.6` is the released default successor to `BIOS_PIBT.5`. It is selectable in
the dashboard, CLI and distributed runtime; BIOS 5 remains selectable as the frozen
comparison baseline. Promotion followed the multi-seed gates below. The result is a
software simulation and edge-runtime prototype, not certified physical AMR firmware.

The goal is not to call ordinary A* “AI.” BIOS 6 makes bounded predictions from measured
local state, remembers repeated traffic delays, explains controller decisions, and sends
fewer redundant packets. Every optimisation is advisory. PIBT, expiring leases and the
independent local protective-stop layer remain authoritative.

## What changed from BIOS 5

### 1. Event-triggered peer communication

BIOS 5 emits heartbeat, intent and task-lease traffic at a fixed rate. BIOS 6 transmits
immediately when state, cell, goal, task, priority or conflict state changes, then uses a
slow bounded refresh as anti-entropy. Repeated bids and lease renewals are suppressed
inside explicit freshness windows.

Healthy links refresh a 20-second task lease once per second. When the configured
channel has packet loss or dead zones, BIOS 6 adapts to a 0.5-second refresh. This is
still 60% below BIOS 5's fixed 5 Hz renewal stream and prevents late duplicate awards
after burst loss.

This reduces network load without turning silence into permanent state. A missed packet
is healed by a later refresh, task catalog gossip, completion gossip or lease expiry.

### 2. Distributed congestion experience

Each AMR measures time spent genuinely held on a directed route edge. It stores a
decaying EWMA delay and shares at most two bounded records every five seconds. A route
edge is considered only after eight samples; it becomes expensive, never impassable.
A peer's cumulative counter is used only for replay/freshness detection: each fresh
remote report contributes one bounded observation, so one authenticated packet cannot
claim a million samples and immediately poison route preference.

The learned cost affects A* route preference and auction ETA. It does not change the
physical energy calculation, collision envelope, map passability or task eligibility.
Under configured packet loss or radio dead zones the experience hint is disabled and
the AMR falls back to the proven BIOS 5 route behaviour. This avoids divergent local
experience maps amplifying a partition.

### 3. Short-horizon occupancy prediction

BIOS 6 projects anonymous moving lidar detections over a 2.4-second horizon. Moving
detections that correlate with a fresh peer pose are not counted twice. Unmatched
detections are deliberately generic: the controller does not need to know whether the
object is a person, forklift or another non-broadcasting actor.

Cooperating peers remain in the existing PIBT and time-windowed INTENT layer. An early
V6 ablation also added peer intent to A* cost; the three-seed Open Floor gate showed that
this double-counted the same conflict and caused route oscillation, so it was removed.

Forecast cells are soft A* penalties with a replan cooldown. A forecast never becomes a
hard obstacle and cannot authorize motion through the safety envelope. Stationary
anonymous objects still use the separate confirmed, expiring blocked-cell layer.

Predictive route reshaping is disabled when the configured channel has packet loss or
dead zones. Local sensing and protective stopping continue, but degraded communication
uses the proven BIOS 5 routes instead of combining divergent forecasts with the fallback
traffic protocol. A Grand Challenge regression exposed and now enforces this boundary.

### 4. Charger-aware dock choice

When an idle AMR crosses the energy charge threshold, BIOS 6 scores reachable docks by
route length plus fresh peer charging goals and intents. A peer heartbeat acts as a soft
decentralized queue signal. No WMS or fleet manager schedules the dock, and stale peer
information expires normally.

### 5. Idle-lane clearing

An idle chassis can become the final obstacle in an otherwise live fleet. On directed
circulation maps, BIOS 6 listens for a peer heartbeat that explicitly reports it as the
blocker, then chooses another reachable mapped dock instead of making a one-cell cosmetic
sidestep. Inside a deterministic geographic radio partition, remote parking is admitted
only under bounded unfinished load and only when the route avoids the radio hole;
otherwise the robot clears locally. On bidirectional single-lane maps it retains the
local BIOS 5 vacate rule. The requesting peer reports a conflict but does not command the
motion.

### 6. Auction-churn recovery

Two liveness failures required separate bounded mechanisms instead of a global longer
lease:

- Under combined random loss and a dead zone, a task that reaches auction epoch 8 has
  direct evidence of repeated ownership churn. Its lease receives a two-second linear
  increment per further epoch, capped at 40 seconds. Low-epoch work, deterministic
  partitions and ordinary crash recovery keep the 20-second lease.
- On a bidirectional map, independent auction windows can each name a different remote
  winner, leaving every robot idle even though bids converged. If the same remote-winner
  state persists for a full lease without a self-award, peers publish winner nominations
  through the normal expiring `AWARD`. The stability timer is bound to the exact
  task/epoch/winner/cost set. The receiver requires a recent matching local bid, applies
  the epoch/cost/robot-ID total order, and rechecks current path, payload, battery reserve
  and deadline feasibility before accepting. The WMS still selects no winner.

Global 30-second leases, heartbeat-as-ownership and immediate peer nomination were all
rejected by ablation because they harmed recovery or healthy-seed timing.

### 7. Controller-generated explanations

Every BIOS 6 brain retains a bounded decision log. Current reason codes include:

- `TASK_ACCEPTED`
- `TASK_COMPLETED`
- `PREDICTIVE_REROUTE`
- `CONGESTION_REROUTE`
- `CHARGER_SELECTION`
- `IDLE_VACATE`

The dashboard replays these records as the Decision Trace. They contain the inputs and
result already used by the deterministic controller; they are not LLM narration and do
not change the decision.

## Benchmark fairness correction

The simulator previously consumed packet-loss and latency draws from one sequential RNG.
A sparse policy consumed fewer draws, shifting the simulated channel seen by every later
packet. That made a communication optimisation and a traffic optimisation impossible to
compare fairly.

`SimNetwork` now derives a stable draw from seed, semantic packet body, source,
destination and simulated send time. A common packet therefore receives the same
loss/latency draw in paired V5/V6 runs even when V6 suppresses unrelated packets. The
message sequence number is intentionally excluded because suppression changes sequence
consumption. Delivery tie-breaking remains deterministic.

This changes the benchmark model, so old checked-in evidence must not be presented as
BIOS 6 release evidence. New paired artifacts must be generated before promotion.

## Release evidence

The final release campaign pairs BIOS 5 and BIOS 6 on seeds 0–2 with the same workload
fingerprint and semantic-draw network model. Across the 15 BIOS 6 showcase runs, 143 of
144 fixed-window task instances complete over 10.019 robot-hours. No robot/robot,
robot/human or robot/rack contacts were observed. This bounds observed simulation
performance; it is not a zero-collision guarantee or safety certification.

- **Open Floor:** V5 and V6 have identical per-seed completion counts and makespans. V6
  sends 12,900 messages versus V5's 20,956, a 38.4% reduction.
- **Human Interaction:** V5 and V6 both complete all 30 task instances with identical
  per-seed makespans. V6 sends 61,357 messages versus 91,128, a 32.7% reduction.
- **Chokepoint:** V6 completes all 24 task instances; V5 completes 22. Seeds 1 and 2
  retain exact makespan parity. Persistent peer nomination resolves seed 0 at 274.5 s,
  while V5 remains at 6/8 at the 320 s cutoff. V6 reduces messages from 44,827 to
  31,644 (29.4%).
- **Dead-Zone Mesh:** V6 completes 18/18 task instances; V5 completes 16/18. V6 reduces
  messages from 167,803 to 97,711 (41.8%). V6 completion times are 581.54 s, 467.24 s
  and 391.46 s. Seed 0 is 0.34 s (0.06%) slower than V5; this is inside the declared
  0.1% worst-seed timing tolerance, while the candidate median is materially lower.
- **Grand Challenge:** V6 completes 47 task instances in the three fixed 800 s windows
  versus V5's 27, a 74.1% aggregate fixed-window increase. Per-seed counts are
  15/16, 16/16 and 16/16 versus 4/16, 14/16 and 9/16, so no seed regresses. V6 reduces
  nonproductive wait ticks from 587,071 to 244,459 (58.4%) and messages from 324,704
  to 214,837 (33.8%). This is a throughput result under a fixed cutoff, not a claim
  that every run is 74.1% faster.
- **Dense Aisles ablation:** on one 8-AMR seed, V5 and tuned V6 both complete 31/32 tasks
  in the fixed 600 s window. Tuned V6 uses about 29% fewer messages; the wait difference
  is within one percent and is not a performance win.

The balanced base lease remains 20 seconds. Only a high-epoch task under combined burst
loss and a radio hole receives capped backoff. The matrix demonstrates meaningful
throughput and communication gains. It does not justify a universal 50% speedup, a
“zero collision” guarantee, a Raspberry Pi timing claim or a production-safety claim.

A final real-process smoke test ran three independently spawned BIOS 6 AMR nodes for
6,000 control ticks each, with deliberately different monotonic-clock offsets and signed
UDP transport. All nodes converged on the 12/12 completion catalog, the referee observed
peer traffic and zero contacts, authentication/replay/malformed counters stayed clean,
and no 20 ms compute deadline was missed. The slowest observed node loop was 1.535 ms and
maximum resident memory was 29.125 MiB on the development Mac. The referee ran in fast
IPC mode, so this proves process separation, protocol execution and measured per-tick
compute on that machine; it is not Raspberry Pi scheduling or performance evidence.

## Promotion gates

BIOS 6 was promoted only after the following gates passed on pinned workloads and fresh
artifacts:

1. **Regression:** complete Python test suite, Ruff, Python compilation and frontend
   JavaScript syntax checks pass.
2. **Safety evidence:** zero observed robot/robot, robot/human and robot/rack contacts in
   the full campaign; separation and exposure are reported, not converted into a safety
   certification claim.
3. **Liveness:** no candidate timeout on any seed where V5 completes; no task-count
   regression in a fixed evidence window.
4. **Performance:** paired median makespan does not regress versus V5 and the worst seed
   remains inside the declared 0.1% timing tolerance.
5. **Communication:** at least 25% fewer messages in healthy-network showcase runs and at
   least 10% fewer under the degraded-network campaign, without reducing task completion.
6. **Compute:** planning CPU maximum remains inside the edge-node budget.
7. **Distributed runtime:** at least three independent AMR processes complete an
   authenticated UDP demonstration with no route manager selecting moves or auction
   winners.
8. **Determinism:** rerunning the same seed/configuration produces the same result and
   workload fingerprint; paired policies receive common semantic channel draws.

BIOS 5 remains available for regression comparison. Future BIOS 6 changes must rerun the
same gates; a failure blocks a new release rather than rewriting this evidence.

## Run it

```bash
source .venv/bin/activate

# Browser demonstration
python backend/server.py
# Open http://127.0.0.1:8000. BIOS 6.0 Predictive is the default.

# Headless paired checks
python -m src.main --scenario showcase_human --policy BIOS_PIBT.5 \
  --allocation-policy auction --robots 5 --seed 7 --duration 520 \
  --json /tmp/bios5-human.json
python -m src.main --scenario showcase_human --policy BIOS_PIBT.6 \
  --allocation-policy auction --robots 5 --seed 7 --duration 520 \
  --json /tmp/bios6-human.json

# Real-process demonstration; the same AMRBrain policy is used by each node.
python edge_demo.py --robots 3 --duration 20 --policy BIOS_PIBT.6 \
  --allocation-policy auction --output /tmp/bios6-distributed.json
```

## Claim boundary

BIOS 6 is decentralized for route negotiation and `auction` task allocation: each AMR
computes its own bid, prediction, route preference and movement decision. The WMS injects
tasks only. It is still honest to call the complete warehouse system hierarchical because
the WMS supplies work, commissioning supplies unique IDs and physical deployments retain
local certified safety hardware. “Decentralized” does not mean “no infrastructure exists.”
