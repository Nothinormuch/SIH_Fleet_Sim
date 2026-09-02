# 10. HTTP API REFERENCE

> This document is the complete contract for the nine JSON endpoints and the static file handler in `backend/server.py`, so that a developer can drive every one of them correctly without opening the server source.

**Audience:** SIH judges and BEL evaluators checking what the dashboard is actually allowed to ask for; teammates and any future developer driving the simulation from `curl`, a script or a new client.
**Reads best after:** [09. Fleet Dashboard](09-DASHBOARD.md)

## Requirements evidenced

| # | Requirement | Where | Evidence |
|---|---|---|---|
| 1 | At least 3 AMRs | `robots` request field | `MIN_ROBOTS = 2`, `MAX_ROBOTS = 100`, enforced not clamped — `backend/server.py:140`, `backend/server.py:207` |
| 2 | Dynamic warehouse environment | Telemetry frame `obstacles[]` | Appearing and clearing obstacles are published per frame — `src/world.py:900`, `src/main.py:257` |
| 4 | Position sharing | Telemetry frame `fleet[].peers` | Per-robot list of peer ids currently in the neighbour table — `src/main.py:206` |
| 5 | Intent sharing | Telemetry frame `fleet[].path`, `fleet[].goal`, `fleet[].priority_key` | Next 8 reserved cells, current goal and the published priority token — `src/main.py:204`, `src/main.py:208` |
| 6 | No central coordination server | `meta.has_manager` | The API returns whether a fleet manager existed at all; peer-to-peer policies report `false` — `src/main.py:697`, `backend/server.py:15` |
| 10 | Deadlock resolution | `summary.deadlocks_detected`, `demo_evidence` | Measured standstill and first release from recorded telemetry, not a scripted label — `src/main.py:513` |
| 13 | Re-routing | `summary.dynamic_reroutes`, `summary.replans` | Aggregated per-brain counters — `src/main.py:428`, `src/main.py:427` |
| 14 | Task re-assignment | `summary.task_reassignments` | — `src/main.py:430` |
| 15 | Edge / local execution | `GET /api/train/model` | Returns the `.json` a Pi flashes, as a browser download — `backend/server.py:734` |
| 16 | Fleet dashboard | Static handler + `GET /api/scenarios` | The whole frontend is served from one stdlib handler with no build step — `backend/server.py:790` |
| 17 | Real-time positions | Telemetry frame `robots[].x/y/th/v` | 10 Hz recorded frames in metres — `src/world.py:879`, `src/settings.py:112` |
| 18 | Battery status | Telemetry frame `robots[].batt` | State of charge as a 0–1 fraction of `battery_full_wh` — `src/world.py:881`, `src/settings.py:38` |
| 19 | Zero inter-robot collisions | `summary.contacts_robot_robot`, `summary.min_separation_m` | Per-kind contact counts and the separation distribution — `src/main.py:416`, `src/metrics.py:109` |
| 20 | ≥20% task-time reduction | `summary.makespan_s` + `summary.completed_all` | The API returns single-run inputs only; the paired comparison is a CLI/benchmark path — `src/metrics.py:289`, `src/main.py:804`. See [12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md). |

---

## 1. Server basics

### 1.1 It is `http.server`, and that is the design

There is no Flask, no FastAPI, no `npm install`, no bundler and no build step. The server is a `ThreadingHTTPServer` from the standard library (`backend/server.py:37`, `backend/server.py:811`), and the frontend is plain ES modules served as files. The stated reason is that "adding a web framework would put a build step between the judges and the demo, and there is nothing here a threading HTTP server cannot do" (`backend/server.py:3-5`).

The practical consequence for an evaluator: a clean Python 3 checkout runs the dashboard with one command and no network access.

### 1.2 Starting it

| Command | Effect | Citation |
|---|---|---|
| `python backend/server.py` | Serves on `http://127.0.0.1:8000` | `backend/server.py:828` |
| `python backend/server.py 9000` | Same, on port 9000 (`sys.argv[1]`) | `backend/server.py:828` |
| `python -m backend.server` | Equivalent module form | `README.md:272` |

`serve()` defaults are `host="127.0.0.1"`, `port=8000` (`backend/server.py:810`). The bind address is loopback only; there is no flag to bind `0.0.0.0`, and reaching the dashboard from another machine requires editing `serve()` or fronting it with a proxy. On start it prints the URL, the frontend directory, and the full list of scenarios, route policies and allocation policies it will accept (`backend/server.py:813-818`).

### 1.3 Routing table

`do_GET` (`backend/server.py:282`) and `do_POST` (`backend/server.py:310`) are hand-written route chains — path equality, no router, no path parameters. `do_HEAD` delegates to `do_GET` and the body is suppressed at write time (`backend/server.py:279-280`, `backend/server.py:268`).

| Method | Path | Handler | Section |
|---|---|---|---|
| `GET` | `/api/scenarios` | `_api_scenarios` — `backend/server.py:286`, `backend/server.py:588` | [§4](#4-get-apiscenarios) |
| `GET` | `/api/train/status` | `_api_train_status` — `backend/server.py:288`, `backend/server.py:722` | [§8.2](#82-get-apitrainstatus) |
| `GET` | `/api/train/model` | `_api_train_model` — `backend/server.py:290`, `backend/server.py:734` | [§8.3](#83-get-apitrainmodel) |
| `GET` | `/api/run` | Always `405` with `Allow: POST` — `backend/server.py:292-295` | [§3](#3-post-apirun) |
| `GET` | anything else | `_static` — `backend/server.py:296`, `backend/server.py:790` | [§9](#9-static-file-serving) |
| `POST` | `/api/run` | `_api_run` — `backend/server.py:347`, `backend/server.py:616` | [§3](#3-post-apirun) |
| `POST` | `/api/scenarios/custom` | `_api_scenarios_custom` — `backend/server.py:321`, `backend/server.py:411` | [§5](#5-post-apiscenarioscustom) |
| `POST` | `/api/train` | `_api_train_start` — `backend/server.py:315`, `backend/server.py:649` | [§8.1](#81-post-apitrain) |
| `POST` | `/api/train/cancel` | `_api_train_cancel` — `backend/server.py:317`, `backend/server.py:756` | [§8.4](#84-post-apitraincancel) |
| `POST` | `/api/model` | `_api_model_upload` — `backend/server.py:319`, `backend/server.py:771` | [§8.5](#85-post-apimodel) |
| `POST` | anything else | `404 {"error": "not found: <path>"}` — `backend/server.py:323-324` | — |

There are no `PUT`, `PATCH` or `DELETE` handlers. `BaseHTTPRequestHandler` answers those with its own `501 Unsupported method`.

### 1.4 Response envelope and headers

Every JSON response goes through `_json` → `_send` (`backend/server.py:271`, `backend/server.py:247`). Errors are always a flat object with an `error` key; two endpoints add extra keys (`hint` on an unknown model — `backend/server.py:631`; `disconnected` on a disconnected custom floor — `backend/server.py:571`; `job`/`hint` on a training conflict — `backend/server.py:702-704`).

Headers set on every response (`backend/server.py:249-264`):

| Header | Value | Reason |
|---|---|---|
| `Content-Type` | `application/json` for API, guessed for static | `backend/server.py:272`, `backend/server.py:804` |
| `Content-Length` | exact byte length | `backend/server.py:251` |
| `Cache-Control` | `no-store` | A rebuilt frontend must not keep serving the old one — `backend/server.py:252-254` |
| `X-Content-Type-Options` | `nosniff` | `backend/server.py:255` |
| `X-Frame-Options` | `DENY` | `backend/server.py:256` |
| `Referrer-Policy` | `no-referrer` | `backend/server.py:257` |
| `Cross-Origin-Opener-Policy` | `same-origin` | `backend/server.py:258` |
| `Content-Security-Policy` | `default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'` | `backend/server.py:259-264` |

There is **no** `Access-Control-Allow-Origin` header and **no** `Content-Encoding` — responses are same-origin only and uncompressed. A long run's payload therefore crosses the socket at full size (see [§6.6](#66-payload-size)).

`protocol_version = "HTTP/1.1"` (`backend/server.py:243`), so keep-alive is on. That is why `_drain` exists: an unread request body would leave the next request on the same connection parsing mid-stream, which surfaces as a connection abort far from the endpoint that caused it (`backend/server.py:382-391`).

### 1.5 Concurrency rules

| Rule | Mechanism | Citation |
|---|---|---|
| One simulation at a time | `_SIM_LOCK` is held across the whole `run_for_dashboard` call | `backend/server.py:59`, `backend/server.py:645-646` |
| One training job at a time | `409` if any job is `running` | `backend/server.py:699-704` |
| Training never takes `_SIM_LOCK` | Runs on its own daemon thread | `backend/server.py:63-65`, `backend/server.py:716-717` |
| The server stays responsive while training | Regression-tested to answer `/api/scenarios` in under 10 s during a run | `tests/test_server.py:249-262` |

A second `POST /api/run` while one is running does not fail — it blocks on `_SIM_LOCK` until the first finishes. Requests are not queued with a bound, so a client that retries on timeout can stack simulations behind the lock.

### 1.6 Everything the server holds is in memory

| Store | Contents | Bound | Lost on restart | Citation |
|---|---|---|---|---|
| `CUSTOM_SCENARIOS` | Custom floors by `custom_<hex8>` id | unbounded | yes | `backend/server.py:76` |
| `_MODELS` / `_MODEL_ORDER` | Trained/uploaded `PolicyNet`s by 12-hex id | `MAX_MODELS = 16`, oldest evicted | yes | `backend/server.py:73-75`, `backend/server.py:85-93` |
| `_JOBS` | Training jobs by 12-hex id | finished jobs pruned to the last 8 | yes | `backend/server.py:71`, `backend/server.py:711-714` |

There is no database, no disk persistence and no session state. This is called out because it produces a real class of confusing failures — see the warning in [§5.6](#56-the-scenario-store-is-in-memory).

---

## 2. Endpoint index

| # | Method | Path | Purpose | Success | Requirement |
|---|---|---|---|---|---|
| 1 | `POST` | `/api/run` | Run one simulation, return map + every frame + summary | `200` | 16, 17, 18, 19, 20 |
| 2 | `GET` | `/api/scenarios` | List runnable showcases, custom floors, policies, allocators | `200` | 16 |
| 3 | `POST` | `/api/scenarios/custom` | Validate and store a hand-drawn floor | `200` | 2, 11, 12 |
| 4 | `POST` | `/api/train` | Start a BIOS_4 neuroevolution job | `202` | 15 |
| 5 | `GET` | `/api/train/status` | Poll a job's generation history | `200` | 15 |
| 6 | `GET` | `/api/train/model` | Download the trained model as a file | `200` | 15 |
| 7 | `POST` | `/api/train/cancel` | Request cooperative cancellation | `202`/`200` | 15 |
| 8 | `POST` | `/api/model` | Upload a previously trained model | `200` | 15 |
| 9 | `GET` | `/api/run` | Method guard, always refuses | `405` | — |
| — | `GET` | `/*` | Static frontend files | `200` | 16 |

---

## 3. `POST /api/run`

Runs one `(scenario, policy, allocation policy, robots, seed, duration)` combination to completion and returns the entire recording in one response. This is the endpoint the dashboard is built on and the one every benchmark script drives.

It is **playback, not streaming**. The simulation runs hundreds of times faster than realtime, so a live socket would mean throttling it back down to wall-clock for no benefit, and a live stream can only be watched once; a recording can be scrubbed, paused on the frame where two robots negotiate a chokepoint, and replayed against another policy on the same seed (`backend/server.py:7-13`, `src/main.py:573-577`).

### 3.1 The defaults trap — read this before benchmarking

> **`parse_run_request` supplies `duration = 120`, `robots = 4` and `seed = 0` regardless of which scenario you asked for** (`backend/server.py:192-197`). Showcase profiles do **not** feed their own `robots`/`seed`/`duration` into the run; those fields exist only in the `/api/scenarios` listing for the UI to copy into its form (`backend/server.py:603-607`, `frontend/js/main.js:499-506`).
>
> So a bare `POST /api/run {"scenario": "showcase_grand_challenge"}` silently runs **4 robots for 120 simulated seconds with 8 tasks** instead of the flagship's 10 robots, 800 seconds and 20 tasks — and returns `200` with `summary.completed_all = false`, which reads as "the policy failed" rather than "you truncated the run". Measured against the current tree:
>
> ```
> POST /api/run {"scenario":"showcase_grand_challenge"}
>   -> 200, meta.duration_s = 120.0, meta.robots = 4, meta.tasks = 8,
>      summary.completed_all = false
> ```
>
> This has already produced two false benchmark conclusions.
>
> **The rule: always pass `duration`, `robots` and `seed` explicitly when driving `/api/run` from anything other than the dashboard form.** Take the correct values from `GET /api/scenarios` (`showcase[].robots`, `showcase[].seed`, `showcase[].duration`) or from `SHOWCASE_SCENARIOS` (`src/scenarios.py:794-825`).

The response does expose the truncation if you look: `meta.requested_robots` and `meta.requested_duration_s` echo what the server decided (`src/main.py:686-687`), and `meta.robots`/`meta.duration_s` report what actually ran. They will agree even when both are wrong, because the default was applied before `run_for_dashboard` was called.

Correct values for the five showcases, from `src/scenarios.py:794-825`:

| Scenario id | `robots` | `seed` | `duration` |
|---|---|---|---|
| `showcase_open_floor` | 4 | 4 | 180 |
| `showcase_chokepoint` | 4 | 7 | 320 |
| `showcase_human` | 5 | 7 | 520 |
| `showcase_dead_zone` | 6 | 4 | 650 |
| `showcase_grand_challenge` | 10 | 1 | 800 |

### 3.2 Request

`Content-Type: application/json` is mandatory (`backend/server.py:325-327`). `Content-Length` is mandatory (`backend/server.py:328-330`). The body must be a JSON **object** (`backend/server.py:164-165`). Maximum body: `MAX_REQUEST_BYTES = 8 * 1024` bytes (`backend/server.py:139`, `backend/server.py:335-336`).

`json.loads` is called with a `parse_constant` hook that raises, so `NaN`, `Infinity` and `-Infinity` are rejected as malformed JSON rather than accepted as floats (`backend/server.py:340-344`).

| Field | Type | Required | Default | Validation | Citation |
|---|---|---|---|---|---|
| `scenario` | string | no | `"open_floor_control"` | Must be a key of `SCENARIOS`, or a live `custom_*` id | `backend/server.py:173`, `backend/server.py:180-185` |
| `policy` | string | no | `"BIOS_PIBT.6"` | Must be in `POLICIES` after alias mapping | `backend/server.py:174-178`, `backend/server.py:186-187` |
| `allocation_policy` | string | no | `"auction_bundle"` | Must be in `ALLOCATION_POLICIES` | `backend/server.py:179`, `backend/server.py:188-190` |
| `robots` | int (or numeric string) | no | **`4`** | `2 ≤ robots ≤ 100`; a float must be integral | `backend/server.py:192`, `backend/server.py:201-209` |
| `seed` | int (or numeric string) | no | **`0`** | `0 ≤ seed ≤ 2147483647`; a float must be integral | `backend/server.py:193`, `backend/server.py:210-211` |
| `duration` | float (or numeric string) | no | **`120`** | finite, `10.0 ≤ duration ≤ 900.0` simulated seconds | `backend/server.py:197`, `backend/server.py:205-214` |
| `model` | string \| null | only for `BIOS_4` | `null` | Must be a string if present; must resolve in `_MODELS` | `backend/server.py:219-221`, `backend/server.py:624-632` |

Cross-field rule: `robots * duration ≤ MAX_ROBOT_SECONDS = 24000` (`backend/server.py:147`, `backend/server.py:215-217`). This preserves the 100-AMR demonstration while bounding one synchronous job to roughly the old 24 × 900 envelope. Note that `robots = 100` and `duration = 900` are each individually legal and together are not.

Type coercion notes, all confirmed by `tests/test_dashboard.py:45-49`:

- Numeric **strings** are accepted: `{"robots": "100", "duration": "120", "seed": "9"}` parses to `100`, `120.0`, `9`.
- Booleans, `null`, objects and arrays are refused for every scalar field with `"<name> must be a scalar value"` — `bool` is excluded explicitly because `isinstance(True, int)` is true in Python (`backend/server.py:169-170`).
- Non-integral floats are refused rather than truncated: `{"robots": 3.5}` → `"robots and seed must be whole numbers"` (`backend/server.py:201-203`).
- Out-of-range values are **refused, not clamped** (`backend/server.py:163`, `tests/test_dashboard.py:31-37`). This matters: a clamped request would return a `200` describing a run you did not ask for.

### 3.3 Accepted values

`policy` — 13 route policies (`src/amr.py:86-87`, sorted as `/api/scenarios` returns them):

`BIOS_1.0.0`, `BIOS_4`, `BIOS_PIBT.1`, `BIOS_PIBT.2`, `BIOS_PIBT.3`, `BIOS_PIBT.5`, `BIOS_PIBT.6`, `central`, `decentralized`, `hierarchical`, `prioritized_space_time_astar`, `stop_and_wait`, `stop_and_wait_competition`.

Two legacy display names are rewritten before validation (`backend/server.py:175-178`):

| Sent | Runs as |
|---|---|
| `Already-Established_algorithm` | `prioritized_space_time_astar` |
| `stop-and-wait(Competition)` | `stop_and_wait_competition` |

`allocation_policy` — 4 values (`src/task_allocation.py:9-18`): `preassigned`, `auction`, `auction_bundle`, `hungarian`. `preassigned` resolves to `None` internally and uses the scenario's per-robot queues instead of announcing tasks (`src/main.py:65-67`).

`scenario` — every key of `SCENARIOS` (`src/scenarios.py:828-843`), which is **18 ids**: the 13 direct builders plus the 5 showcases.

| Scenario id | Listed by `/api/scenarios`? |
|---|---|
| `crossing_chokepoint`, `dense_aisles`, `human_in_aisle`, `manager_dies`, `dead_zone_infra`, `dead_zone_mesh`, `open_floor_control`, `blocked_aisle`, `robot_failure_reassignment`, `partition_recovery`, `sih_acceptance_overlap`, `energy_acceptance`, `seed_99_congestion` | no |
| `showcase_open_floor`, `showcase_chokepoint`, `showcase_human`, `showcase_dead_zone`, `showcase_grand_challenge` | yes |

`/api/scenarios` returns only the showcases and custom floors (`backend/server.py:603-608`), but `/api/run` accepts all 18. The listing under-reports the accepted set; the authoritative list is printed to stderr at startup (`backend/server.py:815`).

### 3.4 `BIOS_4` requires a model

`policy = "BIOS_4"` with no `model` is refused with `400 "BIOS_4 needs a trained model: train one or upload one first"` (`backend/server.py:222-228`, `tests/test_server.py:146-153`). The refusal is deliberate: an untrained BIOS_4 is *legal* — it degrades to always-hold and the liveness valve still moves the fleet — and nothing on the screen would tell you which of the two you were looking at.

A `model` id that is not in the store is `404`, not `400`: the request is well formed, the model is simply not here (`backend/server.py:627-632`). The response carries a `hint` saying models are held in memory and lost across restarts (`tests/test_server.py:155-158`).

### 3.5 Seed 99 substitutes the scenario

If `seed == 99`, the requested scenario is discarded and `seed_99_congestion()` runs instead — a fixed six-AMR launch gridlock (`src/main.py:580-586`, `src/scenarios.py:609-628`). `robots` is ignored (the workload is fixed at 6). `duration` **is** still applied, because the override at `src/main.py:661` only skips custom scenarios.

This is visible rather than silent: `meta.seed_99_demo` is `true`, `meta.requested_scenario` keeps the id you asked for, `meta.scenario` and `map.name` become `seed_99_congestion`, and a `demo_evidence` block is attached (`src/main.py:685-689`, `src/main.py:721`). It is still a surprise for an API client, because a request for `showcase_open_floor` at seed 99 returns a different warehouse.

### 3.6 Example request

```json
{
  "scenario": "showcase_grand_challenge",
  "policy": "BIOS_PIBT.6",
  "allocation_policy": "auction_bundle",
  "robots": 10,
  "seed": 1,
  "duration": 800
}
```

### 3.7 Response `200`

Four top-level keys plus an optional fifth (`src/main.py:681-722`):

| Key | Type | Meaning |
|---|---|---|
| `map` | object | The static warehouse — see [§3.8](#38-response-map) |
| `meta` | object | What actually ran — see [§3.9](#39-response-meta) |
| `frames` | array | 10 Hz telemetry recording — see [§6](#6-the-telemetry-frame-schema) |
| `summary` | object | The run's metrics — see [§7](#7-the-run-summary-schema) |
| `demo_evidence` | object \| null | Non-null only for seed 99 — see [§3.10](#310-response-demo_evidence) |

### 3.8 Response `map`

From `Warehouse.to_json()` (`src/environment.py:88-96`) plus two pedestrian-apron keys added by the runner (`src/main.py:668-680`).

| Field | Type | Meaning |
|---|---|---|
| `name` | string | Warehouse name (equals the scenario name for built-ins) |
| `width` | int | Floor width in cells |
| `height` | int | Floor height in cells |
| `grid` | int[height][width] | Row-major cell codes, `grid[y][x]` |
| `stations` | `[x, y][]` | Pick/drop station cells |
| `docks` | `[x, y][]` | Charging dock cells |
| `pedestrian_apron` | bool | Whether any worker in the first frame walks the perimeter apron |
| `pedestrian_apron_offset_m` | float | Present **only** when `pedestrian_apron` is true |
| `pedestrian_apron_width_m` | float | Present **only** when `pedestrian_apron` is true |

Cell codes (`src/environment.py:17-20`) — the same four values `/api/scenarios/custom` accepts:

| Code | Name | Passable |
|---|---|---|
| `0` | `FREE` | yes |
| `1` | `RACK` | never |
| `2` | `STATION` | yes; tasks target these |
| `3` | `DOCK` | yes; charge pad |

Poses in `frames` are in **metres**, not cells. Convert with `meta.cell_m` (1.4 m — `src/settings.py:305`): `cell_x = floor(robot.x / meta.cell_m)`. This is regression-tested (`tests/test_dashboard.py:396-413`) because treating the 1.4 m pitch as one metre per cell is the mistake that keeps recurring (`tests/test_dashboard.py:280`).

### 3.9 Response `meta`

Every field, from `src/main.py:683-718`:

| Field | Type | Meaning |
|---|---|---|
| `scenario` | string | Name of the scenario that **ran** (`sc.name`) |
| `requested_scenario` | string | The id you sent — differs under seed 99 |
| `policy` | string | Route policy after alias mapping |
| `allocation_policy` | string | Task allocator as requested |
| `seed` | int | Seed used for world, network and builders |
| `requested_robots` | int \| null | The `robots` value the server resolved |
| `requested_duration_s` | float \| null | The `duration` value the server resolved |
| `robots` | int | Robots that actually ran (`sc.n_robots`) |
| `duration_s` | float | Simulated seconds the run was allowed |
| `seed_99_demo` | bool | Whether the seed-99 substitution fired |
| `tasks` | int | Announced task count, else the scenario's `n_tasks` |
| `humans` | int | Mapped worker routes in the scenario |
| `kill_manager_at` | float \| null | Scripted manager-failure time, if any |
| `cell_m` | float | Metres per grid cell (1.4) |
| `pose_units` | string | Always `"metres"` |
| `robot_diameter_m` | float | `2 × robot.radius_m` = 0.70 m (`src/settings.py:18`) |
| `has_manager` | bool | Whether this policy is served by a fleet manager at all |
| `dead_zones` | array | Radio dead zones declared by the scenario |
| `energy_reserve_frac` | float | Battery reserve fraction the auction respects |
| `energy_uncertainty_frac` | float | Energy-estimate margin the auction respects |
| `tasks_catalog` | object[] | One entry per announced task: `id`, `pick`, `drop`, `cargo_type`, `cargo_weight`, `priority`, `deadline` |
| `model` | object \| null | Provenance `meta` of the BIOS_4 model, or `null` |

`has_manager` is the field that answers requirement 6 honestly: it is computed from `MANAGED_POLICIES` (`src/main.py:104`), so a peer-to-peer policy reports `false` and the UI can say a missing manager is the design rather than a failure. `tests/test_server.py:176` asserts `BIOS_4` returns `false`.

`meta.model` exists so the dashboard can say **which** model produced a run; a BIOS_4 result with no model behind it is an untrained control, not a policy (`src/main.py:714-717`). Upload provenance survives round-trip (`tests/test_server.py:175`).

### 3.10 Response `demo_evidence`

`null` unless `seed == 99` (`src/main.py:667`). When present (`src/main.py:551-564`):

| Field | Type | Meaning |
|---|---|---|
| `kind` | string | Always `"six_amr_launch_gridlock"` |
| `robots` | int | `6` |
| `peak_simultaneously_blocked` | int | Largest number of the six AMRs blocked in any one frame |
| `full_gridlock_observed` | bool | Whether that peak reached 6/6 |
| `full_gridlock_detected_s` | float \| null | First frame time at 6/6 standstill |
| `first_release_s` | float \| null | First later frame with fewer than 6 blocked |
| `first_release_latency_s` | float \| null | `first_release_s − full_gridlock_detected_s` |
| `measurement` | string | `"blocked state or non-empty wait-for owner in recorded 10 Hz telemetry"` |

"Blocked" is derived from the same recorded telemetry the Fleet panel renders — `fleet[].blocked_on is not null` or `fleet[].state in {"blocked", "retreat"}` (`src/main.py:528-533`). A scripted label saying "deadlock resolved" would prove nothing (`src/main.py:520`).

### 3.11 Errors

| Code | Trigger | Body | Citation |
|---|---|---|---|
| `400` | Body is not a JSON object | `{"error": "request body must be a JSON object"}` | `backend/server.py:164-165`, `tests/test_dashboard.py:264-270` |
| `400` | Body is not valid JSON, or contains `NaN`/`Infinity` | `{"error": "request body must be valid JSON"}` | `backend/server.py:340-346` |
| `400` | `Content-Length` header is not an integer | `{"error": "invalid Content-Length"}` | `backend/server.py:331-334` |
| `400` | A scalar field is a bool/null/object/array | `{"error": "<name> must be a scalar value"}` | `backend/server.py:169-170` |
| `400` | Unknown scenario | `{"error": "unknown scenario 'zzz'"}` | `backend/server.py:185` |
| `400` | Unknown `custom_*` id | `{"error": "unknown custom scenario 'custom_deadbeef'"}` | `backend/server.py:183` |
| `400` | Unknown policy | `{"error": "unknown policy 'zzz'"}` | `backend/server.py:187` |
| `400` | Unknown allocator | `{"error": "unknown task allocation policy 'zzz'"}` | `backend/server.py:189-190` |
| `400` | `robots`/`seed`/`duration` not numeric | `{"error": "robots, seed and duration must be numbers"}` | `backend/server.py:198-200` |
| `400` | Non-integral `robots`/`seed` | `{"error": "robots and seed must be whole numbers"}` | `backend/server.py:203` |
| `400` | Non-finite duration | `{"error": "duration must be finite"}` | `backend/server.py:205-206` |
| `400` | `robots` outside 2..100 | `{"error": "robots must be between 2 and 100"}` | `backend/server.py:207-209` |
| `400` | `seed` outside 0..2147483647 | `{"error": "seed must be between 0 and 2147483647"}` | `backend/server.py:210-211` |
| `400` | `duration` outside 10..900 | `{"error": "duration must be between 10 and 900 seconds"}` | `backend/server.py:212-214` |
| `400` | `robots × duration > 24000` | `{"error": "requested workload exceeds 24000 robot-seconds"}` | `backend/server.py:215-217` |
| `400` | `model` is present but not a string | `{"error": "model must be a model id string"}` | `backend/server.py:220-221` |
| `400` | `BIOS_4` with no model | `{"error": "BIOS_4 needs a trained model: train one or upload one first"}` | `backend/server.py:227-228` |
| `404` | `model` id not in the store | `{"error": "unknown model '…'", "hint": "models are held in memory and are lost across restarts - upload the .json again"}` | `backend/server.py:629-632` |
| `404` | `custom_*` id vanished between validation and run | `{"error": "unknown custom scenario '…'"}` | `backend/server.py:637-638` |
| `405` | `GET /api/run` | `{"error":"use POST for simulation runs"}` with `Allow: POST` | `backend/server.py:292-295`, `tests/test_dashboard.py:256-259` |
| `411` | No `Content-Length` | `{"error": "Content-Length is required"}` | `backend/server.py:328-330` |
| `413` | Body over 8192 bytes | `{"error": "request body is too large"}` | `backend/server.py:335-336` |
| `415` | `Content-Type` is not `application/json` | `{"error": "Content-Type must be application/json"}` | `backend/server.py:325-327` |
| `500` | Any uncaught exception in the runner | `{"error": "internal server error"}`, traceback to stderr | `backend/server.py:350-355` |

The `500` is reachable from a request that passes every validation rule — see [§11](#11-gaps-found-while-writing-this-document).

---

## 4. `GET /api/scenarios`

The dashboard's bootstrap call: what can be run, and with what. No parameters, no body, no cache (`backend/server.py:286`, `backend/server.py:588-614`).

### 4.1 Response `200`

| Field | Type | Meaning | Citation |
|---|---|---|---|
| `showcase` | object[] | Showcase profiles **followed by** custom floors | `backend/server.py:608-611` |
| `scenarios` | string[] | The `id` of every entry in `showcase`, in the same order | `backend/server.py:610` |
| `policies` | string[] | `sorted(POLICIES)` — 13 route policies | `backend/server.py:612` |
| `allocation_policies` | string[] | `sorted(ALLOCATION_POLICIES)` — 4 allocators | `backend/server.py:613` |

**Custom scenarios come back inside both `showcase` and `scenarios`.** `all_showcase = showcase + custom_scenarios` is built first, and `scenarios` is derived from it (`backend/server.py:608-610`). The frontend relies on this: after saving a floor it re-reads the library rather than splicing the new entry in by hand, "the server already returns custom scenarios inside `showcase`" (`frontend/js/main.js:1798-1799`). `tests/test_server.py:96-107` asserts a saved floor appears in `showcase` with its title, robot count and duration.

Showcase entry fields (the profile minus its `builder` callable — `backend/server.py:603-607`, `src/scenarios.py:794-825`):

| Field | Type | Meaning |
|---|---|---|
| `id` | string | Scenario id for `/api/run` |
| `title` | string | Display name |
| `eyebrow` | string | One-line category label |
| `description` | string | What the scenario demonstrates |
| `robots` | int | The robot count this showcase is designed for |
| `humans` | int | Mapped workers on the floor |
| `seed` | int | The seed this showcase is designed for |
| `duration` | int | Simulated seconds this showcase needs |
| `accent` | string | UI accent colour token |

Custom entry fields (`backend/server.py:592-602`) use the same shape so a client can treat both alike:

| Field | Value |
|---|---|
| `id` | `custom_<hex8>` |
| `title` | The name you saved |
| `eyebrow` | Always `"Custom Builder"` |
| `description` | `"Custom layout · <width>×<height>"` |
| `robots` | `len(starts)` |
| `humans` | `len(humans)` |
| `seed` | The seed you saved |
| `duration` | `int(duration)` you saved |
| `accent` | Always `"lime"` |

> **The `robots`, `seed` and `duration` in a showcase or custom entry are advisory.** They describe what the scenario needs; the server does not apply them when you run it. See [§3.1](#31-the-defaults-trap--read-this-before-benchmarking). For a custom floor they are worse than advisory — the saved values are never used at all ([§11](#11-gaps-found-while-writing-this-document)).

### 4.2 Errors

None specific. Any uncaught exception is `500` (`backend/server.py:303-308`).

---

## 5. `POST /api/scenarios/custom`

Validates a hand-drawn warehouse floor, builds a `Warehouse` from it, checks that it is actually navigable, stores it in memory and returns an id you can pass as `scenario` to `/api/run` (`backend/server.py:411-586`).

The validation is strict on purpose: a floor that saves but cannot be finished produces a `200` run that only ever ends in a timeout, and the timeout looks like a policy failure.

### 5.1 Request

`Content-Type: application/json` and `Content-Length` are both required; the body cap is the same `MAX_REQUEST_BYTES = 8 * 1024` as `/api/run` (`backend/server.py:412-433`).

| Field | Type | Required | Default | Validation | Citation |
|---|---|---|---|---|---|
| `width` | int | no | `20` | `4 ≤ width ≤ 64` | `backend/server.py:451`, `backend/server.py:149-150` |
| `height` | int | no | `20` | `4 ≤ height ≤ 64` | `backend/server.py:452` |
| `grid` | int[][] | no | `[]` | Exactly `height` rows, each exactly `width` cells, every cell in `{0,1,2,3}` | `backend/server.py:453-468` |
| `stations` | `[x,y][]` | no | `[]` | `coordinates()` + must exactly equal the grid's `STATION` cells + at least one | `backend/server.py:492`, `backend/server.py:496-497`, `backend/server.py:512-514` |
| `docks` | `[x,y][]` | no | `[]` | `coordinates()` + must exactly equal the grid's `DOCK` cells + at least one | `backend/server.py:493`, `backend/server.py:498-499`, `backend/server.py:515-517` |
| `starts` | `[x,y][]` | no | `[]` | `coordinates()` + every cell must be `FREE` + `2 ≤ count ≤ 100` | `backend/server.py:494`, `backend/server.py:500-502`, `backend/server.py:518-519` |
| `humans` | `[x,y][]` | no | `[]` | `coordinates()` + every cell must be `FREE` | `backend/server.py:495`, `backend/server.py:520-523` |
| `seed` | int | no | `0` | Whole number, `0 ≤ seed ≤ 2147483647` | `backend/server.py:525-529` |
| `duration` | number | no | `300.0` | Finite, `10.0 ≤ duration ≤ 900.0` | `backend/server.py:530-539` |
| `name` | string | no | `"Custom floor"` | Stripped; 1–80 characters | `backend/server.py:541-546` |

### 5.2 The `whole_number()` and `coordinates()` helpers

`whole_number(name, default, lower, upper)` (`backend/server.py:441-448`) is used for `width` and `height`. It rejects `bool` explicitly before the `int` check, so `true` is not silently `1`.

`coordinates(name)` (`backend/server.py:470-490`) is applied identically to `stations`, `docks`, `starts` **and** `humans`. In order, it requires: the value is a list; each item is a list of exactly length 2; both members are non-bool integers; the cell is inside the declared floor; and no cell is repeated.

### 5.3 Exact error strings

Every one of these is emitted verbatim with `400` (`backend/server.py:547-548`). These are the strings from the code, not paraphrases.

| Error string | Trigger | Citation |
|---|---|---|
| `request body must be a JSON object` | Top-level value is not an object | `backend/server.py:438-439` |
| `width must be a whole number` / `height must be a whole number` | Non-int or bool | `backend/server.py:444` |
| `width must be between 4 and 64` / `height must be between 4 and 64` | Out of side bounds | `backend/server.py:446-447` |
| `grid must contain exactly <height> rows` | Not a list, or wrong row count | `backend/server.py:454-455` |
| `grid row <y> must contain exactly <width> cells` | Row not a list, or wrong length | `backend/server.py:458-460` |
| `grid cell (<x>, <y>) must be one of 0, 1, 2 or 3` | Cell is a bool, a non-int, or outside `{0,1,2,3}` | `backend/server.py:463-466`, `tests/test_server.py:120-124` |
| `<name> must be a list of [x, y] cells` | `stations`/`docks`/`starts`/`humans` is not a list | `backend/server.py:472-473` |
| `<name>[<i>] must be an [x, y] cell` | Item is not a 2-element list | `backend/server.py:476-478` |
| `<name>[<i>] coordinates must be whole numbers` | A coordinate is a bool or non-int | `backend/server.py:480-483` |
| `<name>[<i>] is outside the <width>x<height> floor` | `x` or `y` out of range | `backend/server.py:484-486` |
| `<name> contains duplicate cells` | Same `[x,y]` listed twice | `backend/server.py:488-489` |
| `custom floor needs at least one station` | `len(stations) == 0` | `backend/server.py:496-497` |
| `custom floor needs at least one charging dock` | `len(docks) == 0` | `backend/server.py:498-499` |
| `custom floor needs between 2 and 100 AMR starts` | `len(starts)` outside `MIN_ROBOTS..MAX_ROBOTS` | `backend/server.py:500-502`, `tests/test_server.py:110-118` |
| `stations must exactly match the station cells in the grid` | `set(stations) != {cells with value 2}` | `backend/server.py:504-514` |
| `docks must exactly match the charging-dock cells in the grid` | `set(docks) != {cells with value 3}` | `backend/server.py:509-517` |
| `every AMR start must be on an empty floor cell` | Any start is not on a `FREE` cell | `backend/server.py:518-519` |
| `worker at <x>,<y> is not on an empty floor cell` | First offending human cell, reported by coordinate | `backend/server.py:520-523` |
| `seed must be a whole number` | Bool or non-int | `backend/server.py:526-527` |
| `seed must be between 0 and 2147483647` | Out of range | `backend/server.py:528-529` |
| `duration must be a number` | Bool or non-numeric | `backend/server.py:531-532` |
| `duration must be finite` | `inf` reached through arithmetic | `backend/server.py:534-535` |
| `duration must be between 10 and 900 seconds` | Out of range | `backend/server.py:536-539` |
| `name must be text` | Not a string | `backend/server.py:542-543` |
| `name must contain between 1 and 80 characters` | Empty after `.strip()`, or over 80 | `backend/server.py:545-546` |
| `cannot build warehouse: <exception>` | `Warehouse(...)` raised | `backend/server.py:550-554` |
| `all AMR starts, stations, charging docks and workers must be connected` | Flood fill failed — see below | `backend/server.py:568-572` |

The station/dock rules are bidirectional set equality, not containment. Painting a `2` in the grid without listing it in `stations` fails, and listing a station whose grid cell is not `2` fails, with the same message.

### 5.4 The connectivity check

After the `Warehouse` is built, a flood fill runs from `starts[0]` using `env.neighbors()` — the same adjacency the planner uses (`backend/server.py:556-564`). Every cell in `starts + stations + docks + humans` must be in the reachable set.

Failure returns `400` with an extra key naming the offenders:

```json
{
  "error": "all AMR starts, stations, charging docks and workers must be connected",
  "disconnected": [[5, 2], [6, 3]]
}
```

`tests/test_server.py:127-135` proves it by walling off column 4 of an 8×6 floor.

The check is anchored at `starts[0]`, so a floor split into two navigable halves is rejected even if each half is internally fine — which is correct, because tasks are announced to the whole fleet.

### 5.5 Response `200`

| Field | Type | Meaning |
|---|---|---|
| `id` | string | `custom_<8 hex chars>` — pass this as `scenario` to `/api/run` |
| `name` | string | The stripped name |
| `width` | int | As submitted |
| `height` | int | As submitted |
| `robots` | int | `len(starts)` |

Citation: `backend/server.py:574-586`.

### 5.6 The scenario store is in memory

> **`CUSTOM_SCENARIOS` is a plain module-level `dict` (`backend/server.py:76`). It is never written to disk.**
>
> Restarting the server loses every custom floor. The browser, meanwhile, may still be holding the id in its scenario dropdown, and a run against it fails with `400 "unknown custom scenario 'custom_...'"` (`backend/server.py:182-183`) — or `404` with the same text if the entry disappears between validation and the run (`backend/server.py:637-638`). Neither message says "the server restarted", which is the actual cause almost every time.
>
> Unlike `_MODELS` (capped at 16) and `_JOBS` (pruned to 8), `CUSTOM_SCENARIOS` has **no eviction and no size cap**. Each entry holds a whole `Warehouse` object. A script posting floors in a loop grows the process without bound.
>
> The workaround is to keep the request body that created the floor and re-POST it after a restart. The id will be different.

### 5.7 What the run actually uses

When you run a `custom_*` scenario, `_api_run` injects the stored floor into the request under private keys `_custom_env`, `_custom_starts`, `_custom_humans`, `_custom_duration`, `_custom_seed` (`backend/server.py:639-644`), which `run_for_dashboard` reads from `**extra` (`src/main.py:588-593`).

The runner then **generates** a workload rather than using one you supplied (`src/main.py:598-627`): pick and drop cells are drawn at random from the stations and docks, cargo type from `{normal, fragile, heavy}`, weight from `{8, 18, 36, 72}` kg, priority from 1–3, with `max(2, len(pick_cells) // n_robots)` tasks per robot, seeded by the run's seed. There is no way to specify tasks for a custom floor through the API.

Each `humans` marker becomes a two-waypoint work route: the marker itself, plus the nearest station (or dock) to it (`src/main.py:634-643`). `World.add_human` expands that pair into a closed, rack-safe circuit using the same A* the AMRs plan with, which is why one marker is enough to describe one worker (`src/main.py:628-633`).

Two behaviours to know before benchmarking a custom floor:

- **`robots` is ignored.** The fleet size is `len(starts)`. A request with `robots: 50` against a 2-start floor runs 2 robots and still returns `200`. The `robots × duration ≤ 24000` check is nonetheless evaluated against the number you sent (`backend/server.py:215`).
- **The saved `seed` and `duration` are ignored.** See [§11](#11-gaps-found-while-writing-this-document).

---

## 6. The telemetry frame schema

`frames` is the contract the dashboard renders. Anything extending the UI needs it exactly.

### 6.1 Frame rate and count

Frames are captured every `world_hz / telemetry_hz = 50 / 10 = 5` physics ticks, i.e. at **10 Hz of simulated time** (`src/main.py:350-352`, `src/settings.py:107`, `src/settings.py:112`). A `duration` of 800 s therefore yields up to ~8000 frames.

The recording stops early when the last task completes, and the completion frame is force-captured so the final visible frame agrees with the summary — without it the summary could say 16/16 while the last frame still showed 15/16 and a carried box (`src/main.py:358-369`). `tests/test_dashboard.py:416-425` locks this behaviour down.

### 6.2 Frame-level fields

A frame is `World.snapshot()` (`src/world.py:875-906`) with four keys added by `capture_trace_frame` (`src/main.py:186-221`).

| Field | Type | Meaning | Citation |
|---|---|---|---|
| `t` | float | Simulated seconds, 3 dp | `src/world.py:877` |
| `robots` | object[] | Physical state — [§6.3](#63-the-robots-record-physics) | `src/world.py:878-884` |
| `fleet` | object[] | Brain state — [§6.4](#64-the-fleet-record-brain) | `src/main.py:192-213` |
| `humans` | object[] | Warehouse workers — [§6.5](#65-humans-obstacles-and-auction_events) | `src/world.py:885-899` |
| `obstacles` | object[] | Dynamic obstacles currently present | `src/world.py:900-904` |
| `contacts` | int | **Cumulative** contact events so far this run, all kinds | `src/world.py:905` |
| `manager_alive` | bool | Whether a fleet manager exists and is alive | `src/main.py:214` |
| `tasks_completed` | int | **Unique** task ids completed so far, fleet-wide | `src/main.py:215-218` |
| `auction_events` | object[] | Auction datagrams sent since the previous frame | `src/main.py:219-220` |

`tasks_completed` counts distinct task ids, not per-robot completions. A lossy peer auction can temporarily create duplicate executors, and summing per-robot lists would declare 16/16 while a distinct task was still visibly in flight (`src/main.py:354-358`).

`auction_events` is drained on capture (`src/main.py:220`), so each datagram appears in exactly one frame.

### 6.3 The `robots[]` record (physics)

Ground truth from the world integrator, one entry per chassis (`src/world.py:878-884`).

| Field | Type | Unit | Meaning |
|---|---|---|---|
| `id` | string | — | `AMR01`, `AMR02`, … |
| `x` | float | metres | Centre x, 3 dp. Divide by `meta.cell_m` for a cell |
| `y` | float | metres | Centre y, 3 dp |
| `th` | float | radians | Heading, 3 dp |
| `v` | float | m/s | Forward speed, 3 dp |
| `batt` | float | fraction 0–1 | `battery_wh / battery_full_wh` (480 Wh full — `src/settings.py:38`), 3 dp |
| `carry` | string \| null | — | Task id physically on the chassis, else `null` |

`batt` is the field that satisfies requirement 18. It is a fraction, not a percentage and not watt-hours; multiply by 100 for a percentage and by `meta`-absent `battery_full_wh` (480) for Wh.

`carry` is set only after arrival at the pick location — the payload is on the chassis only while the brain's goal is the task's drop cell (`src/main.py:344-346`).

### 6.4 The `fleet[]` record (brain)

One entry per brain, sorted by robot id (`src/main.py:192-213`). **Join to `robots[]` on `id`** — position and battery are in `robots[]`, everything cognitive is here.

| Field | Type | Meaning | Citation |
|---|---|---|---|
| `id` | string | Robot id | `src/main.py:193` |
| `state` | string | `idle`, `to_pick`, `to_drop`, `charging`, `blocked`, `retreat` | `src/main.py:193`, `src/amr.py:92-97` |
| `mode` | string | `CENTRAL_OK` or `DEGRADED_P2P` | `src/main.py:193`, `src/amr.py:89-90` |
| `task` | string \| null | Current task id | `src/main.py:194` |
| `goal` | `[x, y]` \| null | Current goal cell | `src/main.py:195` |
| `pick` | `[x, y]` \| null | Current task's pick cell | `src/main.py:196` |
| `drop` | `[x, y]` \| null | Current task's drop cell | `src/main.py:197` |
| `cargo_type` | string \| null | `normal`, `fragile`, `heavy`, `hazardous` | `src/main.py:198` |
| `cargo_weight` | float \| null | kg | `src/main.py:199` |
| `task_priority` | int \| null | Task priority | `src/main.py:200` |
| `deadline` | float \| null | Task deadline in simulated seconds | `src/main.py:201` |
| `carry` | string \| null | Task id if the goal is the drop cell | `src/main.py:202-203` |
| `path` | `[x, y][]` | **Intent**: the next up-to-8 cells of the planned path | `src/main.py:204-205` |
| `peers` | string[] | **Position sharing**: sorted ids in the neighbour table | `src/main.py:206` |
| `blocked_on` | string \| null | Wait-for owner: the peer id (or `"gate"`) this robot is waiting behind | `src/main.py:207`, `src/amr.py:2129` |
| `priority_key` | array \| null | Published priority token, or `null` for non-PIBT policies | `src/main.py:208-209` |
| `decision` | object \| null | Most recent decision record, or `null` | `src/main.py:210` |
| `done` | int | Tasks this robot has completed | `src/main.py:211` |
| `failed` | bool | Whether this chassis is currently a scripted failure | `src/main.py:211` |

`path` and `goal` together are the intent broadcast (requirement 5); `peers` is the position-sharing evidence (requirement 4); `blocked_on` is the wait-for edge that makes a deadlock cycle visible (requirement 10).

`priority_key` is the 7-element wire form of `PriorityKey` (`src/priority.py:40-42`), **larger sorts first**:

| Index | Field | Meaning |
|---|---|---|
| 0 | `emergency` | Emergency override |
| 1 | `exiting_branch` | Robot is leaving a dead-end branch |
| 2 | `waiting_age` | Ticks spent waiting |
| 3 | `service_age` | Ticks since last service |
| 4 | `loaded` | Carrying cargo |
| 5 | `distance_bias` | Distance-derived tiebreak |
| 6 | `robot_id` | Final total-order tiebreak (string) |

It is `null` unless the policy is in `PIBT_POLICIES` (`src/main.py:208-209`, `src/amr.py:82`).

`decision` is `{t, robot, code, summary, details}` (`src/amr.py:648-654`). **It is only ever populated for `BIOS_PIBT.6`** — `_record_decision` returns immediately for every other policy (`src/amr.py:646-647`). Do not treat a `null` here as "no decision was made". The per-brain log is capped at 32 entries and the frame carries only the last one (`src/amr.py:656-657`, `src/main.py:210`).

### 6.5 `humans[]`, `obstacles[]` and `auction_events[]`

`humans[]` (`src/world.py:885-899`):

| Field | Type | Meaning |
|---|---|---|
| `id` | string | `H1`, `H2`, … |
| `x`, `y` | float | Position in metres, 3 dp |
| `th` | float | Heading in radians, 3 dp |
| `paused` | bool | Standing still |
| `mode` | string | Current walk mode |
| `yield_ticks` | int | Cumulative ticks spent yielding to an AMR |
| `work_visits` | int | Work locations reached so far |
| `distance_m` | float | Cumulative distance walked, 2 dp |
| `uses_apron` | bool | Whether this worker uses the perimeter apron |

`obstacles[]` (`src/world.py:900-904`) — the dynamic-environment evidence (requirement 2). Entries appear and disappear as scripted `ObstacleEvent`s fire and clear (`src/main.py:257-270`):

| Field | Type | Meaning |
|---|---|---|
| `id` | string | Obstacle id from the scenario |
| `x`, `y` | float | Position in metres, 3 dp |
| `r` | float | Radius in metres |

`auction_events[]` (`src/main.py:85-98`) — the actual datagrams, not a rendering of them. Always present: `type` (`TASK_NEW`, `BID`, `AWARD`, `TASK_DONE`), `src`, `seq`, `t`. Then whichever of these keys the message body carried: `task`, `cost`, `e`, `dl`, `u`, `dst`, `winner`, `future`, `active`, `ae`, `bv`. Absent keys are omitted, not nulled.

### 6.6 Payload size

There is no pagination, no streaming and no compression: the entire recording arrives in one `200`. Size scales as roughly `duration × 10 frames/s × (robots × 2 records)`. A 120-second, 4-robot run produces ~1200 frames. The flagship 800-second, 10-robot run is the largest thing the API returns; budget for tens of megabytes and a multi-minute wall-clock wait before the first byte, since the response is only written after the simulation finishes.

---

## 7. The run summary schema

`summary` is `PolicyResult.to_dict()`, which is `dataclasses.asdict` — so JSON key order follows the field declaration order in `src/metrics.py:91-207` (`src/metrics.py:209-210`). Every field is listed below. All of it is assembled in `_summarize` (`src/main.py:376-510`).

### 7.1 Identity

| Field | Unit | Computation | Citation |
|---|---|---|---|
| `policy` | — | Route policy that ran | `src/metrics.py:95` |
| `scenario` | — | `sc.name` | `src/metrics.py:96` |
| `seed` | — | Seed used | `src/metrics.py:97` |
| `allocation_policy` | — | Allocator, or `null` for `preassigned` | `src/metrics.py:98`, `src/main.py:65-67` |
| `workload_id` | — | Fingerprint of scenario+config+allocator; two runs may only be compared if these match | `src/metrics.py:99`, `src/main.py:122` |
| `sim_seconds` | s | `world.t`, 2 dp — how long the run actually integrated | `src/metrics.py:100`, `src/main.py:410` |
| `robots` | count | `len(brains)` | `src/metrics.py:101` |

### 7.2 Task outcome — the requirement-20 inputs

| Field | Unit | Computation | Citation |
|---|---|---|---|
| `tasks_completed` | count | Distinct task ids completed, fleet-wide | `src/metrics.py:103`, `src/main.py:382-388` |
| `tasks_announced` | count | Announced tasks, else `sc.n_tasks` | `src/metrics.py:104`, `src/main.py:175` |
| `makespan_s` | s | Time the last unique task finished, 2 dp | `src/metrics.py:105`, `src/main.py:412` |
| `completed_all` | bool | **`true` only if every task finished.** | `src/metrics.py:207`, `src/main.py:413` |
| `task_times` | s[] | Per task, `finished − started`, 2 dp; the **minimum** across duplicate executors | `src/metrics.py:106`, `src/main.py:383-386` |
| `throughput_per_robot_hr` | tasks/robot-hour | `tasks_completed / robot_hours`, 2 dp | `src/metrics.py:107`, `src/main.py:415` |

> **`makespan_s` is not a makespan when `completed_all` is `false`.** A run that did not finish has no makespan, so the field carries the wall-clock cutoff (`sim_seconds`) instead — recording it as if it were a makespan would silently turn a failure into a merely-slow result (`src/main.py:410-413`). **Always read `completed_all` before reading `makespan_s`.** Any script that computes a speed-up ratio without checking `completed_all` will produce a number that looks fine and means nothing.

### 7.3 Safety — the requirement-19 inputs

| Field | Unit | Computation | Citation |
|---|---|---|---|
| `contacts_robot_robot` | count | Contact events of kind `robot-robot` | `src/metrics.py:109`, `src/main.py:416` |
| `contacts_robot_human` | count | Kind `robot-human` | `src/metrics.py:110`, `src/main.py:417` |
| `contacts_robot_rack` | count | Kind `robot-rack` | `src/metrics.py:111`, `src/main.py:418` |
| `min_separation_m` | m | Smallest robot-robot separation observed, 3 dp | `src/metrics.py:112`, `src/main.py:419` |
| `p05_separation_m` | m | 5th percentile of the separation distribution, 3 dp | `src/metrics.py:113`, `src/main.py:420` |
| `robot_hours` | robot-hours | `robots × sim_seconds / 3600`, 5 dp — the exposure denominator | `src/metrics.py:114`, `src/main.py:380` |
| `safety_stop_ticks` | ticks | Ticks in certified safety stop, fleet-wide | `src/metrics.py:135`, `src/main.py:488` |

`robot_hours` exists because "zero collisions" is not a testable claim. Zero contacts in a finite run bounds the *rate*; it does not establish zero (`src/metrics.py:4-9`). Turning these fields into a rate with a one-sided 95% upper bound is `safety_report` (`src/metrics.py:213-242`) — which is **not reachable through the HTTP API** and is exercised by the CLI (`src/main.py:788-794`). See [07. Safety](07-SAFETY.md) and [12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md).

### 7.4 Coordination behaviour

All integer counters, summed across brains via `agg()` (`src/main.py:390-391`).

| Field | Meaning | Citation |
|---|---|---|
| `progress_cells` | Cells of net approach to a goal, fleet-wide — partial credit where `tasks_completed` is too coarse | `src/metrics.py:118`, `src/main.py:423` |
| `bios4_unstick` | Times the BIOS_4 liveness valve rescued the policy; reported for **every** policy (zero for the rest) so it is visible how much deadlock freedom came from the backstop | `src/metrics.py:123`, `src/main.py:424` |
| `deadlocks_detected` | Deadlock cycles detected (requirement 10) | `src/metrics.py:124`, `src/main.py:425` |
| `retreats` | Retreat manoeuvres executed | `src/metrics.py:125`, `src/main.py:426` |
| `yields` | Yields granted to a peer | `src/metrics.py:126`, `src/main.py:427` |
| `replans` | Path replans | `src/metrics.py:127`, `src/main.py:427` |
| `dynamic_obstacles_detected` | Unmapped obstacles sensed (requirement 2) | `src/metrics.py:128`, `src/main.py:428` |
| `dynamic_reroutes` | Reroutes caused by them (requirement 13) | `src/metrics.py:129`, `src/main.py:428` |
| `task_reassignments` | Tasks moved between robots (requirement 14) | `src/metrics.py:130`, `src/main.py:430` |
| `nonproductive_wait_ticks` | Ticks spent waiting without progress | `src/metrics.py:134`, `src/main.py:434` |
| `priority_decisions` | PIBT priority comparisons made | `src/metrics.py:198`, `src/main.py:502` |
| `priority_inheritances` | Priority inherited from a blocked peer | `src/metrics.py:199`, `src/main.py:503` |
| `priority_backtracks` | PIBT backtracks | `src/metrics.py:200`, `src/main.py:504` |
| `priority_forced_moves` | Moves forced by a higher-priority peer | `src/metrics.py:201`, `src/main.py:505` |
| `priority_waits` | Waits imposed by priority | `src/metrics.py:202`, `src/main.py:506` |

### 7.5 Auction, energy and allocation

| Field | Unit | Meaning | Citation |
|---|---|---|---|
| `auction_bids_sent` | count | Bids emitted | `src/metrics.py:131`, `src/main.py:431` |
| `energy_bids_suppressed` | count | Bids withheld because the battery reserve would be breached | `src/metrics.py:132`, `src/main.py:432` |
| `energy_no_eligible_rounds` | count | Rounds where no robot was energy-eligible | `src/metrics.py:133`, `src/main.py:433` |
| `charger_contentions_avoided` | count | Dock contentions avoided | `src/metrics.py:160`, `src/main.py:450` |
| `deadline_misses` | count | Tasks finished after their deadline | `src/metrics.py:187`, `src/main.py:477` |
| `allocation_compute_mean_ms` | ms | Mean per-decision allocation compute, 4 dp | `src/metrics.py:188`, `src/main.py:478-480` |
| `allocation_compute_median_ms` | ms | Median, 4 dp | `src/metrics.py:189`, `src/main.py:481-483` |
| `allocation_compute_p95_ms` | ms | Sample at index `min(n−1, int(0.95n))` of the sorted samples, 4 dp | `src/metrics.py:190`, `src/main.py:400-403` |
| `allocation_compute_max_ms` | ms | Maximum, 4 dp | `src/metrics.py:191`, `src/main.py:485-487` |

The p95 is a nearest-rank index, not an interpolated percentile. `metrics.percentile` (`src/metrics.py:245-258`) *is* interpolated but is used by the comparison path, not here.

### 7.6 Radio traffic

| Field | Unit | Meaning | Citation |
|---|---|---|---|
| `msgs_sent` | count | Datagrams sent, fleet-wide | `src/metrics.py:140`, `src/main.py:494` |
| `bytes_sent` | bytes | Encoded bytes, fleet-wide | `src/metrics.py:141`, `src/main.py:495` |
| `msgs_per_robot_s` | msg/robot/s | `msgs_sent / robots / sim_seconds`, 2 dp | `src/metrics.py:142`, `src/main.py:496` |
| `bytes_per_robot_s` | B/robot/s | `bytes_sent / robots / sim_seconds`, 1 dp | `src/metrics.py:143`, `src/main.py:497` |
| `heartbeat_messages_sent` | count | Heartbeats | `src/metrics.py:144`, `src/main.py:435` |
| `intent_messages_sent` | count | Intent broadcasts (requirement 5) | `src/metrics.py:145`, `src/main.py:436` |
| `auction_messages_sent` | count | `TASK_NEW`/`BID`/`AWARD`/`TASK_DONE` | `src/metrics.py:146`, `src/main.py:437` |
| `coordination_messages_sent` | count | Everything else | `src/metrics.py:147`, `src/main.py:438` |
| `heartbeat_messages_suppressed` | count | Heartbeats elided by rate control | `src/metrics.py:148`, `src/main.py:439` |
| `intent_messages_suppressed` | count | Intent broadcasts elided | `src/metrics.py:149`, `src/main.py:440` |
| `lease_renewals_suppressed` | count | Lease renewals elided | `src/metrics.py:150`, `src/main.py:441` |
| `bid_rebroadcasts_suppressed` | count | Bid rebroadcasts elided | `src/metrics.py:151`, `src/main.py:442` |
| `decision_events` | count | Decision records written (BIOS_PIBT.6 only) | `src/metrics.py:152`, `src/main.py:443` |
| `congestion_samples` | count | Congestion observations recorded | `src/metrics.py:153`, `src/main.py:444` |
| `experience_messages_sent` | count | Shared-experience datagrams sent | `src/metrics.py:154`, `src/main.py:445` |
| `experience_updates_received` | count | Shared-experience updates applied | `src/metrics.py:155`, `src/main.py:446` |
| `experience_guided_replans` | count | Replans triggered by peer experience | `src/metrics.py:156`, `src/main.py:447` |
| `predictive_hazards_seen` | count | Predicted hazards observed | `src/metrics.py:157`, `src/main.py:448` |
| `predictive_reroutes` | count | Reroutes taken on a prediction | `src/metrics.py:158`, `src/main.py:449` |

The `*_suppressed` counters are the bandwidth-honesty fields: they say how much traffic the rate control removed, so `msgs_per_robot_s` cannot be read as "this protocol is naturally quiet".

### 7.7 Bounded-future auction

Seventeen counters for the experimental forward-booking auction (`src/metrics.py:161-177`, `src/main.py:451-467`), in declaration order: `future_candidates_evaluated`, `future_bids_sent`, `future_bids_won`, `future_bids_lost`, `future_capacity_rejections`, `stale_future_awards_rejected`, `future_version_mismatches`, `future_lease_renewals`, `future_lease_expiries`, `future_invalidations`, `future_promotions`, `future_promotion_failures`, `future_network_fallbacks`, `future_energy_rejections`, `future_deadline_rejections`, `future_charger_rejections`, `future_hysteresis_prevented`.

All are counts. They are zero for policies that do not run the bounded-future auction. Semantics are in [06. Task Allocation](06-TASK-ALLOCATION.md) and `archive/BIOS6_EXPERIMENTAL_BOUNDED_FUTURE_AUCTION.md`.

### 7.8 Protocol rejections and certificates

Counts of messages the receiving brain refused, which is where a protocol bug shows up first (`src/metrics.py:178-186`, `src/main.py:468-476`):

| Field | Meaning |
|---|---|
| `rejected_unknown_bids` | Bids for a task the receiver does not know |
| `deferred_unknown_bids` | Same, held for later rather than dropped |
| `rejected_epoch_jumps` | Awards from an implausible epoch |
| `rejected_task_conflicts` | Awards conflicting with a held task |
| `rejected_task_completions` | Completion reports that failed verification |
| `rejected_directed_awards` | Directed awards refused |
| `completion_certificates_accepted` | Verified completion certificates accepted |
| `completion_certificates_relayed` | Certificates forwarded on behalf of a peer |
| `task_resurrections_suppressed` | Repeat announcements of an already-completed task, ignored |

The WMS injector applies the same discipline: a `TASK_DONE` counts only if its certificate key matches the announced identity **and** it came either directly from the owner or as a verified relay between two known robots (`src/main.py:272-293`).

### 7.9 Planner cost and fault context

| Field | Unit | Computation | Citation |
|---|---|---|---|
| `plan_cpu_total_s` | s | Brain plan CPU + manager plan CPU, 4 dp | `src/metrics.py:193`, `src/main.py:393` |
| `plan_calls` | count | Brain plan calls + manager plans | `src/metrics.py:194`, `src/main.py:394` |
| `plan_cpu_mean_ms` | ms | `plan_cpu_total_s / plan_calls × 1000`, 3 dp | `src/metrics.py:195`, `src/main.py:500` |
| `plan_cpu_max_ms` | ms | Worst single plan across brains and manager, 3 dp | `src/metrics.py:196`, `src/main.py:395-396` |
| `human_yield_ticks` | ticks | Ticks workers spent yielding | `src/metrics.py:136`, `src/main.py:489` |
| `human_work_visits` | count | Work locations workers reached | `src/metrics.py:137`, `src/main.py:490` |
| `human_distance_m` | m | Total distance walked by workers, 2 dp | `src/metrics.py:138`, `src/main.py:491` |
| `seconds_degraded` | s | Mean seconds per robot spent in `DEGRADED_P2P` | `src/metrics.py:139`, `src/main.py:493` |
| `net_loss` | fraction | Uniform packet-loss probability in force | `src/metrics.py:204`, `src/main.py:507` |
| `manager_killed_at` | s \| null | Scripted manager-failure time | `src/metrics.py:205`, `src/main.py:508` |
| `robot_failures` | count | Number of scripted robot failures | `src/metrics.py:206`, `src/main.py:509` |

`plan_cpu_max_ms` is the edge-feasibility number: it is the worst-case planning latency one node had to absorb. See [08. Edge Deployment](08-EDGE-DEPLOYMENT.md).

### 7.10 `metrics.compare()` refuses rather than guesses

`compare(baseline, candidate)` is an alias for `compare_paired` (`src/metrics.py:437-439`). It is **not exposed over HTTP** — no server or frontend code references it. It runs from the CLI (`src/main.py:796-808`) and from the benchmark harnesses.

It never returns a bare ratio. Its output always carries a `verdict`, and the percentage fields are present **only** when a percentage is defensible:

| `verdict` | When | Percentage fields emitted? | Citation |
|---|---|---|---|
| `"invalid"` | Empty input, duplicate seeds, seed mismatch, missing or mismatched `workload_id`, differing `scenario`/`robots`/`tasks_announced`/`allocation_policy`/`net_loss`, or an unusable baseline cutoff | No — only `baseline`, `candidate`, `verdict`, `reason` | `src/metrics.py:279-286`, `src/metrics.py:312-346` |
| `"incomplete"` | The **candidate** timed out on any seed | No — `reason` names the seeds | `src/metrics.py:348-352`, `src/metrics.py:392-397` |
| `"pass"` | Every paired lower bound ≥ 20% **and** the candidate had zero contacts of any kind | Yes | `src/metrics.py:406-410` |
| `"fail"` | Paired and complete, but the gate was not met | Yes | `src/metrics.py:407-411` |

The refusal is deliberate, and it is the part a sceptical judge should check. If the candidate did not finish, its makespan does not exist; dividing the cutoff by a non-existent makespan would manufacture a speed-up out of a failure. If the two runs did not execute the same workload, the ratio compares two different problems. In both cases the function declines to emit a number at all rather than emit one with a caveat, because a caveat gets dropped when a number is copied into a slide.

The one place it *does* extrapolate, it says so in the field name. When the candidate finishes and the **baseline** hits the cutoff, the reduction is computed against the cutoff and labelled `right_censored_lower_bound`, because the unknown baseline makespan is strictly greater than the cutoff — so the figure is a conservative floor, not an estimate (`src/metrics.py:358-364`, `src/metrics.py:408-409`). `evidence_kind` reports `exact_paired_makespan` or `right_censored_conservative_lower_bound` for the run as a whole, and `median_lower_bound_bootstrap_95pct` gives a deterministic bootstrap interval around the median (`src/metrics.py:401-402`, `src/metrics.py:261-276`).

---

## 8. BIOS_4 model endpoints

BIOS_4 is a 549-parameter learned policy (`src/amr.py:75-77`). These five endpoints train one, watch it, download it, cancel it, and load one back.

### 8.1 `POST /api/train`

Starts one neuroevolution job on a daemon thread and returns immediately (`backend/server.py:649-720`).

**Concurrency rule: exactly one job at a time.** If any job is in state `running`, the request is refused with `409` (`backend/server.py:699-704`, `tests/test_server.py:265-273`). This is deliberate — training already saturates the machine with a process pool, so a second concurrent run would not go faster, it would make both slower and give the user two progress bars that both crawl (`backend/server.py:66-69`).

Training also never takes `_SIM_LOCK`; holding that for half an hour would leave the dashboard looking hung, which from a browser is indistinguishable from a crash (`backend/server.py:63-65`, `tests/test_server.py:249-262`).

No `Content-Type` check on this endpoint. The body is read through `_body(64 * 1024)` (`backend/server.py:651`), so the cap is 64 KiB, not 8 KiB. An **empty** body is an error, not a default: `_body` raises before returning and the handler answers `400 {"error": "empty request body"}` (`backend/server.py:371-373`, `backend/server.py:652-653`).

| Field | Type | Default | Bound | Citation |
|---|---|---|---|---|
| `scenario` | string | `"crossing_chokepoint"` | Must be a key of `SCENARIOS` | `backend/server.py:657-660` |
| `robots` | int | `4` | clamped to 2..12 | `backend/server.py:662` |
| `population` | int | `24` | clamped to 4..64 (`MAX_POPULATION`) | `backend/server.py:663`, `backend/server.py:81` |
| `generations` | int | `30` | clamped to 1..200 (`MAX_GENERATIONS`) | `backend/server.py:664`, `backend/server.py:82` |
| `hidden` | int | `16` | clamped to 2..64 | `backend/server.py:665` |
| `workers` | int | `0` (auto) | clamped to 0..64 | `backend/server.py:666` |
| `episodes` | `[seed, duration][]` | `((0,120),(1,120),(2,240))` | 1–8 pairs, each duration 5.0–900.0 s | `backend/server.py:675-687`, `src/evolve.py:147` |

Note the asymmetry with `/api/run`: these five integers are **clamped**, not refused. `population: 9999` silently becomes 64. Only `scenario` and `episodes` produce errors.

`episodes` is exposed because it is the knob that decides what the policy learns — short episodes are cheap but leave the fleet dispersed, and the congestion this policy exists to solve only appears in the longer ones (`backend/server.py:671-674`).

After clamping, `TrainConfig.validate()` runs (`backend/server.py:692-695`, `src/evolve.py:150-161`). Its guard that matters: **training on a held-out evaluation seed is refused**, with a message ending "That would make the reported result a memorisation score - pick from TRAIN_SEEDS." (`src/evolve.py:157-161`, `tests/test_server.py:298-303`). This is the first thing anyone evaluating the BIOS_4 headline will check.

Response `202`:

| Field | Meaning |
|---|---|
| `job` | 12-hex job id |
| `generations` | Generations after clamping |
| `population` | Population after clamping |
| `params` | Weight count for this network shape |
| `episodes_per_generation` | `(population + 1) × len(episodes)` |

Citation: `backend/server.py:718-720`.

Errors: `400` unknown scenario (with a `known` array — `backend/server.py:659-660`); `400` `"population, generations, robots, hidden and workers must be whole numbers"` (`backend/server.py:667-669`); `400` `"episodes must be a list of [seed, duration] pairs"`, `"between 1 and 8 episodes per genome"`, `"episode duration must be 5-900 s"` (`backend/server.py:680-686`); `400` from `cfg.validate()`; `400` `"expected a JSON object"` (`backend/server.py:654-655`); `409` a run is in progress.

### 8.2 `GET /api/train/status`

Query parameter `job` (`backend/server.py:723`). Returns the job record minus `model` and `cancel` (`backend/server.py:730`).

| Field | Type | Meaning |
|---|---|---|
| `id` | string | Job id |
| `state` | string | `running`, `done`, `cancelled`, `failed` |
| `generation` | int | Last completed generation, `-1` before the first |
| `generations` | int | Target generations |
| `history` | object[] | One entry per completed generation |
| `started_at` | float | Unix time |
| `finished_at` | float \| null | Unix time, `null` while running |
| `scenario`, `robots`, `population` | — | Echo of the request |
| `model_id` | string | Present once finished — pass as `model` to `/api/run` |
| `fitness` | float | Present once finished |
| `error` | string | Present on `failed`: a 3-frame traceback |

Citations: `backend/server.py:705-710`, `backend/server.py:126-136`, `backend/server.py:117-118`.

Each `history` entry (`src/evolve.py:380-397`): `gen`, `best`, `best_so_far`, `mean`, `theta`, `sigma`, `best_tasks`, `best_progress`, `contacts`, `failed`, `episodes`, `elapsed_s`, `serial`, `serial_reason`. `serial` is reported loudly because a silently serial run is ~12× slower and looks identical to a machine that is simply busy (`src/evolve.py:394-395`).

**The weights are deliberately absent from this payload**: they are ~550 floats and the dashboard polls this every second while training runs (`backend/server.py:728-729`). Use `/api/train/model` instead.

Errors: `404 {"error": "unknown job '<id>'"}` — including for a **missing** `job` parameter, which reports `unknown job ''` (`backend/server.py:723`, `backend/server.py:727`, `tests/test_server.py:306-308`).

### 8.3 `GET /api/train/model`

Query parameter `job`. Returns the trained model as a **file download**, not a JSON body to render: `Content-Disposition: attachment; filename="bios4-<job>.json"` (`backend/server.py:749-750`). This has a habit of regressing into a rendered page, so it is asserted directly (`tests/test_server.py:234-237`).

This response is written by hand rather than through `_send`, so it carries `Content-Type`, `Content-Length`, `Content-Disposition` and `Cache-Control: no-store` **but none of the security headers** from §1.4 (`backend/server.py:746-752`).

Body: `PolicyNet.to_dict(meta)` (`src/bios4.py:199-211`) — `format: "bios4-mlp"`, `features`, `n_in`, `n_hidden`, `n_out`, `weights`, `meta`. The `meta` block records provenance (`src/evolve.py:279-294`): `trained_by`, `algorithm`, `fitness`, `generations`, `episodes`, `elapsed_s`, `stopped_early`, `train_seeds`, `eval_seeds_withheld`, `best_tasks`, `config`. `eval_seeds_withheld` is the field that lets a judge confirm the model was not trained on the seeds it is evaluated on (`tests/test_server.py:241-243`).

This file is what a Pi flashes (`backend/server.py:735`) — it is requirement 15's artefact. It loads straight back through `POST /api/model`, which is the whole point of the format (`tests/test_server.py:245-246`).

Errors: `404` unknown job; `409 {"error": "job is <state>; no model to download yet"}` when the job has not produced one (`backend/server.py:743-744`).

### 8.4 `POST /api/train/cancel`

Query parameter `job`; the request body is **ignored** and drained (`backend/server.py:756-769`, `backend/server.py:388-390`). This endpoint is exactly where the keep-alive body-draining bug was found, and it cost a test run to track down.

Cancellation is cooperative and lands between generations, so the run finishes the one it is in. Saying so matters: a button that appears to do nothing for ninety seconds gets pressed five more times (`backend/server.py:765-767`).

| Code | Body | When |
|---|---|---|
| `202` | `{"job": "<id>", "state": "cancelling", "note": "stops after the current generation"}` | Job was running |
| `200` | `{"job": "<id>", "state": "<state>"}` | Job already finished — idempotent, not an error |
| `404` | `{"error": "unknown job '<id>'"}` | No such job |

**A cancelled run still yields its best genome.** `state` becomes `cancelled` rather than `failed`, and `model_id`, `fitness` and the model are all stored (`backend/server.py:126-136`). Throwing the work away would punish the user for stopping a run that had already learned something — which is exactly when people stop them (`backend/server.py:129-131`, `tests/test_server.py:276-295`).

### 8.5 `POST /api/model`

Uploads a previously trained model so it can be run against any scenario (`backend/server.py:771-786`). No `Content-Type` check. The raw body **is** the model JSON — there is no envelope.

Size cap: `MAX_MODEL_BYTES = 4 * 1024 * 1024` (`src/bios4.py:219`), enforced from the `Content-Length` header *before* the body is allocated, so a 400 MB upload is rejected in one comparison rather than read into memory to be measured (`backend/server.py:360-364`, `backend/server.py:374-776`).

Response `200`:

| Field | Meaning |
|---|---|
| `model` | 12-hex model id — pass as `model` to `/api/run` |
| `meta` | The provenance block from the file |
| `params` | `len(model.w)` |
| `hidden` | `model.n_hidden` |

Citation: `backend/server.py:785-786`.

Errors:

| Code | Trigger | Citation |
|---|---|---|
| `400` | `ModelError` — malformed JSON, wrong shape, implausible hidden size, or a mismatched feature set | `backend/server.py:777-783`, `src/bios4.py:254-272` |
| `413` | Empty body, oversized body, or an unparseable `Content-Length` | `backend/server.py:774-776`, `backend/server.py:367-378` |

`ModelError` messages are written to be read by a person, because uploading a model trained against an older feature set is a normal thing to do by accident and "bad request" is not a useful answer to it (`backend/server.py:779-782`). The feature-set check is the one that matters: a renumbered input does not crash, it quietly computes a different function, and the run looks like a badly trained policy rather than a loading bug (`tests/test_server.py:186-196`). The refusal names the problem — `"different observation layout"` — and tells the user to retrain.

### 8.6 The model store

Uploaded and trained models both land in `_MODELS` via `_remember_model` (`backend/server.py:85-93`). It is an in-memory dict, capped at `MAX_MODELS = 16`, evicting oldest-first — "a dev tool, not a model registry" (`backend/server.py:75`).

Consequences an API client must handle:

1. **A restart loses every model.** The `404` from `/api/run` says so in its `hint` (`backend/server.py:631-632`).
2. **A 17th upload silently evicts the first.** A previously working `model` id starts returning `404` with no other signal.
3. There is no list-models endpoint. The only way to enumerate ids is to keep them client-side.

Keep the `.json` file. Re-uploading is the recovery path, and it returns a **new** id.

---

## 9. Static file serving

Any `GET` that does not match an API route is served from `frontend/` (`backend/server.py:790-807`). `/` and `""` become `/index.html` (`backend/server.py:791-792`).

Path handling: the route is `posixpath.normpath`'d and stripped of leading slashes, joined onto `FRONTEND`, `resolve()`d, and then checked with `Path.relative_to` against the resolved frontend directory — otherwise `/../../secrets` is a file read (`backend/server.py:793-800`). Because `normpath` collapses `..` before the join, an ordinary traversal attempt such as `/../pyproject.toml` becomes `pyproject.toml` inside `frontend/` and returns `404`, not `403`. The `403` branch exists for what survives normalisation, such as a symlink pointing out of the tree.

| Code | Trigger |
|---|---|
| `200` | File exists; MIME type from `mimetypes`, with `; charset=utf-8` appended for `text/*` and `application/javascript` (`backend/server.py:804-806`) |
| `403` | `{"error": "forbidden"}` — resolved path escaped `frontend/` (`backend/server.py:799-800`) |
| `404` | `{"error": "not found: <route>"}` — no such file (`backend/server.py:801-802`) |

`.js`, `.png` and `.css` types are registered explicitly at import time so a bare Windows Python does not serve JavaScript as `text/plain` and get it refused by the CSP (`backend/server.py:153-155`).

---

## 10. cURL cookbook

Copy-pasteable against a server started with `python backend/server.py`.

### 10.1 Run the flagship showcase correctly

Every parameter explicit. This is the invocation to use for anything you will quote.

```bash
curl -sS -X POST http://127.0.0.1:8000/api/run \
  -H 'Content-Type: application/json' \
  -d '{
    "scenario": "showcase_grand_challenge",
    "policy": "BIOS_PIBT.6",
    "allocation_policy": "auction_bundle",
    "robots": 10,
    "seed": 1,
    "duration": 800
  }' \
  -o grand_challenge.json
```

Then check what actually ran before reading any metric:

```bash
python -c "
import json; d = json.load(open('grand_challenge.json'))
m, s = d['meta'], d['summary']
print('ran', m['robots'], 'robots for', m['duration_s'], 's on', m['scenario'])
print('completed_all', s['completed_all'], 'makespan', s['makespan_s'],
      'tasks', s['tasks_completed'], '/', s['tasks_announced'])
print('contacts rr/rh/rack', s['contacts_robot_robot'],
      s['contacts_robot_human'], s['contacts_robot_rack'])
print('frames', len(d['frames']))
"
```

If `meta.duration_s` is `120.0` you hit the default and the run is meaningless — see [§3.1](#31-the-defaults-trap--read-this-before-benchmarking).

The same run with the stop-and-wait baseline, for the requirement-20 pair:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/run \
  -H 'Content-Type: application/json' \
  -d '{"scenario":"showcase_grand_challenge","policy":"stop_and_wait",
       "allocation_policy":"auction_bundle","robots":10,"seed":1,"duration":800}' \
  -o grand_challenge_baseline.json
```

Note that the API returns two independent summaries; it does not compute the reduction. Pairing them is `metrics.compare_paired`, which runs offline ([§7.10](#710-metricscompare-refuses-rather-than-guesses)).

### 10.2 The seed-99 deadlock demonstration

```bash
curl -sS -X POST http://127.0.0.1:8000/api/run \
  -H 'Content-Type: application/json' \
  -d '{"policy":"BIOS_PIBT.6","seed":99,"duration":180}' \
  -o seed99.json
python -c "import json;print(json.load(open('seed99.json'))['demo_evidence'])"
```

The scenario field is omitted on purpose: seed 99 substitutes `seed_99_congestion` regardless ([§3.5](#35-seed-99-substitutes-the-scenario)).

### 10.3 List everything runnable

```bash
curl -sS http://127.0.0.1:8000/api/scenarios | python -m json.tool
```

Extract the correct parameters for a showcase instead of typing them:

```bash
curl -sS http://127.0.0.1:8000/api/scenarios \
  | python -c "
import json,sys
for s in json.load(sys.stdin)['showcase']:
    print(f\"{s['id']:<26} robots={s['robots']:<3} seed={s['seed']:<3} duration={s['duration']}\")
"
```

### 10.4 Post a small custom floor and run it

An 8×6 floor: one station at `(0,1)`, one dock at `(7,4)`, two AMR starts, one worker. This is the shape `tests/test_server.py:77-94` uses.

```bash
curl -sS -X POST http://127.0.0.1:8000/api/scenarios/custom \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "Two-aisle demo floor",
    "width": 8, "height": 6,
    "grid": [
      [0,0,0,0,0,0,0,0],
      [2,0,0,0,0,0,0,0],
      [0,0,1,1,0,0,0,0],
      [0,0,1,1,0,0,0,0],
      [0,0,0,0,0,0,0,3],
      [0,0,0,0,0,0,0,0]
    ],
    "stations": [[0,1]],
    "docks": [[7,4]],
    "starts": [[1,1],[1,4]],
    "humans": [[3,5]],
    "seed": 7,
    "duration": 300
  }'
```

Response: `{"id":"custom_ab12cd34","name":"Two-aisle demo floor","width":8,"height":6,"robots":2}`.

Run it — and pass `seed` and `duration` explicitly, because the values you saved with the floor are **not** applied ([§11](#11-gaps-found-while-writing-this-document)):

```bash
curl -sS -X POST http://127.0.0.1:8000/api/run \
  -H 'Content-Type: application/json' \
  -d '{"scenario":"custom_ab12cd34","policy":"BIOS_PIBT.6","seed":7,"duration":300}' \
  -o custom_run.json
```

Keep the floor's request body. A server restart deletes the floor and the id will not come back ([§5.6](#56-the-scenario-store-is-in-memory)).

### 10.5 Train, watch, download, run

```bash
# start
curl -sS -X POST http://127.0.0.1:8000/api/train \
  -H 'Content-Type: application/json' \
  -d '{"scenario":"crossing_chokepoint","robots":4,"population":24,
       "generations":30,"hidden":16,"workers":0,
       "episodes":[[0,120.0],[1,120.0],[2,240.0]]}'
# -> {"job":"a1b2c3d4e5f6", ...}

# watch
curl -sS 'http://127.0.0.1:8000/api/train/status?job=a1b2c3d4e5f6' | python -m json.tool

# stop early, keeping the best genome found so far
curl -sS -X POST 'http://127.0.0.1:8000/api/train/cancel?job=a1b2c3d4e5f6'

# download the model file
curl -sS -OJ 'http://127.0.0.1:8000/api/train/model?job=a1b2c3d4e5f6'

# load it back and run it
curl -sS -X POST http://127.0.0.1:8000/api/model \
  -H 'Content-Type: application/json' \
  --data-binary @bios4-a1b2c3d4e5f6.json
# -> {"model":"9f8e7d6c5b4a", ...}

curl -sS -X POST http://127.0.0.1:8000/api/run \
  -H 'Content-Type: application/json' \
  -d '{"scenario":"crossing_chokepoint","policy":"BIOS_4","model":"9f8e7d6c5b4a",
       "robots":4,"seed":0,"duration":120}'
```

`status` also carries `model_id` once the job finishes, so the download-and-re-upload round trip is only needed to get the model onto another machine.

---

## 11. Gaps found while writing this document

Each of these was reproduced against the current tree. None is smoothed over elsewhere in this document.

### 11.1 A custom floor's saved `seed` and `duration` are never used

`_api_run` writes:

```python
request["_custom_duration"] = float(request.get("duration", custom.get("duration", 180.0)))
request["_custom_seed"] = int(request.get("seed", custom.get("seed", 0)))
```

(`backend/server.py:643-644`.) `request` is the dict returned by `parse_run_request`, which **always** contains `duration` and `seed` because they are defaulted at `backend/server.py:192-197`. The `custom.get(...)` fallbacks are unreachable.

Reproduced: a floor saved with `seed: 7, duration: 300` is listed by `/api/scenarios` as `seed 7, duration 300`, and a bare `POST /api/run {"scenario": "custom_..."}` runs it at **seed 0 for 120 s**. The listing advertises values the run cannot use.

### 11.2 `MAX_CUSTOM_SIDE = 64` is unreachable through the API

`MIN_CUSTOM_SIDE = 4`, `MAX_CUSTOM_SIDE = 64` (`backend/server.py:149-150`) but `MAX_REQUEST_BYTES = 8 * 1024` (`backend/server.py:139`). A minimal 64×64 request body — compact JSON, one station, one dock, two starts, no workers — measures **8447 bytes** and is rejected with `413 {"error": "request body is too large"}` before any grid validation runs.

Measured body sizes for square floors with that minimal payload: 48×48 → 4831 B, 56×56 → 6511 B, 60×60 → 7447 B, 62×62 → 7939 B, 64×64 → 8447 B. The practical ceiling is around 60×60, and less once `starts`, `humans` and a longer `name` are added.

The dashboard never hits this because its builder is a fixed 22×14 grid (`frontend/js/main.js:1513-1514`). Only a direct API client asking for the documented maximum does.

### 11.3 A valid request can return `500`

`parse_run_request` accepts `{"scenario": "seed_99_congestion", "robots": 4, "seed": 0}` — the scenario is a key of `SCENARIOS` (`src/scenarios.py:841`) and every bound is satisfied. But `seed_99_congestion` raises `ValueError` unless `seed == 99` and `n_robots == 6` (`src/scenarios.py:623-628`), the seed-99 substitution branch is not taken (`src/main.py:580`), and the exception escapes `_api_run` into the generic handler at `backend/server.py:350-355`.

Reproduced: `500 {"error": "internal server error"}` with a traceback on stderr. It should be a `400` naming the constraint. There is no cross-check in `parse_run_request` between a scenario and the seed or robot count it demands.

### 11.4 `robots` is silently ignored for custom scenarios

The fleet size on a custom floor is `len(starts)` (`src/main.py:594`); the request's `robots` never reaches the builder. Reproduced: `{"scenario": "custom_...", "robots": 50}` against a 2-start floor returns `200` with `meta.robots = 2`. The value is still used for the `robots × duration ≤ 24000` check (`backend/server.py:215`), so it can cause a rejection while having no effect on what runs.

### 11.5 `/api/scenarios` under-reports what `/api/run` accepts

The listing returns 5 showcases plus custom floors (`backend/server.py:603-608`), while `/api/run` accepts all 18 keys of `SCENARIOS` (`backend/server.py:184`). The 13 unlisted ids — including `blocked_aisle` (requirement 12), `robot_failure_reassignment` (requirement 14) and `partition_recovery` — are runnable but invisible to any client that discovers scenarios through the API. They are printed to stderr at startup (`backend/server.py:815`) and defined at `src/scenarios.py:828-843`.

### 11.6 `POST /api/model` reports `413` for a bad `Content-Length`

`_body` raises `ValueError("bad Content-Length")` for an unparseable header (`backend/server.py:369-370`), and `_api_model_upload` maps every `ValueError` to `413` (`backend/server.py:774-776`). A malformed header is a `400` condition, not a payload-too-large one. `/api/run` gets this right with an explicit `400` (`backend/server.py:331-334`).

---

## Related documents

- [02. Architecture](02-ARCHITECTURE.md) — where the server sits relative to the simulation core
- [03. Decentralized Protocol](03-DECENTRALIZED-PROTOCOL.md) — what the `auction_events` and `peers` fields mean on the wire
- [06. Task Allocation](06-TASK-ALLOCATION.md) — semantics of the auction and `future_*` counters
- [07. Safety](07-SAFETY.md) — how the contact and separation fields become a bounded rate
- [08. Edge Deployment](08-EDGE-DEPLOYMENT.md) — where the downloaded model file goes
- [09. Fleet Dashboard](09-DASHBOARD.md) — the client that consumes every schema above
- [11. Scenarios](11-SCENARIOS.md) — what each `scenario` id actually builds
- [12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md) — the offline paired comparison the API deliberately does not perform
- [13. Testing](13-TESTING.md) — `tests/test_server.py` and `tests/test_dashboard.py` in full
- [15. Limitations](15-LIMITATIONS.md) — the in-memory stores and the single-process run lock
