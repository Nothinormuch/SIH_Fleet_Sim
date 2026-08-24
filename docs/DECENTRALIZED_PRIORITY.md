# Decentralized AMR Priority and Path Conflict Resolution

This branch implements an edge-only coordination policy named
`decentralized_pibt`. Its purpose is narrow and testable: decide which AMR may occupy
the next grid cell, displace lower-priority traffic when space exists, and serialize
entry to single-file warehouse aisles without a fleet-manager decision.

The long-range route remains local A*. PIBT resolves only the next move. The onboard
continuous safety controller remains authoritative and may reject any network-derived
decision. The simulator contains a low-speed recovery creep for a locally verified
free-cell escape; this is a simulation motion primitive, not a substitute for a
certified safe-direction function on real hardware.

## Why PIBT

Priority Inheritance with Backtracking (PIBT) was designed for iterative multi-agent
path finding, including warehouse pickup-and-delivery workloads. It assigns a unique
priority at each step. If a high-priority agent requests an occupied vertex, the
occupant temporarily inherits the priority and recursively chooses another vertex. If
that chain cannot move, the original requester backtracks to another candidate.

The original paper states that PIBT can be implemented in a fully decentralized form
and proves finite-time reachability under a graph condition: every adjacent vertex pair
must belong to a simple cycle of length at least three. A normal warehouse can violate
that condition at shelf spurs and dead ends. The exPIBT work addresses this limitation
with temporary priorities and movement restrictions in attached trees.

Research sources:

- [Okumura et al., PIBT, IJCAI 2019](https://www.ijcai.org/proceedings/2019/76)
- [Fujitani et al., temporary priority for tree-shaped paths](https://arxiv.org/abs/2205.12504)
- [Reference Python implementation by the PIBT author](https://github.com/Kei18/pypibt)

The code in `src/priority.py` is an independent, dependency-free implementation of the
core mechanism; no source was copied from the reference project.

## Priority order

Every heartbeat carries a frozen `PriorityKey`. Larger values move first and every AMR
uses the same lexicographic comparison.

| Rank | Field | Current rule | Purpose |
| ---: | --- | --- | --- |
| 1 | `emergency` | battery below 10% | Preserve a robot at operational risk. |
| 2 | `exiting_branch` | current cell is in a tree appendage and the goal is outside it | Drain dead ends before admitting conflicting traffic. |
| 3 | `waiting_age` | seconds continuously blocked, quantized | Prevent starvation. |
| 4 | `service_age` | seconds since current task began, quantized | Bound task starvation across repeated conflicts. |
| 5 | `loaded` | robot is carrying toward the drop point | Avoid wasting already-executed pickup travel. |
| 6 | `distance_bias` | negative Manhattan distance to goal | Prefer the robot nearer completion when stronger fields tie. |
| 7 | `robot_id` | stable unique ID | Produce a total order with no equal keys. |

The key is latched when broadcast. A robot never compares its continuously increasing
local value against an older peer value; doing that allows both sides of a conflict to
believe they lost.

## Decision procedure

At the 10 Hz traffic loop, each AMR independently performs the following operation:

```text
positions <- own sensor cell + fresh peer heartbeat cells
goals     <- own goal + peer goals
preferred <- own A* next cell + peer first intent cells

for robot in descending published priority:
    try preferred cell, then goal-directed neighbours, then wait
    reject an already-reserved target
    reject a two-robot edge swap
    if target is occupied:
        occupant inherits requester's priority
        recursively assign the occupant first
    if recursive assignment fails:
        roll back tentative assignments and try another target
```

`pibt_step()` is pure: it has no socket, global coordinator, system clock, or mutable
singleton. Given the same snapshot, every edge node returns the same next
configuration. It rejects contradictory snapshots and partial configurations instead
of manufacturing unsafe movement.

## Topology and corridor handling

`src/topology.py` computes the graph 2-core once per warehouse map. Vertices peeled
from the core form tree appendages. A robot that must leave its current appendage gets
temporary exit priority. This implements the priority signal motivated by exPIBT, but
it does **not yet** implement all movement restrictions from that paper; therefore its
deadlock-free theorem must not be claimed for this simulator.

Long degree-two aisle runs are treated as exclusive blocks. The contender with the
highest frozen claim key receives an expiring lease. Important protocol properties are:

- claim expiry is a duration interpreted on the receiver's local clock;
- a request's priority is frozen for the whole lease attempt;
- the winner waits one propagation round before entry;
- admission is latched, so the winner does not restart the gate timer every 100 ms;
- physical presence inside a block outranks every remote claim;
- releases are optional; TTL expiry repairs lost release messages.

These leases are peer-to-peer messages. They do not require a server or synchronized
clocks.

## Wire additions

| Message | Field | Meaning |
| --- | --- | --- |
| `HB` | `pk` | Seven-element serialized `PriorityKey`. |
| block `CL` | `ttl` | Lease duration; evaluated against the receiver's local time. |
| block `CL` | `pk` | Frozen priority for deterministic contender convergence. |
| `IN` | existing `cells`, `w` | Ordered next cells and occupancy windows used as preferred moves. |

All messages remain self-contained and idempotent JSON datagrams.

## Verification

Run the regression suite:

```bash
python -m pytest tests -q
```

The priority tests cover serialization and total ordering, a three-robot inheritance
chain, edge-swap rejection with backtracking, deterministic four-agent rotation, and
temporary priority for a tree appendage. The existing suite also covers A*, corridor
decomposition, swept collision detection, braking bounds, malformed messages, network
determinism, dead zones, and a single-robot end-to-end task.

Run the same task set against all policies:

```bash
python run.py --scenario open_floor_control --policy all --robots 4 --seeds 3
python run.py --scenario dense_aisles --policy all --robots 4 --seeds 3
python run.py --scenario crossing_chokepoint --policy all --robots 4 --seeds 3
```

Development snapshot, seed 0 with four AMRs:

| Scenario | Stop-and-wait | Decentralized PIBT | Inter-robot contacts |
| --- | ---: | ---: | ---: |
| `open_floor_control` | 9/16 tasks, timeout | 16/16 in 153.3 s | 0 |
| `dense_aisles` | 5/16 tasks, timeout | 5/16, timeout | 0 |
| `crossing_chokepoint` | 0/12 tasks, timeout | 1/12, timeout | 0 |

This is evidence that the resolver improves progress, but it is **not** evidence of the
required 20% makespan reduction: a makespan ratio is undefined when the baseline does
not finish. `metrics.compare()` intentionally reports that comparison as incomparable.

## Known limitation and next engineering step

PIBT assumes discrete, synchronized moves. This simulator has asynchronous lossy
messages, noisy cell estimates, acceleration limits, turn-in-place dynamics, and a
continuous protective field. A valid grid displacement can therefore be physically
awkward to execute. Rack-lined stress scenarios can still record rack contacts during
abrupt give-way/retreat manoeuvres, and the extreme single-corridor case does not
converge.

The next step is not another priority weight. It is a two-phase **motion primitive**:

1. reserve the target cell and publish the inherited move;
2. brake to a stable cell centre;
3. rotate in place;
4. execute the reserved one-cell transition with a bounded time window;
5. acknowledge completion or expire the reservation and recompute.

Only after that controller bridge is implemented should this project run multi-seed
parameter sweeps and publish a 20% completion-time result.
