# Bidirectional idle-clearance repair: verification in progress

Frozen commit: `063d20d4b2bf75f0e2d0d3d8634eb747680356c4`.
Frozen worktree: `/private/tmp/bios7-release-clearance.wsWyLd`.
AMR SHA256: `c19cea1422c93e68527304083bcf51ea967a012cb8dc02d77f3830b3cd32d7dc`.
Executable-source manifest:
`879821a191575e6004bf40e3378f16a50e9df6be1330fd5614845b4730a0be1a`.
Original V6 control remains immutable at
`8e4ab51727e4fcd7f372de8b55b99c38cb213512`.

## Verified repair

The independent review of 98f3433 found a taskless off-centre AMR discarding a
valid staged escape route when directed circulation was disabled. That candidate's
read-only reproduction and interrupted evidence remain preserved under their
original filenames. The user explicitly approved applying the finding.

The fix retains validated metric waypoints on either map type. Bidirectional
idle-clearance motion also passes through the ordinary block-admission and
intersection-arbitration path; the directed-map cell-lease path is unchanged.
Selecting recovery does not grant ownership or bypass traffic checks. Physical
dimensions, speed limits, reserve, clearance margins and acceptance cutoffs did
not change. A centred robot with a clear ordinary route keeps its normal follower.

The regression failed on the pre-fix source because its selected target had no
installed recovery. On the final fix, a full `brain.step` simulation clears the
captured state into its safe destination at 12.84 simulated seconds for both V6
and V7, with zero contacts. The unmodified failing case had not translated after
60 seconds. Stationary neighbouring bodies and fresh waiting-peer reports remain
present throughout; they are not removed to produce progress.

Thirty added tests cover four rotations, two policies, zero/noisy localization,
different noise seeds, retained controlled-block leases, a newly detected obstacle
after planning, and the unchanged clear-centred fast path. The focused recovery,
idle-corridor and V7 group passed 157 tests. The final complete suite passed
1,006 tests in 182.11 host seconds; Ruff and diff checks passed. The earlier
development test invocation with incomplete lease tests was interrupted and is
not reported as a successful suite.

## Release evidence still required

New regression50 and the complete registered holdout were started from this frozen
source. Old-source completed stages do not count as a passing prerequisite for
the new source. Stress, repeats, capacity, causal checks and latest-source live LAN
timing must be recorded under their own correct scope before release approval.
Workers use the same deterministic tasks, seeds, physical settings and strict
V6 completion-time/message/byte comparison. The already observed seed range is
not replaced by easier seeds or called untouched holdout evidence.

The root backend on port 8001 was idle and was restarted to load this controller.
Port 8000 and unrelated applications were not stopped. The prepared seven-round
Windows package matches the current controller fingerprint
`9509986f53f1ca8dc927f320f1b6c99da5cd169e2b21b4bd6f48a7a960b023bc`;
preparation is not a measured LAN run. Private session keys remain outside Git.

BIOS 6 remains the default. No merge or push has occurred. Laptop software-in-the-loop
tests are not Raspberry Pi measurements, physical safety certification or universal
AMR compatibility evidence.
