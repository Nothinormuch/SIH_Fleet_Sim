# 00. THE PROBLEM STATEMENT

> What SIH26123 asks for, the twenty requirements and two success criteria we hold
> ourselves to, and the engineering reading of the statement that shaped what we built.

**Audience:** everyone. This is the entry point to the documentation set.
**Reads next:** [01. Requirements Traceability](01-REQUIREMENTS-TRACEABILITY.md)

---

## 1. The statement as issued

| Field | Value |
| --- | --- |
| Problem Statement ID | **26123** |
| Title | Edge-AI Based Distributed Fleet Coordination for Autonomous Mobile Robots (AMRs) in Smart Warehouses |
| Organization | Bharat Electronics Limited |
| Department | Bharat Electronics Limited |
| Category | Software |
| Theme | Smart Automation |

**Background.** Modern smart warehouses rely on fleets of Autonomous Mobile Robots (AMRs)
to move goods efficiently. As fleet sizes grow, relying entirely on a centralized cloud
server for path planning causes high network latency, Wi-Fi dead-zone vulnerabilities, and
single-point-of-failure risks. To ensure continuous operation, modern robotics is shifting
toward decentralized, edge-computing solutions where robots can talk to each other directly
and make split-second decisions on the fly.

**Description.** The objective is to design a decentralized coordination and
collision-avoidance framework for a multi-robot fleet (at least 3 AMRs) operating in a
dynamic warehouse environment. The system must run locally on edge hardware (e.g.,
Raspberry Pi or Jetson Nano onboard each robot) and handle:

1. **Decentralized Communication** — inter-robot messaging to share position and intent
   without a central server.
2. **Dynamic Multi-Agent Conflict Resolution** — resolving deadlocks and avoiding
   collisions at narrow intersections or choke points in real time.
3. **Task Allocation & Re-routing** — automatically re-assigning pickup points or changing
   paths if one robot encounters a blocked aisle.

**Expected solution.** A multi-robot simulation featuring a decentralized network stack (a
peer-to-peer communication protocol where robots share localization data locally),
multi-agent path planning implemented for edge hardware, and a fleet dashboard — a
lightweight monitoring UI that visualizes the entire fleet's real-time positions and
battery status.

**Success criteria.** Zero inter-robot collisions, and a minimum 20% reduction in total
task completion time compared to traditional stop-and-wait methods when handling
overlapping paths.

---

## 2. The twenty requirements

We decomposed the statement into twenty checkable requirements. Every one is traced to an
implementation and a piece of evidence in
[01. Requirements Traceability](01-REQUIREMENTS-TRACEABILITY.md); this table is the
vocabulary the rest of the documentation uses, and requirement numbers appear at the top of
every document in the set.

| # | Requirement | Source | What must be demonstrated |
| ---: | --- | --- | --- |
| 1 | Multi-robot fleet | Explicit | At least 3 AMRs |
| 2 | Dynamic warehouse environment | Explicit | A warehouse map with changing conditions |
| 3 | Decentralized communication | Explicit | Robots communicate directly over a local network |
| 4 | Position sharing | Explicit | Robots share localization/position |
| 5 | Intent sharing | Explicit | Robots communicate where and what they intend to do |
| 6 | No central coordination server | Core | Coordination does not depend on a central server |
| 7 | Multi-agent path planning | Explicit | An algorithm that plans paths for multiple robots |
| 8 | Collision avoidance | Explicit | Robots avoid one another |
| 9 | Real-time conflict resolution | Explicit | Conflicts handled while robots are moving |
| 10 | Deadlock resolution | Explicit | Situations where robots block each other are resolved |
| 11 | Narrow intersection / choke-point handling | Explicit | Coordination demonstrated at bottlenecks |
| 12 | Blocked aisle handling | Explicit | A robot detects and responds to a blocked aisle |
| 13 | Re-routing | Explicit | A robot changes its route automatically |
| 14 | Task re-assignment | Explicit | A pickup task can be reassigned to another robot |
| 15 | Edge / local execution | Explicit | Algorithms run locally on robot/edge hardware |
| 16 | Fleet dashboard | Explicit | A real-time fleet monitoring UI |
| 17 | Real-time positions on dashboard | Explicit | Locations of all robots are shown |
| 18 | Battery status | Explicit | Battery state of robots is shown |
| 19 | **Zero inter-robot collisions** | Success criterion | 0 collisions demonstrated |
| 20 | **≥20% task-time reduction** | Success criterion | Beat the stop-and-wait baseline by at least 20% |

---

## 3. The two success criteria, stated precisely

The statement's two criteria are the ones that decide the submission, and both need
sharpening before they can be tested. How we sharpened them, and why, is argued in
[12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md); the short version:

**Criterion 19 — "zero inter-robot collisions."** Absence of an event across finitely many
runs is not the same as a rate of zero. We therefore report two things rather than one: the
observed contact count, which is what the criterion literally asks for, *and* a one-sided
95% upper bound on the contact rate per 1000 robot-hours, which is what the observation
actually supports. We also count more than the criterion requires — robot/robot,
robot/human and robot/rack contacts are accounted separately, because a fleet that avoids
its own members while hitting the racking has not solved the problem.

**Criterion 20 — "≥20% reduction versus stop-and-wait on overlapping paths."** A speedup
figure is meaningless without a pinned scenario: the same pair of policies can differ by 5%
or 300% depending on map topology and robot density, so an unpinned number can be
manufactured to order. We therefore pin the scenario, the fleet sizes, the seeds, the
cutoff and the allocation policy in advance, hash every input that is not the independent
variable into a `workload_id`, and refuse any comparison whose paired fingerprints do not
match. Because the stop-and-wait baseline never completes within the cutoff, the reported
figure is a conservative *right-censored lower bound* on the true reduction, not an exact
speedup — see [15. Limitations](15-LIMITATIONS.md).

---

## 4. How we read the statement

The background section of SIH26123 makes a technical argument, and parts of that argument
do not match how AMR fleets are actually deployed. We take this seriously rather than
repeating it back, because the design consequences are real: several of the statement's
implied choices would make a fleet *less* safe or *slower*, and a submission that adopts
them uncritically inherits those outcomes.

Each point below is stated with the design consequence it produced. The full argument,
including sources, is in [14. Engineering Findings](14-FINDINGS.md) and `archive/CRITIQUE.md`.

### 4.1 "Centralized" and "cloud" are not the same thing

Production fleets — Amazon Robotics, Locus, Geek+, 6 River, OTTO — do not put path planning
in the cloud. They run an on-premises fleet manager on the LAN at roughly 1–5 ms round
trip. The latency argument in the background section holds against an architecture that is
not deployed in this industry.

*Consequence:* our centralized baseline is an **on-prem reservation-based planner**, not a
cloud strawman. Beating only a deliberately weak comparator is transparent to any judge who
knows the field, so we carry three baselines rather than two — see
[05. Coordination Policies](05-COORDINATION-POLICIES.md).

### 4.2 The latency arithmetic

An AMR at 1–2 m/s covers 1–2 mm per millisecond. A 50 ms round trip is 6–10 cm of travel,
and global routing runs at 0.1–1 Hz in any case. Network round-trip time is not the dominant
cause of warehouse collisions; localization error is, and localization error is a positional
offset that is unaffected by how the fleet is coordinated.

*Consequence:* we do not claim latency as our advantage. The measured advantage is in
throughput under congestion, which is a plan-quality and traffic-management result.

### 4.3 Peer-to-peer does not fix a Wi-Fi dead zone

It is the same radio and the same hole: a robot that cannot reach the server cannot reach
its peers either. Infrastructure-mode Wi-Fi additionally relays "peer-to-peer" frames
through the access point, so such a link is not decentralized at layer 2 and inherits the
identical failure. A genuine dead-zone fix requires a *different link* — 802.11s mesh,
Wi-Fi Direct, or UWB — which the statement does not mention.

*Consequence:* we model **both** cases explicitly and test them. `dead_zone_infra` and
`dead_zone_mesh` are separate scenarios precisely because they behave differently, and the
distinction is the honest answer to the statement's claim. See
[03. Decentralized Protocol](03-DECENTRALIZED-PROTOCOL.md) and
[11. Scenarios](11-SCENARIOS.md).

### 4.4 Three control loops, conflated into one

Protective stopping runs at 10–100 Hz, onboard and certified. Local avoidance runs at about
10 Hz, onboard. Global routing runs at 0.1–1 Hz, and a central planner is entirely
appropriate there. Commercial AMRs already perform the first two onboard — the "split-second
decisions" the statement wants moved to the edge were never in the cloud to begin with.

*Consequence:* the architecture separates the three rates explicitly, and this is the single
most load-bearing structural decision in the system. See
[02. Architecture](02-ARCHITECTURE.md).

### 4.5 Decentralized safety inverts the real safety architecture

Under ISO 3691-4, EN ISO 13849 and ANSI/RIA R15.08, protective stopping must be independent,
local and certified — a PLd/SIL2-rated scanner. It may not depend on receiving a Wi-Fi
message from a peer. Messaging buys efficiency; it must never be load-bearing for safety.

*Consequence:* **Layer 0 does not consult the protocol to decide whether to stop.** A
protective stop cannot be overridden by any coordination decision. There is one deliberate
seam — a low-speed creep window that lets a stuck robot edge free — and because it is the
one place where peer-derived state can relax the envelope, it is documented explicitly
rather than glossed. See [07. Safety](07-SAFETY.md).

### 4.6 Decentralizing can degrade plan quality

Optimal multi-agent path finding is NP-hard. Making it local makes it myopic, not tractable.
At the densities where choke points actually matter, task time is dominated by plan quality
rather than round-trip time, so the efficiency claim can come out backwards — and in our own
early measurements it did.

*Consequence:* we present full decentralization as a **degraded mode that must be good
enough**, not as a superior architecture. That framing is what the evidence supports.

### 4.7 Distributed deadlock detection re-centralizes itself

Cycle detection in a wait-for graph requires global state, and every practical distributed
scheme needs a total order — a robot ID, a priority, or a token — which is itself a centrally
assigned artifact. Distributed detection also converts deadlock into livelock, and the
statement sets no liveness criterion.

*Consequence:* we do not claim to have escaped this. We use an explicit priority order,
document where it comes from, and treat liveness as a measured property. Two of the hardest
bugs in the project came from exactly this area, and both are documented as findings: peers
must arbitrate on *published* state rather than live state, and ageing must be bucketed
rather than continuous. See [03. Decentralized Protocol](03-DECENTRALIZED-PROTOCOL.md).

### 4.8 "Zero collisions" is a rate, not a count

In an asynchronous system with message loss and crashes, guaranteed agreement is impossible
(Fischer–Lynch–Paterson), and the only universally safe fallback is to stop and wait — the
very baseline the statement disparages. The criterion also counts *only* robot-robot
contacts, excluding humans, forklifts and racking, and a shared-intent protocol is
structurally blind to anything that does not broadcast.

*Consequence:* we report a rate with an interval, we count rack and human contacts
separately, and we put a **non-communicating dynamic obstacle** — a warehouse worker walking
a cross-aisle — into the demonstration. Detections of that worker deliberately carry no
identity. It is the case the statement omits, and it is the one that shows the safety layer
is real rather than decorative.

### 4.9 "20% versus stop-and-wait" needs a pinned scenario

Nobody deploys naive stop-and-wait; the state of the art is reservation-based traffic
management. And the speedup between any two policies swings widely with topology and
density.

*Consequence:* the scenario, fleet sizes, seeds and cutoff are pinned in advance and
fingerprinted. See [12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md).

### 4.10 N ≥ 3 cannot test the hypothesis the statement advances

The justification for decentralization is *scaling*. Congestion, cascading deadlock and
O(N²) message load appear at 20–100+ robots. At N = 3 a centralized planner wins trivially,
and three robots working three disjoint aisles satisfy both success criteria while
demonstrating nothing.

*Consequence:* we satisfy the stated minimum of 3, and then evaluate at **4, 6 and 8**
robots on a map built to force overlap, reporting each fleet size separately so the trend is
visible rather than averaged away.

### 4.11 Two self-contradictions in the statement

First, it requires execution on constrained edge hardware while defining the deliverable as
a simulation. Second, it forbids a central server and then requires a dashboard aggregating
the entire fleet's live state — which is a central aggregator with the same single point of
failure and the same connectivity dependence.

*Consequence:* both are answered structurally rather than argued away. The first is answered
by the no-I/O agent boundary, which lets the identical agent code run in the simulation and
on a Pi — see [08. Edge Deployment](08-EDGE-DEPLOYMENT.md). The second is answered by making
the dashboard a **passive reader**: it consumes recorded telemetry, issues no coordination,
and the fleet's behaviour is unchanged if it is closed — see
[09. Fleet Dashboard](09-DASHBOARD.md).

### 4.12 "Edge-AI" is in the title, not in the requirement

Nothing the statement describes needs learning. MAPF, CBS, ECBS, ORCA and auction-based
allocation are CPU-bound search and geometry. That is why the statement names a GPU board
(the Jetson Nano — itself end-of-life, superseded by the Orin Nano) for a workload
containing no neural network.

*Consequence:* we did build a learned policy, but only where learning genuinely helps and
without pretending otherwise. `BIOS_4` is a 549-parameter MLP that **arbitrates among
existing hand-written manoeuvres** rather than driving the wheels — a policy-selection
problem, which is a reasonable use of learning, rather than end-to-end control, which is
not. Its limitations, including the fact that a trained model expires when the simulator
underneath it changes, are documented in
[05. Coordination Policies](05-COORDINATION-POLICIES.md).

---

## 5. What we built, in one page

A decentralized AMR fleet coordination system delivered as a simulation, structured so that
the agent code is deployable unchanged onto edge hardware.

- **One agent, three deployments.** `AMRBrain.step(t, sensors, inbox) -> (actuation, outbox)`
  performs no I/O; transport and world are injected. The same implementation runs the
  headless benchmark at roughly 22× realtime, a multi-process UDP demonstration, and an
  unmodified drop onto a Raspberry Pi. [02. Architecture](02-ARCHITECTURE.md)
- **Three control loops at their own rates**, with protective stopping independent of the
  radio. [07. Safety](07-SAFETY.md)
- **A peer-to-peer protocol** carrying position, intent horizons, block claims and auction
  bids, with modelled loss, latency, range limits, dead zones and partitions.
  [03. Decentralized Protocol](03-DECENTRALIZED-PROTOCOL.md)
- **Thirteen route policies and four allocation policies** sharing one follower and one
  safety layer, so any measured difference is attributable to coordination and nothing else.
  [05. Coordination Policies](05-COORDINATION-POLICIES.md),
  [06. Task Allocation](06-TASK-ALLOCATION.md)
- **A browser dashboard** — a 3D digital twin that draws the things which are otherwise
  invisible: published intent horizons, live peer links, wait-for arrows, and blocks under
  token control. [09. Fleet Dashboard](09-DASHBOARD.md)
- **A strict acceptance gate** that pins the experiment, fingerprints every paired input,
  refuses mismatches, and exits non-zero on failure.
  [12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md)
- **A catalogue of measured findings that contradicted the obvious design**, which is the
  part of this project we would most want a judge to read.
  [14. Engineering Findings](14-FINDINGS.md)
- **An explicit statement of what we have not demonstrated.**
  [15. Limitations](15-LIMITATIONS.md)

---

## 6. Where to go next

| If you want to | Read |
| --- | --- |
| Check every requirement against evidence | [01. Requirements Traceability](01-REQUIREMENTS-TRACEABILITY.md) |
| Understand the system | [02. Architecture](02-ARCHITECTURE.md) |
| Interrogate the decentralization claim | [03. Decentralized Protocol](03-DECENTRALIZED-PROTOCOL.md) |
| Interrogate the safety claim | [07. Safety](07-SAFETY.md) |
| See the numbers | [12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md) |
| Run the demo | [16. Demo Runbook](16-DEMO-RUNBOOK.md) |
| Find what we did *not* do | [15. Limitations](15-LIMITATIONS.md) |
