# SIH26123 — where the problem statement is wrong, and what we build instead

The statement asks for decentralised P2P coordination, multi-agent collision avoidance
and task re-allocation for ≥ 3 AMRs on Pi/Jetson-class hardware, delivered as a
simulation with a fleet dashboard. Success criteria: **zero inter-robot collisions** and
**≥ 20 % faster than stop-and-wait**.

The premise is wrong in specific, citable ways. These are load-bearing errors, not
nitpicks: a submission that repeats the statement's framing back at the panel is arguing
from a false model of how AMR fleets work. Being visibly aware of that is the
differentiator — the statement does not need to be right for the submission to be.

## The errors

**"Centralised" is equivocated with "cloud."** Real fleets — Amazon Robotics, Locus,
Geek+, 6 River, OTTO — run an on-prem fleet manager on the LAN at ~1–5 ms round trip. The
latency argument only works against an architecture nobody deploys.

**The latency numbers do not survive arithmetic.** AMRs run 1–2 m/s ≈ 1–2 mm per ms.
50 ms is 6–10 cm, and global routing runs at 0.1–1 Hz anyway. Network round-trip is not
what causes warehouse collisions; localisation error is — and localisation error is a
constant positional offset that does not care how the fleet is coordinated.

**P2P does not fix Wi-Fi dead zones.** Same radio, same hole: if a robot cannot reach the
server it cannot reach its peers either. Worse, infrastructure-mode Wi-Fi relays
"peer-to-peer" frames through the access point, so it is not decentralised at layer 2 and
inherits the identical failure. A real dead-zone fix needs a *different link* — 802.11s
mesh, Wi-Fi Direct, UWB — which the statement never mentions. `transport.py` models both
cases and `tests/test_core.py` asserts the result.

**The three control loops are conflated.** Safety stop 10–100 Hz (onboard, certified),
local avoidance ~10 Hz (onboard), global route 0.1–1 Hz (central is fine). Commercial
AMRs already do the first two onboard — the "split-second decisions" the statement wants
moved to the edge were never in the cloud. The standard layered architecture already
solves the stated problem.

**Decentralised safety inverts the real safety architecture.** Under ISO 3691-4 /
EN ISO 13849 / ANSI-RIA R15.08, protective stopping must be independent, local and
certified (PLd/SIL2 scanner). It may not depend on a Wi-Fi message from a peer. Messaging
is for *efficiency*, never for safety.

**Decentralising degrades plan quality.** Optimal MAPF is NP-hard; going local makes it
myopic, not tractable. At the densities where chokepoints matter, task time is dominated
by plan quality rather than round-trip time, so the efficiency claim can come out
backwards. Our own measurements so far show exactly that: the centralised reservation
planner outperforms the decentralised fallback.

**Distributed deadlock detection re-centralises itself.** Cycle detection in a wait-for
graph needs global state, and every practical scheme needs a total order — robot ID,
priority, or a token — which is a centrally assigned artifact. It also converts deadlock
into livelock, and the statement sets no liveness criterion.

**"Zero collisions" is unfalsifiable and unreachable as stated.** Absence over finitely
many runs is not evidence (safety is a rate with a confidence interval). In an
asynchronous system with loss and crashes you cannot guarantee agreement (Fischer–Lynch–
Paterson), and the only safe fallback is… stop-and-wait, the disparaged baseline. It also
counts *only* robot-robot contacts — humans, forklifts and racks are excluded, and a
shared-intent protocol is structurally blind to anything that does not broadcast.

**"20 % vs stop-and-wait" is a self-selected strawman.** Nobody runs naive stop-and-wait;
the real state of the art is reservation-based traffic management. Speedup swings from
~5 % to 300 % with map topology and density, so the number is meaningless without a
pinned scenario — and can be manufactured to order by choosing a friendly map.

**N ≥ 3 cannot test the hypothesis.** The whole justification is *scaling*; congestion,
cascading deadlock and O(N²) message load appear at 20–100+. At N = 3 centralised wins
trivially, and three robots in three disjoint aisles satisfy both criteria while solving
nothing.

**Two outright self-contradictions.** (a) "must run on constrained edge hardware" versus
a deliverable that is a simulation. (b) "no central server" versus a dashboard
aggregating the whole fleet's live state — which is a central aggregator with the same
single point of failure and the same connectivity dependence.

**"Edge-AI" is a title-only buzzword.** Nothing described needs learning: MAPF/CBS/ECBS/
ORCA and auctions are CPU-bound search and geometry. That is why the statement names a
GPU board (Jetson Nano — also EOL, superseded by Orin Nano) for a workload with no neural
network in it.

## What we build instead

1. **Hierarchical, not fully decentralised.** Central optimiser when reachable, negotiated
   local fallback on partition, certified local safety stop always. We say plainly that
   full decentralisation is a *degraded mode*, not a superior architecture.
2. **Three baselines, not two.** Stop-and-wait, *reservation-based centralised*, and ours.
   Beating only the weak one is transparent to any judge who knows the field.
3. **Safety as a rate with an interval**, plus a packet-loss sweep and a partition test.
   "0 contacts in N robot-hours, 95 % upper bound X per 1000 robot-hours" beats "zero
   collisions". `metrics.py` refuses to emit a speedup ratio when a policy fails to
   complete.
4. **A non-communicating dynamic obstacle in the demo** — a warehouse worker walking a
   cross-aisle. It is the case the statement forgot and the one that proves the safety
   layer is real rather than decorative. Detections deliberately carry no identity.

Cheapest contradiction to neutralise: run the planner node unmodified on an actual Pi and
publish per-cycle CPU time and RAM alongside the sim. The architecture already supports
it — the agent does no I/O — but no hardware is available for this build, so the harness
reports `plan_cpu_mean_ms` / `plan_cpu_max_ms` from the host instead, and the report must
say so rather than implying a Pi measurement.
