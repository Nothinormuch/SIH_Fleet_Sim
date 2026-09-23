# Repaired seven-round LAN campaign: completion recovered, timing still fails

Measured September 6, 2026 on the Mac and Lenovo Ethernet link. All seven
declared rounds ran once on committed candidate
`13f8b88c8319406b1c449aa52c15bdde8650ddf7`, controller fingerprint
`e088d58268e30801b5c864620acc3d3d9b373fe1090aff8bfd6c1d8fe9d99b86`.
Every referee report confirms verified source/workload at startup, unchanged
source at the end, and actual peer communication across distinct hosts.
The original reports are retained unmodified. Windows controller reports are
embedded in `referee.json`; session keys are not included.

## Measured outcome

- **47/47 declared tasks completed**, versus 32/47 in the previous candidate's
  seven-round campaign. This is a completion comparison, not an exact speedup.
- **Zero robot/robot, robot/human and robot/rack contacts in all seven rounds.**
- **3/7 strict passes**, not a release pass. Three-AMR sensor-loss and overlap,
  and ten-AMR blocked-aisle rounds passed all their gates.
- Ten-AMR overlap completed 10/10 but had two late Mac controller starts.
- Ten-AMR chokepoint completed 10/10 but had five Windows full-cycle overruns
  (maximum 43.104 ms), one late Mac start and two referee timing misses.
- Ten-AMR human crossings completed 10/10 but had five Windows full-cycle
  overruns (maximum 40.389 ms) and one referee timing miss.
- The controller-failure round recovered its one declared job: AMR01 stopped
  while owning T000 at 2.00 s, its process exit was confirmed, and AMR04 finished
  T000 at 47.72 s. However, four Mac full cycles overran (maximum 44.049 ms),
  eleven controller starts were late, and the referee missed nine ticks
  (maximum lag 124.260 ms). This fixture has one recovery job, not ten jobs.
- Sensor-loss stop response in the three-AMR test was **217.822 ms**, with
  observed motion before the outage and recovery afterward.
- **No stale motion was accepted.** The failure round rejected 47 unexpected
  stale motion frames; these are an additional measured timing/transport defect,
  not successful commands and not contacts. Other rounds had no unexpected
  stale-motion rejections.

All controllers passed the computation-duration metric, but this does not erase
the full-cycle, scheduled-start, referee or freshness failures. The control
period remains 20 ms. Maximum individual phases are not simultaneous samples;
subtracting their maxima cannot locate a specific overrun.

## Follow-up, not yet proven

Instrument sensor intake, actuator transmission, synchronous durable journal
writes and late wake-ups separately, retaining the full-cycle metric. Test a
scoped best-effort macOS thread QoS hint with restoration on exit. Slow storage
and OS scheduling are hypotheses requiring new measurements, not confirmed
causes. Do not disable journal durability, remove humans, expand these windows,
or relax the 20 ms/freshness gates to obtain a pass.

The Windows console initially displayed only the start of round six, but it
subsequently advanced and all seven host lifecycles finished. No cleanup hang
was established, so no speculative cleanup fix is justified by that output.

New source must receive its own tests and source-pinned measurements. The older
headless acceptance campaign is not evidence for the repaired controller bytes.
Latest-source registered headless acceptance and two-host timing gates remain
required before personal-main promotion or collaborator branch publication.
This is networked software-in-the-loop evidence, not Raspberry Pi performance,
physical-AMR safety certification, hard-real-time proof or universal compatibility.
