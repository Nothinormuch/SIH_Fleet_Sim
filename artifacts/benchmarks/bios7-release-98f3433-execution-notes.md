# Exit-junction candidate: release verification in progress

Frozen commit: `98f34333e44f5e14ad6828402c8fa5da920c1096`.
Worktree: `/private/tmp/bios7-release-apron.oVtcvl`.
Executable-source manifest:
`bf522ab07397ade0a0310c77b9078602dc491cd1ae6da1c09c3959c26fd1810c`.
Original V6 control remains clean at `8e4ab51727e4fcd7f372de8b55b99c38cb213512`.

This frozen candidate is not edited while measured workers run. Three independent
stage queues (regression50, holdout, stress) started on September 6, 2026; workers
are serial and counterbalanced within each queue, with at most three concurrent
headless workers on the ten-core/16-GiB Mac. Their wall/CPU timings are shared-host
simulation measurements, not independent-controller latency or Raspberry Pi timing.
No heavy benchmark will run during the later live LAN timing campaign.

The registered inputs, 1,200-second capacity/SIH cutoffs, physical parameters,
strict current-source V6 time/message/byte gates and SIH 20% gate are unchanged.
Existing stage files are never overwritten, and failures/worker errors are retained.
The 50-AMR stage must pass against this exact source before new capacity expansion.
Old-source successes do not satisfy that prerequisite.

The preceding `83e22ca` capacity experiment was intentionally stopped after its
holdout had failed and the repaired candidate was ready. Its 100-AMR attempt was
interrupted and is not evidence of either successful or failed algorithm execution.
The separate interruption note preserves the raw report and exact scope.

Pre-freeze verification: 973 Python tests, five live-twin JavaScript tests, focused
Ruff checks, main.js syntax and diff checks passed. The exact seed-2012 four-way
development comparison passes all three nonregression metrics, but is an observed
development case, not untouched holdout or full release evidence.

Final verdicts must come from completed raw stage reports. Missing/unrun stages,
latest-source ten-controller live timing, and default-selection tests remain
outstanding until explicitly verified. BIOS 6 remains the default; no remote has
been pushed or merged by this step.
