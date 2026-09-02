# 13. TESTING AND VERIFICATION

> This document records what the automated suite actually verifies, what it demonstrably does not, and the exact commands a judge can run to check both claims independently.

**Audience:** SIH judges and BEL evaluators verifying that the coordination and safety claims rest on executable evidence rather than assertion; teammates who must answer "what does that test actually prove?" under questioning.
**Reads best after:** [12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md)

## Requirements evidenced

| # | Requirement | Where | Evidence |
|---|---|---|---|
| 1 | At least 3 AMRs | `tests/test_edge_runtime.py:80`, `tests/test_resilience.py:14` | Three real OS processes exchange authenticated multicast; scenario tests run 3-10 robot fleets |
| 3 | Decentralized communication | `tests/test_core.py:664`, `tests/test_edge_runtime.py:80` | Wire codec round-trips and rejects garbage; real UDP multicast between separate processes |
| 4 | Position sharing | `tests/test_core.py:421`, `tests/test_benchmark.py:167` | Received protocol times derive from the receiver clock; idle heartbeat clears a stale peer route |
| 5 | Intent sharing | `tests/test_benchmark.py:187`, `tests/test_core.py:357` | A robot without a goal never broadcasts an old path as intent; task protocol carries epoch, deadline, lease |
| 6 | No central coordination server | `tests/test_core.py:465`, `tests/test_core.py:478` | Decentralized and V3 runs assert `FleetManager is None` and no central route mode |
| 7 | Multi-agent path planning | `tests/test_core.py:532`, `tests/test_core.py:546`, `tests/test_priority.py:88` | Space-time planner resolves head-on corridors; reservations ban edge swaps; random PIBT steps yield no vertex conflict or edge swap |
| 8 | Collision avoidance | `tests/test_core.py:42`, `tests/test_core.py:580`, `tests/test_core.py:586` | Swept-segment distance catches a position swap; protective field scales with speed; permitted speed is the inverse of the braking equation |
| 9 | Real-time conflict resolution | `tests/test_priority.py:26`, `tests/test_priority.py:48` | Total order on priority keys; backtracking rejects a two-robot edge swap |
| 10 | Deadlock resolution | `tests/test_priority.py:34`, `tests/test_priority.py:60`, `tests/test_seed_99.py:44` | Priority inheritance pushes a three-robot chain; four-agent rotation is legal; Seed 99 measures and breaks a full six-robot gridlock |
| 11 | Narrow intersection / chokepoint | `tests/test_priority.py:765`, `tests/test_priority.py:794`, `tests/test_core.py:562` | Wave members do not refill mid-phase; staging one cell before an occupied mouth; corridor blocks have two mouths |
| 12 | Blocked aisle handling | `tests/test_resilience.py:14` | Blocked aisle is detected and rerouted with zero contacts |
| 13 | Re-routing | `tests/test_resilience.py:14`, `tests/test_bios4.py:240` | `dynamic_reroutes >= 1` on a blocked aisle; reroute is rate-limited |
| 14 | Task re-assignment | `tests/test_resilience.py:27` | A failed auction winner's task is reassigned and completed |
| 15 | Edge / local execution | `tests/test_edge_runtime.py:49`, `tests/test_edge_runtime.py:80` | Runtime uses the local clock and emits peer traffic; three separate processes with three distinct clock offsets |
| 16 | Fleet dashboard | `tests/test_server.py:72`, `tests/test_dashboard.py:249` | HTTP API answers over a real server; `/api/run` is POST-only with security headers |
| 17 | Real-time positions | `tests/test_dashboard.py:279`, `tests/test_dashboard.py:396` | Canvas converts metric poses to grid cells; metric frames stay inside free map cells |
| 18 | Battery status | `tests/test_priority.py:134`, `tests/test_priority.py:223` | Energy-infeasible tasks are rejected; drop admission uses bounded drain capacity |
| 19 | Zero inter-robot collisions | 17 assertions across 6 files (see [§4](#4-coverage-of-the-load-bearing-claims)) | `contacts_robot_robot == 0` on every end-to-end scenario run |
| 20 | ≥20% task-time reduction | `tests/test_benchmark.py:65`-`118`, `tests/test_seed_99.py:74` | The paired-comparison *gate logic* is unit-tested; the measured reduction itself is produced by the benchmark, not the suite — see [§4](#4-coverage-of-the-load-bearing-claims) |

Requirement 2 (dynamic environment) is exercised through scenarios rather than a single test: moving workers (`tests/test_dashboard.py:73`), timed obstacles (`tests/test_core.py:619`), and blocked aisles (`tests/test_resilience.py:14`).

---

## 1. How to run it

```bash
python -m pip install -e ".[dev]"
python -m pytest tests -q
```

`pyproject.toml:41` pins `testpaths = ["tests"]`, so a bare `python -m pytest -q` from the repository root collects the same set.

> ### ⚠️ Do not run the dashboard server while testing
>
> **Stop `python -m backend.server` (or `python backend/server.py`) before running the suite.**
>
> The test fixture at `tests/test_server.py:31` binds `("127.0.0.1", 0)` — an ephemeral port — so a live server on port 8000 does not cause a bind conflict. The damage is CPU contention instead. Six tests in `tests/test_server.py` start real evolutionary training jobs, and two of them assert on wall-clock timing:
>
> - `tests/test_server.py:249` asserts the server answers `/api/scenarios` in under 10 seconds while training (`tests/test_server.py:256`).
> - `tests/test_server.py:276` polls a 120-second deadline for the first generation to land (`tests/test_server.py:285`).
>
> A dashboard server that is itself training, or any other heavy job on the box, can push these past their deadlines and produce a **failure that is an artifact of the machine, not of the code**. Treat a timing failure in `tests/test_server.py` as a contention signal and re-run on an idle machine before believing it.

Node.js is optional but changes what runs. Three tests shell out to `node` to exercise frontend modules (`tests/test_dashboard.py:279`, `:307`, `:345`); they are guarded by `@pytest.mark.skipif(NODE is None, ...)` where `NODE = shutil.which("node")` (`tests/test_dashboard.py:28`). **Without Node these three skip silently**, and a green run on a Node-less machine covers strictly less than it appears to.

---

## 2. The verified count

| Property | Value |
|---|---|
| Collected tests | **228** (`python -m pytest tests --collect-only -q`, 0.74 s) |
| Result | **228 passed, 0 failed, 0 skipped** |
| Wall-clock duration | **1078.99 s (~18 min)** — heavily contended, see below |
| Commit | `7740efb` ("Fix: closed panels kept eating the clicks meant for the menu") |
| Date observed | 2026-09-02, 19:09 IST |
| Platform | Windows 11, CPython 3.13.14, 16 logical CPUs |

**Method.** `python -m pytest tests -q` from the repository root, full output captured to a file, exit code recorded. The collected total was confirmed separately with `--collect-only -q`, which reports 228 in 0.74 s and agrees with the progress percentages in the run itself (72 tests = 31%).

**The duration figure is not a clean measurement, and should not be quoted as the suite's cost.** At the time of this run the same machine was also executing:

| PID | Workload |
|---|---|
| 24404 | `python backend/server.py` — a live dashboard on port 8000 |
| 20696 | `python benchmark.py --seeds 30 --jobs 10` — a ten-worker acceptance benchmark |
| 11804 | a second concurrent `python -m pytest -q` |
| 4720 | this run |

Three heavyweight Python workloads on 16 cores. The honest statement is that **228 tests passed at commit `7740efb`, and the suite took ~18 minutes on a machine that was saturated**. On an idle machine the dominant cost is the six real-training tests in `tests/test_server.py` plus the end-to-end scenario runs in `tests/test_resilience.py` and `tests/test_seed_99.py`; expect single-digit minutes. A judge reproducing this should run on an idle box and will likely see a materially shorter time.

Earlier project notes cite 144, 148, 163, 196, 222 and 228 tests at different commits. **228 is the figure at `7740efb`**; the others are stale and should not be repeated.

---

## 3. Test inventory

Counts are pytest-collected (parametrized cases expanded), so they exceed the number of `def test_` statements in files that use `@pytest.mark.parametrize`.

| File | Tests | Area covered | Requirements |
|---|---:|---|---|
| [`tests/test_core.py`](../tests/test_core.py) | 54 | Geometry and swept collision, A\*/space-time planning, Hungarian assignment, wire codec and HMAC, replay window, safety envelope, dead zones, statistics, one lone-robot integration run | 3, 4, 5, 6, 7, 8, 9, 11, 13, 19 |
| [`tests/test_priority.py`](../tests/test_priority.py) | 49 | PIBT priority engine, priority inheritance and rotation, cell/block leases, task gossip and catalogs, radio dead zones, chokepoint wave discipline, V5 energy-aware auction | 3, 5, 9, 10, 11, 14, 18 |
| [`tests/test_bios4.py`](../tests/test_bios4.py) | 27 | Learned policy: parameter shape, action masking, JSON round-trip, feature-layout refusal, malformed-model rejection, adversarial "always hold"/"always proceed" models | 8, 10, 13, 15 |
| [`tests/test_benchmark.py`](../tests/test_benchmark.py) | 19 | Acceptance-gate integrity, workload fingerprinting, paired/censored comparison arithmetic, V3 commit and staging behaviour at merges and corners | 4, 5, 7, 9, 11, 20 |
| [`tests/test_dashboard.py`](../tests/test_dashboard.py) | 17 | Run-request validation, showcase scenario liveness, worker/pedestrian routing, HTTP method and security headers, canvas geometry and 3D cargo lifecycle (via Node) | 2, 16, 17, 19 |
| [`tests/test_server.py`](../tests/test_server.py) | 16 | Real `ThreadingHTTPServer`: scenario listing, custom floor validation, model upload, training job lifecycle, cancellation, held-out-seed guard, 404/409 paths | 15, 16 |
| [`tests/test_task_protocol.py`](../tests/test_task_protocol.py) | 14 | Generation-bound task terminality, descriptor hashing, completion certificates, owner impersonation and false relay, MTU bound | 3, 4, 5, 14 |
| [`tests/test_auction_bundle.py`](../tests/test_auction_bundle.py) | 12 | Bounded future auction: bid capacity, future-cost ordering, lease renewal/expiry, ownership hardening, epoch poisoning, replay-state bounds | 6, 14 |
| [`tests/test_resilience.py`](../tests/test_resilience.py) | 6 | End-to-end SIH failure scenarios: blocked aisle, robot failure, network partition, 20% packet loss, human in aisle, negative control | 2, 10, 12, 13, 14, 19 |
| [`tests/test_comparison_baselines.py`](../tests/test_comparison_baselines.py) | 4 | Baseline policies are real registered execution paths; competition stop-and-wait stays non-cooperative | 20 |
| [`tests/test_edge_runtime.py`](../tests/test_edge_runtime.py) | 4 | Deployment boundary: default policy, local clock, hardware sensor schema, three real processes over authenticated UDP multicast | 1, 3, 6, 15 |
| [`tests/test_seed_99.py`](../tests/test_seed_99.py) | 3 | Fixed six-AMR launch-congestion demo: workload pinning, gridlock detection and release, stop-and-wait contrast | 10, 11, 19, 20 |
| [`tests/test_terminal_journal.py`](../tests/test_terminal_journal.py) | 3 | Fail-closed journal persistence: round-trip, checksum/truncation rejection, record and size bounds | 15 |
| **Total** | **228** | | |

---

## 4. Coverage of the load-bearing claims

For each claim the submission makes, the test that verifies it — or the absence of one.

| Claim | Verified by | What the test actually proves |
|---|---|---|
| Swept collision checking, not endpoint sampling | `tests/test_core.py:42` | `segments_min_distance` returns 0 for two robots exchanging cells — the case endpoint-only checking misses. Directly guards against a spuriously perfect safety record. |
| Permitted speed is always stoppable | `tests/test_core.py:586` | For gaps 0.2-3.0 m, `stop_field_m(max_speed_for_clearance(gap)) <= gap`. An algebraic invariant, checked at five points, not a proof over the continuum. |
| Head-on closing speed is budgeted | `tests/test_core.py:594` | Documented in-test as "a real collision source: both robots braked correctly for their own speed and hit anyway." |
| Reservations forbid edge swaps | `tests/test_core.py:546` | Space-time reservation table rejects the swap. |
| PIBT produces no vertex conflict or edge swap | `tests/test_priority.py:88` | Randomized: `test_random_step_outputs_have_no_vertex_conflict_or_edge_swap`. Property-based over generated steps — the strongest structural safety test in the suite. |
| Deadlock is broken, not merely avoided | `tests/test_seed_99.py:44` | Asserts `full_gridlock_observed` is true, `peak_simultaneously_blocked == 6`, and `0 < first_release_latency_s <= 1.0`. It proves the fleet *entered* total gridlock and left it within one second. |
| Priority inheritance resolves chains | `tests/test_priority.py:34` | Three-robot chain is pushed. |
| No central coordinator in decentralized modes | `tests/test_core.py:465`, `tests/test_core.py:478` | Asserts the `FleetManager` is absent and no central route mode is active for V3/decentralized runs. |
| Zero inter-robot collisions (req 19) | 17 assertions in `tests/test_benchmark.py`, `test_bios4.py`, `test_comparison_baselines.py`, `test_dashboard.py`, `test_resilience.py`, `test_seed_99.py` | Every end-to-end scenario asserts `contacts_robot_robot == 0`. **Note:** `tests/test_core.py` and `tests/test_priority.py` — the two largest files, 103 of 228 tests — contain no such assertion. They test unit-level invariants, not run-level contact counts. |
| A bad learned model cannot cause a collision | `tests/test_bios4.py:204` | `test_a_model_that_always_proceeds_cannot_cause_a_collision`. The design rests on guarantees not depending on what the network learned; this and `:190` (`always_holds_cannot_freeze_the_fleet`) are the two tests that defend it. |
| Wire tampering is rejected | `tests/test_core.py:397`, `tests/test_task_protocol.py:165` | HMAC rejects tampered and unsigned packets; owner impersonation and false relay are refused. |
| Replay is bounded | `tests/test_core.py:412`, `tests/test_auction_bundle.py:319` | Reordering accepted, duplicates and old data rejected; replay session state is bounded and expires. |
| Three real processes, three real clocks | `tests/test_edge_runtime.py:80` | Asserts `separate_processes`, `authenticated_transport`, `control_deadlines_met`, and `len(set(clock_offsets_s)) == 3`. This is the strongest edge-execution evidence in the suite (req 15). |
| **≥20% task-time reduction (req 20)** | **No test measures it.** | `tests/test_benchmark.py:65`-`118` verify the *comparison machinery* against hand-built `_result(...)` fixtures: that an exact paired comparison passes only on matching workloads, that censoring yields a conservative lower bound, and that the gate refuses a candidate timeout, a contact, or a seed/fingerprint mismatch. The measured 20% figure is produced by running the benchmark — see [12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md). The suite proves the gate cannot be fooled; it does not prove the system passes it. |
| Stop-and-wait baseline is genuinely uncooperative | `tests/test_comparison_baselines.py:30` | Guards against a flattering baseline. Paired with `tests/test_seed_99.py:74`, which asserts stop-and-wait completes **zero** of six tasks on the demo workload while BIOS_6 completes all six. |
| No false coordination win on an empty floor | `tests/test_resilience.py:82` | Negative control: on an open floor with no contention, BIOS_3 and stop-and-wait must land within 10% of each other. Catches a benchmark that credits coordination for something else. |
| Training cannot memorize evaluation seeds | `tests/test_server.py:298` | Rejects held-out seeds with a "memorisation" error. Keeps the headline number honest. |

---

## 5. What the tests do NOT cover

This section exists because a green suite is not the same as a working system, and this repository has proved that twice.

### 5.1 Failure-path coverage is better than the project notes claim, but concentrated

Earlier project notes record that "only about 10" tests target failure conditions. **That figure is stale.** Measured at `7740efb` by two definitions:

| Definition | Count | Method |
|---|---:|---|
| Tests asserting on a raised exception | **24** | Test body contains `pytest.raises` or the `_expect_error` helper (`tests/test_server.py:60`) |
| Tests injecting a fault or feeding malformed/tampered/unauthorized input | **~60** | Above, plus tests whose subject is packet loss, radio dead zones, partition, robot failure, blocked aisle, lease expiry, staleness, or degraded network |

The narrow figure of 24 is mechanically reproducible; the ~60 figure involves judgement about what counts as a fault, and reasonable people would move a handful of tests either way. Neither is 10.

The distribution matters more than the total. The 24 assert-on-raise tests are concentrated in input validation: 10 in `tests/test_server.py`, 5 in `tests/test_bios4.py`, 2 each in `test_dashboard.py`, `test_task_protocol.py`, `test_terminal_journal.py`. **`tests/test_core.py` and `tests/test_priority.py` contain none** — the 103 tests covering the planner, the priority engine and the safety envelope are almost entirely happy-path property tests.

The consequence: **the resilience claim rests mainly on six end-to-end scenario tests** (`tests/test_resilience.py`), not on unit tests. Those six are real — they inject a robot failure, a network partition, 20% packet loss and a blocked aisle, and assert completion with zero contacts — but they are six runs at fixed seeds, not a search. The broader fault search lives in `fault_campaign.py`, which is not part of the suite.

### 5.2 No test loads the dashboard page

**This is the largest coverage gap in the repository.** No test in `tests/` opens `frontend/index.html`, constructs a DOM, or executes `frontend/js/main.js`. The three Node tests (`tests/test_dashboard.py:279`, `:307`, `:345`) import `environment.js` and `digital-twin.js` as isolated modules with a stubbed `global.window`; they never call `boot()` and never touch the page. Of eight JS modules totalling 5,553 lines, two are partially exercised and `main.js` (1,825 lines) is not exercised at all.

The failure mode this permits has occurred twice:

1. **A merge kept a feature's files and dropped its wiring.** Every assertion still passed, because the assertions were also true when the feature was ignored.
2. **An `index.html` rewrite deleted a panel that `main.js` still wired in `boot()`.** That would throw on first paint and kill every listener registered after it — the dashboard would load and then do nothing. All 148 tests passed at that commit.

Both are invisible to a Python test suite that never renders the page.

#### The consumer-versus-provider check

The cheap check that catches this class of bug: **every id the JS looks up must exist in the HTML.**

```bash
# consumers: every el('x') / getElementById('x') in the JS
grep -rhoE "(el|getElementById)\(\s*['\"][^'\"]+['\"]" frontend/js/*.js | grep -oE "['\"][^'\"]+" | tr -d "\"'" | sort -u > /tmp/consumed
# providers: every id= in the page
grep -oE "\bid\s*=\s*[\"'][^\"']+" frontend/index.html | grep -oE "[\"'][^\"']+" | tr -d "\"'" | sort -u > /tmp/provided
comm -23 /tmp/consumed /tmp/provided   # anything printed is a consumer with no provider
```

**Result at `7740efb`: 128 ids provided, 107 distinct ids consumed, and 2 consumed ids have no provider.**

| Consumed id | Call site | Status |
|---|---|---|
| `progressTasks` | `frontend/js/main.js:1472` | No matching `id=` in `frontend/index.html` |
| `progressTime` | `frontend/js/main.js:1473` | No matching `id=` in `frontend/index.html` |

Both are read inside `updateSummaryProgress(frame)` (`frontend/js/main.js:1465`), which **is called on every frame** from `frontend/js/main.js:1013`. The function computes `done` and `total`, then writes them to elements that do not exist. It does not throw, because both writes are guarded:

```js
const taskElement = el('progressTasks');   // main.js:1472 → null
const timeElement = el('progressTime');    // main.js:1473 → null
if (taskElement) { taskElement.textContent = `${done} / ${total}`; }
if (timeElement) { timeElement.textContent = `t = ${frame.t.toFixed(1)} s`; }
```

This is the benign half of the failure mode described above — the null-guard converts a crash into silent dead code, so a summary-progress readout is computed every frame and discarded. It is a live defect at `7740efb`, it is invisible to all 228 tests, and the two-line check above finds it in under a second. Either the two elements should be restored to `index.html` or `updateSummaryProgress` and its call site should be removed.

**Direction matters.** The consumed-without-provider direction (above) is sound. The reverse direction — ids in the HTML never named in the JS — reports 23 entries and is **not** reliable: several are reached indirectly, through a string array (`frontend/js/hud.js:40`-`44`) or a `set(id, value)` helper (`frontend/js/main.js:1731`), and others are styling or layout anchors with no JS consumer by design. Only run the check in the consumer→provider direction.

### 5.3 Other gaps worth stating

| Gap | Consequence |
|---|---|
| No CSS or visual regression testing | Layout breakage ships green |
| No WebSocket/live-stream test | `frontend/js/network.js` (166 lines) is untested |
| No test asserts the 20% figure | See [§4](#4-coverage-of-the-load-bearing-claims); the number comes from the benchmark |
| Fault search is outside the suite | `fault_campaign.py` is run manually, not in CI |
| Three Node tests skip silently without Node | A green run on a Node-less machine covers less than it appears to |
| No hardware-in-the-loop test | `tests/test_edge_runtime.py:80` uses three real processes on one host, not three physical edge devices |

---

## 6. Determinism and seeding

Reproducibility is a design property here, not a test convention.

- **Every simulation entry point takes a seed.** `run_scenario(sc, policy, seed=0, ...)` (`src/main.py:107`) constructs `World(env, cfg, seed)` (`src/world.py:232`) and `SimNetwork(cfg, seed=seed)` (`src/main.py:126`).
- **The radio model uses a content-addressed per-packet RNG, not a global stream** (`src/transport.py:167`-`173`). Each packet's loss and latency draw comes from `blake2b` over `[seed, src, dst, message.type, round(t, 6), delivery_identity_body(message)]`, seeding a fresh `random.Random` per packet. Sequence numbers are deliberately excluded.

  This exists for *counterfactual fairness*, documented at `src/transport.py:162`-`166`: a policy that suppresses one redundant packet must not shift a global RNG and thereby change the loss and latency of every later packet. Without it, an event-triggered policy would be compared against a baseline on a different random tape, and the benchmark would be meaningless. `tests/test_core.py:697` (`test_unrelated_packet_does_not_shift_later_loss_draws`) is the test that enforces it, and `tests/test_core.py:684` asserts the network model is deterministic for a fixed seed.

- **Determinism is asserted directly** in `tests/test_bios4.py:40` (forward pass), `tests/test_bios4.py:217` (fixed model and seed), `tests/test_priority.py:60` (four-agent rotation), and `tests/test_benchmark.py:40` (workload fingerprint).
- **The benchmark pins its workload by fingerprint.** `tests/test_benchmark.py:53` asserts the acceptance scenario pins the overlap workload, and `compare_paired` refuses a fingerprint or seed mismatch (`tests/test_benchmark.py:109`), so two halves of a comparison cannot silently diverge.
- **The demo workload is frozen.** `seed_99_congestion()` raises unless it gets exactly 6 robots and seed 99 (`tests/test_seed_99.py:37`-`41`).

One consequence worth stating plainly: because seeds are fixed, the suite verifies behaviour *on those seeds*. It is regression testing, not a search over the input space.

---

## 7. The benchmark as a test

The acceptance benchmark is a release gate with CI-compatible exit codes:

```python
return 0 if payload["verdict"] == "pass" else 2   # src/benchmark.py:316
```

with `raise SystemExit(main())` at `src/benchmark.py:320`. So:

| Exit code | Meaning |
|---|---|
| `0` | Gate completed and passed |
| `2` | Gate completed and failed — **or** argparse rejected the arguments (`src/benchmark.py:295`, `parser.error`) |

Exit 2 is therefore not unambiguous: a completed failing gate and an invalid `--robots` string produce the same code. A CI job that distinguishes them should read `verdict` from the emitted JSON (`artifacts/benchmarks/sih-acceptance.json` by default, `src/benchmark.py:288`) rather than trusting the exit code alone.

Run it as:

```bash
python benchmark.py --seeds 30 --jobs 8      # strict SIH acceptance gate
```

Defaults are 30 seeds over fleet sizes 4, 6, 8 with a 20.0% threshold (`src/benchmark.py:269`-`283`). Full methodology, the measured reduction, and the confidence treatment are in [12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md).

---

## 8. Platform notes — a caught portability defect

The repository shipped a Windows-fatal bug that Linux CI could not see, and it is worth documenting because the pattern generalizes.

**The defect.** `TerminalJournal.sync()` performs an atomic write: write to a temporary file, `fsync` it, `os.replace` it into position, then `fsync` the *containing directory* to harden the rename itself. That last step is a POSIX idiom. **Windows cannot open a directory as a file descriptor at all**, so `os.open(directory, os.O_RDONLY)` raises `PermissionError`.

`PermissionError` is a subclass of `OSError`, and the whole write was wrapped in one broad handler (`src/terminal_journal.py:145`):

```python
except (OSError, TerminalJournalError) as exc:
    self.stats["write_failures"] += 1
    raise TerminalJournalError("terminal journal write failed") from exc
```

So the directory `fsync` — a durability nicety after the data was already safely in place — was caught by the same clause as a genuine write failure and re-raised as a domain error. **100% of journal writes failed on Windows while Linux CI stayed green.** The data had already been written correctly; only the optional hardening step failed, and the error handling could not tell the difference.

**The fix is present.** `_fsync_directory()` at `src/terminal_journal.py:28` isolates the platform-specific call and returns quietly when it is unavailable:

```python
def _fsync_directory(directory: Path) -> None:
    try:
        directory_fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return                      # src/terminal_journal.py:39-40
    try:
        os.fsync(directory_fd)
    except OSError:
        pass                        # src/terminal_journal.py:42-44
    finally:
        os.close(directory_fd)
```

Its docstring (`src/terminal_journal.py:29`-`36`) states the reasoning explicitly, ending "Unavailable, not broken." The call site is `src/terminal_journal.py:143`, after `os.replace`, and now cannot convert a missing platform feature into a failed write.

**What caught it.** The branch's own two new tests — `test_terminal_journal_round_trip_keeps_latest_generation` (`tests/test_terminal_journal.py:19`) and `test_terminal_journal_rejects_tampering_and_truncation` (`tests/test_terminal_journal.py:30`) — fail on Windows against the unfixed code, because both call `sync()` and the first asserts `stats == {"loads": 1, "writes": 1, "write_failures": 0}` (`tests/test_terminal_journal.py:27`). That assertion on the write-failure counter is what converts a silent platform difference into a red test.

**The generalizable lesson:** a broad `except OSError` around a block that mixes an essential operation with an optional one cannot distinguish "the write failed" from "a platform nicety is unavailable." Isolate best-effort calls in their own helper with their own handler. And a suite that only ever runs on one OS cannot see this class of bug at all — this project's development machine is Windows and CI is Linux, which is the only reason it surfaced.

---

## 9. What a judge should run

An ordered sequence that takes under ten minutes on an idle machine and demonstrates the system is real rather than described.

```bash
# 0. Ensure nothing is holding the CPU or port 8000 (see §1 warning).
#    On Windows:  netstat -ano | findstr :8000
```

| # | Command | Expected | Proves |
|---|---|---|---|
| 1 | `python -m pip install -e ".[dev]"` | Installs cleanly, no pinned deps beyond pytest/ruff | The package is real (`pyproject.toml:12`) |
| 2 | `python -m pytest tests --collect-only -q \| tail -1` | `228 tests collected` | The suite is the size claimed here |
| 3 | `python -m pytest tests -q` | `228 passed` | §2. Single-digit minutes when idle |
| 4 | `python -m pytest tests/test_seed_99.py -q` | `3 passed` | Deadlock is entered and broken within 1 s (req 10); stop-and-wait completes 0/6 while BIOS_6 completes 6/6 (req 20 contrast) |
| 5 | `python -m pytest tests/test_edge_runtime.py -q` | `4 passed` | Three real OS processes, three distinct clocks, authenticated UDP multicast, zero contacts (reqs 1, 3, 6, 15) |
| 6 | `python -m pytest tests/test_resilience.py -q` | `6 passed` | Blocked aisle, robot failure, partition, 20% loss, human in aisle, negative control (reqs 10, 12, 13, 14, 19) |
| 7 | `python benchmark.py --seeds 5 --jobs 8; echo $?` | `0` | The gate runs and passes on a reduced seed set. Use `--seeds 30` for the full evidence run ([12](12-BENCHMARK-AND-EVIDENCE.md)) |
| 8 | The `comm -23` check in [§5.2](#the-consumer-versus-provider-check) | prints `progressTasks`, `progressTime` | The known frontend gap is real and reproducible — and that this document is not overstating its own coverage |
| 9 | `python -m backend.server` then open <http://127.0.0.1:8000> | Dashboard loads and runs a scenario | Reqs 16, 17, 18 — **only verifiable by eye**, because no test loads the page ([§5.2](#52-no-test-loads-the-dashboard-page)) |

Step 9 is not optional. It is the only verification of the dashboard requirements, and [§5.2](#52-no-test-loads-the-dashboard-page) explains why the suite cannot substitute for it.

---

## Related documents

- [01. Requirements Traceability](01-REQUIREMENTS-TRACEABILITY.md) — the full requirement-to-code map
- [07. Safety](07-SAFETY.md) — the safety argument these tests defend
- [09. Dashboard](09-DASHBOARD.md) — the frontend that [§5.2](#52-no-test-loads-the-dashboard-page) says is untested
- [12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md) — the measured 20% result
- [14. Findings](14-FINDINGS.md) — defects found, including the two frontend incidents
- [15. Limitations](15-LIMITATIONS.md) — what the system does not do
- [16. Demo Runbook](16-DEMO-RUNBOOK.md) — the live demonstration sequence
