# Frozen 86f42c3 execution context

This is supporting development evidence, not release approval. The candidate and
control worktrees were not edited during these runs.

- Candidate: `86f42c3f22e7f35481cb4b397e14d94613fe42fd`.
- Executable-source manifest: `dff0f6e17023fe202d88f535840d57590df8040395166aaccfc0a4cecb642ee7`.
- Controller file `src/amr.py`: `02bbdfad086a6bf54ad0cb5827e8469adde5e258ac6ffe3d160137499c990ec5`.
- Immutable original BIOS6 control: `8e4ab51727e4fcd7f372de8b55b99c38cb213512`.
- Personal and collaborator remote mains remained unchanged; no release was pushed.

## Regression checks before freezing

The full then-collected pytest suite passed **885 tests in 108.83 host seconds**.
Three additional explicit BIOS7 mixed-warehouse regressions then passed in 52.68
host seconds: ten AMRs, known development seeds 0/1/4, twenty tasks per case, the
unchanged 800-second showcase window, real modeled human travel, racks and the
existing radio dead zone. All sixty tasks completed with zero contacts. These
three cases are not untouched holdout evidence.

The five live-twin JavaScript tests passed; every frontend JavaScript file parsed.
Ruff and diff checks passed. Independent review checked the physical staging and
controlled-corridor idle-recovery regressions. The exceptional failed-direct
recovery search now examines at most 81 staging candidates instead of 49; this
bounded increase is not a claim of unchanged live-loop timing.

## Shared-host campaign execution

The regression50 and stress stages each use serial paired workers, but the two
campaigns shared this Mac. After stress completed, its worker slot was used for
the registered holdout stage. Host CPU/wall/RSS fields are shared-host headless
diagnostics, not unloaded control-loop or Raspberry Pi measurements. The dashboard
server on port 8001 remained open. No new live Mac/Windows campaign ran concurrently.

The stress stage completed all 108 declared comparison workers: BIOS7 completed
36/36 cases and 318/318 tasks with zero robot–robot, robot–human and robot–rack
contacts. All exact per-case time/message/byte nonregression checks against the
current-source BIOS6 comparator passed. Required positive human, blocked-aisle and
failure observations were present. The failure fixture contains one recoverable
job, not a full loaded-fleet failure campaign. Reassignment counters include peer
observations, not unique jobs. This stage does not contain the SIH20% gate.

## Subsequent protocol review

Review found that heartbeat task ID and delivery goal alone cannot bind observed
motion to the owner's exact task generation/descriptor/auction epoch. A small
authenticated-message reproduction attributed old-generation motion to a newer
local claim with the same task ID. This changes advisory corridor admission, not
task ownership or a physical lease; no acceptance collision is inferred from the
witness. Nevertheless, the identity binding needs correction before promotion.

The frozen 86f42c3 files remain unchanged. The root development tree is receiving
the fix, and its new sources require new evidence. The 86f42c3 holdout was selected
for an intentional early stop to avoid presenting an obsolete candidate as final;
its partial raw rows and interruption details remain visible in the
[interruption note](bios7-release-86f42c3-holdout-interruption.md). The 50-AMR run
completed as a test of the bounded idle-tail/recovery repair, not final BIOS7 approval.

The [regression50 stage](bios7-release-86f42c3-regression50.json) passed all three
workers. Current-source BIOS6 and BIOS7 both completed 100/100 tasks in 379.24
simulated seconds, with zero contacts, 197,646 messages and 37,079,309 bytes.
Passage admission is inapplicable on this directed-circulation floor: the shared
recovery/runtime changes, not the V7-only mechanism, explain this improvement.
The immutable original control reproduced 100/100 in 884.28 seconds with 19
rack-contact ticks; that unsafe control is excluded from valid speedup claims.
The earlier corrected `5cce5de` run had stopped at 99/100 at 1,200 seconds, so this
is meaningful liveness recovery without a relaxed physical or workload setting.

The first identity-bearing development screen retained full task identity on every
eligible heartbeat. It completed the known 3/10-AMR chokepoint cases safely, but
three-AMR bytes rose 5.80% versus current-source BIOS6. The
[raw development result](bios7-identity-development-doorway.json) retains that
regression; it is not a release pass. Compact session-bound identity references and
delayed old-session handling require their own review and fresh measurements.
