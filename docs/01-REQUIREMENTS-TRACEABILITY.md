# 01. REQUIREMENTS TRACEABILITY

> Every one of SIH26123's twenty requirements traced to an implementation, a test, a
> demonstration and a document — with the gaps named rather than papered over.

**Audience:** judges and evaluators auditing the submission against the problem statement.
**Reads best after:** [00. Problem Statement](00-PROBLEM-STATEMENT.md)

This is the spine of the documentation set. Each row goes requirement → code → evidence in
one hop. Where a requirement is only partly satisfied, the row says so and
[§3](#3-gaps-caveats-and-honest-status) explains what is missing.

---

## 1. The matrix

**Status vocabulary.** *Implemented & tested* — code exists, an automated test asserts the
behaviour. *Implemented & measured* — additionally quantified by the benchmark or a scenario
run. *Implemented, hardware unverified* — the code runs and is tested, but never on the
physical hardware the statement names.

| # | Requirement | Status | Primary implementation | Test | Read |
| ---: | --- | --- | --- | --- | --- |
| 1 | At least 3 AMRs | **Implemented & measured** | `src/main.py:146` builds one `AMRBrain` per start cell; `src/distributed_demo.py:164` refuses fewer than 3 processes | `tests/test_edge_runtime.py:80`, `tests/test_resilience.py:14` | [02](02-ARCHITECTURE.md), [08](08-EDGE-DEPLOYMENT.md) |
| 2 | Dynamic warehouse environment | **Implemented & tested** | `src/main.py:226-270` injects obstacles, humans and failures during a run; `src/world.py:900` publishes them per frame | `tests/test_resilience.py:14` | [11 §4](11-SCENARIOS.md) |
| 3 | Decentralized communication | **Implemented & tested** | 13 message types, `src/messages.py:38-53`; real IPv4 multicast at `src/transport.py:194` | `tests/test_core.py:664`, `tests/test_edge_runtime.py:80` | [03](03-DECENTRALIZED-PROTOCOL.md) |
| 4 | Position sharing | **Implemented & tested** | `HEARTBEAT` body `p`/`c`, `src/messages.py:460-462`; consumed at `src/amr.py:5276-5277` | `tests/test_core.py:421` | [03 §3](03-DECENTRALIZED-PROTOCOL.md) |
| 5 | Intent sharing | **Implemented & tested** | `INTENT` built `src/amr.py:5239-5264`, encoded `src/messages.py:501-515` | `tests/test_benchmark.py:187` | [03 §4](03-DECENTRALIZED-PROTOCOL.md) |
| 6 | **No central coordination server** | **Implemented & tested** | `MANAGED_POLICIES` `src/main.py:104`; the sole manager construction site is `src/main.py:166`, so every other policy runs `manager = None` | `tests/test_core.py:465`, `tests/test_core.py:478` assert `FleetManager is None` | [03 §6](03-DECENTRALIZED-PROTOCOL.md) |
| 7 | Multi-agent path planning | **Implemented & tested** | `src/planner.py:127` space-time A\*; `src/priority.py:84` PIBT | `tests/test_core.py:532`, `:546`, `tests/test_priority.py:88` | [04](04-PATH-PLANNING.md) |
| 8 | Collision avoidance | **Implemented & measured** | `_safety` at `src/amr.py:868`, applied after every policy's output at `src/amr.py:603` | `tests/test_core.py:42`, `:580`, `:586` | [07](07-SAFETY.md) |
| 9 | Real-time conflict resolution | **Implemented & tested** | PIBT runs inside the 10 Hz reactive loop, `src/amr.py:1880` | `tests/test_priority.py:26`, `:48` | [05](05-COORDINATION-POLICIES.md) |
| 10 | Deadlock resolution | **Implemented & tested** | `src/amr.py:2227` (panic-on-stick), `:2143` (cycle-break replan), `:2494` | `tests/test_priority.py:34`, `:60`, `tests/test_seed_99.py:44` | [05 §3](05-COORDINATION-POLICIES.md) |
| 11 | Narrow intersection / chokepoint | **Implemented & measured** | `src/amr.py:1463`, `:2320`, `:1690`; corridor geometry `src/environment.py:135-153` | `tests/test_priority.py:765`, `:794` | [05 §4](05-COORDINATION-POLICIES.md) |
| 12 | Blocked aisle handling | **Implemented & tested** | `src/amr.py:2774` promotes stationary anonymous returns; `:2863` writes an expiring block into the local map | `tests/test_resilience.py:14` | [04 §6.2](04-PATH-PLANNING.md) |
| 13 | Re-routing | **Implemented & tested** | `_replan` at `src/amr.py:2693`, reached from 22 trigger sites | `tests/test_resilience.py:14`, `tests/test_bios4.py:240` | [04 §6](04-PATH-PLANNING.md) |
| 14 | Task re-assignment | **Implemented & tested** | `src/amr.py:4690`, `:4727` — lease expiry returns the task to the pool | `tests/test_resilience.py:27` | [06 §5](06-TASK-ALLOCATION.md) |
| 15 | Edge / local execution | **Implemented, hardware unverified** | `src/edge_runtime.py:266`; `pyproject.toml:11` declares zero runtime dependencies, AST-verified stdlib-only | `tests/test_edge_runtime.py:49`, `:80` | [08](08-EDGE-DEPLOYMENT.md) |
| 16 | Fleet dashboard | **Implemented & tested** | `frontend/index.html:27`, served by `backend/server.py:790` with no build step | `tests/test_server.py:72`, `tests/test_dashboard.py:249` | [09](09-DASHBOARD.md) |
| 17 | Real-time positions on dashboard | **Implemented & tested** | 10 Hz telemetry `src/world.py:879`; rendered `frontend/js/main.js:922` | `tests/test_dashboard.py:279`, `:396` | [09 §3](09-DASHBOARD.md) |
| 18 | Battery status | **Implemented & tested** | On the wire as `HB.b` (`src/messages.py:462`); in telemetry `src/world.py:881`; **a decision input** at `src/amr.py:4114` | `tests/test_priority.py:134`, `:223` | [06 §4](06-TASK-ALLOCATION.md), [09 §4](09-DASHBOARD.md) |
| 19 | **Zero inter-robot collisions** | **Measured — criterion met** | Contact definition `src/world.py:691`; counters `src/main.py:416` | 17 assertions across 6 files | [07](07-SAFETY.md), [12 §6](12-BENCHMARK-AND-EVIDENCE.md) |
| 20 | **≥20% task-time reduction** | **Measured — criterion met** | Paired gate `src/benchmark.py:269-281`; `src/metrics.py:289` refuses a ratio when a policy fails to complete | `tests/test_benchmark.py:65-118` | [12](12-BENCHMARK-AND-EVIDENCE.md) |

---

## 2. The two success criteria

### Requirement 19 — zero inter-robot collisions

**Result: 0 contacts of every kind — robot/robot, robot/human and robot/rack — across 180
runs and 268.54 robot-hours** (88.54 candidate, 180.00 baseline), measured 2026-09-02 at
commit `7740efb`.

We report the observed count *and* the rate bound it supports, because absence over finitely
many runs is not a rate of zero:

| Robots | Contacts | Robot-hours | One-sided 95% upper bound (per 1000 robot-h) | Worst separation |
| ---: | ---: | ---: | ---: | ---: |
| 4 | 0 | 12.77 | 232.54 | 0.853 m |
| 6 | 0 | 27.51 | 107.91 | 0.871 m |
| 8 | 0 | 48.27 | 61.50 | 0.876 m |

Two things a judge should be told before asking. The candidate's bound is *weaker* than the
baseline's precisely because it finishes three times faster and so accrues less exposure —
this is arithmetic, not a safety regression. And the published bounds are about 0.9%
optimistic because of a Wilson–Hilferty approximation in the χ² helper. Both are explained in
[12 §6](12-BENCHMARK-AND-EVIDENCE.md) and [15](15-LIMITATIONS.md).

### Requirement 20 — ≥20% reduction versus stop-and-wait

**Result: PASS at every fleet size.** Minimum per-seed bounds **64.07% / 50.63% / 33.38%** at
4 / 6 / 8 robots, against a 20% threshold. All 90 candidate runs completed all 1,620 tasks;
all 90 stop-and-wait runs hit the 1200 s cutoff without completing.

The figure is a **conservative right-censored lower bound**, not an exact speedup, because the
baseline never completes. No exact baseline mean, median or p95 exists to quote. The bound is
sufficient for the criterion — which asks for *at least* 20% — but must be stated in those
words. Full derivation in [12 §4](12-BENCHMARK-AND-EVIDENCE.md).

The gate is strict by construction: every paired run is fingerprinted with a SHA-256
`workload_id` over every input except the route policy, and the comparator refuses mismatched
or missing pairs rather than averaging around them.

---

## 3. Gaps, caveats and honest status

Stated here so a judge does not have to find them.

**Requirement 15 is the one material gap.** The agent performs no I/O, has zero runtime
dependencies, and the multi-process UDP runner has been executed live — three separate OS
processes exchanging authenticated multicast, 0 deadline misses, ~28 MB RSS per node. But
**nothing has ever run on a physical Raspberry Pi or Jetson Nano.** The per-node CPU and
memory figures in [08. Edge Deployment](08-EDGE-DEPLOYMENT.md) are labelled ESTIMATE, and a
calibration procedure to replace them with measurements in about an hour is given there. All
CPU numbers in this repository are host-measured.

**Requirements 16 and 17 have no scenario, and cannot.** They are properties of the service
and the telemetry stream rather than of any simulated situation, so they are evidenced by
`backend/server.py:616-647` and the 10 Hz frames every run emits, plus the dashboard tests —
not by a scenario id.

**Requirement 6 is evidenced structurally, not by a scenario.** No single scenario proves the
absence of a coordinator; the argument is that `MANAGED_POLICIES` (`src/main.py:104`) is the
only path to a `FleetManager`, that the shipped policies are not in it, and that two tests
assert `FleetManager is None`. The strongest live demonstration is the multi-process UDP run,
where a judge can capture the multicast traffic themselves — see
[16. Demo Runbook](16-DEMO-RUNBOOK.md).

**The dashboard is a passive reader, and one code comment overstates this.**
`backend/server.py:20` describes it as joining the multicast group and reading the robots'
datagrams. It does not: there is no socket in `backend/` or `frontend/`, and the dashboard is
downstream of a completed simulation. The *conclusion* — that it issues no coordination and
the fleet is unaffected if it is switched off — is correct and is what should be said.

**Requirement 19's benchmark scenario contains no pedestrians.** `sih_acceptance_overlap`
never passes `humans=`, so its robot/human bound duplicates the robot/robot bound and carries
no independent information. Human-safety evidence comes from the mixed-traffic showcases
instead, at 7.83 robot-hours. And because the simulated worker paths with A\* and rejects
approaches at 1.26 m — a *larger* threshold than the AMR's 0.45 m guard — "zero human
contacts" is a property of both parties avoiding each other, not of the robots alone.

**Two scenarios do not demonstrate what their names suggest.** `manager_dies` is inert under
the default policy, because the kill is guarded by `manager is not None` and the default
`BIOS_PIBT.6` builds no manager; it requires `--policy hierarchical`. And `preassigned` on any
`showcase_*` scenario announces zero tasks, so it reports a flawless-looking 0/0 result from
robots that never move. Neither should be used as evidence. Both are detailed in
[15. Limitations](15-LIMITATIONS.md).

**Coverage gaps in the test suite.** No test loads the dashboard page, and few tests target
failure paths — so the resilience argument rests more on the scenario suite than on unit
tests. See [13. Testing](13-TESTING.md).

---

## 4. How to verify each claim yourself

```bash
python -m pytest tests -q                 # full suite: 228 pass, ~7 min
python benchmark.py --seeds 30 --jobs 8   # the acceptance gate: exit 0 = pass, 2 = fail
python edge_demo.py                       # three real OS processes over UDP multicast
python backend/server.py                  # dashboard at http://127.0.0.1:8000
```

Verified 2026-09-02 at commit `7740efb`: **228 tests pass in about 7 minutes.** The server
tests bind ephemeral ports (`("127.0.0.1", 0)`), so a dashboard running on port 8000 does *not*
break the suite — an earlier note in this project claimed otherwise and was wrong for this tree.
On Windows, `SO_REUSEADDR` can let a second `python backend/server.py` silently bind an
already-used port without erroring, so check with `netstat` rather than trusting that the
process started cleanly.

Always pass `duration`, `robots` and `seed` explicitly when driving `POST /api/run` — it
defaults to 120 s / 4 robots / seed 0 regardless of scenario and will silently truncate a
long showcase. See [10. HTTP API Reference](10-API-REFERENCE.md).

---

**Related:** [00. Problem Statement](00-PROBLEM-STATEMENT.md) ·
[12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md) ·
[13. Testing](13-TESTING.md) · [15. Limitations](15-LIMITATIONS.md) ·
[16. Demo Runbook](16-DEMO-RUNBOOK.md)
