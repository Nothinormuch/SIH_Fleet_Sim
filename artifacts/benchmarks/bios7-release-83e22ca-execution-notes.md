# Final execution-identity candidate: verification notes

Candidate commit: `83e22ca02b7402909f28434d5b723fc86419e5fb`.
Frozen worktree: `/private/tmp/bios7-release-identity.m1nDny`.
Executable-source manifest:
`22b6df35df3bc04d5dc89db06260caeb6822e10b1833e85e7536fec0e294dc08`.
Control remains the immutable original BIOS 6 commit
`8e4ab51727e4fcd7f372de8b55b99c38cb213512`.

## Pre-campaign verification

- Full Python suite: **952 passed in 163.02 seconds**. The tested AMR source is
  `abad3611e9f4ef0d4c8259c221cab6a516d8d4a18c18860d422c6439a4e08fd4`.
- Focused Ruff checks and `git diff --check` passed.
- Five live-twin JavaScript tests passed; every first-party frontend JavaScript
  file passed Node's syntax check.
- The independent protocol review included delayed old sessions, peer expiry,
  previously unseen older sessions, missing declarations, and partial-body
  re-entry between observation samples. A session transition conservatively
  disables that peer's optional early-release optimization at this observer.
- The development artifact `bios7-compact-identity-development-doorway.json`
  predates the final any-session-transition rule. It is retained as a known-case
  communication diagnostic, not the final release result.

## Execution conditions

The frozen sources must not be edited. Raw stage files preserve every attempted
worker, including errors, unsafe controls, and incomplete workloads. Missing or
interrupted stages do not pass. The new candidate must earn its own regression50
prerequisite before capacity expansion; the earlier `86f42c3` pass is insufficient.

The Mac has ten physical/logical cores and 16 GiB RAM. Initially two, then up to
three headless campaign workers run concurrently, serial within each campaign.
This scheduling change does not change scenarios, seeds, simulated cutoffs,
physical parameters, worker resource allowances, or acceptance thresholds.
Headless wall/CPU times are shared-host measurements, not unloaded edge timing.
No heavy headless campaign may run during the separate live LAN timing campaign.

Headless `bytes_sent` counts serialized simulated protocol messages, including
the added execution identity fields. It is **not authenticated Ethernet wire
traffic**. HMAC/replay checks and actual multi-host traffic are separate evidence.
The communication comparison is cumulative per workload, not a claim that the
instantaneous packet rate or bytes per second always decreases.

## Completed stages so far

- `stress`: 108/108 workers; BIOS 7 completed 36/36 runs and 318/318 tasks with
  zero contacts. Every required human/fault coverage check and per-case current-V6
  completion-time/message/byte nonregression check passed.
- `repeat`: 8/8 workers; all four configurations matched their own semantic result
  hashes across two repetitions. BIOS 7 completed 60/60 tasks without contacts.
- `regression50`: 3/3 workers and the strict prerequisite passed. Both current-V6
  and BIOS 7 completed 100/100 tasks in 379.24 simulated seconds without contacts,
  sending 197,646 messages / 37,079,309 serialized protocol bytes each. This is a
  shared reliability repair on a directed layout, not a V7-only throughput gain.
  The original V6 again completed in 884.28 seconds with 19 rack-contact ticks;
  that unsafe result is retained and excluded from valid speedup claims.

Capacity expansion was started only after that new-source prerequisite passed.

## Holdout release failure

The registered holdout stopped after the seed 2012 pair: 52/120 worker results are
retained and 68 remain unrun. Source/run integrity passed and there were no worker
errors. BIOS 7 completed 12 of 13 observed runs, totaling 376/390 tasks, with zero
contacts. This is a failed campaign, not a 100% completion result.

On seed 2012, BIOS 7 completed only 16/30 tasks at the 1,200-second cutoff. Both
original and current-source V6 completed 30/30 in 871.14 seconds with zero contacts.
BIOS 7 sent 152,877 messages / 29,247,657 protocol bytes versus 101,686 / 19,191,300
for both controls. Completion, time and communication release requirements were
not met. A median computed from the other completed pairs cannot hide this failure.

The passive diagnosis reproduced every non-timing result field. A taskless robot
inside the controlled corridor remained the explicit blocker of loaded peers, but
fell outside their six-cell advertised intent horizon. The early geometric-request
return prevented the existing vacate/drain path from checking those fresh explicit
wait-for dependencies. No actual owner-route re-entry was seen among the 142
observed release events; weakening execution-identity checks is not justified.

The already-started fixed-floor 100-AMR group may supply separately scoped finite
evidence; it cannot override the failed holdout gate. No further capacity expansion
is approved for this rejected candidate. Preserve any intentional interruption
record separately from algorithmic failures.

Seeds 2000–2012 have now been observed. A later fixed-range retest must disclose
that only 2013–2029 remain unobserved, not replace failed seeds with easier ones.

The dashboard backend was restarted on port 8001 with this candidate. Its existing
recording still identifies the historical tested source rather than implying the
old result came from this checkout. No launch default has been changed and no
personal or collaborator branch has been pushed by this verification step.

Final stage outcomes and any limitations must be read from the accompanying raw
reports; these execution notes alone are not a release approval.
