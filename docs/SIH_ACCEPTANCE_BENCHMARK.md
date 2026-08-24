# SIH acceptance benchmark

This is the release gate for the problem statement's measurable success criterion:
at least a 20% reduction in total task-completion time relative to traditional
stop-and-wait, while observing no inter-robot contact in the evaluated runs.

The gate compares `BIOS_PIBT.3` against `stop_and_wait` under the same decentralized
`auction` allocation policy. It is intentionally stricter than comparing averages:
every candidate run must complete, every paired workload must match, and the minimum
per-seed improvement bound must pass.

## Pinned experiment

- Scenario: `sih_acceptance_overlap`
- Map: two open bays joined by one 13-cell single-file chokepoint
- Work: three tasks per robot; alternating task directions force overlapping routes
- Fleets: 4, 6 and 8 robots
- Seeds: 0 through 29 for each fleet
- Cutoff: 1200 simulated seconds
- Baseline route policy: `stop_and_wait`
- Candidate route policy: `BIOS_PIBT.3`
- Allocation policy for both: `auction`
- Required reduction: 20%

Each run carries a SHA-256 `workload_id` over the map, starts, task catalog, allocation
policy, network/failure settings, seed and full controller configuration. Route policy
is the only excluded input because it is the independent variable. The comparator
refuses missing/duplicate seeds, empty fingerprints, mismatched fingerprints, candidate
timeouts, or differences in any paired invariant.

## Why the result is a lower bound

All stop-and-wait runs reached the fixed 1200 s cutoff without completing. A timeout is
not a makespan and is never substituted as one.

For a candidate makespan `C`, unknown baseline makespan `B`, and cutoff `D`:

```text
B > D
true reduction = 1 - C/B
therefore true reduction > 1 - C/D
```

The artifact reports `100 * (1 - C/D)` as a conservative right-censored lower bound.
Because the baseline makespans are unknown, this benchmark does not report an exact
baseline mean, median, p95, or exact speedup percentage.

## Acceptance rules

A fleet passes only when all of these are true:

1. all 30 candidate runs complete all announced tasks;
2. candidate robot/robot, robot/human and robot/rack contacts total zero;
3. every seed is paired by an identical nonempty workload fingerprint;
4. the minimum per-seed exact reduction or censored lower bound is at least 20%.

The overall verdict passes only when every fleet passes. The CLI exits `0` on pass and
`2` on a completed failing gate.

## Measured result

Generated on 2026-08-24 with 5000 deterministic bootstrap resamples per fleet:

| Robots | Candidate completion | Baseline completion | Candidate median | Candidate p95 | Minimum bound | Median bound | Median-bound bootstrap 95% interval |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 4 | 30/30 | 0/30 | 389.38 s | 407.88 s | 63.03% | 67.55% | 67.19–68.31% |
| 6 | 30/30 | 0/30 | 599.23 s | 623.43 s | 45.50% | 50.06% | 49.68–50.85% |
| 8 | 30/30 | 0/30 | 758.63 s | 787.62 s | 32.48% | 36.78% | 35.73–37.59% |

All 90 candidate runs completed all 1,620 tasks. The largest candidate makespan was
810.20 s, below the 960 s value corresponding to a 20% bound at the 1200 s cutoff.
There were zero observed robot/robot, robot/human and robot/rack contacts across
93.3722 candidate robot-hours.

Zero observed contacts do not prove an impossible collision rate of zero. The per-fleet
one-sided 95% upper bounds reported in the JSON are 228.636, 99.319 and 58.780
robot/robot contacts per 1000 robot-hours for 4, 6 and 8 robots respectively. More
exposure—not stronger wording—is what lowers those bounds.

## Reproduce

```bash
python -m pytest -q
python benchmark.py --seeds 30 --jobs 8
```

The command writes:

- `artifacts/benchmarks/sih-acceptance.json`: versioned metadata, strict comparison,
  safety summaries, every paired bound, and every raw run
- `artifacts/benchmarks/sih-acceptance.csv`: 180 raw policy-run rows for independent
  analysis

The checked-in JSON records `git_commit` as the repository `HEAD` and
`source_tree_dirty: true` because this evidence was generated from the implementation
changes in the working tree on branch `codex/sih-acceptance-benchmark`. Commit or tag
the final tree and rerun the command when immutable release provenance is required.

This is simulation evidence under the pinned assumptions, not a hardware safety
certification and not proof of progress during permanent partitions or physical robot
failure.
