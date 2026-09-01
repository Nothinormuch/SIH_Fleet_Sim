# BIOS 6 three-way comparison for SIH 26123

## Decision

BIOS 6 with Auction V2 passes the repository's defined SIH acceptance campaign.
Across 90 deterministic overlapping-path runs (30 seeds each at 4, 6, and 8
robots), it completed 1,620 of 1,620 tasks with zero recorded robot-robot,
robot-human, or robot-rack contacts, zero detected deadlocks, and zero deadline
misses. The worst paired completion-time result was a conservative 34.353%
reduction relative to the 1,200 s timeout of both comparison implementations,
which exceeds the problem statement's 20% target.

This is simulation evidence tied to commit
`027d144563fecf1f4fc7440de1653a271d021172`. It is not physical safety
certification and is not proof of universal completion for every possible map,
task set, fault, or fleet size.

## What was compared

| Configuration | Task allocation | Traffic/path coordination | Architecture |
|---|---|---|---|
| BIOS 6 + Auction V2 | Battery-aware bounded peer auction | BIOS_PIBT.6 local peer coordination | Decentralized |
| Competition stop-and-wait | Deterministic static preassignment | Occupancy-only stop/wait with event-driven retry and local detour | Non-cooperative baseline |
| Prioritized space-time A* | Hungarian assignment | Fleet-manager prioritized space-time A* | Centralized reference |

The collaboration branch's two labels originally selected the same Python
`stop_and_wait` backend. Its JavaScript "already established" implementation was
not imported by the simulator and used a separate random, non-physical test. The
integration therefore adds two real Python execution policies rather than claiming
that UI labels are algorithms.

The policies run in the same 50 Hz world, kinematics, sensing, swept-contact
detector, and task catalog. Allocation is deliberately part of the architecture
under test, so this is an end-to-end architecture comparison, not a traffic-only
algorithm comparison. Catalog digests must match for every paired result. Baseline
timeouts are treated as right-censored lower bounds, never as completed makespans.

## SIH overlapping-path acceptance results

| Fleet | BIOS completion | BIOS median / p95 | Stop-and-wait completion | Central reference completion | Minimum time reduction or lower bound |
|---:|---:|---:|---:|---:|---:|
| 4 robots | 30/30 runs, 360/360 tasks | 383.820 s / 414.476 s | 0/30 runs, 14/360 tasks | 0/30 runs, 12/360 tasks | 64.715% |
| 6 robots | 30/30 runs, 540/540 tasks | 555.870 s / 585.027 s | 0/30 runs, 10/540 tasks | 0/30 runs, 3/540 tasks | 50.635% |
| 8 robots | 30/30 runs, 720/720 tasks | 725.200 s / 780.460 s | 0/30 runs, 10/720 tasks | 0/30 runs, 3/720 tasks | 34.353% |

Across those 90 BIOS runs, the minimum observed separation was 0.853 m, the
maximum message rate was 11.92 messages per robot-second, and the maximum local
planning call was 8.034 ms. All three cross-process semantic replay probes matched
for BIOS, stop-and-wait, and the centralized reference.

Because neither baseline completed an SIH overlap run before the 1,200 s cutoff,
the reported percentages are conservative lower bounds rather than exact speedups.
The result describes these checked-in implementations; it must not be generalized
into a claim that BIOS outperforms every possible centralized or prioritized planner.

## Supporting cases and honest limits

| Case | BIOS 6 | Competition stop-and-wait | Central reference | Interpretation |
|---|---:|---:|---:|---|
| Blocked aisle, 3 robots | 10/10, 24.40 s | 10/10, 23.10 s | 10/10, 32.78 s | Stop-and-wait is 5.628% faster when coordination overhead is unnecessary. |
| Human aisle, 4 robots | 10/10, median 435.19 s | 0/10 | 0/10 | BIOS clears the dynamic scenario with zero human contacts. |
| Partition/heal, 4 robots | 10/10, 19.38 s | 10/10, 17.68 s | 10/10, 36.84 s | Stop-and-wait is 9.615% faster in this short, uncontended task. |
| Robot failure, 3 robots | 10/10, 47.06 s | 0/10 | 0/10 | BIOS performs the task reassignment; static ownership cannot recover. |
| Open floor, 5 robots | 9/10 within 240 s | 0/10 | 0/10 | One BIOS seed completed 14/15 while still moving the final task at cutoff. |

The open-floor exception is a time-budget miss, not a deadlock: replaying the same
seed with a 400 s observation window completed 15/15 at 278.12 s with zero contacts
and zero detected deadlocks. The original 240 s result remains in the evidence file
so the cutoff is not changed after observing the outcome.

Across all 140 BIOS runs in the campaign, the result was 139/140 completed within
the configured cutoffs and 1,929/1,930 tasks completed, with zero contacts of every
recorded type, zero detected deadlocks, and zero deadline misses.

## SIH 26123 coverage

- **At least three AMRs:** tested at 3, 4, 5, 6, and 8 robots.
- **Decentralized communication:** BIOS robots exchange task, heartbeat, intent,
  priority, lease, completion, and experience messages peer-to-peer; the WMS only
  announces the catalog.
- **Dynamic conflict resolution:** peer arbitration, priority inheritance,
  backtracking, circulation, expiring claims, and the bounded clearance recovery run
  at the robot edge.
- **Task allocation and rerouting:** Auction V2 allocates tasks without a dispatcher;
  dynamic-obstacle rerouting and failed-robot task reassignment are exercised in the
  supporting campaign.
- **Edge execution:** the same `AMRBrain.step` implementation runs in headless
  simulation and the repository's authenticated UDP multi-process demonstration.
- **Fleet dashboard:** the lightweight frontend exposes fleet position, battery,
  tasks, decisions, messages, safety data, and the newly selectable baselines.
- **Success criteria:** zero recorded inter-robot contacts and a worst-case measured
  lower-bound improvement of 34.353% in the defined overlapping-path acceptance
  campaign.

## Reproduce

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
pytest -q
python baseline_comparison.py --seeds 30 --fault-seeds 10 --jobs 8
```

The machine-readable evidence is
`artifacts/benchmarks/bios6-three-way-comparison.json`.
