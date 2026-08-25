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

In the early auction, only the three nearest fresh, idle, sufficiently charged robots
are candidates for a task. The candidate count expands every 20 seconds while a task
remains unclaimed. It therefore converges to the original open BIOS_PIBT.3 auction
instead of sacrificing completion merely to report fewer messages. Missing peer state
widens participation rather than suppressing it.

## Frozen initial parameters

- emergency reserve: 15% of nominal capacity
- estimate uncertainty: 10%
- loaded-motion multiplier: 1.35
- pickup/drop service allowance: 12 seconds
- initial candidate count: 3 robots per task
- candidate expansion: one robot every 20 seconds
- energy retry backoff when no task is feasible: 5 seconds
- post-charge auction re-entry: 45%

## Preliminary paired result

Eight deterministic seeds used `energy_acceptance`, eight robots, sixteen tasks,
heterogeneous starting SOC, and identical workloads for BIOS_PIBT.3 and BIOS_PIBT.5.

- completion: BIOS 3 = 7/8; BIOS 5 = 8/8
- aggregate auction bids: 310,596 -> 279,686 (9.95% reduction)
- aggregate messages: 632,813 -> 592,561 (6.36% reduction)
- hardest seed: BIOS 3 timed out at 1200 s; BIOS 5 completed at 1020.64 s
- hardest-seed bids: 101,529 -> 68,956 (32.08% reduction)
- on the seven seeds completed by both policies, makespan was unchanged
- observed robot/robot, robot/human and robot/rack contacts: zero

These are preliminary eight-seed results, not the final acceptance matrix. They support
continued evaluation but do not justify a universal speedup claim.

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
