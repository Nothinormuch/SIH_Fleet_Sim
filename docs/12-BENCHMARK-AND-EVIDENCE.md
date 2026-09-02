# 12. BENCHMARK AND EVIDENCE

> The measured answer to both success criteria, the experiment that produced it, and the
> precise sense in which the 20% figure is a lower bound rather than a speedup.

**Audience:** judges and evaluators checking the headline claims; teammates who must quote
them correctly under questioning.
**Reads best after:** [11. Scenarios](11-SCENARIOS.md)

## Requirements evidenced

| # | Requirement | Where | Evidence |
| --- | --- | --- | --- |
| 19 | **Zero inter-robot collisions** | [§6](#6-safety-result) | 0 contacts of every kind across 268.54 robot-hours, 180 runs |
| 20 | **≥20% task-time reduction vs stop-and-wait** | [§5](#5-completion-time-result) | Minimum per-seed bounds 64.07% / 50.63% / 33.38% at 4 / 6 / 8 robots |
| 1 | At least 3 AMRs | [§2](#2-the-pinned-experiment) | Evaluated at 4, 6 and 8 |
| 11 | Chokepoint handling | [§2](#2-the-pinned-experiment) | `sih_acceptance_overlap` — two bays joined by one 13-cell single-file corridor |

---

## 1. What this gate is

`benchmark.py` is a **strict paired release gate**, not a demo. It exits `0` on pass and `2`
on a completed failing gate, so it is usable in CI. It is deliberately harder to pass than a
comparison of averages: every candidate run must complete, every paired workload must match
by fingerprint, and the **minimum** per-seed bound must clear the threshold — not the mean,
not the median.

The design follows from the critique in
[00. Problem Statement §4.9](00-PROBLEM-STATEMENT.md#49-20-versus-stop-and-wait-needs-a-pinned-scenario):
an unpinned speedup number is meaningless because the same pair of policies can differ by 5%
or 300% with topology and density. So everything except the independent variable is pinned
and hashed.

## 2. The pinned experiment

| Parameter | Value |
| --- | --- |
| Scenario | `sih_acceptance_overlap` |
| Map | Two open bays joined by one **13-cell single-file chokepoint** |
| Work | Three tasks per robot; alternating task directions force overlapping routes |
| Fleets | 4, 6 and 8 robots |
| Seeds | 0–29 for each fleet (30 paired runs per fleet, 180 runs total) |
| Cutoff | 1200 simulated seconds |
| Baseline route policy | `stop_and_wait` |
| Candidate route policy | `BIOS_PIBT.5` |
| Allocation policy (both) | `auction` |
| Required reduction | 20% |
| Bootstrap resamples | 5000, deterministic |

Every run carries a SHA-256 `workload_id` over the map, starts, task catalogue, allocation
policy, network and failure settings, seed and the full controller configuration. **Route
policy is the only excluded input**, because it is the independent variable. The comparator
refuses missing or duplicate seeds, empty fingerprints, mismatched fingerprints, candidate
timeouts, and any difference in a paired invariant.

## 3. Acceptance rules

A fleet passes only when all four hold:

1. all 30 candidate runs complete every announced task;
2. candidate robot/robot, robot/human and robot/rack contacts total zero;
3. every seed is paired by an identical non-empty workload fingerprint;
4. the minimum per-seed exact reduction or censored lower bound is at least 20%.

The overall verdict passes only when every fleet passes.

## 4. Why the result is a lower bound, not a speedup

**Every stop-and-wait run reached the 1200 s cutoff without completing — 0/30 at all three
fleet sizes, in both the August and September runs.** A timeout is not a makespan, and it is
never substituted for one.

For a candidate makespan `C`, an unknown baseline makespan `B`, and cutoff `D`:

```
B > D
true reduction = 1 - C/B
therefore      1 - C/B  >  1 - C/D
```

The artifact reports `100 × (1 − C/D)` — a **conservative right-censored lower bound**.
Because the baseline makespans are unknown, this benchmark **cannot and does not report** an
exact baseline mean, median, p95, or an exact speedup percentage. Anyone quoting one is
quoting something this experiment did not measure.

The bound is nevertheless sufficient for the success criterion: the criterion asks for at
least 20%, and a lower bound above 20% establishes that regardless of how much larger the
true figure is. See [15. Limitations](15-LIMITATIONS.md) for what this costs us.

## 5. Completion-time result

Generated **2026-09-02** at commit `7740efb`, 5000 bootstrap resamples per fleet, 2625 s of
wall time. Artifacts: `artifacts/benchmarks/sih-acceptance-2026-09-02.json` and `.csv`.

| Robots | Candidate completion | Baseline completion | Candidate median | Candidate p95 | **Minimum bound** | Median bound | Median-bound bootstrap 95% |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 4 | 30/30 | 0/30 | 383.89 s | 410.19 s | **64.07%** | 68.01% | 67.47–68.88% |
| 6 | 30/30 | 0/30 | 550.25 s | 583.11 s | **50.63%** | 54.15% | 53.51–54.85% |
| 8 | 30/30 | 0/30 | 718.88 s | 785.90 s | **33.38%** | 40.09% | 38.53–41.35% |

**Verdict: PASS at every fleet size, and overall.**

All 90 candidate runs completed all 1,620 announced tasks. The largest single candidate
makespan was **799.48 s**, comfortably below the 960 s that corresponds to a 20% bound at a
1200 s cutoff — so no individual seed came close to failing.

The margin narrows as the fleet grows (64% → 51% → 33%), which is the expected and honest
shape: more robots on one 13-cell corridor means more contention, and the coordination
advantage is progressively spent on congestion. We report each fleet separately rather than
averaging precisely so this trend is visible.

## 6. Safety result

| Robots | Candidate robot-hours | Candidate contacts (r/r, r/h, r/rack) | Candidate 95% upper bound | Candidate worst separation | Baseline robot-hours | Baseline 95% upper bound | Baseline worst separation |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 4 | 12.7652 | 0, 0, 0 | 232.54 | 0.853 m | 39.9999 | 74.211 | 0.876 m |
| 6 | 27.5088 | 0, 0, 0 | 107.91 | 0.871 m | 60.0000 | 49.474 | 0.890 m |
| 8 | 48.2699 | 0, 0, 0 | 61.50 | 0.876 m | 80.0001 | 37.105 | 0.913 m |

Bounds are one-sided 95% upper limits on robot/robot contacts per 1000 robot-hours.
**Totals: 0 contacts of any kind across 88.54 candidate robot-hours and 180.00 baseline
robot-hours — 268.54 robot-hours in all.**

### The counter-intuitive part, stated before a judge finds it

**The candidate's safety bound is weaker than the baseline's** — 232.54 against 74.211 at
four robots — and this is not a safety regression. It is arithmetic. The bound falls only
with exposure, and the candidate *finishes the work in a third of the time*, so it accrues
12.77 robot-hours where the baseline accrues 40.00 by sitting in the cutoff. Going faster
buys less exposure, and less exposure is a weaker statistical bound on an unobserved rate.

The two claims must therefore be quoted together and never traded off: **the candidate
observed zero contacts while doing three times the work.** Lowering the bound requires more
runs, not stronger wording.

## 7. Reproduction, and why re-running mattered

The previously checked-in artifact was generated **2026-08-25 at commit `781a4dfc`**.
Between that commit and this one, **19 files under `src/` and `backend/` changed** — including
`amr.py`, `world.py`, `planner.py`, `metrics.py`, `scenarios.py` and `settings.py`. A result
measured against a simulator that no longer exists is provenance, not evidence, so the gate
was re-run rather than re-quoted.

| Robots | August minimum bound (`781a4dfc`) | September minimum bound (`7740efb`) |
| ---: | ---: | ---: |
| 4 | 63.64% | 64.07% |
| 6 | 51.17% | 50.63% |
| 8 | 34.16% | 33.38% |

The result reproduces to within a percentage point across a substantial rewrite of the
simulation. That stability is itself worth more than either individual number.

One real behavioural change did show up: **message load fell about 12%** (14.90 → 13.18
messages per robot-second at four robots), which is the message-suppression work landing.

### Provenance of this run

- `git_commit`: `7740efb03480155f76a8cdac4d146e47f544f024`
- `source_tree_dirty`: `true` — the tree carried in-progress **documentation and frontend**
  work. Verified with `git diff --name-only 07337e0..7740efb -- src/ backend/ benchmark.py`:
  **no simulation, server or benchmark file changed at any point during the run window.** The
  only commits landing during execution touched `frontend/` and `tools/`.
- Command: `python benchmark.py --seeds 30 --jobs 10 --json artifacts/benchmarks/sih-acceptance-2026-09-02.json --csv artifacts/benchmarks/sih-acceptance-2026-09-02.csv`

To reproduce:

```bash
python -m pytest tests -q          # 228 pass, ~7 min
python benchmark.py --seeds 30 --jobs 8
```

## 8. What this run does *not* establish

Stated here rather than left for a judge to find. The full list is in
[15. Limitations](15-LIMITATIONS.md).

- **The planning-CPU figures from this run are not usable.** `candidate_plan_cpu_mean_ms`
  reads 0.156 / 0.251 / 0.274 ms against August's 0.029 / 0.052 / 0.071 ms. That is host-load
  contamination — this run used `--jobs 10` on a machine simultaneously running a dozen other
  processes — not a five-fold regression. Quote the August figures, or re-measure on an idle
  host. For the edge-feasibility argument see
  [08. Edge Deployment](08-EDGE-DEPLOYMENT.md).
- **`plan_calls` undercounts**, so every `plan_cpu_*` figure in the repo is a lower bound on
  real search CPU regardless of host load.
- **This scenario contains no pedestrians.** `sih_acceptance_overlap` never passes `humans=`,
  so the robot/human bounds in the table above are vacuous duplicates of the robot/robot
  bounds. Human-safety evidence comes from the mixed-traffic showcases instead — see
  [07. Safety](07-SAFETY.md).
- **The published bounds are about 0.9% optimistic.** The χ² quantile helper uses a
  Wilson–Hilferty approximation returning 5.936870 for χ²₀.₉₅(2) against an exact 5.991465.
  Corrected, the candidate bounds are approximately 234.7 / 108.9 / 62.1 rather than
  232.54 / 107.91 / 61.50. This does not move the verdict.
- **Zero observed contacts is not a proven zero rate**, and this is simulation evidence under
  pinned assumptions — not a hardware safety certification, and not proof of behaviour during
  permanent partition or physical robot failure.

---

*Supersedes `archive/SIH_ACCEPTANCE_BENCHMARK.md`, which documents the August run and additionally
mis-cites the artifact's commit as `b1d3c82…` when the JSON records `781a4df…`.*

**Related:** [07. Safety](07-SAFETY.md) · [11. Scenarios](11-SCENARIOS.md) ·
[13. Testing](13-TESTING.md) · [15. Limitations](15-LIMITATIONS.md) ·
[16. Demo Runbook](16-DEMO-RUNBOOK.md)
