# Five-preset demo comparison — September 6, 2026

The user paused the broader seed campaign and requested a demo-only merge if
BIOS7 performs better than BIOS6. This comparison uses all five unchanged
dashboard presets, their existing fleet counts, seeds, workloads and time limits.
All three policies use the same frozen be01a30 source and Auction V2 allocator.
It isolates routing-policy differences, not an untouched historical V6 release.
Execution was serial with alternating policy order. Raw scenario/config/source
fingerprints and results are in `bios7-demo-be01a30.json`.

Both BIOS6 and BIOS7 completed 52/52 tasks across these presets, with zero contacts.
Open Floor (91.32 s), Human Interaction (297.06 s) and Dead-Zone Mesh (210.86 s)
were equal. BIOS7 improved Chokepoint from 254.00 to 232.42 s (8.50% less time),
but regressed Grand Challenge from 368.06 to 418.68 s (13.75% more time).
The sum of independent preset completion times increased from 1221.30 to
1250.34 s; this is not a single warehouse makespan or a universal score.

Stop-and-wait completed 16/52 tasks within the preset windows: 4/8 open, 0/8
chokepoint, 6/10 human, 4/6 dead zone and 2/20 mixed. All had zero contacts.
Because these baseline runs timed out, their cutoffs are not completed-task
makespans and must not be presented as exact speedup percentages.

## Decision

The requested condition for a clear demo-wide upgrade is not met. No merge or
push is approved by this report. The candidate remains on personal branch seven.
The broader campaign is stopped, not passed: seed 2017 completed only 19/30
BIOS7 tasks versus 30/30 for current-source BIOS6. Its incomplete raw report is
preserved in `bios7-release-be01a30.WsynIg/holdout.json`; stress and repeat stages
were not run. Earlier five-minute progress updates described partial results only.
The latest LAN campaign separately completed 47/47 tasks but failed strict timing
in three rounds. None of these limitations is waived by the demo comparison.
