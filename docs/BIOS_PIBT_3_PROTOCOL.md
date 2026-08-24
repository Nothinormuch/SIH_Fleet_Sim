# BIOS_PIBT.3: Decentralized Allocation + Traffic Protocol

`BIOS_PIBT.3` combines the collision-free traffic foundation of
`BIOS_PIBT.2` with decentralized, congestion-aware task allocation. The default
combination is:

```text
route/traffic = BIOS_PIBT.3
task allocation = auction
```

That combination has no fleet manager. The WMS injects task descriptions but does not
choose a robot, route, priority, cell, or corridor direction. Every award and movement
decision is computed onboard from peer messages. The dashboard is a passive listener.

The optional `hungarian` allocation remains a centralized comparison baseline. It is
not part of the fully decentralized V3 claim.

## How V2 and allocation relate

V2 and the allocation layer solve different decisions:

| Layer | V3 decision | Mechanism |
| --- | --- | --- |
| Task allocation | Which AMR should execute which task? | Replicated batch auction and expiring peer award |
| Admission | How much work may enter a shared resource? | Drop-cell capacity and bounded corridor waves |
| Route | Which legal grid path reaches the target? | Local A*; directed circulation where topology permits |
| Traffic | Who owns the next cell/corridor? | V2 cell leases, block leases, frozen priority and PIBT |
| Safety | May the chassis execute the command now? | Independent 50 Hz onboard protective field |

Allocation never supplies a route. Routing never changes task ownership. A task award
sets only the pickup/drop goal; the winning AMR computes its own route and negotiates
every movement with peers.

## Protocol

### 1. Replicated task catalog

The WMS broadcasts `TASK_NEW` once. Every V3 robot gossips unfinished catalog entries,
so a robot that missed the injector packet learns it from peers. On bidirectional maps
or lossy links, robots also gossip `TASK_DONE`; one lost completion cannot permanently
hold a traffic wave or cause a completed job to be reissued.

### 2. Bounded decentralized batch auction

An idle AMR locally estimates:

```text
cost = A*(robot -> pickup) + A*(pickup -> drop) + battery penalty
```

It broadcasts bids for a bounded set of tasks. After one bid window, peers apply the
same deterministic ordering. On a directed warehouse graph, replicated greedy matching
fills several independent jobs in one round. On a bidirectional chokepoint, each task
has one contract-net winner and no peer cascades it to a second-best robot from a
slightly different snapshot.

Only the winning robot creates an authoritative `AWARD`. It renews that award as an
expiring lease while executing the task. A silent/crashed winner therefore releases
work automatically without a coordinator.

### 3. Congestion admission before motion

Two finite resources are controlled during allocation:

- a drop cell admits at most two active jobs;
- a bidirectional single-file corridor admits an immutable two-task directional wave.

The corridor batch does not refill when its first task finishes. Both member task IDs
must finish before peers derive the next direction from the lowest unfinished task.
Robots already on the pickup side win; an AMR is not sent through the corridor empty
and immediately back loaded. This prevents two opposing queues and prevents all robots
from being drained into one bay.

### 4. Topology-specific traffic

On rack maps, V3 retains V2's strongly connected one-way circulation graph, two-phase
destination-cell leases and deterministic merge priority. Head-on edges do not exist.

On maps that cannot be oriented without losing reachability, V3 uses:

- directional task waves;
- an exclusive expiring block lease;
- a one-cell-early staging check at the corridor mouth;
- a two-phase destination-cell merge gate;
- PIBT priority inheritance/backtracking outside the block.

Normal V3 traffic never executes the legacy reverse/retreat manoeuvre.

### 5. Bounded invariant repair

Packet loss can make two AMRs enter different corners of one quantized merge cell
without physically contacting. If repeated heartbeats reveal this violated invariant,
the lower unique robot ID freezes ownership for that repair. The other AMR selects an
adjacent non-corridor cell that maximizes measured separation, exits at recovery speed,
and replans. The owner holds until the cell is unique again. Dynamic priority cannot
flip the repair owner mid-turn.

Idle robots may not park in or enter a bidirectional controlled block. An idle robot
found inside must move monotonically toward the nearest mouth.

## Conditional liveness argument

No asynchronous system can promise progress during a permanent partition or when a
failed chassis physically blocks the only route. V3's no-deadlock argument requires:

1. unique robot IDs;
2. eventual delivery of repeated heartbeat, lease and catalog messages;
3. nonfailed robots execute an admitted cell transition in bounded time;
4. directed cycles retain at least one empty cell;
5. bidirectional waves contain a finite number of finite tasks;
6. cell pitch/localization error satisfy the physical clearance budget.

Under those assumptions, the directed graph has no head-on wait edge and an empty cell
propagates backward through each queue. A bidirectional block has one direction and one
lease owner per finite wave; completing the bounded batch forces a phase change rather
than unbounded same-direction admission. Merge contenders have a total order, leases
expire, missed catalog state is repeated, and a duplicate-cell state has one immutable
repair owner. Therefore no protocol wait can remain closed forever under the stated
assumptions.

This is a traffic liveness argument, not a mathematical zero-collision claim. Continuous
safety remains the independent protective layer and finite simulation only measures an
observed contact rate.

## Reproducible evidence

```bash
python -m pytest tests -q
python benchmark.py --seeds 30 --jobs 8
```

The pinned acceptance scenario runs 30 paired seeds for 4, 6 and 8 robots, with three
alternating-direction tasks per robot and a 1200 s cutoff. Both policies receive the
same decentralized auction catalog; SHA-256 workload identities make that equality
machine-checkable.

`BIOS_PIBT.3` completes 30/30 runs for every fleet. Stop-and-wait completes 0/30 at
each fleet size. Because the baseline is right-censored, the honest result is a
conservative lower bound, not an exact speedup. The minimum per-seed bounds are 63.03%
(4 robots), 45.50% (6) and 32.48% (8), all above the required 20%. Candidate medians
are 389.38 s, 599.23 s and 758.63 s. All 1,620 candidate tasks complete with zero
observed robot/robot, robot/human or robot/rack contacts across 93.3722 robot-hours.

See [`SIH_ACCEPTANCE_BENCHMARK.md`](SIH_ACCEPTANCE_BENCHMARK.md) for the censoring proof,
strict pass conditions, bootstrap intervals, provenance and limitations. The release
gate also requires the current Python regression suite, lint, Python bytecode
compilation, and frontend JavaScript syntax checks to pass.
