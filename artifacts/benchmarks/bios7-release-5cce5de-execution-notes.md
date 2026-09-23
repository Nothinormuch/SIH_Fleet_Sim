# Frozen 5cce5de execution notes

The candidate controller sources remained frozen in
`/private/tmp/bios7-release-candidate.PzWV9N`; the original control remained the
clean `8e4ab51727e4fcd7f372de8b55b99c38cb213512` checkout.

The stress stage completed all 108 declared comparison workers: all 36 BIOS 7
cases completed 318/318 tasks with zero robot/robot, robot/human or robot/rack
contacts and strict per-case current-source V6 nonregression. Human, blockage and
single-task failure coverage were positively measured. These are separate stress
fixtures, not a combined loaded warehouse failure campaign.

Both the corrected V6 comparator and BIOS 7 in the exact 50-AMR regression
completed 99/100 tasks within the unchanged 1,200-second simulated window, with
zero contacts. Original V6 reproduced 100/100 tasks and 19 rack-contact ticks.
The incomplete corrected runs require investigation; eliminating contacts alone
does not establish release readiness. The 100-AMR expansion was not started.

After this liveness regression was observed, the unrelated holdout queue was
deliberately stopped to reserve a diagnostic worker. Its active subprocess at
seed 2004 (original stop-and-wait) received SIGINT. The harness records this as an
external worker error and finishes the current paired case before stopping.
That interrupted row is not a measured algorithm failure, simulation timeout or
performance denominator. Completed rows remain retained; the partial campaign
is not a 30-seed holdout pass. No seed was replaced or result overwritten.

Headless campaigns overlapped on this laptop. Their host CPU/wall timings are not
unloaded real-time controller measurements. Small browser smoke tests also ran
during this period. No physical AMR or Raspberry Pi was tested.

Later root-worktree revisions fix two redundant comparison-validity labels for
incomplete candidates and keep UI identity tied to displayed evidence. They do
not modify the frozen controller, its physics or existing raw reports. An
incomplete candidate's `incomplete` verdict, null speedup and failed release gate
remain authoritative even if the frozen report's redundant validity flags say
otherwise.
