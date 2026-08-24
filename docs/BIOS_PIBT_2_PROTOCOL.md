# BIOS_PIBT.2: Decentralized Directed-Cell Protocol

`BIOS_PIBT.2` is the high-density successor to the reactive `BIOS_PIBT.1`
policy. Version 1 is retained as a benchmark. Version 2 changes the traffic model
instead of adding another retreat heuristic.

## Failure that motivated V2

The original dashboard accepted `robots=100` but the server silently clamped the run
to 24 AMRs. Even at 24, seed 20 completed only 11/96 tasks in 300 seconds and produced
376 detected wait cycles. The standard warehouse contains 59 short non-passing aisle
segments; V1's long-corridor threshold protected none of them. Replicated PIBT then
requested sideways moves in cells where the chassis physically could not move sideways,
and repeated retreat recovery concentrated robots at shared junctions.

V2 removes that failure mode with prevention and backpressure.

## Protocol rules

### 1. Local safety is independent

The 50 Hz protective controller remains below all messaging. It sees anonymous lidar
detections, including people and failed robots, and may reject any lease or route. A
network message never overrides braking.

### 2. Route on a strongly connected circulation graph

The rack map is converted to alternating one-way aisle and perimeter lanes. The graph
is checked in tests to satisfy both properties:

- every free cell is reachable from every other free cell;
- no traversable edge also permits its reverse.

Consequently, two planned AMRs cannot meet in a head-on edge swap. A longer loop is
accepted in exchange for eliminating an unrecoverable physical configuration.
Maps such as the single bidirectional chokepoint, which cannot be made one-way while
preserving reachability, continue to use block leases and PIBT.

### 3. Lease every destination cell

Before entering a cell, an AMR broadcasts an idempotent `CLAIM` containing the cell's
deterministic resource ID, a local-clock TTL, route epoch, and frozen priority key.
It waits one complete propagation round, then enters only if its claim is the winner.
Physical occupancy outranks every packet. A claim is renewed while the move is pending
and is released early when the target changes; lost releases are repaired by TTL expiry.

This restores the discrete invariant that V1 accidentally violated in continuous
physics: one robot owns one cell. Two chassis can otherwise occupy different corners
of a large grid cell without touching and then deadlock on the same departure.

### 4. Resolve merges with one total order

Every heartbeat carries this frozen lexicographic key, larger first:

1. emergency battery state;
2. exiting a tree branch;
3. waiting age;
4. task service age;
5. loaded state;
6. negative distance to goal;
7. unique robot ID.

At a merge, contenders first publish intent for a propagation round. Every edge node
then compares the same frozen keys. The loser remains outside the destination cell;
it does not retreat into another traffic stream. Waiting age prevents a permanently
fixed low-priority robot.

### 5. Repair route discontinuities, never execute them

Continuous motion can cross a pose-estimation cell boundary before the old waypoint is
consumed. If the next route cell is no longer adjacent to the measured cell, V2 stops,
replans from the measured cell, and refuses the diagonal shortcut. This prevents a
route repair from cutting a rack corner.

### 6. Do not exceed physical capacity

The default cell pitch is 1.4 m. A 0.70 m diameter AMR, 0.30 m standstill field and
pose noise cannot execute a one-cell invariant on the old 1.0 m pitch: two correctly
centred neighbours have zero clearance budget. The larger pitch is a physical
admissibility condition, not a priority tuning parameter.

For requested fleets above 24, the dense warehouse scales from 31x21 to 61x41 rather
than silently placing 100 robots on the 24-robot benchmark floor. The dashboard now
reports and runs the requested fleet size, up to 100.

## Messages and state machine

| State | Action | Exit condition |
| --- | --- | --- |
| `REQUEST` | Publish next cell, intent and frozen key. | One propagation round elapsed. |
| `CLAIMED` | Re-broadcast expiring cell lease. | Own claim is the deterministic winner. |
| `COMMITTED` | Drive one adjacent centre-to-centre transition. | Measured cell reaches target. |
| `RELEASE` | Release old lease; publish the next request. | Immediate; TTL is the loss fallback. |
| `WAIT` | Stay outside the claimed cell and age priority. | Occupant leaves or a later claim wins. |

All state is onboard. The HTTP dashboard is a passive listener and is not in this
state machine.

## Conditional liveness argument

No asynchronous, lossy, partitionable system can give an unconditional liveness
guarantee. V2 makes a narrower claim under explicit assumptions:

1. the communication component eventually delivers a repeated heartbeat/claim within
   a bounded interval;
2. robot IDs are unique and nonfailed robots execute an admitted one-cell move in
   bounded time;
3. the route graph is strongly connected and has no reverse edge;
4. no directed cycle is completely occupied (capacity admission leaves a hole);
5. task service is finite and idle AMRs vacate a peer's published goal/intent;
6. cell pitch and localisation error satisfy the physical clearance budget.

Under these assumptions:

- **mutual exclusion:** the propagation gate, total-order claim and physical-occupancy
  override admit at most one robot to a destination cell;
- **no head-on deadlock:** reverse directed edges do not exist;
- **queue progress:** an occupied-cell wait follows the directed route. A finite wait
  cycle would require a completely occupied directed cycle, excluded by assumption 4;
  therefore some front robot reaches a hole and moves, and the hole propagates backward;
- **merge progress:** a finite contender set has one maximum frozen key; its lease is
  admitted, and waiting age supplies fairness across later rounds;
- **task progress:** strong connectivity supplies a finite route to every target, and
  repeated queue/merge progress eventually executes that route.

This is a deadlock/starvation argument for the traffic protocol. Collision safety still
belongs to the independent continuous protective layer and must be validated as a rate,
not inferred from the protocol proof.

## Reproducible evidence

The 34-test regression suite verifies the directed graph, cell-resource identity, positive
clearance budget, PIBT properties, braking, swept collision detection and message
round-trips:

```bash
python -m pytest tests -q
```

Pinned high-density comparison (`dead_zone_mesh`, 24 robots, seed 20, 300 s):

| Policy | Tasks | Robot contacts | Rack contacts | Wait cycles | Retreats |
| --- | ---: | ---: | ---: | ---: | ---: |
| `BIOS_PIBT.1` | 14/96 | 0 | 0 | 174 | 64 |
| `BIOS_PIBT.2` | 18/96 | 0 | 0 | 0 | 0 |

This is a 28.6% increase in completed work at the cutoff, not a makespan claim: neither
policy completed all 96 tasks in 300 seconds. V2 reached 95/96 by 2400 seconds in the
development trace with zero robot/rack contacts and no detected wait cycles. A run that
does not complete has no makespan, so the project does not report it as a completion-time
speedup.
