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

The new stress stage completed all 108 workers and passed: 36/36 V7 cases,
318/318 tasks, zero contacts, and per-case makespan/message/byte nonregression
against current-source V6. Every declared human/fault coverage check was exercised.
Its robot-failure scenario contains one recovery job; this is not a claim of
recovery from every failure in a fully loaded warehouse.

The fresh regression50 stage passed all three workers. V7 and current-source V6
both completed 100/100 tasks in 379.24 simulated seconds with zero contacts;
their cumulative messages and bytes matched. The unsafe immutable V6 reference
(19 rack-contact events) remains excluded from valid performance claims. The
new-source capacity stage was started only after this prerequisite passed.

The repeat stage also passed all eight workers. Each of the four configurations
matched its own repeated non-timing simulation result exactly. V7's two 30/30
runs had zero contacts and passed all three current-source V6 nonregression gates.

The complete registered holdout is running from this frozen source. Old-source
completed stages do not count as a passing prerequisite for the new source.
The complete registered SIH campaign and latest-source live LAN timing must finish and
be recorded under their own correct scope before release approval.
Workers use the same deterministic tasks, seeds, physical settings and strict
V6 completion-time/message/byte comparison. The already observed seed range is
not replaced by easier seeds or called untouched holdout evidence.

The user subsequently deferred 100-AMR testing for the demonstration. The capacity
queue was intentionally interrupted before its first 100-AMR worker, and the raw
partial evidence and exact interruption accounting were preserved separately in
`bios7-release-063d20d-capacity-scope-change.md`. That capacity stage did not pass;
100+ scalability is not a release claim. The passed fixed-floor 50-AMR regression
remains valid and is distinct from the interrupted expansion queue.

The frozen-source causal checks finished all twelve workers. In the development
SIH ten-AMR seed-0 case, V7 completed 30/30 tasks in 833.08 seconds versus 968.12
for both V6 controls and V7 with passage release disabled: an exact 13.9487%
reduction. Against current-source V6, messages fell from 112,641 to 99,296 and
serialized bytes from 21,323,922 to 18,922,397. Every configuration had zero
contacts. This is a known development case, not the registered holdout campaign.

The doorway control (three AMRs, seed 2000) completed in 51.20 seconds with V7
versus 54.88 with both V6 controls and release disabled. V7 also sent fewer
messages and bytes. On the open-floor control, all four configurations completed
12/12 tasks in 69.76 seconds with identical 2,601 messages and 491,832 bytes.
Disabling passage release therefore removes the measured congestion benefit;
there is no measured speed benefit on this open-floor case. These controlled
ablations isolate a policy effect, not a universal improvement guarantee.

The root backend on port 8001 was idle and was restarted to load this controller.
Port 8000 and unrelated applications were not stopped. The prepared seven-round
Windows package matches the current controller fingerprint
`9509986f53f1ca8dc927f320f1b6c99da5cd169e2b21b4bd6f48a7a960b023bc`;
preparation is not a measured LAN run. Private session keys remain outside Git.

Actual browser verification selected the existing Grand Challenge profile with
BIOS 7, Auction V2, ten AMRs, five workers, seed 1 and the original 800-second
window. The loaded 4,188-frame recording reported 20/20 tasks in 418.7 simulated
seconds, zero robot/robot, robot/human and robot/rack contacts, and no browser
errors. Changing the draft selector did not relabel the preceding V6 recording.
This is dashboard/simulator workflow evidence, not live independent-host timing.

BIOS 6 remains the default. No merge or push has occurred. Laptop software-in-the-loop
tests are not Raspberry Pi measurements, physical safety certification or universal
AMR compatibility evidence.
