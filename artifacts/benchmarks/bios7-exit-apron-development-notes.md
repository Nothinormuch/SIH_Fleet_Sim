# Idle corridor drain and exit-junction clearance candidate

This is development evidence, not release approval. BIOS 6 remains the default.
No acceptance scenario, seed, task catalog, physical margin or simulated cutoff
was changed. The failed `83e22ca` campaigns and UI windows remain recorded.

## Changes

An idle V6/V7 robot already inside a controlled corridor now honors fresh,
cycle-safe explicit wait-for dependencies even after moving beyond the requester's
finite advertised intent. It retains the physical block token and uses the existing
route, body and destination checks for each clearance step. Seventeen focused
tests include actual three-body completion and negative stale/ownership cases.

BIOS 7 additionally waits for the full body to clear the traversable junction
immediately outside the corridor before removing that task's auction admission
pressure. The footprint is the corridor plus its traversable neighboring cells,
derived from immutable topology and cached per block. Existing radius and clearance
margin remain 0.35 m and 0.10 m. Admission clearance still neither completes the
task nor releases spatial authority. Identity/session checks and use-time re-entry
revocation remain active. Tests cover junction occupancy, partial-body clearance,
and re-entry before the next observation sample.

The dashboard's aggregate `energy_bids_suppressed` counter includes ranked
candidate filtering, so its label is now "Candidate checks filtered", displayed
neutrally with its actual meaning. It is not a unique rejected-bid or battery-fault
count. An incomplete run remains explicitly incomplete.

## Exact seed 2012 development comparison

AMR source SHA256:
`2bbe49b506975a749a0c5eba488a37a0b63c127e4a69e0ad0f984d1d5a0ccda1`.
Raw paired report: `bios7-exit-apron-development-seed2012.json`.
Report SHA256: `d36c0b05a2b03cc19f3d030a1baa9479b4d31f92c37e1815736bb7d252318714`.

All four configurations completed the unchanged 30-task workload without any
robot, human or rack contact. Original and current-source V6 both took 871.14 s,
sending 101,686 messages and 19,191,300 simulated protocol bytes. BIOS 7 took
820.94 s, sending 96,293 messages and 18,288,162 bytes. The exact time reduction
against either V6 control is 5.76%, with 5,393 fewer messages and 903,138 fewer
bytes. The no-release V7 ablation took 871.14 s and sent 101,686 messages /
19,318,676 bytes, retaining the same execution-identity overhead.

The earlier idle-drain-only result (875.88 s with more messages/bytes than V6)
remains in `bios7-idle-drain-development-seed2012.json`. Completing that workload
alone did not pass the strict nonregression gate.

This is an observed, previously failing development case, not untouched holdout
evidence. No stop-and-wait run was part of this focused four-way diagnostic.

## Verification and next gates

- Full Python suite: 973 passed in 185.88 host seconds.
- Five live-twin JavaScript tests passed; main.js syntax and focused Ruff checks pass.
- Full suite and development simulation shared the host with an older headless
  capacity worker. These are not unloaded control-loop or Raspberry Pi timings.
- The registered release stages must run on a clean frozen candidate. Known
  seeds 2000–2012 must be retained, with 2013–2029 disclosed as previously unseen.
- Latest-source live ten-controller Mac/Windows testing with dashboard load is
  still required. Neither these tests nor old three-controller evidence replaces it.

No source covered by the development report was edited during its paired workers.
