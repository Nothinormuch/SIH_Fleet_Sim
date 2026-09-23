# Durable-worker candidate: seven-round LAN audit

Measured September 6, 2026, using frozen candidate
`be01a30399aec332f73f6e776ab086cb791d0e9d`. Controller source SHA-256:
`2908d09776e4474ec18b7d126b05cfeb55607eb2c7cc1d789595f94be7927c3a`.
The raw referee reports record source verification and end-of-run integrity.

## Outcome

All seven rounds executed across the Mac and Windows laptop over Ethernet.
Together they completed **47/47 declared tasks**, with **zero robot–robot,
robot–human and robot–rack contacts**. Every round reported real multi-host
participation. No stale motion was accepted, and no unexpected stale motion
rejections occurred. This is finite closed-loop software evidence, not a physical
AMR or Raspberry Pi measurement.

**Only four of seven rounds passed every original gate.** The aggregate raw
`campaign.json` correctly retains `success: false`:

- Sensor-loss acceptance, 3 AMRs: 3/3 tasks, strict pass.
- Overlapping paths, 3 AMRs: 3/3 tasks, strict pass.
- Overlapping paths, 10 AMRs: 10/10 tasks, strict pass.
- Chokepoint, 10 AMRs: 10/10 tasks, timing failure.
- Human crossing, 10 AMRs: 10/10 tasks, timing failure.
- Blocked aisle, 10 AMRs: 10/10 tasks, strict pass.
- Robot-failure reassignment, 10 AMRs: 1/1 recovered task, timing failure.

## What changed and what remains unresolved

Durable journal writes now use a bounded background worker. While a write awaits
acknowledgement, the controller holds motion at zero and withholds its ordered
outbound messages. It resumes from fresh sensor data only after acknowledgement;
it does not replay an old movement command. Checksums, atomic replacement and
durable synchronization remain enabled. Failure or acknowledgement timeout is
not treated as success. All journals drained without worker failures in this run.

The human-crossing round measured a background journal write of approximately
38.45 ms while its controller cycles remained within budget. That demonstrates
the intended separation of durable I/O from controller execution in this run;
the round still failed because of late wakes/referee timing. The chokepoint and
failure rounds also retained timing failures. This is not proof that arbitrary
disk stalls or desktop scheduling pauses can be tolerated indefinitely.

The operator was asked to save work and close unused applications while retaining
the demo, Codex and PowerShell. These live runs are not controlled unloaded-host
performance comparisons. Do not attribute every improvement solely to the code
change, or infer an operating-system root cause from simultaneous pauses alone.

## Publication boundary

The user approved a software-prototype release **only after the latest-source
SIH/headless release gates pass**, explicitly retaining failed live-timing results.
Those independent gates were still running when this audit was written. This
audit does not approve publication or turn any failed timing result into a pass.
Hard-real-time, physical safety certification, universal compatibility and
universal completion claims remain unsupported. Private session key files and
transfer archives are deliberately excluded from this evidence directory.
