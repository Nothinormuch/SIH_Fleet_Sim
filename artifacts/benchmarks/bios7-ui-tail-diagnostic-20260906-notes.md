# Frozen BIOS 7 showcase tail diagnostics — not acceptance passes

Source: immutable candidate `83e22ca` at `/private/tmp/bios7-release-identity.m1nDny`.
Policy/allocation: `BIOS_PIBT.7` / `auction_bundle`; 10 AMRs, seed 0.

The original declared windows and failed verdicts are preserved. A passive external
observer retained existing controller/world references and supplied sensor/actuator
values; it performed no extra sense, RNG, controller, or physics calls. It retained
the original Scenario duration, task announcements, and workload fingerprint, and
extended only the runner's iteration bound for the separately labelled diagnostic
continuation. At each original cutoff, every saved non-CPU summary field matched
the original artifact exactly. Source hashes matched before and after each run.
The shared-host wall/CPU measurements are not live control-loop timing evidence.

## Open floor

- Original result: **18/20 at 180 s — FAIL remains unchanged**.
- At the cutoff, AMR03 and AMR07 were executing their remaining deliveries, with
  measured body speeds of approximately 0.317 and 0.304 m/s, respectively.
- Diagnostic continuation: **20/20, makespan 202.08 s** (simulation ends 202.10 s).
- Zero robot–robot, robot–human, and robot–rack contacts throughout continuation.
- Saved same-source BIOS 6 control also completed 18/20 at the original 180 s.
- Conclusion: late completion in this exact case, not a permanent tail stall.

## Chokepoint

- Original result: **12/20 at 320 s — FAIL remains unchanged**.
- Subsequent completed counts: 13 at 330 s, 14 at 360 s, 15 at 390 s, 16 at 420 s,
  17 at 480 s, 19 at 510 s, then 20/20.
- Diagnostic continuation: **20/20, makespan 528.34 s** (simulation ends 528.36 s).
- Zero robot–robot, robot–human, and robot–rack contacts throughout continuation.
- Saved same-source BIOS 6 control completed 10/20 at the original 320 s.
- Conclusion: sustained throughput through a single-file bottleneck; no permanent
  stall in this exact case. This does not establish a universal liveness guarantee.

## UI and interpretation issues

The open/chokepoint cards are defined for four robots with 180/320-second windows.
Changing the fleet to ten retains those windows while each builder increases work
from eight to twenty tasks. This is a confirmed workload/time-window presentation
mismatch, not permission to change historical acceptance windows. A future UI
should distinguish fixed-window performance tests from explicitly declared
run-to-completion demonstrations, display the generated task count, and warn when
the fleet is changed without a corresponding capacity assessment.

The rendered label `Energy-risk bids blocked` is too narrow: the underlying
`energy_bids_suppressed` counter includes both energy/deadline/path eligibility
failure and the nearest-candidate shortlist filter. Suggested truthful label:
**Eligibility / shortlist bids skipped**. It must not be interpreted as a count
of actual battery-risk assignments avoided.

These two inherited UI-window failures must not be conflated with the separately
reported held-out seed 2012 BIOS 7 liveness regression. No controller fix, default
switch, merge, or publication was performed as part of these diagnostics.

Raw observer records:

- `bios7-ui-open0-tail-diagnostic-20260906.json`
- `bios7-ui-choke0-tail-diagnostic-20260906.json`

External reproducible observer:
`/tmp/bios7-showcase-tail.m5fCO6/observe.py`.
