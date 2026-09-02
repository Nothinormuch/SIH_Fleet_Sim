# Fault and recovery campaign

This campaign is the release gate for degraded communications and process failure. It
uses the same decentralized `BIOS_PIBT.5` auction stack as the acceptance benchmark.
It is simulation evidence, not a physical safety certification.

## Final result

Generated on 2026-08-25 with 30 deterministic seeds per condition:

| Condition | Completed | Median makespan | p95 makespan | Contacts |
| --- | ---: | ---: | ---: | ---: |
| 0% packet loss | 30/30 | 384.030 s | 411.668 s | 0 |
| 5% packet loss | 30/30 | 385.810 s | 416.035 s | 0 |
| 10% packet loss | 30/30 | 392.120 s | 412.687 s | 0 |
| 20% packet loss | 30/30 | 388.120 s | 412.941 s | 0 |
| Network partition then heal | 30/30 | 19.380 s | 19.380 s | 0 |
| Auction winner crashes | 30/30 | 47.280 s | 47.280 s | 0 |

All 180 runs completed. Robot-failure runs recorded 60 task-reassignment
observations. Robot/robot, robot/human and robot/rack contact totals were all zero.

The campaign caught a real convergence defect before release: under loss, peers could
retain different unclaimed corridor-wave memberships after completion gossip arrived in
different orders. Active claims now retain their admitted phase, while unclaimed wave
membership is re-derived deterministically from the canonical smallest unfinished task.
A seed-specific regression preserves that behavior.

The BIOS 5 campaign also caught an empty-approach/loaded-wave conflict at 20% loss.
Cross-corridor work is no longer awarded before approach: after lease convergence and
fleet quiescence, one elected idle bidder repositions to the pickup side and participates
in the next auction. This restored the failing seed from 2/12 to 12/12 tasks.

## Reproduce

```bash
python fault_campaign.py --seeds 30 --jobs 8
```

Machine-readable evidence is in
`artifacts/benchmarks/fault-campaign.json`. Passing this campaign demonstrates recovery
under the tested model; it does not demonstrate unbounded progress during a permanent
partition, physical actuator safety, or Raspberry Pi performance.
