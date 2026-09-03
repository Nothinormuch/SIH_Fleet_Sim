# 12. BENCHMARK AND EVIDENCE

> The measured answer to the SIH success criteria, the pinned experiment that produced it,
> and the exact boundary between a conservative lower bound and an exact speedup.

**Audience:** judges and evaluators checking headline claims; teammates who must quote them
correctly under questioning.
**Reads best after:** [11. Scenarios](11-SCENARIOS.md)

## Requirements evidenced

| # | Requirement | Evidence |
| --- | --- | --- |
| 19 | **Zero inter-robot collisions** | 0 observed contacts of every kind across 268.39 robot-hours and 180 current-campaign policy runs |
| 20 | **At least 20% task-time reduction vs stop-and-wait** | Minimum per-seed bounds 65.22% / 50.63% / 33.46% at 4 / 6 / 8 robots |
| 1 | At least 3 AMRs | Evaluated at 4, 6 and 8 AMRs |
| 11 | Chokepoint handling | `sih_acceptance_overlap`: two bays joined by one 13-cell single-file corridor |

---

## 1. Current acceptance verdict

The released default stack, **`BIOS_PIBT.6` with Auction V2**
(`allocation_policy=auction_bundle`), passes the strict deterministic SIH acceptance gate:

- 90/90 candidate runs complete;
- 1,620/1,620 candidate tasks complete;
- 0 robot/robot, robot/human or robot/rack contacts are observed;
- 0 deadlocks are detected;
- the minimum conservative reduction bound clears 20% for every fleet; and
- semantic output is stable under `PYTHONHASHSEED` 0, 1 and 42.

This is finite deterministic simulation evidence, not universal completion, a proof of a
zero contact rate, Raspberry Pi timing, or physical safety certification.

## 2. Pinned experiment

| Parameter | Value |
| --- | --- |
| Scenario | `sih_acceptance_overlap` |
| Map | Two open bays joined by one **13-cell single-file chokepoint** |
| Work | Three tasks per AMR; alternating directions force overlapping routes |
| Fleets | 4, 6 and 8 AMRs |
| Seeds | 0-29 for each fleet: 30 paired runs per fleet, 180 policy runs total |
| Cutoff | 1,200 simulated seconds |
| Baseline route policy | `stop_and_wait` |
| Candidate route policy | `BIOS_PIBT.6` |
| Allocation policy, both sides | `auction_bundle` (Auction V2) |
| Required reduction | 20% |
| Bootstrap resamples | 5,000, deterministic |

Every paired run carries a SHA-256 `workload_id` over the map, starts, task catalogue,
allocation policy, network and failure settings, seed and controller configuration. Route
policy is the only excluded input because it is the independent variable inside this gate.
The comparator refuses missing or duplicate seeds, empty or mismatched fingerprints,
candidate timeouts, candidate contacts and any paired-invariant difference.

## 3. Acceptance result

Generated **2026-09-03** from clean collaboration commit `c26516a6c35f204a704d7e2bd0089cd3b067e594`.

| AMRs | Candidate completion | Baseline completion | Candidate median | Candidate p95 | Candidate maximum | **Minimum bound** | Median bound | Bootstrap 95% for median bound |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 4 | 30/30 | 0/30 | 383.89 s | 406.01 s | 417.32 s | **65.22%** | 68.01% | 67.47-68.88% |
| 6 | 30/30 | 0/30 | 552.63 s | 581.79 s | 592.38 s | **50.63%** | 53.95% | 53.58-54.85% |
| 8 | 30/30 | 0/30 | 718.88 s | 785.90 s | 798.44 s | **33.46%** | 40.09% | 39.56-41.48% |

**Verdict: PASS at every fleet size and overall.**

The acceptance margin narrows as fleet density rises. That shape is expected: more AMRs
share the same single-file corridor, so more of the coordination advantage is spent on
contention. Reporting each fleet separately prevents a favorable average from hiding the
hardest 8-AMR case.

## 4. Why the percentages are lower bounds

Every stop-and-wait run reaches the 1,200 s cutoff without completing: 0/30 at all three
fleet sizes. A timeout is not a makespan and is never substituted for one.

For candidate makespan `C`, unknown baseline makespan `B`, and cutoff `D`:

```text
B > D
true reduction = 1 - C/B
therefore      1 - C/B > 1 - C/D
```

The artifact reports `100 x (1 - C/D)`, a conservative right-censored lower bound. It
cannot report an exact baseline median, p95 or speedup because the baseline never finishes.
The lower bound is nevertheless sufficient for the SIH criterion: even its smallest value
is above 20%.

## 5. Safety result

| AMRs | Candidate robot-hours | Candidate contacts r/r, r/h, r/rack | Candidate 95% upper bound per 1,000 robot-hours | Candidate worst separation | Baseline robot-hours | Baseline 95% upper bound | Baseline worst separation |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 4 | 12.7380 | 0, 0, 0 | 233.037 | 0.853 m | 39.9999 | 74.211 | 0.876 m |
| 6 | 27.4913 | 0, 0, 0 | 107.977 | 0.871 m | 60.0000 | 49.474 | 0.890 m |
| 8 | 48.1633 | 0, 0, 0 | 61.633 | 0.876 m | 80.0001 | 37.105 | 0.913 m |

Totals: 0 observed contacts across 88.3926 candidate robot-hours and 180.0000 baseline
robot-hours. A zero count bounds an unobserved rate; it does not prove a zero rate.

The candidate's rate bound is numerically weaker than the baseline's because the candidate
finishes much earlier and therefore accumulates less exposure. A stronger statistical bound
requires more exposure, not stronger wording.

This scenario contains no pedestrians. Its robot/human contact field is therefore not
human-safety evidence. Use the mixed-traffic campaigns in [07. Safety](07-SAFETY.md) for
human-flow evidence.

## 6. Same-commit BIOS 5 control

The former accepted stack, `BIOS_PIBT.5` with plain `auction`, was re-run from the same
clean commit, fleets, seeds, map, starts and task catalogue. Each architecture uses its own
allocator on both sides of its separate stop-and-wait gate, so this is a complete-stack
comparison, not a route-only attribution experiment.

| AMRs | BIOS 5 minimum bound | BIOS 6/V2 minimum bound | BIOS 5 median | BIOS 6/V2 median | BIOS 5 p95 | BIOS 6/V2 p95 | Message change | Byte change |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 4 | 64.07% | **65.22%** | 383.89 s | 383.89 s | 410.19 s | **406.01 s** | **-28.18%** | **-29.23%** |
| 6 | 50.63% | 50.63% | **550.25 s** | 552.63 s | 583.11 s | **581.79 s** | **-19.05%** | **-20.01%** |
| 8 | 33.38% | **33.46%** | 718.88 s | 718.88 s | 785.90 s | 785.90 s | **-14.72%** | **-15.50%** |

Across all 90 candidate runs:

- messages fall from 4,267,412 to 3,499,262: **18.0% fewer**;
- bytes fall from 815,307,628 to 661,250,772: **18.9% fewer**;
- nonproductive wait changes from 521,869 to 520,676 ticks: **0.23% fewer**;
- both stacks complete 1,620/1,620 tasks with zero observed contacts and zero detected
  deadlocks.

The 6-AMR BIOS 6/V2 median is 2.38 s slower while its p95 is 1.32 s better. The defensible
conclusion is therefore **equivalent completion time with slightly stronger worst-case
acceptance bounds and materially lower coordination traffic**, not a dramatic speedup over
BIOS 5.

## 7. Reproduction and provenance

Current default command:

```bash
python -m pytest tests -q
python benchmark.py --seeds 30 --jobs 10
```

The benchmark defaults now resolve to `BIOS_PIBT.6` and `auction_bundle`. Explicit commands
that preserve dated evidence are:

```bash
python benchmark.py --candidate BIOS_PIBT.6 --allocation-policy auction_bundle \
  --seeds 30 --jobs 10 \
  --json artifacts/benchmarks/sih-acceptance-bios6-v2-2026-09-03.json \
  --csv artifacts/benchmarks/sih-acceptance-bios6-v2-2026-09-03.csv

python benchmark.py --candidate BIOS_PIBT.5 --allocation-policy auction \
  --seeds 30 --jobs 10 \
  --json artifacts/benchmarks/sih-acceptance-bios5-control-2026-09-03.json \
  --csv artifacts/benchmarks/sih-acceptance-bios5-control-2026-09-03.csv
```

Artifacts:

- `artifacts/benchmarks/sih-acceptance.json` and `.csv`: current BIOS 6/V2 evidence;
- `artifacts/benchmarks/sih-acceptance-bios6-v2-2026-09-03.json` and `.csv`: dated copy;
- `artifacts/benchmarks/sih-acceptance-bios5-control-2026-09-03.json` and `.csv`:
  same-commit BIOS 5 control;
- `artifacts/benchmarks/sih-acceptance-2026-09-02.json` and `.csv`: prior BIOS 5 evidence.

The current artifacts record `source_tree_dirty: false`. The runs used 10 parallel workers;
their wall-clock and planner-CPU measurements must not be presented as Raspberry Pi or
isolated edge-compute timing.

## 8. Judge-safe wording

> BIOS 6 with Auction V2 passed 90 out of 90 deterministic SIH acceptance runs and
> completed 1,620 out of 1,620 tasks with zero observed contacts. Against stop-and-wait,
> the minimum conservative completion-time reduction bounds were 65.22%, 50.63% and
> 33.46% for 4, 6 and 8 AMRs. Compared with BIOS 5 on the same clean source commit, the
> upgraded stack preserved completion and safety while sending 18.0% fewer coordination
> messages overall.

Do not translate that statement into universal completion, guaranteed physical safety, an
exact stop-and-wait speedup, or a claim that Auction V2 alone caused the communication
reduction.

---

**Related:** [07. Safety](07-SAFETY.md) - [11. Scenarios](11-SCENARIOS.md) -
[13. Testing](13-TESTING.md) - [15. Limitations](15-LIMITATIONS.md) -
[16. Demo Runbook](16-DEMO-RUNBOOK.md)
