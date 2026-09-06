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

## Completed checks

The stress stage passed all 108 workers: BIOS 7 completed 36/36 cases and 318/318
tasks with zero contacts and all strict current-V6 time/message/byte checks passing.
The repeat stage passed all eight workers, including identical semantic outputs
for both executions of each of its four configurations.

Actual browser QA used the updated root backend on port 8001, whose controller
sources match the frozen candidate. Grand Challenge's existing ten-AMR/five-worker
profile, seed 1 and 800-second window, completed 20/20 tasks in 418.7 s with zero
contacts. Its 4,188-frame recording loaded as BIOS 7; changing the draft selector
had not relabelled the prior BIOS 6 recording. No browser errors were reported.
This is UI workflow verification, not live multi-host timing evidence.

Review also clarified two presentation semantics without changing measurements:
World._record logs repeated pair contacts at most once per second, so nonzero
counts are logged contact events, not an exhaustive count of overlapping ticks.
The reported minimum separation is centre-to-centre, not surface gap or physical
braking margin. Older notes calling nonzero counts "ticks" should be read with
this correction. The zero-contact gate and all raw metrics are unchanged.
The HTML explanations/HUD label were corrected and eight policy-profile tests
passed. This HTML-only presentation update does not alter controller source or
the frozen executable-source manifest; final browser reload verification follows.
