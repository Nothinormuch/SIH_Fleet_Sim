# BIOS 7: bounded corridor passage release (experimental)

BIOS 7 is selectable as `BIOS_PIBT.7`; BIOS 6 remains the default. This candidate
keeps Auction V2 and the existing traffic/safety hierarchy. It does not introduce
a central winner selector, increase robot speed, shrink robot bodies, relax the
20 ms control budget or claim a universal completion guarantee.

## What changes

BIOS 6 can continue counting a delivery task against a narrow corridor's auction
admission capacity after the loaded robot has already passed through that corridor.
BIOS 7 separates that task-admission pressure from the physical corridor token.

Each robot independently observes the task owner inside the corridor, then fully
outside it. The observation must refer to the same task generation, descriptor,
auction epoch and owner. A fresh pose, delivery goal and exit-to-drop route that
does not re-enter the corridor are required. Only then can that observer stop
counting the completed passage against future task admission. Every actual move
still passes the existing physical token, spatial arbitration and sensor checks.

Missing or stale observations retain the conservative admission behavior. New
ownership, re-entry or a return to the entry side invalidates the optimization.
Static exit-route caching is bounded; live ownership/occupancy is revalidated.

An ablation, `bios7_no_release`, runs BIOS 7 with passage release disabled. This
helps distinguish the algorithm's effect from measurement and supporting changes.

## Reliability and deployment support

- Network intake has both packet and elapsed-work limits. A budget-exhausted peer
  backlog is discarded until the socket is drained; it is not accepted as fresh
  position or lease evidence. Controllers issue a stop while intake is degraded.
- Sensor backlog and post-planning age checks prevent overdue computations from
  being published as fresh motion. The downstream command watchdog remains required.
- Visualization refresh occurs after the selected actuator command is sent. Full
  cycle timing includes the remaining work; this is not hard-real-time certification.
- The two-host runner pins exact source/workload bytes, verifies authenticated
  foreign-host peer receipts, correlates commands to sensor tokens and keeps
  simulated physics separate from independent controllers. See
  [the network deployment instructions](22-MULTIHOST-DEPLOYMENT.md).

## Reproduce the finite comparison

The untouched control commit is
`8e4ab51727e4fcd7f372de8b55b99c38cb213512`. Create a separate clean checkout of that
commit. Run the following from the frozen BIOS 7 candidate, replacing the control
path with that checkout:

```bash
python bios7_acceptance.py --execute \
  --control-root /absolute/path/to/clean-bios6-control \
  --cases sih --robots 10 --seeds 3 \
  --configurations bios6,bios7,bios7_no_release,stop_wait,centralized \
  --output artifacts/benchmarks/bios7-reproduction.json
```

Every policy receives the same serialized scenario and physical configuration.
Policy order is counterbalanced, source hashes are checked before and after each
worker, and prior outputs are not overwritten. A timed-out baseline gives a
completion-time **lower bound**, not an exact measured speedup. Seed 99 is excluded
because it is a hand-built demonstration, not an acceptance sample.

Use `--phase holdout --cases sih,humans,open --robots 3,10` for unseen seeds beginning
at 1000. Capacity studies use `--phase scaling`, which explicitly distinguishes a
fixed floor from scaled floor/service capacity and records free-cell density. A
100-robot headless result is not evidence of 100 physical robots or live Pi timing.

## Promotion is not automatic

A three-seed improvement does not approve this candidate. Required evidence includes
the stated SIH 20% gate on the declared overlapping-path campaign, contact-free task
completion, held-out non-regression, ownership/recovery checks, communication cost,
and live timing under dashboard load. Record failures and unrun cases. The harness
never promotes BIOS 7 or changes repository defaults.

## Development evidence (not release approval)

The immutable algorithm snapshot `12d1ac2` was compared with control `8e4ab51`.
Subsequent network-runner and observer fixes do not change that traffic algorithm.
The [three-seed ten-AMR campaign](../artifacts/benchmarks/bios7-fixed-control-final-development.json)
completed 90/90 tasks under each BIOS policy, with zero observed contacts. BIOS 7
made each run 8.42–16.30% faster than BIOS 6; the median paired improvement was
14.35%, and the sum of the three completion times fell 13.12%. Messages fell from
319,644 to 281,638 (11.89%).

The slowest BIOS 7 run finished at 815.84 seconds. Stop-and-wait did not finish
within its 1,200-second window, so the minimum conservative margin is 32.01%, not
an exact completed-baseline speedup. All three cases pass the stated 20% gate.
They are development seeds, not proof of universal superiority. The aspirational
15% median gain versus BIOS 6 and 10% gain on every seed were not reached.

The [six-case human screen](../artifacts/benchmarks/bios7-human-final-development.json)
used 3/10 AMRs and seeds 3–5: 39/39 tasks, zero contacts, and identical completion
times for BIOS 6/7. This is non-regression evidence, not a human-throughput gain.
The [initial held-out sample](../artifacts/benchmarks/bios7-heldout-1000-1002.json)
used seeds 1000–1002: both policies completed 90/90 tasks with zero contacts.
Total makespan fell 13.16%, with a 12.76% median paired gain. Messages fell from
326,690 to 286,876. Safety-stop ticks, however, increased from 6,796 to 7,886;
this is not an improvement in every metric. These three observed seeds must not
be relabeled as the thirty fresh seeds in the later release plan.

The [capacity screen](../artifacts/benchmarks/bios7-capacity-screen.json) completed
its 3-, 10- and 25-robot cases without contacts. At 50 robots both policies
completed 100/100 tasks but recorded **19 rack-contact ticks**, so expansion to
100 robots stopped. The directed-layout capacity runs showed no V7 timing gain
over V6. Fixed/scaled 50 used the same rounded map dimensions; they are not two
independent geometries. This screen is a recorded failure, not release approval.

## Shared safety repair under verification

A continuous-motion trace found a recovery segment passing too close to a rack
corner even though the destination grid cell was free. A temporary creep speed
also expired before the off-centre recovery was finished. The new candidate
checks the complete swept footprint, supports a bounded intermediate waypoint,
and retains the recovery speed cap and mapped braking check until completion.

Integration testing then exposed a separate lifecycle mismatch: a 1.4-m recovery
at 0.20 m/s needs at least seven seconds of translation, but the old six-second
retreat timeout could cancel it before completion. Validated recoveries now have
a bounded, physically sufficient lifetime and require metric centre acquisition.
These changes are shared by current-source V6 and V7; comparisons must not
attribute their effect solely to the V7 passage-release algorithm.

The [release checklist](24-BIOS7-RELEASE-CHECKLIST.md) defines the new frozen-source
campaign and hardware boundaries. Until it passes, these repairs remain under
verification, BIOS 6 remains the default, and earlier failures stay visible.

### Important centralized-reference limitation

The repository's prioritized space-time A* + Hungarian reference stalled in the
overlap campaign. Dispatcher execution was present, but a source audit found that
replanning rebuilds reservations for only a pending subset, physical delays do not
invalidate the schedule, and task transitions can replace a scheduled route with
unscheduled local A*. These defects predate BIOS 7. Retain that result as a failure
of this repository reference, not proof of superiority over a validated centralized
MAPF solver or industrial fleet system. A repaired reference needs independent
validation and a fresh comparison; neither frozen control is silently repaired.

Both headless campaigns shared this Mac while running different serial workers.
Their deterministic completion/contact/message results remain the measured outputs;
their wall/CPU maxima are not unloaded edge-computer timing benchmarks. See the
[execution context](../artifacts/benchmarks/bios7-execution-context.json).
