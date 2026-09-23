# Edge Lab stress extension — 2026-09-06

Status: **experimental; not merged or pushed**. BIOS 6 and Auction V2 remain the
tested stack. All failures are retained alongside retests. These are software results
on the current Mac, not Raspberry Pi measurements or physical safety certification.

## Implementation

- Edge Lab supports 3–10 separate `edge_node.py` processes, selectable scenarios and
  seed, dynamic robot selection, bounded controller-card scrolling, and real event
  annotations. Live 2D fallback includes workers and dropped pallets.
- Shared-space profiles cover opposing tasks, a one-cell doorway, a dropped pallet,
  a stopped controller and variable human crossings. Existing isolated lanes remain
  available as a short interface smoke test.
- The socket referee applies actual obstacle events and terminates the scheduled
  controller process. Its chassis remains present; surviving peers must finish its
  active task. The WMS never chooses the winner. Unsupported restart/partition
  scenarios fail explicitly instead of silently omitting those events.
- Ports are allocated for the actual fleet size, with overlapping ranges rejected.
- The detector previously discarded single-cell articulation doorways. These now
  include both mouth cells as an exclusive directional zone; overlapping zones merge.
  Ordinary corners and gaps with alternate routes remain unchanged.
- Static pickup/drop corridor directions use a bounded cache. Battery, eligibility,
  ownership and current congestion decisions are not cached. All 45 tested headless
  semantic outcomes were unchanged by this optimization.
- Optional UI metadata is refreshed at 10 Hz while actuator commands remain 50 Hz.
  Timing evidence includes complete I/O/control/journal cycles, phase maxima, thread
  CPU time, late wakeups and skipped schedule slots. No timing threshold was relaxed.
- Sensor-loss timing uses the actual datagram cut/return wall times and requires a
  non-stop command before the cut, a stop during it and subsequent recovery.
- Final live evidence includes source SHA-256 hashes. Batch evidence similarly pins
  the working sources, rather than claiming uncommitted code equals its HEAD commit.

## Headless acceptance

`artifacts/deployment/edge-stress-headless-final.json` records **45/45 completed runs,
237/237 tasks and zero robot/robot, robot/human or robot/rack contacts**. Five scenarios,
3/6/10 robots and seeds 0/1/2 were declared in advance. Non-human profiles use fixed
geometry/workloads; their seeds should not be represented as different layouts.

Nine human cases accumulated **580.22 m** of pedestrian movement. Human speed, pauses
and route direction vary using a separate seeded RNG; robots receive no future-intent
messages. Humans still cooperate through local collision avoidance. This does not
test people intentionally stepping inside an unavoidable stopping distance.

The first 20-case sweep failed two 10-robot doorway cases with zero tasks completed.
After the topology fix, both passed on the unchanged workload. First failures and
targeted retests remain in `edge-stress-first-pass.json` and `edge-doorway-retest.json`.
The 10-robot human case has identical non-CPU results under Python hash seeds 0/1/42,
recorded in `edge-stress-determinism.json`. Live UDP scheduling is not deterministic.

## Paired SIH refresh

`artifacts/benchmarks/sih-edge-stress-refresh.json` and its CSV record 12 candidate
runs against 12 matched stop-and-wait runs: 4/6/8/10 AMRs, seeds 0/1/2, the unchanged
1,200-second cutoff and identical Auction V2 workloads. All candidate runs completed
all **252 tasks**. All baseline runs timed out.

Minimum conservative reduction bounds were **65.22%, 52.90%, 37.54%, and 19.32%** at
4, 6, 8 and 10 robots respectively. **Overall strict gate: FAIL**, because the 10-robot
bound misses 20%. Its candidate times were 968.12, 890.82 and 899.14 seconds. These
are censored lower bounds, not exact speedups. A 19.32% bound does not prove the true
reduction is below 20%, but it cannot establish the required margin either.

The older 90-run evidence remains separately scoped; this 12-run refresh does not
replace it with an equally broad release claim.

## Live browser-open verification

The 3D page was exercised through scenario/fleet selection, reload while running,
top-down camera and AMR10 selection. The final HTTP campaign ran serially with the
browser open, apart from batch jobs. The HTTP runner itself does not instrument GPU
load or prove browser visibility; those were inspected separately.

- First 10-robot human run: **10/10 tasks**, zero contacts, full-cycle maximum **6.00 ms**.
- Doorway before caching: **10/10**, zero contacts, but two calculation overruns and
  one late wakeup; maximum full cycle **22.59 ms**. Strict FAIL retained.
- Cached doorway retest: **10/10**, zero contacts, no overruns or late wakeups, maximum
  full cycle **19.72 ms**. PASS, with little timing margin. It completed in **172.72 s**.
- Final five-profile campaign: every declared task completed, zero contacts across
  all five runs. Blockage, failure recovery, sensor loss and humans passed.
- The overlap run completed **10/10 tasks**, but AMR10 woke more than one 20 ms period
  late once. All calculation and full-cycle durations stayed within budget.
  **The five-profile campaign is therefore FAIL, not a release approval.**
- The failed controller held T000 when stopped. A surviving peer completed it.
  Sensor-loss response was **222.26 ms** to a stop command, with recovery confirmed;
  this is not time to physically brake to rest.

Detailed evidence: `edge-live-browser-doorway-10.json`,
`edge-live-browser-doorway-10-cached.json`, `edge-live-browser-human-10.json`, and
`edge-live-ui-campaign.json` in `artifacts/deployment/`.

## Remaining release work

Final verification: **259 Python tests and 5 JavaScript tests passed**. Targeted Ruff
checks and Git whitespace checks passed. Existing personal assets and untracked
presentation folders were preserved and excluded from this change.

1. Stabilize and characterize scheduler timing on the intended edge host, isolated
   from dashboard rendering; a desktop Python process cannot promise hard real time.
   Keep full-cycle and wakeup gates, rather than hiding the residual failure.
2. Improve and independently retest the ten-robot throughput margin. Do not change
   the pinned cutoff, drop the failing seed or substitute a timeout for a makespan.
3. Expand independent holdout layouts/seeds and non-cooperative pedestrian/detection
   stress tests before broadening safety claims.

## Reproduce

```bash
source .venv/bin/activate
python backend/server.py 8001
# Open http://127.0.0.1:8001/edge-lab.html and choose scenario, fleet and seed.
```

In a second terminal, with the page visible and no active run:

```bash
source .venv/bin/activate
python edge_lab_campaign.py --port 8001
```

Run batch jobs separately from live timing tests:

```bash
python edge_stress_acceptance.py --robots 3,6,10 --seeds 3 --jobs 2
python benchmark.py --robots 4,6,8,10 --seeds 3 --jobs 2 --json artifacts/benchmarks/sih-edge-stress-refresh.json --csv artifacts/benchmarks/sih-edge-stress-refresh.csv
python -m pytest -q
node --test tests/test_live_twin.mjs
```
