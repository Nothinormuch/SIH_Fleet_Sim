# BIOS 6 predictive collective intelligence

## Status

`BIOS_PIBT.6` is an experimental successor to the released `BIOS_PIBT.5` policy. It is
implemented on `codex/real-game` and is selectable in the dashboard, CLI and distributed
runtime. It is **not** the repository release default yet. BIOS 5 retains that role until
the release gates below pass across multiple seeds.

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
sidestep. On bidirectional single-lane maps it retains the local BIOS 5 vacate rule; an
idle robot must not cross the contested lane merely to park. This is still a local
decision; the requesting peer does not command the idle robot.

### 6. Controller-generated explanations

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

## Current tuning evidence

These are paired development checks on seeds 0–2, not checked-in release evidence. Each
pair uses the same workload fingerprint and the semantic-draw network model described
above. No robot/robot, robot/human or robot/rack contacts were observed in these candidate
runs.

- **Open Floor:** V5 and V6 have identical per-seed completion counts and makespans. V6
  sends 12,900 messages versus V5's 20,956, a 38.4% reduction.
- **Human Interaction:** V5 and V6 both complete all 30 task instances with identical
  per-seed makespans. V6 sends 61,357 messages versus 91,128, a 32.7% reduction.
- **Chokepoint:** both policies complete 6/8 tasks on seed 0 and 8/8 on seeds 1 and 2,
  with identical completion times on the completed seeds. V6 reduces messages from
  44,827 to 33,401 (25.5%), but the strict liveness gate remains incomplete because
  seed 0 reaches the common cutoff.
- **Dead-Zone Mesh:** V6 completes 18/18 task instances; V5 completes 16/18. V6 reduces
  messages from 167,803 to 90,817 (45.9%). The V6 completion times are 509.7 s, 472.2 s
  and 393.9 s.
- **Grand Challenge:** V6 completes 43 task instances in the three fixed 800 s windows
  versus V5's 27, a 59.3% aggregate increase. V6 also reduces nonproductive wait ticks
  from 587,071 to 280,315 (52.3%) and messages from 324,704 to 220,542 (32.1%). This is
  not a claim that every run is 59.3% faster: seed 1 completes 12 tasks under V6 versus
  14 under V5, so the no-regression promotion gate fails even though seeds 0 and 2
  improve from 4 to 15 and 9 to 16 respectively.
- **Dense Aisles ablation:** on one 8-AMR seed, V5 and tuned V6 both complete 31/32 tasks
  in the fixed 600 s window. Tuned V6 uses about 29% fewer messages; the wait difference
  is within one percent and is not a performance win.

Lease ablations explain why the seed-1 tail is not hidden by a demo-only constant. A
faster degraded-link renewal lowers availability in seeds 0 and 2. Extending the claim
lease from 20 to 30 seconds lets seed 1 complete, but seed 0 falls to 5/16 because failed
ownership is retained longer. This is the expected availability-versus-at-most-once
tradeoff during a partition. The balanced 20-second expiring lease remains unchanged.

The matrix demonstrates large potential and meaningful communication savings. It does
not justify a universal 50% speedup, a “zero collision” guarantee, or a production claim.

A final real-process smoke test ran three independently spawned BIOS 6 AMR nodes for
6,000 control ticks each, with deliberately different monotonic-clock offsets and signed
UDP transport. All nodes converged on the 6/6 completion catalog, the referee observed
peer traffic and zero contacts, authentication/replay counters stayed clean, and every
50 Hz control deadline was met. The slowest observed node loop was 7.15 ms and maximum
resident memory was 29.24 MiB on the development Mac. This proves process separation and
protocol execution on the tested machine; it is not Raspberry Pi performance evidence.

## Promotion gates

BIOS 6 becomes the release default only when all gates pass on pinned workloads and
fresh checked-in artifacts:

1. **Regression:** complete Python test suite, Ruff, Python compilation and frontend
   JavaScript syntax checks pass.
2. **Safety evidence:** zero observed robot/robot, robot/human and robot/rack contacts in
   the full campaign; separation and exposure are reported, not converted into a safety
   certification claim.
3. **Liveness:** no candidate timeout on any seed where V5 completes; no task-count
   regression in a fixed evidence window.
4. **Performance:** paired median makespan does not regress versus V5 and the worst seed
   remains within the declared tolerance.
5. **Communication:** at least 25% fewer messages in healthy-network showcase runs and at
   least 10% fewer under the degraded-network campaign, without reducing task completion.
6. **Compute:** planning CPU maximum remains inside the edge-node budget.
7. **Distributed runtime:** at least three independent AMR processes complete an
   authenticated UDP demonstration with no route manager selecting moves or auction
   winners.
8. **Determinism:** rerunning the same seed/configuration produces the same result and
   workload fingerprint; paired policies receive common semantic channel draws.

If any gate fails, BIOS 5 remains default and the failing V6 mechanism is reported as an
ablation rather than hidden.

## Run it

```bash
source .venv/bin/activate

# Browser demonstration
python backend/server.py
# Open http://127.0.0.1:8000 and select BIOS 6.0 Predictive.

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
