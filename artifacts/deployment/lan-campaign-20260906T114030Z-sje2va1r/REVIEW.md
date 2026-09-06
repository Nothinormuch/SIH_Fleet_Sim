# Seven-round Mac/Lenovo LAN campaign: release gate failed

Observed on September 6, 2026. All seven declared sessions ran once; none was
omitted or automatically retried. The raw `campaign.json`, seven `referee.json`
files and seven Mac agent reports are preserved without changing their values.
Windows controller reports are embedded in each referee report.

Source: candidate public-entrypoint commit
`6545a238b63b9a9b11608a01109be708a9af3ce5`, controller fingerprint
`8578c87abdc2e09189a22c4f576690dbcf007742d3ace36ba4c86bc97cda900a`.
Both machines verified the same source/workload, and every report confirms that
source remained unchanged through its run. The observed hosts were an ARM MacBook
Air running Python 3.14.7 and a Windows Lenovo running Python 3.13.15. Neither
reported Raspberry Pi hardware. The Ethernet addresses are link-local addresses,
not public deployment endpoints. No session key is included in these artifacts.

## Results

- Sensor-loss, three AMRs: **PASS**, 3/3 jobs. Stop-command response was
  202.030 ms, with measured motion before the outage and recovery afterward.
- Overlapping paths, three AMRs: **PASS**, 3/3 jobs.
- Overlapping paths, ten AMRs: **FAIL**, 2/10 jobs. Sixteen referee deadline
  misses; maximum referee lag 220.646 ms. Two Mac controllers had an execution
  deadline miss, and scheduling misses were also observed. Eleven unexpected
  stale motion frames were rejected, not applied.
- Chokepoint, ten AMRs: **FAIL**, despite completing 10/10 jobs. Two referee
  deadline misses, controller scheduling misses, and one Windows full-cycle
  overrun (23.991 ms).
- Human crossings, ten AMRs: **FAIL**, 3/10 jobs. One referee deadline miss
  plus controller scheduling misses. No robot execution/full-cycle overrun.
- Blocked aisle, ten AMRs: **FAIL**, despite completing 10/10 jobs and observing
  the declared pallet obstacle. A Mac controller had one late scheduled start; this
  remains a timing failure even though compute-duration and referee gates passed.
- Controller failure, ten AMRs: **FAIL**, despite recovering the single declared
  job. AMR01's process exit was confirmed after its stop request; AMR02 completed
  the job at simulation time 47.68 s. One referee deadline miss and one controller
  scheduling miss prevent a full pass. This fixture contains only one recovery
  job, not a fully loaded fleet-failure campaign.

Total: **2/7 sessions passed; 32/47 jobs completed.** Every session measured zero
robot/robot, robot/human and robot/rack contacts. Every robot had authenticated
peer receipt from the other host. The referee never assigned a winner or relayed
robot peer messages. These are useful positive measurements, but not a release.

## Diagnosis boundary and next checks

The maximum Mac control-cycle duration was 231.241 ms while the same controller's
maximum measured thread CPU usage per tick was 5.286 ms. This supports investigating
host scheduling pauses separately from algorithm compute cost; it does not prove
which OS event caused the pause. Windows also had a full-cycle overrun, so timing
cannot be attributed exclusively to the Mac or dismissed as a network issue.

The ten-AMR overlap run completed its last job at 16.42 s and then made no further
completions before 180 s. Several controllers ended blocked with thousands of
safety-stop ticks. Human crossing ended with seven active tasks. These require a
captured-state liveness diagnosis; a longer time box, easier replacement layout,
or removal of humans would not repair the registered failures.

Reproduce the ten-node socket case locally with unchanged sources and bounded
telemetry, then add a regression for the exact failing state before changing
coordination. Retain the 20 ms timing and freshness gates. A fixed runtime must
be packaged with a new fingerprint and measured on both laptops again; these
reports must never be relabelled as results from that future source.

The subsequent unchanged-source single-host socket diagnostic completed 3/10
jobs in the same 180-second overlapping-path window, with zero contacts. Its
`local-overlap10-diagnostic.json` includes 18 telemetry snapshots at approximately
ten-second intervals, controller reports and the unchanged source fingerprint.
The local runner uses authenticated loopback multicast and independent processes,
not the Mac/Lenovo TCP bridge; this is diagnosis, not a second LAN acceptance run.
From about 100 s onward it made no further completions. Several AMRs retained the
same measured positions while their planned paths changed. Thus fixing the
Windows installation or firewall alone cannot resolve this failure. Captured
geometry points to continuous-motion recovery in a close-clearance cluster;
the exact control-state regression is still needed before claiming a repair.

No promotion, personal-main push or collaborator publication is authorized by
this failed campaign. The candidate remains on `seven`; collaborator main stays
untouched. This is networked software-in-the-loop evidence, not physical AMR
safety certification, Raspberry Pi performance, or universal AMR compatibility.
