# Comprehensive Fleet Algorithm Evolution & Version Comparison Guide

## Executive Overview

This document provides an exhaustive, comparative technical analysis of all traffic coordination, task allocation, and collision-avoidance algorithms implemented in the **SIH Fleet Simulation** repository (`SIH_Fleet_Sim`).

It traces the algorithmic lineage from the naive **`stop_and_wait`** baseline through centralized paradigms up to the state-of-the-art **`BIOS_PIBT.5`** (Energy-Feasible Decentralized Auction + Directed Circulation + Replicated PIBT).

---

## 1. Algorithmic Genealogy & Evolution Timeline

Each version was engineered to systematically eliminate a proven failure mode identified in empirical stress testing:

```
[ stop_and_wait ]
       |  Pathology: 100% gridlock in single-file aisles & junctions (0/90 completion)
       v
[ central (Hungarian + ST-A*) ]
       |  Pathology: Single Point of Failure (SPOF); Wi-Fi hiccup freezes entire fleet
       v
[ hierarchical (Central + P2P fallback) ]
       |  Pathology: Schedule interference; peer yielding fights optimizer timetable
       v
[ BIOS_1.0.0 (Reactive Heuristics) ]
       |  Pathology: Panic-on-stick causes 15 rack collisions; cyclic livelocks
       v
[ BIOS_PIBT.1 (Decentralized Priority Inheritance) ]
       |  Pathology: Reversing into retreat bays wedges chassis in dense short aisles
       v
[ BIOS_PIBT.2 (Directed Circulation + Cell Leases) ]
       |  Innovation: Strongly connected 1-way graph eliminates head-on deadlocks structurally
       |  Limitation: WMS still required for central task dispatching
       v
[ BIOS_PIBT.3 (Decentralized Batch Auction + Corridor Waves) ]
       |  Innovation: Bounded peer auction; drop-cell limits; corridor directional waves
       |  Pathology: High network broadcast churn; blind to battery depletion & cargo weight
       v
[ BIOS_4 (Neuroevolution PolicyNet) ]   [ BIOS_PIBT.5 (Production Flagship) ]
       |                                          |
 549-param learned policy                  Energy-feasible dynamic admission
 0 rack contacts                           Cargo physics & hard deadlines
 Halved replanning churn                   Sparse bidding (47.2% bid reduction)
                                           100% benchmark completion (90/90)
```

---

## 2. Detailed Technical Breakdown by Version

### 2.1. `stop-and-wait(Competition)` — The Enhanced Baseline
* **Primary Role:** The honest, computationally optimized baseline required by the SIH problem statement to measure the $\ge 20\%$ speedup criterion.
* **Mechanism:**
  * Global Layer 2: Standard static $A^*$ shortest path.
  * Traffic Layer 1: Evaluates immediate next cell $C_{\text{next}}$. If local LiDAR senses any object inside $C_{\text{next}}$, sets $\text{hold} = \text{True}$ and commands velocity $v = 0.0$.
  * Communication: Sends bare telemetry heartbeats only (so the GUI and benchmark logger function). Withholds all priority, intent, and reservations.
* **Why it Fails:** When two robots enter a narrow single-file aisle in opposite directions, both see each other in their next cell. Both stop and wait forever. **Result: 0/90 runs completed in the SIH acceptance benchmark.**

---

### 2.2. `central` — The Centralized Cloud Reservation Paradigm
* **Primary Role:** Replicates the industry standard (e.g., Amazon Robotics / Kiva Systems).
* **Mechanism:**
  * Central Fleet Manager computes global task assignments via the $O(N^3)$ Hungarian algorithm.
  * Computes collision-free global space-time reservations using Space-Time $A^*$ (time-expanded reservations).
  * AMRs are thin execution clients: they execute the manager’s time-stamped schedule without peer-to-peer negotiation.
* **Why it Fails:** **Single Point of Failure (SPOF).** If the central manager process crashes or network connectivity drops for $>3.0\text{ s}$, the entire fleet executes an emergency stop and parks. It cannot run autonomously on edge hardware.

---

### 2.3. `hierarchical` — Hybrid Manager with Degraded P2P Fallback
* **Primary Role:** Evaluates whether a central optimizer can be backed up by decentralized peer yielding when disconnected.
* **Mechanism:**
  * Runs `central` timetable when `mode == MODE_CENTRAL`.
  * Drops to `DEGRADED_P2P` when the manager times out.
* **Why it Fails:** **Interference pathology.** Layering peer yielding on top of a centralized timetable caused AMRs to defer to peer plans that the central optimizer had already deconflicted. The mechanisms clashed, cutting throughput to roughly half of the pure central baseline (974 replans).

---

### 2.4. `BIOS_1.0.0` — Early Reactive P2P Coordination
* **Primary Role:** First attempt at pure decentralized coordination without a central coordinator.
* **Mechanism:**
  * 1D scalar priority attached to heartbeats.
  * "Panic-on-Stick": When blocked for $>2.5\text{ s}$, an AMR was allowed to creep into "any free adjacent cell" to unstick itself.
* **Why it Fails:** **Physical rack collisions.** The unstick heuristic edged AMRs sideways into cells where differential-drive turning swept their chassis footprint directly into shelving racks (15 rack collisions observed in 420-second benchmarks).

---

### 2.5. `BIOS_PIBT.1` — Decentralized Priority Inheritance with Backtracking
* **Primary Role:** Replicated execution of Okumura et al. PIBT algorithm.
* **Key Mechanisms:**
  * **Pure-Function PIBT (`pibt_step`):** Deterministic solver run independently by every AMR over identical local peer snapshots.
  * **7-Tuple Lexicographic `PriorityKey`:**
    $$\mathbf{K} = \langle \text{emergency}, \text{exiting\_branch}, \text{waiting\_age}, \text{service\_age}, \text{loaded}, \text{dist\_bias}, \text{robot\_id} \rangle$$
  * **Priority Inheritance:** When high-priority robot $A$ requests cell occupied by $B$, $B$ temporarily inherits $A$'s priority to clear out.
  * **Retreat Bays:** Lower priority robots execute physical reverse maneuvers into side bays (`ST_RETREAT`).
* **Why it Fails:** In dense rack layouts with 59 short non-passing aisles, retreat maneuvers caused cascading backups that jammed shared junctions.

---

### 2.6. `BIOS_PIBT.2` — Directed Circulation Graphs & 2-Phase Cell Leases
* **Primary Role:** Structural conflict elimination rather than reactive evasion.
* **Key Mechanisms:**
  * **Strongly Connected Directed Circulation Graph:** The warehouse grid is converted into alternating one-way aisles and perimeter loops ($G = (V, \vec{E})$). Head-on edge swaps become topologically impossible!
  * **2-Phase Destination-Cell Leases:** An AMR broadcasts an idempotent `CLAIM` token with a local-clock TTL. Waits 1 propagation round (450 ms at merges) to verify it holds the highest frozen priority key before advancing.
  * **Discontinuous Route Repair:** Continuous turning can cross quantization boundaries before consuming a waypoint. V2 stops and replans rather than allowing diagonal shortcuts across rack corners.
  * **Physical 1.4 m Pitch:** Discretization scale increased from 1.0 m to 1.4 m to ensure two 0.70 m chassis plus standstill safety fields fit comfortably.

---

### 2.7. `BIOS_PIBT.3` — Decentralized Batch Auction & Congestion Control
* **Primary Role:** Completely eliminates the central WMS dispatcher; decentralizes both traffic AND task allocation.
* **Key Mechanisms:**
  * **Decentralized First-Price Auction:** Idle AMRs estimate bid cost:
    $$\text{Cost} = A^*(\text{robot} \to \text{pick}) + A^*(\text{pick} \to \text{drop}) + \text{battery\_penalty}$$
  * **Replicated Batch Matching:** Robots observe an auction window and deterministically match bids without an auctioneer.
  * **Congestion Admission Limits:**
    * Max 2 active tasks per physical drop station.
    * Immutable 2-task directional waves through bidirectional chokepoints (both must finish before wave reverses).
  * **Completion Gossip:** AMRs gossip `TASK_DONE` so lost packets cannot re-issue finished tasks.

---

### 2.8. `BIOS_4` — Neuroevolution PolicyNet (AI Edge Controller)
* **Primary Role:** Proof that Layer-0 safety protects even learned black-box neural networks.
* **Mechanism:**
  * 549-parameter MLP policy network trained via Natural Evolution Strategies (NES) over held-out warehouse seeds.
  * Replaces manual traffic decision logic at Layer 1; outputs discrete verbs (proceed, hold, yield, replan).
* **Performance:** Delivered 13/48 tasks on held-out seeds with **0 rack contacts** (compared to 15 for BIOS 1.0.0) and halved replanning churn to 729.

---

## 3. Spotlight on `BIOS_PIBT.5`: The Production Standard

`BIOS_PIBT.5` represents the pinnacle of the repository’s decentralized architecture. It refines V3 by integrating real-world physics, cargo constraints, and network bandwidth optimization.

```
+-----------------------------------------------------------------------------+
|                          BIOS_PIBT.5 ENHANCEMENT SUITE                      |
+-----------------------------------------------------------------------------+
|  1. Energy-Feasible Admission     |  2. Dynamic Cargo Physics & Deadlines   |
|  - Predicts complete mission Wh   |  - Cargo multipliers (Fragile, Heavy...) |
|  - Reserves 15% emergency battery |  - Full payload weight energy penalty   |
|  - Guarantees return to charger   |  - Hard delivery deadlines via TTL      |
+-----------------------------------+-----------------------------------------+
|  3. Sparse Bidding (Candidate Top-3)| 4. Liveness & Idle Repositioning      |
|  - Only top-3 nearest AMRs bid    |  - Automatic vacancy of drop stations   |
|  - Max 12 feasible tasks / round  |  - Asymmetric duplicate task cancel     |
|  - 47.2% reduction in auction bids|  - Zero fleet broadcast saturation      |
+-----------------------------------------------------------------------------+
```

### Pillar 1: Energy-Feasible Task Admission
In traditional fleets, a robot with 25% battery accepts a task, runs out of power mid-transit, and becomes a permanent metallic wall in an aisle.

`BIOS_PIBT.5` computes a strict forward energy model before submitting a bid:
$$E_{\text{mission}} = \left( E_{\text{approach}} + E_{\text{loaded}} \cdot M_{\text{cargo}} \cdot W_{\text{payload}} \right) + E_{\text{handling}} + E_{\text{docking}}$$
Where:
- $E_{\text{approach}} = P_{\text{move}} \cdot \left( \frac{d(\text{curr} \to \text{pick})}{v_{\text{cruise}}} \right)$
- $E_{\text{loaded}} = P_{\text{move}} \cdot 1.35 \cdot \left( \frac{d(\text{pick} \to \text{drop})}{v_{\text{cruise}}} \right)$
- $W_{\text{payload}} = 1.0 + 0.35 \cdot \left( \frac{\text{CargoWeight}}{100\text{ kg}} \right)$
- $E_{\text{handling}} = P_{\text{idle}} \cdot (12\text{ s} \cdot M_{\text{cargo}})$
- $E_{\text{docking}} = P_{\text{move}} \cdot \left( \frac{d(\text{drop} \to \text{nearest\_charger})}{v_{\text{cruise}}} \right)$

$$\text{Projected Battery Frac} = \text{Battery}_{\text{curr}} - \frac{E_{\text{mission}} \cdot (1 + \text{UncertaintyMargin})}{C_{\text{battery\_wh}}}$$

> **The Invariant:** If $\text{Projected Battery Frac} < 0.15$ ($15\%$ Emergency Reserve), **the robot suppresses its bid entirely.**

---

### Pillar 2: Cargo Physics & Hard Deadlines
* **Heterogeneous Cargo Types:**
  * `normal`: Multiplier $1.0\times$
  * `fragile`: Multiplier $1.1\times$
  * `heavy`: Multiplier $1.4\times$
  * `hazardous`: Multiplier $1.25\times$
* **Service Allowance:** Cargo multiplier scales the 12-second physical loading time.
* **Deterministic Task Urgency:**
  Tasks are sorted in auctions by:
  $$\text{Urgency} = \langle -\text{Priority}, \; \text{HasHardDeadline}, \; \text{DeadlineTTL}, \; \text{BidCost}, \; \text{TID} \rangle$$

---

### Pillar 3: Sparse Bidding & Candidate Pruning
In V3, every idle robot broadcasted bids for every announced task, causing quadratic packet storms ($O(N \cdot M)$).

In `BIOS_PIBT.5`:
1. **Top-3 Nearest Candidates Only:** For every task, only the 3 closest, idle, energy-feasible AMRs are permitted to bid.
2. **Bundle Cap:** An AMR bids on at most its **12 best feasible tasks** per round.
3. **Dynamic Admission:** If one of the top-3 candidates becomes busy or fails, the next closest AMR in the live peer table automatically steps in without needing a master coordinator.
4. **Impact:**
   * **$47.20\%$ reduction in auction bids** ($310,596 \to 164,000$).
   * **$24.64\%$ reduction in total fleet network messages** ($632,813 \to 476,875$).
   * **$0\%$ loss in makespan:** Identical completion time down to the hundredth of a second!

---

### Pillar 4: Idle Repositioning & Duplicate Cleanup
* **Drop Point Clearing:** Upon delivering cargo, an idle robot does not park at the drop cell. It automatically yields to an off-aisle parking dock or vacant buffer cell.
* **Asymmetric Duplicate Cancellation:** Under rare network partitions, two AMRs may both believe they won a task. The gossip protocol detects duplicate claims, forces the higher robot ID to cancel, vacate its current aisle, and enter a 5-second backoff before bidding again.

---

## 4. Master Parameter & Architectural Difference Matrix

| Technical Parameter / Feature | 🛑 `stop-and-wait(Competition)` | 🏢 `central` | 🔀 `hierarchical` | ⚠️ `BIOS_1.0.0` | 🧩 `BIOS_PIBT.1` | 🛣️ `BIOS_PIBT.2` | 📦 `BIOS_PIBT.3` | 🤖 `BIOS_4` | 🚀 `BIOS_PIBT.5` (Production) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Architectural Model** | Decentralized (Textbook) | Centralized Server | Hybrid (Central + P2P) | Edge P2P Heuristic | Edge P2P Multi-Agent | Edge P2P Circulation | Fully Decentralized | Neuroevolution Edge | Fully Decentralized Edge |
| **Task Allocation Method** | Static queue / External | Central Hungarian ($O(N^3)$) | Central Hungarian | Static queue | Static queue | Static queue | Bounded Peer Batch Auction | Bounded Peer Auction | **Energy-Feasible Top-3 Sparse Auction** |
| **Route Graph Topology** | Bidirectional Grid | Bidirectional Space-Time | Bidirectional | Bidirectional Grid | Bidirectional 2-Core | **Directed Circulation (1-way)** | Directed or 2-Core | Directed Circulation | **Directed Circulation + Directional Waves** |
| **Traffic Arbitration Slot** | Next-cell LiDAR check | Central reservation table | Central schedule / P2P | Local priority heuristic | Replicated PIBT | 2-Phase Cell Lease | 2-Phase Cell Lease + Wave | 549-param PolicyNet | **2-Phase Lease + Staging + Wave** |
| **Deadlock Prevention** | None (Halts) | Global coordination | Global coordination | Panic-on-stick | Replicated PIBT | Strongly Connected Graph | Graph + Directional Waves | Learned verb selection | **Topological Graph + Corridor Directional Waves** |
| **Deadlock Resolution** | None (Permanent) | Re-plan globally | Re-plan globally | Creep adjacent cell | Retreat to passing bay | None needed (Prevented) | None needed (Prevented) | Periodic replan valve | **Deterministic Invariant Repair (Zero retreats)** |
| **Wire Protocol Data** | Bare telemetry only | Server commands | Server commands / Heartbeat | Heartbeat + Scalar rank | Heartbeat + 7-tuple Key | Heartbeat + Key + CLAIM | Heartbeat + Key + BID/AWARD | Heartbeat + Features | **Heartbeat + Key + BID/AWARD + Energy/Cargo** |
| **Network Overhead** | Very Low ($\sim 2\text{ msg/s}$) | Moderate (Server load) | High (Interference) | Low ($\sim 5\text{ msg/s}$) | Moderate ($\sim 10\text{ msg/s}$) | Moderate ($\sim 10\text{ msg/s}$) | High ($310\text{k bids/run}$) | Low ($\sim 5\text{ msg/s}$) | **Optimized ($164\text{k bids/run}, -47.2\%$)** |
| **Energy & Battery Model** | None (Ignored) | None (Ignored) | None (Ignored) | None | Battery in priority rank | Battery in priority rank | Simple battery penalty | Battery feature input | **Full Wh Model: $15\%$ reserve, payload \& dock cost** |
| **Cargo Heterogeneity** | None (All uniform) | None | None | None | None | None | None | None | **4 types (Normal, Fragile, Heavy, Hazardous)** |
| **Hard Deadline Support** | No | No | No | No | No | No | No | No | **Yes (Relative TTL urgency ordering)** |
| **Candidate Pruning** | N/A | N/A | N/A | N/A | N/A | N/A | No (Open broadcast) | No | **Yes (Top-3 nearest feasible AMRs only)** |
| **Physical Safety Authority** | 50 Hz ISO 3691-4 field | 50 Hz ISO 3691-4 field | 50 Hz ISO 3691-4 field | 50 Hz field (compromised)| 50 Hz ISO 3691-4 field | 50 Hz ISO 3691-4 field | 50 Hz ISO 3691-4 field | 50 Hz ISO 3691-4 field | **50 Hz ISO 3691-4 field + Velocity Profiling** |
| **Single Point of Failure** | Zero | **Critical (Server)** | **Critical (Server)** | Zero | Zero | Zero | Zero | Zero | **Zero (100% Autonomous Uptime)** |
| **Benchmark Completion** | **0 / 90 (0.0%)** | 90 / 90 (when server up)| Unstable (Interference) | 7 / 48 (14.5%) | Partial (Spur jams) | 90 / 90 (100%) | 90 / 90 (100%) | 13 / 48 (27.1%) | **90 / 90 (100.0%)** |
| **Rack Collision Count** | **0** | **0** | **0** | **15 Contacts** | **0** | **0** | **0** | **0** | **0 Contacts** |

---

## 5. Catalog of Efficiency Enhancements

The evolution from `stop_and_wait` to `BIOS_PIBT.5` introduced dozens of multi-disciplinary optimizations across four core domains:

### A. Algorithmic & Graph Optimizations
1. **Elimination of Head-On Edge Swaps via Strongly Connected Graphs:** By converting 2-way warehouse aisles into alternating 1-way circulation loops, head-on conflicts are eliminated mathematically ($A \to B \land B \to A = \emptyset$).
2. **Discrete Invariant Restoration ($1.4\text{ m}$ Pitch):** Upscaling cell pitch from $1.0\text{ m}$ to $1.4\text{ m}$ provides sufficient spatial margin for physical $0.70\text{ m}$ AMR footprints and certified standstill safety zones.
3. **Discontinuous Route Clamping:** Prevents robots from cutting corners across racking when continuous turns cross quantization boundaries prematurely.
4. **Staging Distance Buffer:** Followers stage 1 cell earlier at chokepoint entrances, leaving full braking distance for the departing lead robot.

### B. Network & Messaging Optimizations
5. **Sparse Bidding & Top-3 Candidate Pruning:** Cuts auction bid messages by $47.20\%$ without affecting task makespan.
6. **Task Catalog Gossip & Decentralized Completion Verification:** Broadcasts `TASK_DONE` and unfinished catalog lists; heals packet loss up to $20\%$ with zero central database.
7. **Local Clock Relative TTLs:** Deadlines and leases are transmitted as remaining durations rather than epoch timestamps, eliminating the need for complex NTP clock synchronization across edge Linux boards.
8. **Asymmetric Claim Latches:** Latches priority keys during the heartbeat period to prevent two robots from simultaneously deciding they outrank each other.

### C. Kinematic & Physical Motion Optimizations
9. **Curvature-Aware Deceleration:** AMRs calculate remaining distance to the next 90-degree turn:
   $$v_{\text{profile}} = \sqrt{2 \cdot a_{\max} \cdot d_{\text{rem}} + v_{\text{turn}}^2}$$
   This completely prevents sliding/skidding into shelving racks during turns.
10. **In-Place Alignment During Holds:** When an AMR is held, it locks linear drive ($v=0$) but continues turning toward its next waypoint, eliminating heading errors before motion resumes.
11. **Clearance-Increasing Creep Primitive:** In tight quarters, recovery motion is permitted only when the instantaneous relative velocity vector strictly increases all neighbor gaps.

### D. Operational & Fleet Longevity Optimizations
12. **Stranding Prevention via Dynamic Wh Modeling:** By reserving 15% emergency power and accounting for the return journey to a charger, no robot ever dies in active traffic.
13. **Payload-Aware Acceleration & Energy Scaling:** Heavy cargo increases motor draw estimates by up to 35%, ensuring energy feasibility under maximum mechanical strain.
14. **Drop Station Off-Aisle Vacancy:** AMRs instantly vacate delivery stations upon task completion, preventing finished robots from acting as stationary walls.

---

## 6. Empirical Verification & Release Evidence

### 6.1. Acceptance Benchmark Performance (`sih_acceptance_overlap`)
Evaluated across 90 deterministic seeds (4, 6, and 8 robots) against a 1,200-second cutoff:
* **`stop_and_wait`:** **0/90 completed (0%)** — 100% timed out due to aisle deadlock.
* **`BIOS_PIBT.5`:** **90/90 completed (100%)** — All 1,620 tasks delivered.
  * **4 Robots:** Median makespan $384.03\text{ s}$ ($\ge 63.64\%$ faster than baseline cutoff).
  * **6 Robots:** Median makespan $554.70\text{ s}$ ($\ge 51.17\%$ faster than baseline cutoff).
  * **8 Robots:** Median makespan $721.33\text{ s}$ ($\ge 34.16\%$ faster than baseline cutoff).
* **Inter-Robot Contacts:** **Zero (0)** contacts across 88.65 robot-hours.

### 6.2. Energy Stress Benchmark (`energy_acceptance`, 8 AMRs, 16 Tasks)
Paired comparison between `BIOS_PIBT.3` and `BIOS_PIBT.5`:
* **Task Completion:** BIOS 3 completed 7/8 seeds; BIOS 5 completed **8/8 seeds (100%)**.
* **Hardest Seed:** BIOS 3 timed out at $1,200\text{ s}$; BIOS 5 finished at **$1,020.64\text{ s}$**.
* **Aggregate Auction Bids:** Decreased from $310,596$ to **$164,000$ ($47.20\%$ reduction)**.
* **Total Network Messages:** Decreased from $632,813$ to **$476,875$ ($24.64\%$ reduction)**.

### 6.3. Fault Injection & Resilience Smoke Tests
* **5% Uniform Packet Loss:** Completed 16/16 tasks at $528.66\text{ s}$ with 0 collisions.
* **Mid-Run Process Kill (Robot Failure):** Instant lease timeout and task reassignment completed in $46.82\text{ s}$.
* **Network Partition & Heal:** Partitioned clusters operated independently and merged cleanly in $19.38\text{ s}$.

---

## 7. Conclusion: Why BIOS_PIBT.5 is the Definitive Solution

`BIOS_PIBT.5` achieves what neither traditional centralized automation nor naive decentralized heuristics could accomplish:

1. **Centralized-grade efficiency** without a centralized point of failure.
2. **Guaranteed collision-free safety** governed by an uncompromised 50 Hz local ISO 3691-4 optical barrier.
3. **Deadlock-free liveness** through structural graph orientation and 2-phase spatial leases.
4. **Hardware and energy viability** through payload-aware forward battery modeling and sparse network bidding.
