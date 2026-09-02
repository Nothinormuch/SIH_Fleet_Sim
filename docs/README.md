# SIH26123 — Documentation

**Edge-AI Based Distributed Fleet Coordination for Autonomous Mobile Robots in Smart
Warehouses** · Problem Statement **26123** · Bharat Electronics Limited · Smart Automation

A decentralized AMR fleet coordination system, delivered as a simulation and structured so
the agent code deploys unchanged onto edge hardware.

---

## Both success criteria, up front

| Criterion | Result |
| --- | --- |
| **Zero inter-robot collisions** | **0 contacts of every kind** — robot/robot, robot/human, robot/rack — across 180 runs and **268.54 robot-hours** |
| **≥20% task-time reduction vs stop-and-wait** | **PASS at every fleet size** — minimum per-seed lower bounds **64.07% / 50.63% / 33.38%** at 4 / 6 / 8 robots |

Measured 2026-09-02 at commit `7740efb` on the pinned `sih_acceptance_overlap` scenario,
30 seeds per fleet, 1200 s cutoff. The reduction figure is a conservative right-censored
**lower bound**, not an exact speedup — the stop-and-wait baseline never completes.
Full method, provenance and caveats: **[12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md)**.

---

## Start here

| You are | Read, in this order |
| --- | --- |
| **A judge with 10 minutes** | [00. Problem Statement](00-PROBLEM-STATEMENT.md) → [01. Requirements Traceability](01-REQUIREMENTS-TRACEABILITY.md) → [12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md) |
| **A judge who wants to probe** | [03. Decentralized Protocol](03-DECENTRALIZED-PROTOCOL.md) and [07. Safety](07-SAFETY.md) — the two claims the submission rests on — then [15. Limitations](15-LIMITATIONS.md) |
| **An evaluator checking rigour** | [14. Engineering Findings](14-FINDINGS.md) and [15. Limitations](15-LIMITATIONS.md). These are where the project is most itself |
| **A teammate before the jury slot** | [16. Demo Runbook](16-DEMO-RUNBOOK.md), then [09. Fleet Dashboard](09-DASHBOARD.md) |
| **A developer** | [02. Architecture](02-ARCHITECTURE.md) → [10. HTTP API Reference](10-API-REFERENCE.md) → [13. Testing](13-TESTING.md) |

---

## The set

| # | Document | What it establishes |
| --- | --- | --- |
| 00 | [Problem Statement](00-PROBLEM-STATEMENT.md) | The statement verbatim, the 20 requirements, and our engineering reading of where its premise needs care |
| 01 | [Requirements Traceability](01-REQUIREMENTS-TRACEABILITY.md) | **The spine.** All 20 requirements → code → test → document, with the gaps named |
| 02 | [Architecture](02-ARCHITECTURE.md) | The no-I/O agent boundary and the three-rate control stack that let one implementation serve benchmark, UDP demo and Pi |
| 03 | [Decentralized Protocol](03-DECENTRALIZED-PROTOCOL.md) | All 13 message types, the transport, dead zones and partitions, and the evidence that no coordinator exists |
| 04 | [Path Planning](04-PATH-PLANNING.md) | A\*, space-time A\*, PIBT, block decomposition, replanning triggers, and measured per-call cost |
| 05 | [Coordination Policies](05-COORDINATION-POLICIES.md) | All 13 route policies, the baselines, every deadlock-breaking mechanism, and the learned policy |
| 06 | [Task Allocation](06-TASK-ALLOCATION.md) | The auctioneer-free auction, bundling, battery-aware bidding, and the re-assignment lifecycle |
| 07 | [Safety](07-SAFETY.md) | The braking-equation protective field, closing velocity, the 360° guard, contact accounting, and the statistical limit of "zero" |
| 08 | [Edge Deployment](08-EDGE-DEPLOYMENT.md) | The edge runtime, systemd units, the multi-process UDP demonstration, and what has *not* run on hardware |
| 09 | [Fleet Dashboard](09-DASHBOARD.md) | The 3D twin, real-time positions and battery, and the rendering of things otherwise invisible |
| 10 | [HTTP API Reference](10-API-REFERENCE.md) | Every endpoint, parameter, validation rule and schema |
| 11 | [Scenarios](11-SCENARIOS.md) | All 18 scenarios, what each proves, and the requirement → scenario evidence map |
| 12 | [Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md) | The acceptance gate, the measured result, and why it is a lower bound |
| 13 | [Testing](13-TESTING.md) | The suite, what it covers, and candidly what it does not |
| 14 | [Engineering Findings](14-FINDINGS.md) | Measurements that contradicted the obvious design. The most distinctive part of this project |
| 15 | [Limitations](15-LIMITATIONS.md) | Every known gap, defect and overclaim risk, stated before a judge finds it |
| 16 | [Demo Runbook](16-DEMO-RUNBOOK.md) | Pre-flight, the timed demo script, the evidence walk, and 15+ anticipated judge questions |

---

## Quick start

```bash
python -m pytest tests -q                 # full suite: 228 pass, ~7 min
python backend/server.py                  # dashboard at http://127.0.0.1:8000
python edge_demo.py                       # three real OS processes over UDP multicast
python benchmark.py --seeds 30 --jobs 8   # the acceptance gate: exit 0 = pass, 2 = fail
```

> **When driving `POST /api/run` directly, always pass `duration`, `robots` and `seed`.**
> It defaults to 120 s / 4 robots / seed 0 regardless of scenario and will silently truncate
> an 800-second showcase. See [10. HTTP API Reference](10-API-REFERENCE.md).

---

## Conventions in this set

- **Every behavioural claim carries a `file.py:LINE` citation.** Anything that could not be
  verified says so rather than guessing.
- **Each claim is marked** implemented and tested / implemented / simulated only / not
  implemented.
- **Limits are stated before results.** [15. Limitations](15-LIMITATIONS.md) exists so that
  nothing in this set can be used against the submission by someone reading it carefully.

## Archive

`archive/` holds the working documents this set supersedes — the BIOS protocol and release
notes, the original findings and critique files, the earlier deployment and demo guides, and
the August acceptance-benchmark write-up. They are kept for provenance. **Where they disagree
with a numbered document, the numbered document is correct**; several are known to be stale,
and the specific contradictions are listed in [15. Limitations](15-LIMITATIONS.md).
