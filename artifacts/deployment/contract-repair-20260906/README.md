# Live recovery and actuator-contract repair: local evidence

This is a **candidate checkpoint, not release approval**. The earlier seven-round
Mac/Lenovo campaign remains failed and unaltered. Personal main and collaborator
repositories have not been updated by these diagnostics.

## Verified cause

`AMRBrain._safety_base` used to return zero linear velocity, nonzero angular
velocity and `safety_stop=True` for protective turning. Headless `World.step`
recorded that flag but still executed the turn. The live `SafeCommandGate`
correctly forced BOTH velocities to zero. Thus the same recovery could rotate in
headless tests and remain stationary indefinitely behind the live adapter.

The captured-cluster regression was changed to execute commands through the actual
vendor gate before physics. Before the contract repair it produced **zero net
movement**, reproducing the deployment discrepancy. Afterward all declared
zero/noisy-localization variants clear the cluster through the unchanged gate.

The repair makes STOP mean brake both axes in both paths. A near-stationary
protective turn is a separate normal command, admitted only after checking the
simulated circular footprint, mapped braking envelope, unchanged standstill
margin and nearby objects' predicted motion. Momentum, a closing obstacle,
contact-envelope readings and an explicit stop still reject that turn. Sensor
timeout, late computation, invalid commands and actuator watchdogs retain hard
stops. This is NOT a reset of a physical emergency stop and is not certification
for an arbitrary vendor chassis or rotating cargo footprint.

Related recovery changes keep a bounded metric escape committed while steering,
retain destination occupancy/corridor/priority admission, and try additional
staging points in the current or already admitted adjacent cell only when the
original duplicate-cell options cannot be executed. No body radius, speed limit,
contact definition, map, task catalog or evidence cutoff was reduced.

## All local attempts are retained

- `before.json`: 45-second diagnostic with added read-only coordination telemetry
  and the original AMR algorithm; 2/10 completed. Not an acceptance-window result.
- `committed-escape-overlap10.json`: first geometric/commitment repair, original
  180-second overlap window; 3/10 completed, no contacts, timing failed.
- `committed-pibt-overlap10.json`: additional inherited-side-step commitment,
  original 180-second window; 2/10 completed, no contacts. Not a release pass.
- `hard-stop-contract-overlap10.json`: full 180-second window; **PASS, 10/10**,
  no contacts, no controller compute/cycle/scheduler misses or skipped slots.
  All jobs were complete by the 95.12-second stored snapshot. Largest complete
  controller cycle was **12.0143 ms**, below the unchanged 20 ms budget.
- `hard-stop-contract-humans10.json`: full 240-second window; **PASS, 10/10**,
  no contacts, no controller compute/cycle/scheduler misses or skipped slots.
  Three humans were present, with 132 randomized behavior events. All jobs were
  complete by the 75.12-second stored snapshot. Largest complete controller cycle
  was **12.0390 ms**. Scenario event coverage passed.

The last two reports share fingerprint
`7abea79c75dd78fc401d4cc9760d11aa90c3125f65fd5fe64717903d564da9ef`;
each report verifies unchanged runtime source through the run. They were serial
single-host Mac software-in-the-loop measurements using ten independent edge
processes and authenticated loopback UDP. A `caffeinate -i -s` assertion applied
only for each measurement's process lifetime on AC power. No global power,
scheduler-priority, firewall or timing-threshold settings were changed. No heavy
test suite ran concurrently. These runs used a bounded diagnostic snapshot callback,
not the two-host dashboard publisher, so dashboard-load validation remains pending.

Snapshot times are upper bounds on completion time, not exact makespan claims.
Live runs are scheduling-dependent; this is not a deterministic speedup comparison
between the unsuccessful and successful attempts. Complete JSON reports and earlier
failures are preserved, not replaced by successful-only statistics.

## Mixed-warehouse regression discovered during verification

The first full suite after the command-contract change finished with 1,067 passes
and two failures: the unchanged Grand Challenge seed 4, ten AMRs, twenty tasks and
800-second window, under both V6 and V7. It had no contacts but only 16 completions.
The command repair therefore was not promoted on the strength of the two live passes.

Raw headless state diagnostics are retained here:

- `grand4-contract-diagnostic.json`: the initial 16/20 stopped state.
- `grand4-translation-signal.json`: separating the internal translation-protection
  signal from the hard-stop command alone still completed only 16/20.
- `grand4-clearance-probes.json`: offline post-run geometry probes showed that the
  front chassis had no valid separating route, while a follower had a clear bay.
- `grand4-queue-recovery.json` and `grand4-queue-timeline.json`: allowing that
  follower to retreat only after its leader became idle improved completion to
  19/20 but still missed the original window. These attempts are failures, not
  alternative acceptance cutoffs.

The refined repair handles a sustained, scanner-confirmed stationary fan-in queue
whether the leader is idle or carrying a task. It moves one eligible loaded
follower into one geometrically validated adjacent cell, retaining the original
task and acquiring normal block/cell admission. It rejects brief queues, stale
peers, moving/retreating/charging leaders, missing scanner corroboration and
occupied escape cells. Both original full-scenario seed-4 regression tests then
passed, with all twenty tasks and zero contacts. Full-suite and live reruns of the
final source are separate gates; earlier local live passes are not relabelled.

The internal translation-protection signal now persists through a protective
turn, so recovery is not erased by safe angular motion while translation remains
blocked. An ordinary navigation turn clears it. Explicit hard-stop commands are
honoured before creep classification, including nonzero velocity inputs.

## Next release gates

The refined candidate, controller fingerprint
`e088d58268e30801b5c864620acc3d3d9b373fe1090aff8bfd6c1d8fe9d99b86`,
passed **1,083 Python tests in 162.66 host seconds** (`full-suite.xml`), all five
JavaScript live-twin tests, Ruff on changed Python files and `git diff --check`.
This full-suite result includes both previously failing seed-4 scenarios. It is
not a live deadline measurement or a replacement for the registered performance campaign.

`grand4-final-checkpoint.json` independently captures the refined V7 state and
same-source result: **20/20 tasks in 455.78 simulated seconds**, zero robot/robot,
robot/human and robot/rack contacts, 54,875 messages and 11,064,102 serialized bytes.
Its source fingerprint remained unchanged during the run. The earlier 16/20
timeout is not a completed-baseline denominator for an exact speedup claim.

Repeat the frozen-source headless acceptance campaign, since shared
controller/physics command semantics changed. Also repeat the complete seven-round
Mac/Lenovo campaign with dashboard publication and retain
every result. Do not reuse the previous ZIP or label these single-host reports as
two-host proof. No physical Raspberry Pi, physical AMR safety or universal
warehouse compatibility has been demonstrated here.
