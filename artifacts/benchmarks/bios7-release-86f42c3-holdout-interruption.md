# Intentional interruption of the 86f42c3 holdout campaign

This note accompanies the unmodified raw
[holdout report](bios7-release-86f42c3-holdout.json). It is not an algorithm failure
or a release approval. The campaign was intentionally stopped after an independent
review identified a task-generation binding gap in heartbeat evidence used for
BIOS 7 corridor admission. The correction is being developed separately; the
frozen source was not edited during this campaign. This finding concerns passage
evidence, not permission to bypass task ownership or physical spatial leases.

## Exact scope

- Candidate: `86f42c3f22e7f35481cb4b397e14d94613fe42fd`.
- Source manifest: `dff0f6e17023fe202d88f535840d57590df8040395166aaccfc0a4cecb642ee7`.
- Frozen checkout: `/private/tmp/bios7-release-tail.xQ1prU`.
- Untouched control: `8e4ab51727e4fcd7f372de8b55b99c38cb213512`,
  `/private/tmp/bios7-control.WrdEhP`.
- Registered stage: release `holdout`, ten AMRs, seeds 2000–2029, unchanged
  1,200-second simulation cutoff and 1,800-second worker wall allowance.
- Coordinator PID: `43611`. The source identity, exact output path, child PPID
  and frozen worker script path were checked before each signal.

## Deliberate process stops

- At `2026-09-06T07:32:22.932131+00:00`, SIGTERM was sent to worker PID `43905`,
  owned by coordinator `43611`: SIH seed 2001, `bios6_safety_fixed`.
- At `2026-09-06T07:32:23.194564+00:00`, SIGTERM was sent to worker PID `43950`,
  owned by coordinator `43611`: SIH seed 2001, `bios6`.

An earlier inspected worker had finished before signals were sent. Ownership and
the current configuration were re-resolved; no stale PID was signaled. The harness
records worker errors and finishes the current comparator pair before stopping
progression, so the two remaining owned workers were stopped individually. The
coordinator exited normally through its failure-reporting path with exit code 2.
The independently running regression50 coordinator PID `41731` and its worker
PID `43846` were not signaled. No orphan holdout worker remained.

## What was retained

The report contains eight rows: six measured worker results and two intentional
SIGTERM errors. Seed 2000 has a complete four-policy comparison. At seed 2001,
stop-and-wait and BIOS 7 finished their workers before interruption; the two V6
comparators have no measured results. BIOS 7 completed 30/30 tasks in each of the
two observed seeds, with zero contacts. Stop-and-wait reached the logical cutoff
with 0/30 tasks in each seed; those are censored observations, not completed times.

There are 112 unrun workers, and the declared headless stage did not pass.
`source_integrity` remains true; the aggregate run-integrity gate is false because
two workers were intentionally interrupted. The harness's generic stop reason
must be read with this note: the SIGTERM rows have no performance score and are
not evidence of an algorithmic failure. No raw row was edited or removed.

The two headless campaigns shared host CPU while they overlapped. Their wall/CPU
figures are not unloaded live-controller or Raspberry Pi timing evidence. The
completed [stress stage](bios7-release-86f42c3-stress.json) remains evidence for its
exact snapshot, not approval of the superseded candidate. A later source freeze
requires its own registered acceptance campaign. No further 86f42c3 campaign was
started after this interruption.
