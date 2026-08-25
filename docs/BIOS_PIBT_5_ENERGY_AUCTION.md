# BIOS 5 energy-feasible decentralized auction

## Status

`BIOS_PIBT.5` is a software-only experimental extension of BIOS_PIBT.3. It does not
change Layer-0 stopping, PIBT movement resolution, cell/block leases, corridor waves,
or completion gossip. It changes only which idle robots send task bids and when an
idle robot returns to charging.

## Admission rule

For each robot/task pair the robot deterministically estimates energy for:

1. its current cell to pickup;
2. pickup to drop with a loaded-motion multiplier;
3. fixed pickup/drop service time;
4. drop to the nearest reachable charger;
5. a declared uncertainty margin.

The robot bids only when projected post-task energy is at least the declared emergency
reserve. A fixed `battery > 80%` threshold is deliberately not used: the same task has
different energy cost for robots at different locations.

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

These are eight-seed results, not the final acceptance matrix. They support continued
evaluation but do not justify a universal speedup claim.

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

## Required release evidence

Before BIOS 5 becomes the default, run at least 30 paired seeds across multiple fleet
sizes and packet-loss levels. The release comparison must report completion, makespan,
bid and total message counts, energy suppressions, charging time, reassignments, final
SOC distribution, and contacts. A policy fails the gate if it lowers messages by
leaving tasks unfinished.
