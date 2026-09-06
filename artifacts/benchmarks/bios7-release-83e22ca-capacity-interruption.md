# Intentional interruption of the 83e22ca capacity stage

This execution note supplements the unchanged raw report
[bios7-release-83e22ca-capacity.json](bios7-release-83e22ca-capacity.json).
It does not add a measured worker result or change a benchmark verdict.

## Reason and scope

On 2026-09-06, the separate registered holdout for frozen candidate
`83e22ca02b7402909f28434d5b723fc86419e5fb` failed: seed 2012 completed only
16/30 BIOS 7 tasks within its 1,200-second simulation cutoff, while both BIOS 6
controls completed all 30. That failure remains in
[bios7-release-83e22ca-holdout.json](bios7-release-83e22ca-holdout.json).

The parent task explicitly requested stopping this obsolete capacity campaign
instead of spending more resources on the already-rejected candidate. This
superseded the earlier instruction to finish the current fixed-floor 100-AMR
comparison. No scenario, simulation cutoff, acceptance threshold or source file
was changed.

## Exact interruption

Process identities and parent-child ownership were verified immediately before
signalling. Only these three processes were stopped:

- Boundary watcher PID 53586: SIGTERM at `2026-09-06T09:16:57.059761+00:00`.
- Capacity coordinator PID 50443: SIGSTOP at
  `2026-09-06T09:16:57.060160+00:00` to prevent further worker creation.
- Its sole worker, PID 52683 with PPID 50443: SIGTERM at
  `2026-09-06T09:16:57.181271+00:00`.
- Coordinator PID 50443 then received SIGINT at
  `2026-09-06T09:16:57.181304+00:00` and SIGCONT at
  `2026-09-06T09:16:57.181308+00:00`, allowing it to exit.

All three processes were absent on the post-stop check. No other job was
signalled. The worker being interrupted was `fixed_floor`, 100 robots, seed 0,
configuration `bios7`. Its measured output had not been returned or saved.
Therefore this attempt is **externally interrupted and unmeasured**, not an
algorithmic timeout, safety failure, successful completion, or timing score.

## Evidence retained and work not measured

The raw report remains byte-for-byte unchanged with three of 27 declared worker
results saved. All three are the scaled-floor 50-AMR, seed-0 comparison:

- Untouched BIOS 6: 100/100 tasks, 884.28-second makespan, zero robot-robot and
  robot-human contacts, but 19 robot-rack contacts. It is not a safe performance
  baseline.
- Same-source BIOS 6: 100/100 tasks, 379.24-second makespan, all contact counts zero.
- BIOS 7: 100/100 tasks, 379.24-second makespan, all contact counts zero.

Of the remaining 24 declared workers, one was the interrupted/unmeasured
fixed-floor 100-AMR BIOS 7 attempt. The other 23 never started: both fixed-floor
100-AMR BIOS 6 controls; all three scaled-floor 100-AMR configurations; and all
three configurations for fixed/scaled floors at 3, 10 and 25 robots. No 100-AMR
completion, contact, performance or scalability result can be inferred here.
The partial capacity stage is not a release pass.

Raw report SHA-256 before and after stopping:
`775a182dccc722daf3177544399b48fdf2d851bd7c7d9c02ba211dc0e269634f`.

Candidate source manifest:
`22b6df35df3bc04d5dc89db06260caeb6822e10b1833e85e7536fec0e294dc08`.
Untouched-control source manifest:
`9f11ef40edc199d004c2a704dc46b59963bc66121051bedc9438489e61f7550c`.

Wall/CPU context remains shared-host headless execution, not unloaded live-control,
hard-real-time or Raspberry Pi performance evidence.
