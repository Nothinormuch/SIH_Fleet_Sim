# BIOS 5 energy-feasible decentralized auction

## Status

`BIOS_PIBT.5` is the default software policy and an extension of BIOS_PIBT.3. It does not
change Layer-0 stopping, PIBT movement resolution, cell/block leases, corridor waves,
or completion gossip. It changes which idle robots send task bids, how cargo urgency
is ordered, and when an idle robot returns to charging.

Tasks remain backward compatible. Missing fields mean `cargo_type="normal"`, zero
weight, priority 1, and no hard completion deadline. Auction close time remains
`bid_deadline`; delivery deadline is a separate receiver-local field transported as a
relative TTL. The existing peer claim table remains authoritative for leases, while
`Task.lease_owner` and `Task.lease_until` mirror it for inspection.

## Admission rule

For each robot/task pair the robot deterministically estimates energy for:

1. its current cell to pickup;
2. pickup to drop with a loaded-motion multiplier;
3. cargo-adjusted pickup/drop handling time;
4. drop to the nearest reachable charger;
5. a declared uncertainty margin.

The robot bids only when projected post-task energy is at least the declared emergency
reserve. A fixed `battery > 80%` threshold is deliberately not used: the same task has
different energy cost for robots at different locations.

Every AMR is the same 100 kg-capacity model. Cargo uses configurable factors: normal
1.0, fragile 1.1, heavy 1.4, and hazardous 1.25. The weight factor is
`1 + 0.35 * cargo_weight / 100 kg`. Cargo and weight multiply committed task-motion
energy; the post-drop charger leg is unladen. Cargo type also multiplies the 12-second
handling allowance, so it is not a display-only field.

A BIOS 5 bid is rejected when the robot is not idle, already owns work, is charging,
has no valid pickup/drop path, exceeds payload capacity, would cross the 15% reserve
after task plus docking, or cannot complete the drop before a hard deadline. Declared
priority orders tasks first, then earliest hard deadline, then bid cost and stable IDs.
The WMS only repeats task announcements and never selects or assigns an auction winner.

## Candidate reduction and liveness

Only the three nearest fresh, idle, sufficiently charged robots are candidates for a task,
and each robot broadcasts at most its twelve best feasible tasks per round. Candidate
membership is live rather than age-expanded: busy, charging, failed, stale, and
energy-infeasible peers leave the set, automatically admitting the next robot. This
preserves liveness without turning every long-running task back into a broadcast-open
auction. Missing peer state widens participation rather than suppressing it.

## Frozen initial parameters

- emergency reserve: 15% of nominal capacity
- estimate uncertainty: 10%
- loaded-motion multiplier: 1.35
- pickup/drop service allowance: 12 seconds
- initial candidate count: 3 robots per task
- bid bundle: twelve best feasible tasks per robot and round
- candidate replacement: live peer state; no task-age expansion
- energy retry backoff when no task is feasible: 5 seconds
- post-charge auction re-entry: 45%
- identical-model payload capacity: 100 kg
- full-payload energy premium: 35%
- task order: priority, hard deadline, bid cost, stable IDs

## Refined paired result

Eight deterministic seeds used `energy_acceptance`, eight robots, sixteen tasks,
heterogeneous starting SOC, and identical workloads for BIOS_PIBT.3 and BIOS_PIBT.5.

- completion: BIOS 3 = 7/8; BIOS 5 = 8/8
- aggregate auction bids: 310,596 -> 164,000 (47.20% reduction)
- aggregate messages: 632,813 -> 476,875 (24.64% reduction)
- hardest seed: BIOS 3 timed out at 1200 s; BIOS 5 completed at 1020.64 s
- hardest-seed bids: 101,529 -> 39,118 (61.47% reduction)
- on the seven seeds completed by both policies, makespan was unchanged
- observed robot/robot, robot/human and robot/rack contacts: zero

Against the first successful BIOS 5 implementation itself, the refined candidate rule
reduced bids from 279,686 to 164,000 (41.36%) and total messages from 592,561 to
476,875 (19.52%). All eight per-seed makespans were bit-for-bit unchanged at the
reported two-decimal resolution. A rejected four-task-bundle experiment did reduce
traffic further but stalled seed 2 at 3/16 tasks; it is not part of the implementation.

These are energy-stress results, not the stop-and-wait acceptance comparison. They
measure the benefit of sparse energy-feasible bidding without claiming validation
accuracy or a universal speedup.

## Final smoke gates

The refined implementation also completed three deterministic liveness checks with no
robot/robot, robot/human, or robot/rack contacts:

- `energy_acceptance`, seed 0, 5% uniform packet loss: 16/16 at 528.66 s;
- `robot_failure_reassignment`, seed 0: 1/1 at 46.82 s after one robot failure;
- `partition_recovery`, seed 0: 4/4 at 19.38 s.

These are targeted regression gates, not pooled resilience evidence.

Reproduce each side of the paired experiment:

```bash
python run.py --scenario energy_acceptance --robots 8 --seeds 8 \
  --policy BIOS_PIBT.3 --allocation-policy auction --json /tmp/bios3-energy.json
python run.py --scenario energy_acceptance --robots 8 --seeds 8 \
  --policy BIOS_PIBT.5 --allocation-policy auction --json /tmp/bios5-energy.json
```

## Default-policy release evidence

The strict stop-and-wait gate ran 30 paired seeds at each of 4, 6 and 8 robots. BIOS 5
completed 90/90 candidate runs and all 1,620 tasks; stop-and-wait completed 0/90 before
the fixed 1,200-second cutoff. Minimum conservative reduction bounds were 63.64%,
51.17% and 34.16%. No robot/robot, robot/human or robot/rack contacts were observed
across 88.6512 candidate robot-hours.

During release testing, an asymmetric auction view exposed a duplicate worker that
continued after a peer had completed the same logical task. Completion convergence now
cancels the duplicate, forces it to vacate its current traffic lane before bidding
again, and has a dedicated regression test. The formerly failing 8-robot seed 10 then
completed 24/24 tasks in 727.16 seconds.

A separate 20% packet-loss seed exposed an unsafe liveness fallback: awarding a task
before an empty cross-corridor approach could oppose an active loaded wave. BIOS 5 now
elects one idle bidder to reposition only after two lease windows of quiescence, then
re-auctions from the pickup side. The formerly failing seed completes 12/12 tasks in
391.34 seconds with zero observed contacts.

These results justify BIOS 5 as the software-demo default. They remain simulation
evidence, not physical safety certification or a universal performance guarantee.
