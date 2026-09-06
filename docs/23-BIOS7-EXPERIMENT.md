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
