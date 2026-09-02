# 03. DECENTRALIZED PROTOCOL

> This document specifies every byte the fleet puts on the wire, proves that the shipped policies build no coordinator, and states exactly where that proof stops.

**Audience:** SIH judges and BEL evaluators assessing whether "decentralized" is a property of this code or an adjective in its README; teammates who must answer protocol questions live.
**Reads best after:** [02. Architecture](02-ARCHITECTURE.md)

## Requirements evidenced

| # | Requirement | Where | Evidence |
|---|---|---|---|
| 1 | At least 3 AMRs | [§6.3](#63-what-happens-if-the-dashboard-is-switched-off) | `src/distributed_demo.py:164` refuses `robots < 3`; the checked-in run is 3 processes (`artifacts/benchmarks/bios6-distributed-demo.json`) |
| 3 | Decentralized communication | [§2](#2-complete-message-inventory), [§5](#5-the-transport-layer) | 13 message types, `src/messages.py:38`–`src/messages.py:53`; real IPv4 multicast socket at `src/transport.py:231`–`src/transport.py:249` |
| 4 | Position sharing | [§3](#3-position-sharing-requirement-4) | `HEARTBEAT` body `p`/`c`, `src/messages.py:460`–`src/messages.py:462`; consumed at `src/amr.py:5276`–`src/amr.py:5277` |
| 5 | Intent sharing | [§4](#4-intent-sharing-requirement-5) | `INTENT` built at `src/amr.py:5239`–`src/amr.py:5264`, encoded at `src/messages.py:501`–`src/messages.py:515`, consumed at `src/amr.py:5294`–`src/amr.py:5302` |
| 6 | No central coordination server | [§6](#6-why-there-is-no-central-server-requirement-6) | `MANAGED_POLICIES` at `src/main.py:104`; manager built only under that condition at `src/main.py:165`–`src/main.py:173` |
| 18 | Battery status | [§3](#3-position-sharing-requirement-4) | Battery fraction is a first-class wire field (`HB.b`, `src/messages.py:462`) validated at `src/messages.py:285` and consumed by peers at `src/amr.py:5283`. **The dashboard does not read it from the wire** — see the scoping note in [§3](#3-position-sharing-requirement-4) and [09. Dashboard](09-DASHBOARD.md). |

Requirements 7–14 are resolved by mechanisms that *ride* on this protocol but are specified elsewhere: [04. Path Planning](04-PATH-PLANNING.md), [05. Coordination Policies](05-COORDINATION-POLICIES.md), [06. Task Allocation](06-TASK-ALLOCATION.md). This document specifies only the messages and the transport.

---

## 1. The four design rules

These are stated in the module docstring at `src/messages.py:3`–`src/messages.py:19` and each is verifiable in the code below.

| Rule | What it means | Where it is enforced |
|---|---|---|
| Every message is advisory | No message can command motion; nothing here is on the safety path | Layer 0 (`src/amr.py:868`) reads `Sensors`, never `msg`; `AMRBrain.step` applies `_safety` last (`src/amr.py:603`) |
| Every message is self-contained and idempotent | A receiver that misses one packet is not left in a wrong state | `INTENT` carries the whole horizon, not a delta (`src/amr.py:5247`); `CLAIM` carries a TTL so a lost `RELEASE` self-heals (`src/messages.py:542`) |
| Every message carries a total-order key | Conflict resolution needs a tiebreak that cannot deadlock | `PriorityKey` is a 7-tuple ending in `robot_id` (`src/priority.py:32`–`src/priority.py:38`) |
| The wire is measured, not asserted | `encode` returns bytes; the agent sums them | `src/amr.py:630` adds `len(msg.encode(m))` per emitted message; aggregated at `src/main.py:494`–`src/main.py:497` |

The third rule carries an admission the code itself makes at `src/amr.py:2152`–`src/amr.py:2154`: `robot_id` is assigned centrally at commissioning. Every practical distributed scheme needs some externally-supplied total order. "No central server" here means no server participates in *runtime* coordination decisions, not that no centrally-issued identifier exists.

---

## 2. Complete message inventory

### 2.1 The envelope

Every datagram is compact JSON — chosen so a judge can `tcpdump` the group and read the protocol without a decoder (`src/messages.py:143`–`src/messages.py:145`). The envelope is type-independent (`src/messages.py:87`–`src/messages.py:98`):

| Field | Type | Required | Meaning | Validation |
|---|---|---|---|---|
| `v` | int | yes | Protocol version, currently `1` | `src/messages.py:262`, mismatch → `unsupported_version` |
| `type` | str | yes | Two-letter type code from `ALL_TYPES` | `src/messages.py:264` |
| `src` | str | yes | Sender id, ≤64 chars, `[A-Za-z0-9_.:-]+` | `src/messages.py:266`, `src/messages.py:57` |
| `seq` | int | yes | Non-negative per-sender counter | `src/messages.py:268`, incremented at `src/amr.py:5683` |
| `t` | float | yes | **Sender** clock, 0…1e15 | `src/messages.py:270` |
| `body` | object | yes | Type-specific | `src/messages.py:275` |
| `sid` | str | no | Random session/boot id, added by the UDP transport | `src/messages.py:272`; generated at `src/transport.py:222` |
| `mac` | str | no | HMAC-SHA256 hex over the canonical envelope minus `mac` | `src/messages.py:159`, verified at `src/messages.py:200`–`src/messages.py:203` |

`t` is useful only for ordering a single sender's own messages (`src/messages.py:72`–`src/messages.py:74`). Nothing compares `t` across senders. Everything a receiver must compare against its own clock is transmitted as a **bounded relative duration** and converted on receipt — see [§4.3](#43-why-every-deadline-on-the-wire-is-a-duration).

Hard limits (`src/messages.py:55`–`src/messages.py:65`):

| Constant | Value | Effect |
|---|---|---|
| `MAX_DATAGRAM_BYTES` | 2048 | `encode` raises above this (`src/messages.py:162`); `decode_packet` rejects with reason `oversized` (`src/messages.py:177`) |
| `MAX_ID_LENGTH` | 64 | Bounds `src`, `sid`, `dst`, `winner`, `owner`, `bo`, `to` |
| `MAX_TASK_ID_LENGTH` | 128 | Bounds `task`, `active` |
| `MAX_INTENT_CELLS` | 64 | Bounds `IN.cells` |
| `MAX_PLAN_CELLS` | 1024 | Bounds `PS.cells` — **unreachable**, see [§13](#13-defects-and-contradictions-found) |
| `MAX_EXPERIENCE_RECORDS` | 8 | Bounds `EX.edges` |
| `MAX_RELATIVE_TIME_S` | 86 400 | Ceiling on every relative duration |
| `cfg.net.mtu_bytes` | 1400 | The simulated radio drops anything larger as loss (`src/transport.py:149`–`src/transport.py:153`) |

### 2.2 Every message type

Sizes below are **measured**, by encoding a representative populated message of each type on this repository at HEAD. `plain` is `encode(m)` (what the benchmark meters). `auth` is `encode(m, secret=…, session_id=…)` — the real UDP form; the difference is a constant **98 bytes** (26 for `"sid":"<16 hex>"`, 72 for `"mac":"<64 hex>"`), confirmed against the checked-in distributed run where `374419/1379 − 239277/1379 = 98.000` bytes/packet exactly.

| Code | Name | Body fields (type) | plain / auth bytes | Emitted by | Trigger and rate | Ingested at | Req |
|---|---|---|---|---|---|---|---|
| `HB` | HEARTBEAT | `p` [f,f,f], `c` [i,i], `b` f∈[0,1], `m` str, `s` str, `task` str\|null, `pr` f, `bo` str\|null, `g` [i,i]\|null, `pk` list[7]\|absent | 217 / 315 (PIBT, full)<br>173 / 271 (baseline) | every robot, `src/amr.py:5054` and `src/amr.py:5028` | ≤5 Hz gate (`src/amr.py:624`, `rates.heartbeat_hz=5.0`). `BIOS_PIBT.6` further suppresses to 0.6 s idle / 0.3 s cruising / 5 Hz in conflict unless a field changed (`src/amr.py:677`–`src/amr.py:687`) | `src/amr.py:5273`–`src/amr.py:5293` | 3, 4, 18 |
| `IN` | INTENT | `cells` list[[i,i]] ≤64, `w` list[[f,f]] (relative), `pr` f, `e` int | 203 / 301 (6 cells) | every non-baseline robot, `src/amr.py:5084` | inside the same ≤5 Hz gate, only when a route exists; V6 sends on horizon change, on active conflict, or every 0.4 s (`src/amr.py:5076`–`src/amr.py:5082`) | `src/amr.py:5294`–`src/amr.py:5302` | 5 |
| `CL` | CLAIM (block form) | `b`=1, `g` int (zone id), `pr` f, `e` int, `ttl` f, `pk` list[7]\|absent | 132 / 230 (with `pk`)<br>103 / 201 (without) | `src/amr.py:2366` | keep-alive throttled to ≥0.5 s → ≤2 Hz, evaluated every control tick (`src/amr.py:2361`, called at `src/amr.py:600`) | `src/amr.py:5624`–`src/amr.py:5655` | 9, 10, 11 |
| `CL` | CLAIM (cell form) | `c` [i,i], `ttl` f, `pr` f, `e` int | 100 / 198 | **nothing** — constructor only (`src/messages.py:518`) | never | never (the ingest branch requires `b`, `src/amr.py:5624`) | — |
| `RL` | RELEASE (block form) | `b`=1, `g` int | 75 / 173 | `src/amr.py:2374` | on leaving a claimed zone | `src/amr.py:5629`–`src/amr.py:5632` | 9 |
| `RL` | RELEASE (cell form) | `c` [i,i] | 72 / 170 | **nothing** — constructor only (`src/messages.py:526`) | never | never | — |
| `YD` | YIELD | `c` [i,i], `to` str | 85 / 183 | `src/amr.py:1315`, `src/amr.py:2480` | once per yield decision, when `blocked_since` transitions from `None` | **no receiver** — see [§2.4](#24-wire-forms-that-exist-but-are-never-exchanged) | observability only |
| `TN` | TASK_NEW | `task` str, `pk` [i,i], `dp` [i,i], `e` int, `ct` enum, `cw` f, `pr` int 1–100, `g` int, `dh` sha256, `dd` f\|absent, `ttl` f\|absent, `due` f\|absent | 245 / 343 | WMS injector (`src/main.py:305`) and **any robot**, as gossip (`src/amr.py:5179`) | WMS: every 4 s (`traffic.wms_announcement_period_s`). Robot gossip: one catalog entry per 1.0 s, suppressed when the network is demonstrably healthy (`src/amr.py:5145`–`src/amr.py:5187`) | `src/amr.py:5306`–`src/amr.py:5436` | 14 |
| `BD` | BID | `task` str, `cost` f, `e` int, `g`+`dh` (paired), `future`+`active`+`ae`+`bv` (bundle only) | 175 / 273<br>221 / 319 (bundle) | robots only, `src/amr.py:3522`, `src/amr.py:3651`, `src/amr.py:3689` | event-driven: once per (task, epoch) opening, or once per bundle round; V6 re-broadcasts only on a cost change ≥0.25 or after 0.6 s (`src/amr.py:689`–`src/amr.py:700`) | `src/amr.py:5437`–`src/amr.py:5468` | 14 |
| `AW` | AWARD | `task` str, `cost` f, `e` int, `g`+`dh`, `winner` str, `dst` str (manager only), `ttl` f, `future`+`active`+`ae`+`bv` | 203 / 301 | robots (self-elected winner, `src/amr.py:3552`; lease renewal, `src/amr.py:5114`) and the optional manager (`src/fleet_manager.py:153`) | winner announcement at auction close; lease renewal at 1.0 s healthy / 0.5 s degraded (`src/amr.py:5097`–`src/amr.py:5102`) | `src/amr.py:5469`–`src/amr.py:5556` | 14 |
| `TD` | TASK_DONE | certificate form: `cv`,`task`,`g`,`dh`,`owner`,`e`,`oph`,`finished`,`nonce`,`result`,`relay`; legacy form: `task`,`e`,`owner`,`relay` | 340 / 438 (certificate)<br>84 / 182 (legacy) | robots, `src/amr.py:3176`, `src/amr.py:5230`, `src/amr.py:5233` | on completion, then gossiped one entry per 1.0 s (`src/amr.py:5189`–`src/amr.py:5237`) | `src/amr.py:5557`–`src/amr.py:5621` | 14 |
| `MB` | MGR_BEACON | `e` int | 65 / 163 | **manager only**, `src/fleet_manager.py:114` | 0.5 s, and only when `route_planning` is on | `src/amr.py:5622`–`src/amr.py:5623` (sets `_mgr_seen`) | baseline |
| `PQ` | PLAN_REQ | `s` [i,i], `g` [i,i], `ns` bool | 93 / 191 | **central-baseline robots only**, `src/amr.py:2592`; also `src/amr.py:2677` | 1 Hz route loop (`rates.route_hz`) | `src/fleet_manager.py:98`–`src/fleet_manager.py:102` | baseline |
| `PS` | PLAN_RSP | `dst` str, `cells` list[[i,i]] ≤1024, `e` int, `w` list[f] (relative) | 125 / 223 (6 cells)<br>656 measured worst case on shipped maps | **manager only**, `src/fleet_manager.py:249` | 1 Hz, throttled to goal changes or 5 s per robot (`src/fleet_manager.py:180`–`src/fleet_manager.py:183`) | `src/amr.py:5656`–`src/amr.py:5669` | baseline |
| `EX` | EXPERIENCE | `edges` list of `[ax,ay,bx,by,delay_s,samples]`, ≤8 records | 107 / 205 (2 records) | `BIOS_PIBT.6` robots, `src/amr.py:777` | ≥5 s, ≤2 records, and **disabled entirely under packet loss or dead zones** (`src/amr.py:762`–`src/amr.py:766`) | `src/amr.py:5303`–`src/amr.py:5305` | 13 (efficiency only) |

`MB`, `PQ` and `PS` exist to run the **central and hierarchical baselines** the fleet is measured against. No shipped policy emits or consumes them — see [§6](#6-why-there-is-no-central-server-requirement-6).

### 2.3 Validation before anything reaches the brain

`decode_packet` (`src/messages.py:167`–`src/messages.py:210`) returns `(message, reason)` where the reason is a stable metric label, not exception text. No packet can raise into the control loop. The rejection reasons, in evaluation order:

| Reason | Cause | Line |
|---|---|---|
| `not_bytes` | Non-`bytes` input | `src/messages.py:176` |
| `oversized` | >2048 bytes | `src/messages.py:178` |
| `invalid_json` | Malformed UTF-8 or JSON, **including `NaN`/`Infinity`** (rejected by a `parse_constant` hook) | `src/messages.py:180`–`src/messages.py:186` |
| `invalid_envelope` | Top-level value is not an object | `src/messages.py:188` |
| `invalid_auth` | `mac` present but not 64 hex chars, or HMAC mismatch (`hmac.compare_digest`) | `src/messages.py:192`, `src/messages.py:203` |
| `auth_unconfigured` | `mac` present, `require_auth` set, no local secret | `src/messages.py:196` |
| `auth_missing` | `require_auth` set and no `mac` at all | `src/messages.py:205` |
| `unsupported_version`, `unknown_type`, `invalid_source`, `invalid_sequence`, `invalid_sender_time`, `invalid_session`, `invalid_body` | Envelope shape | `src/messages.py:262`–`src/messages.py:277` |
| `invalid_heartbeat`, `invalid_priority_key`, `invalid_intent`, `invalid_task`, `invalid_task_descriptor_hash`, `invalid_bid`, `invalid_award`, `invalid_completion_certificate`, `invalid_task_done`, `invalid_block_claim`, `invalid_cell_claim`, `invalid_yield`, `invalid_manager_beacon`, `invalid_plan_request`, `invalid_plan_response`, `invalid_experience` | Per-type body shape | `src/messages.py:280`–`src/messages.py:440` |

Numeric validation refuses `bool` (which is an `int` in Python) and non-finite floats (`src/messages.py:220`–`src/messages.py:231`). `TN` additionally recomputes the descriptor hash and rejects the packet if it does not match the declared fields (`src/messages.py:331`–`src/messages.py:336`), so a task's identity cannot be silently mutated in transit even by an authenticated sender.

Outbound messages are validated by the same function before encoding (`src/messages.py:155`–`src/messages.py:157`), so a schema drift in the agent is a `ValueError` at the sender, not a mystery at the receiver.

### 2.4 Wire forms that exist but are never exchanged

Being precise about this is more useful to a judge than a longer type list.

| Form | Status | Consequence |
|---|---|---|
| `CL`/`RL` **cell form** (`msg.claim`, `msg.release`, `src/messages.py:518`–`src/messages.py:527`) | Constructors and validators exist; **no call site anywhere in `src/` or `tests/`**; the ingest branch at `src/amr.py:5624` matches only `b.get("b")` | Per-cell leases *are* used, but they are transmitted as **block-form claims with a synthetic zone id** — `_cell_zone_id` = `1_000_000 + y*width + x` (`src/amr.py:1653`). One code path, two zone namespaces, distinguished by `_is_cell_zone` (`src/amr.py:1656`). |
| `YD` (YIELD) | Emitted at `src/amr.py:1315` and `src/amr.py:2480`; **no ingest branch exists** | Write-only. Its stated purpose (`src/messages.py:42`) is to make deadlock-breaking *observable* — in a packet capture and in the metrics counter `stats["yields"]`. It carries no coordination load. Do not claim a receiver acts on it. |
| `MB`, `PQ`, `PS` | Fully implemented on both sides, but only for `central`, `prioritized_space_time_astar` and `hierarchical` | Baseline infrastructure. See [§6](#6-why-there-is-no-central-server-requirement-6). |

---

## 3. Position sharing (requirement 4)

**One message carries position: `HEARTBEAT`.**

`msg.heartbeat` (`src/messages.py:448`–`src/messages.py:473`) packs:

| Field | Content | Precision | Why it is on the wire |
|---|---|---|---|
| `p` | Continuous pose `(x, y, θ)` in metres/radians | rounded to 3 dp (`src/messages.py:461`) | Physical-gap checks that a cell index cannot express — e.g. the convoy clearance test at `src/amr.py:1918`–`src/amr.py:1925` |
| `c` | Discrete grid cell `(x, y)` | exact ints | The occupancy input to PIBT (`src/amr.py:1873`) and to the block-token owner test (`src/amr.py:2311`–`src/amr.py:2313`) |
| `b` | Battery fraction ∈ [0,1] | 3 dp | Peer energy-feasibility screening in the auction (`src/amr.py:4341`–`src/amr.py:4344`) and the `emergency` priority field below 10 % (`src/amr.py:5008`) |
| `m` | Mode: `CENTRAL_OK` or `DEGRADED_P2P` | — | Telemetry |
| `s` | State: `idle`/`to_pick`/`to_drop`/`charging`/`blocked`/`retreat` | — | Drives the intent-lifecycle rule at `src/amr.py:5291` |
| `task` | Current task id or null | — | Independent liveness evidence for an owner's task lease (`src/amr.py:4703`) |
| `pr` | Scalar published priority | 4 dp | Legacy arbitration key for non-PIBT decentralized policies (`src/amr.py:1825`) |
| `bo` | `blocked_on` — **which peer this robot is waiting for** | — | This field is what makes distributed wait-for-graph cycle detection possible at all (`src/messages.py:453`–`src/messages.py:458`); consumed by `_find_cycle` via `src/amr.py:5279` |
| `g` | Goal cell, or null | — | An idle robot parked on somebody else's destination is an obstruction that never clears on its own; nothing in a purely reactive scheme tells a stationary robot it is in the way (`src/messages.py:465`–`src/messages.py:468`) |
| `pk` | 7-element `PriorityKey`, PIBT policies only | ints + id | See [§7](#7-decentralized-priority-arbitration) |

**Rate.** `_broadcast` is gated at `rates.heartbeat_hz = 5.0` (`src/amr.py:624`, `src/settings.py:111`). Under `BIOS_PIBT.6` the heartbeat is additionally *event-triggered*: it is sent immediately whenever a signature of (cell, state, task, goal, blocked_on, battery quantised to 5 %, priority key) changes, and otherwise at 0.3 s while cruising, 0.6 s while idle, and the full 5 Hz whenever a conflict is active — `src/amr.py:5044`–`src/amr.py:5065` and `src/amr.py:677`–`src/amr.py:687`. Suppressions are counted, not hidden (`stats["heartbeat_messages_suppressed"]`).

**Reception.** `src/amr.py:5273`–`src/amr.py:5293` writes into a `Peer` record (`src/amr.py:128`–`src/amr.py:144`), whose docstring is the correct mental model: *"What one robot believes about another. Always stale, never authoritative."* One important lifecycle rule lives here: when a heartbeat reports `state == idle` or `goal is None`, the receiver **clears that peer's stored intent** (`src/amr.py:5291`–`src/amr.py:5293`). Without it, an idle peer that has stopped sending `INTENT` would keep a stale route alive on every neighbour forever, and different robots would run PIBT against different ghost reservations.

**Scoping note for requirement 18.** Battery is genuinely a protocol field and peers genuinely act on it. The *dashboard*, however, reads battery from simulator ground truth (`src/world.py:881`), not from `HB.b`. Requirement 18 as a display requirement is evidenced by [09. Dashboard](09-DASHBOARD.md); this document evidences only that battery is shared peer-to-peer and consumed by the allocator and the priority key.

---

## 4. Intent sharing (requirement 5)

### 4.1 What an intent horizon is

An **intent horizon** is the next *K* grid cells this robot means to occupy, each with a `[t_enter, t_exit]` window. It is not a reservation and it is not a request: it is a published prediction that peers may use to plan around, and which they may also ignore.

`_intent_horizon` (`src/amr.py:5239`–`src/amr.py:5264`) builds it:

| Step | Rule | Line |
|---|---|---|
| Guard | If `self.goal is None`, publish nothing at all. A finished task can leave its last waypoint in `path`; those cells are history, not intent, and republishing them would recreate on every peer the ghost route the idle heartbeat just cleared | `src/amr.py:5244`–`src/amr.py:5245` |
| Horizon length | `K = traffic.intent_horizon = 6` cells | `src/amr.py:5246`, `src/settings.py:140` |
| Slice | `path[pidx : pidx+K]` — the *remaining* route, so the message is a complete statement, never a delta | `src/amr.py:5247` |
| Trim | On directed-circulation policies, drop leading cells equal to `_last_cell`. Pose quantisation flips the cell index before the continuous follower reaches the centre; publishing that cell as future intent tells peers this AMR plans to *stay*, and a priority-inheritance chain cannot then push through it | `src/amr.py:5248`–`src/amr.py:5255` |
| Windows | `v_nom = 0.8 · v_max = 0.96 m/s`; `step = cell_m / v_nom = 1.4/0.96 ≈ 1.458 s`; cell *i* gets `[t + i·step, t + i·step + step + 0.4]`. The 0.4 s tail is margin for acceleration and turns | `src/amr.py:5258`–`src/amr.py:5263` |

So a default horizon looks about **8.75 s** ahead (6 × 1.458 s), with windows overlapping by 0.4 s.

### 4.2 How far ahead, at what rate, and how it expires

| Property | Value | Evidence |
|---|---|---|
| Cells published | 6 | `src/settings.py:140` |
| Wall-clock lookahead | ≈8.75 s at nominal speed | derived from `src/amr.py:5258`–`src/amr.py:5263` |
| Hard cap on the wire | 64 cells | `src/messages.py:59`, enforced at `src/messages.py:300` |
| Emission rate | ≤5 Hz (inside the heartbeat gate). `BIOS_PIBT.6`: on horizon change, on active conflict within 3 cells, or every 0.4 s — whichever comes first | `src/amr.py:5076`–`src/amr.py:5082`, `src/settings.py:233` |
| Measured actual rate | 1.77 Hz per robot (424 sent + 177 suppressed over 4 robots × 60 s, `sih_acceptance_overlap`, `BIOS_PIBT.6`, seed 0) | run at HEAD; suppression counters at `src/amr.py:5090` |
| Expiry — intent | Cleared after `peer_stale_s` (1.0 s) of silence | `src/amr.py:5675`–`src/amr.py:5679` |
| Expiry — whole peer | Record deleted after `6 × peer_stale_s` = 6.0 s | `src/amr.py:5680`–`src/amr.py:5681` |
| Expiry — semantic | Cleared immediately on any heartbeat reporting `idle` or no goal, regardless of age | `src/amr.py:5291` |

`_expire_peers` carries the reason in its docstring (`src/amr.py:5672`–`src/amr.py:5674`): *silence is information*. Holding stale intents is how a fleet politely gridlocks around a robot that died ten seconds ago.

### 4.3 Why every deadline on the wire is a duration

`INTENT` windows are transmitted as **offsets from transmission**, not as absolute sender timestamps (`src/messages.py:510`–`src/messages.py:513`), and the receiver rebases them onto its own clock at `src/amr.py:5297`–`src/amr.py:5300`. The same treatment is applied to every other cross-node deadline:

| Message | Field | Semantics |
|---|---|---|
| `IN` | `w` | `[enter_offset, exit_offset]` seconds from send |
| `TN` | `ttl` | seconds until the bid window closes |
| `TN` | `due` | seconds until the task deadline |
| `AW` | `ttl` | seconds of task lease remaining |
| `CL` | `ttl` | seconds of zone lease remaining |
| `PS` | `w` | per-cell earliest-entry offsets |

Absolute timestamps only ever worked while every robot shared the simulator clock. Separate edge nodes have unrelated monotonic epochs, and assuming otherwise is a quiet form of re-centralisation. The multi-process demo makes this falsifiable: the three children are launched with clock offsets of 10 000 s, 20 000 s and 30 000 s (`src/distributed_demo.py:190`–`src/distributed_demo.py:192`), and the run asserts three distinct epochs (`tests/test_edge_runtime.py:93`). Legacy absolute fields `dl` and `u` are still *accepted* for old-trace compatibility (`src/amr.py:5338`, `src/amr.py:5477`) but no constructor emits them.

Receivers also **clamp** every incoming duration, so a hostile or buggy sender cannot mint an unbounded lease: bid windows are capped at 4 × `auction_bid_window_s` (`src/amr.py:5336`–`src/amr.py:5337`), zone claims at 2 × `bios_claim_ttl_s` (`src/amr.py:5640`–`src/amr.py:5641`), and task leases at `_max_incoming_task_lease_s()` (`src/amr.py:5475`–`src/amr.py:5476`).

### 4.4 How a receiver actually uses intent

| Consumer | Use | Line |
|---|---|---|
| PIBT snapshot | `preferred[peer] = peer.intent[0]` — the peer's next cell becomes its preferred move in the replicated resolver; `goals[peer] = peer.goal or peer.intent[-1]` | `src/amr.py:1874`–`src/amr.py:1876` |
| Block admission | A peer whose intent contains any cell of a controlled block is a *contender* for that block | `src/amr.py:1544` |
| Liveness valve | `_bios_unstick` excludes the first cell of every peer's intent from its escape options | `src/amr.py:2250`–`src/amr.py:2252` |
| Idle parking | Cells in any peer's intent are excluded when choosing a parking cell | `src/amr.py:3217`–`src/amr.py:3218`, `src/amr.py:3284`–`src/amr.py:3288` |
| Charger selection | A dock appearing in a peer's intent gets a soft cost penalty — a decentralised appointment with no global schedule | `src/amr.py:846`, `src/settings.py:280` |
| Merge staging | A peer at a junction whose onward intent collides with ours triggers one-cell backpressure | `src/amr.py:1611`–`src/amr.py:1622` |

Critically, **baseline policies are never given intent**. `_broadcast` short-circuits for `stop_and_wait*` and the central policies and sends heartbeats only (`src/amr.py:5023`–`src/amr.py:5035`). The comment there states the reason explicitly: the dashboard must work for every baseline or the comparison silently becomes "with telemetry vs without", but lending the baselines *intent* would flatter our own result. This is visible in the measured counters — `intent_messages_sent` is 0 for every baseline across all 420 rows of `artifacts/benchmarks/bios6-three-way-comparison.json`.

---

## 5. The transport layer

### 5.1 The abstraction boundary

Two implementations satisfy the same three-method interface (`send`, `poll`, `close`), and `AMRBrain` cannot tell them apart — it receives an `inbox` list and returns an `outbox` list and never touches a socket (`src/amr.py:547`–`src/amr.py:548`). The `PeerTransport` protocol is declared at `src/edge_runtime.py:37`–`src/edge_runtime.py:42`.

| | `SimNetwork` | `UdpMulticastTransport` |
|---|---|---|
| Defined at | `src/transport.py:75` | `src/transport.py:194` |
| Used by | The batch simulator/benchmark, via `src/main.py:126` | The multi-process demo (`src/distributed_demo.py:53`) and the single-node deployment binary (`src/edge_runtime.py:379`) |
| Purpose | Evidence: hundreds of times faster than realtime with a seeded impairment model, which is the only way to get a collision *rate* with a confidence interval | Demonstration: real datagrams a judge can packet-capture |
| Authentication | None — `msg.encode(message)` with no secret (`src/transport.py:146`) | HMAC-SHA256 with a PSK, `require_auth=True` in the demo (`src/distributed_demo.py:55`) |
| Replay protection | None (in-process, no adversary) | 64-wide sliding window keyed `(src, sid)` (`src/transport.py:48`–`src/transport.py:72`, `src/transport.py:281`–`src/transport.py:286`) |
| Blocking | N/A | Never — `setblocking(False)` at `src/transport.py:249`; an empty poll returns `[]` and the agent proceeds on stale peer data, which is the behaviour that must be correct under loss, so it is exercised all the time |

Everything in [12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md) is produced through `SimNetwork`. State that plainly rather than letting a judge assume the headline numbers came off a radio.

### 5.2 Determinism, and why it is not a nicety

`SimNetwork` gives each *semantic* packet a stable seeded draw derived from `blake2b(seed, src, dst, type, round(t,6), semantic_body)` (`src/transport.py:167`–`src/transport.py:173`). `seq` is deliberately **excluded**, because an event-triggered policy consumes fewer sequence numbers than a periodic one; including it would make the two policies face different channels. `delivery_identity_body` (`src/messages.py:115`–`src/messages.py:138`) further strips additive protocol-upgrade fields so a certificate-bearing `TASK_DONE` draws the same loss as its legacy equivalent.

The reason is paired-comparison integrity: a policy that suppresses one redundant packet must not shift a global RNG and thereby change the loss and latency of every later packet in the run. Otherwise "policy A beat policy B" could be the dice. Two tests pin this: `tests/test_core.py:684` (same seed, same delivery count) and `tests/test_core.py:697` (an unrelated extra packet does not shift a later delivery).

There is one shared-state exception worth stating: `self._tie = count()` (`src/transport.py:93`) breaks equal-arrival-time ties in the delivery heap and *is* global. It affects only the relative order of two packets scheduled for the identical float timestamp.

### 5.3 Modelled impairments

All parameters live in `NetSpec` (`src/settings.py:115`–`src/settings.py:133`).

| Impairment | Parameter | Default | Model | Line |
|---|---|---|---|---|
| Latency | `latency_mean_s` / `latency_jitter_s` | 0.004 s / 0.003 s | Gaussian, floored at 0.5 ms | `src/transport.py:177`–`src/transport.py:178` |
| Packet loss | `loss` | 0.0 | Uniform per (packet, link) Bernoulli draw | `src/transport.py:174`–`src/transport.py:176` |
| MTU | `mtu_bytes` | 1400 | Oversized frames are **dropped loudly** and counted as loss, so an oversized intent horizon shows up as a protocol bug rather than mysterious gridlock | `src/transport.py:149`–`src/transport.py:153` |
| Dead zones | `dead_zones` | `()` | Tuples of `(cx, cy, radius)` in **cells**; a node is in a hole if `hypot(p − c) ≤ r` | `src/transport.py:116`–`src/transport.py:123` |
| Link topology | `peer_traffic_via_ap` | **True** | See below | `src/transport.py:130`–`src/transport.py:141` |
| Partition | `set_partition(groups)` | `None` | Explicit islands; any pair not in a common group is unreachable | `src/transport.py:110`–`src/transport.py:112`, `src/transport.py:126`–`src/transport.py:129` |

Latency is deliberately small because on a real on-premises LAN it *is* 1–5 ms; inflating it to make a decentralized scheme look better would be dishonest in the direction that flatters us.

**The dead-zone model is the argument, not a detail** (`src/transport.py:14`–`src/transport.py:25`). The problem statement asserts that peer-to-peer messaging solves Wi-Fi dead zones. In infrastructure-mode 802.11, a frame from robot A to robot B is *relayed by the access point*: same radio, same hole. `peer_traffic_via_ap=True` is therefore the honest default, and under it **a robot that cannot reach the server cannot reach its peers either — P2P inherits the identical failure**. Setting it to `False` models a genuinely different link (802.11s mesh, Wi-Fi Direct, UWB) and only then does the advantage appear. Both configurations are in the sweep: `dead_zone(mesh_radio=False|True)` at `src/scenarios.py:435`–`src/scenarios.py:454`. The claim is tested, not asserted, at `tests/test_core.py:721`–`tests/test_core.py:746`. **The finding is that the fix for dead zones is a different radio, not a different software topology, and the problem statement never names a link layer.**

Every drop is attributed to a cause, not lumped together: `dropped_loss`, `dropped_deadzone`, `dropped_partition` (`src/transport.py:97`–`src/transport.py:98`).

### 5.4 The real UDP implementation

`UdpMulticastTransport` (`src/transport.py:194`) joins administratively-scoped group **239.26.1.23 port 26123** (`src/transport.py:44`–`src/transport.py:45`; the group is mnemonic for SIH26123). Setup details that matter:

- `SO_REUSEADDR` always, `SO_REUSEPORT` where it exists — Windows has neither the constant nor the need (`src/transport.py:232`–`src/transport.py:236`).
- `ip_mreq` is built as two packed IPv4 addresses (`src/transport.py:240`). The native `4sl` struct format is 16 bytes on LP64 and only happened to work on some kernels.
- `IP_MULTICAST_LOOP` is enabled so several nodes on one host hear each other; the agent discards its own `src` at `src/amr.py:5269`.
- A `sendto` failure is counted as a lost packet and swallowed, never raised (`src/transport.py:258`–`src/transport.py:261`) — the protocol tolerates loss, so the correct response to a send failure is to carry on.
- The PSK must be ≥16 bytes (`src/transport.py:220`–`src/transport.py:221`); the deployment runbook generates 32 random bytes (`archive/EDGE_DEPLOYMENT.md:31`).
- Replay-window state is bounded against an attacker minting session ids: 1024 sessions max, 3600 s TTL, LRU eviction (`src/transport.py:290`–`src/transport.py:306`).

Per-node counters (`src/transport.py:227`–`src/transport.py:229`) separate `malformed`, `auth_failed`, `oversized`, `replayed` and `send_failed`, so the demo can assert all four are zero (`src/distributed_demo.py:278`–`src/distributed_demo.py:282`).

---

## 6. Why there is no central server (requirement 6)

### 6.1 The strongest evidence: the manager is only ever built for a baseline

There is exactly one place in the runner that constructs a coordinator:

```
src/main.py:100   # Which policies are served by a fleet manager at all. …
src/main.py:104   MANAGED_POLICIES = (*CENTRAL_POLICIES, POLICY_HIERARCHICAL)
…
src/main.py:165   manager = None
src/main.py:166   if policy in MANAGED_POLICIES \
src/main.py:167           or allocation_policy == ALLOCATION_HUNGARIAN:
src/main.py:170       manager = FleetManager(sc.env, cfg, …)
src/main.py:173       net.register(MANAGER_ID)
```

`CENTRAL_POLICIES = (central, prioritized_space_time_astar)` (`src/amr.py:85`). Evaluating the tuple at HEAD gives:

| Builds a `FleetManager` | Does not |
|---|---|
| `central`, `prioritized_space_time_astar`, `hierarchical` | `stop_and_wait`, `stop_and_wait_competition`, `BIOS_1.0.0`, `decentralized`, `BIOS_PIBT.1`, `BIOS_PIBT.2`, `BIOS_PIBT.3`, `BIOS_PIBT.5`, `BIOS_PIBT.6`, `BIOS_4` |

The shipped default policy is **`BIOS_PIBT.6`** (`src/main.py:745`), which is in the right-hand column. When `manager is None`, the entire manager branch of the tick loop — `manager.step()` and every message it would emit — is skipped (`src/main.py:319`–`src/main.py:324`), and `MANAGER_ID` is never registered on the network, so a `PLAN_REQ` would have no destination even if one were sent.

Three independent chains reinforce this:

1. **The robot never asks.** `PLAN_REQ` is emitted only inside `if self.policy in CENTRAL_POLICIES:` (`src/amr.py:2580`–`src/amr.py:2595`). A `BIOS_PIBT.*` robot has no code path that requests a plan.
2. **The robot never listens.** `_mgr_seen` is written only by `MGR_BEACON` and by a `PLAN_RSP` addressed to this robot (`src/amr.py:5623`, `src/amr.py:5662`). With no manager, `_mgr_seen` stays at its initial `-1e9` (`src/amr.py:372`) and the mode latches to `DEGRADED_P2P` on the first route tick (`src/amr.py:2571`–`src/amr.py:2573`). The brain is *born* in `MODE_P2P` (`src/amr.py:217`).
3. **The failure mode of the baseline is demonstrated, not argued.** A `central`-policy robot whose manager is unreachable clears its path and parks (`src/amr.py:2580`–`src/amr.py:2588`). The comment is the argument: *"a purely centralised fleet with an unreachable manager does not degrade — it parks."* The `kill_manager_at` scenario knob (`src/main.py:226`–`src/main.py:230`) makes that observable.

Tests pin the property directly: `tests/test_core.py:465` and `tests/test_core.py:478` assert `not any(frame["manager_alive"] …)` across a whole dashboard run, and `tests/test_core.py:511` asserts the converse for the Hungarian comparison so the assertion cannot pass vacuously.

Task allocation is a **separate, selectable responsibility** from routing (`src/main.py:163`–`src/main.py:164`). `ALLOCATION_HUNGARIAN` builds a manager for assignment only; the shipped default is `auction_bundle` (`src/main.py:749`), which is peer-to-peer. That separation is why the traceability claim is precise: the shipped configuration has no coordinator for *either* responsibility.

### 6.2 What the WMS is, and what it is not

A `TASK_NEW` source exists (`WMS_ID`), and honesty requires saying what it does. It is the **order source**, equivalent to the warehouse management system that already exists in any real facility. It:

- announces tasks every 4 s as idempotent catalog entries (`src/main.py:295`–`src/main.py:317`);
- observes `TASK_DONE` certificates only so it can stop repeating finished jobs (`src/main.py:272`–`src/main.py:293`);
- **never evaluates a bid, chooses a winner, or sends an `AWARD`** — the comment at `src/main.py:273`–`src/main.py:276` states this and the code contains no such branch.

Every robot also gossips the catalog itself (`src/amr.py:5145`–`src/amr.py:5187`), so a task missed in a radio hole is learned from a peer rather than from a retry the WMS must coordinate. `tests/test_priority.py:329` pins that a missed task is recovered by peer gossip with no manager present.

### 6.3 What happens if the dashboard is switched off

**Nothing.** This is worth being exact about, because it is the second question after "is there a manager".

The shipped dashboard (`backend/server.py`) is not a live aggregator at all. `/api/run` calls `run_for_dashboard` (`backend/server.py:646`), which calls `run_scenario` with a `trace` list (`src/main.py:665`) and returns only after the *entire simulation has finished*. The frontend then replays the recorded frames. `backend/server.py` constructs **no socket of any kind**: the only three `UdpMulticastTransport(...)` call sites in the repository are the robot worker (`src/distributed_demo.py:53`), the WMS task source (`src/distributed_demo.py:210`), and the deployed edge node (`src/edge_runtime.py:379`). There is no `EventSource`, no WebSocket, and no `text/event-stream` anywhere in `backend/` or `frontend/`.

So the honest statement is stronger than "passive reader": **the dashboard is downstream of a completed simulation and is not in the loop even in principle.** Switching it off removes the display and changes nothing about robot behaviour, because no robot has any channel to it.

The corresponding limitation must be stated in the same breath: **the dashboard cannot display the multi-process UDP demo.** The two demonstrations are disjoint. Requirements 16–18 are evidenced by the batch-simulation dashboard; requirement 6 is evidenced by the multi-process runner; no single artefact shows both at once. See [15. Limitations](15-LIMITATIONS.md).

⚠️ **Two shipped documents overclaim here.** `backend/server.py:20` states "In the distributed runner the dashboard is a **passive multicast listener**: it joins the group and reads the same datagrams the robots send each other", and `archive/DEMO_AND_JUDGING.md:33`–`archive/DEMO_AND_JUDGING.md:35` draws that listener in its architecture diagram. **No such listener exists in this repository.** The underlying claim (the dashboard cannot influence the fleet) is true and is in fact more strongly true than described — but the mechanism described is not implemented. Do not repeat the "joins the group" sentence to a judge.

### 6.4 Where "fully decentralized" honestly stops

| Residual centralisation | Why it exists | Where it is admitted in code |
|---|---|---|
| `robot_id` as the final total-order tiebreak | Any distributed tiebreak needs an externally-supplied unique order | `src/amr.py:2152`–`src/amr.py:2154`, `src/messages.py:14`–`src/messages.py:16` |
| A shared PSK for the whole fleet | Authenticates *membership*, not *role*; it is not per-device identity, rotation, or provisioning | `src/messages.py:148`–`src/messages.py:150`, `archive/WIRE_PROTOCOL.md:31`–`archive/WIRE_PROTOCOL.md:33` |
| A WMS that issues orders | Warehouses have one; it is not a motion coordinator | `src/messages.py:46` |
| Deadlock cycle detection needs global state | Approximated from broadcasts, so it works exactly where the radio works — and degrades precisely where partitions make deadlock likeliest | `src/amr.py:2149`–`src/amr.py:2151` |
| A `FM0`-only directed-award check | The shared PSK cannot distinguish roles, so a directed `AW` is accepted only from literal `"FM0"` and only under Hungarian allocation | `src/amr.py:5485`–`src/amr.py:5492` |

The last row is a real weakness and should be volunteered rather than discovered: with a shared PSK, any fleet member could forge `src: "FM0"`. The mitigation is per-device keys, which this prototype does not implement.

---

## 7. Decentralized priority arbitration

The full PIBT mechanism is specified in [05. Coordination Policies](05-COORDINATION-POLICIES.md). This section covers only what is *on the wire* and the two protocol-level rules that were learned the hard way.

### 7.1 The published key

`PriorityKey` (`src/priority.py:23`–`src/priority.py:51`) is a frozen, lexicographically-ordered 7-tuple. Larger moves first.

| Rank | Field | Rule | Built at |
|---:|---|---|---|
| 1 | `emergency` | battery < 10 % | `src/amr.py:5008` |
| 2 | `exiting_branch` | current cell is in a tree appendage (graph 2-core complement) and the goal is outside it | `src/amr.py:5009`, `src/topology.py:36`–`src/topology.py:40` |
| 3 | `waiting_age` | seconds continuously blocked ÷ `priority_age_quantum_s` | `src/amr.py:5010`–`src/amr.py:5011` |
| 4 | `service_age` | seconds since the current task began ÷ quantum | `src/amr.py:5012`–`src/amr.py:5013` |
| 5 | `loaded` | carrying toward the drop point | `src/amr.py:5014` |
| 6 | `distance_bias` | −Manhattan distance to goal | `src/amr.py:5015` |
| 7 | `robot_id` | stable id — produces a total order with no equal keys | `src/amr.py:5016` |

It serialises to `HB.pk` as a 7-element list (`src/priority.py:40`–`src/priority.py:42`), validated at `src/messages.py:294`–`src/messages.py:296`, and parses defensively — a malformed key degrades to `PriorityKey(robot_id=…)` rather than raising (`src/priority.py:45`–`src/priority.py:51`).

### 7.2 Rule 1 — arbitrate on published state, never on live state

```
src/amr.py:4964   def _arbitration_key(self) -> tuple[float, str]:
src/amr.py:4965       """The key both sides of a conflict compare. Published, not live.
```

**The failure this prevents.** Priority ages while a robot waits, so a robot's *live* key is always slightly higher than the value its peers last heard. If robot A compares *its live key* against *B's published key*, then B does the same. Inside one heartbeat period both can conclude they lost. Both yield. Neither is wrong, so no cycle-breaker can fix it — the wait-for graph shows a mutual block that is genuinely symmetric. That is a livelock that presents as a deadlock.

**The fix.** Latch the key at the instant of publication and compare published against published (`src/amr.py:5037`–`src/amr.py:5043`). Both robots then evaluate the same two numbers and the relation is antisymmetric: exactly one yields. `_peer_outranks` implements it for both key generations — the rich `PriorityKey` for PIBT policies, the scalar `pr` for the older decentralized ones (`src/amr.py:1819`–`src/amr.py:1825`), and in both branches the left-hand side is `self._pub_priority_key` / `self._pub_priority`, never a freshly computed value.

The same discipline is applied a second time to zone leases: a claim's rank is frozen for the whole lease attempt and *not* recomputed on each keep-alive (`src/amr.py:2354`–`src/amr.py:2359`), because otherwise a waiting robot's accumulating age would repeatedly steal the token from the current winner before it could cross the mouth.

### 7.3 Rule 2 — priority must never invert queue order

```
src/amr.py:1547  # A contender is only a contender if it can actually go first. A peer
src/amr.py:1548  # queued BEHIND us at the same mouth cannot: we are the thing in its way.
src/amr.py:1549  # Yielding to it on priority is a textbook priority inversion - the robot
src/amr.py:1550  # in front stops for the robot it is itself blocking, and the queue never
src/amr.py:1551  # moves. Ageing makes this certain rather than unlikely, because the one
src/amr.py:1552  # stuck at the back accrues priority fastest.
```

**The failure this prevents.** Two robots queue at the same mouth of a single-file aisle. The one at the back is blocked, so its `waiting_age` climbs fastest and it eventually outranks the robot in front. The front robot then yields to a robot that physically cannot proceed until the front robot moves. The queue deadlocks — and ageing, the very mechanism meant to prevent starvation, guarantees it rather than merely risking it.

**The fix** (`src/amr.py:1557`–`src/amr.py:1566`): partition contenders by which mouth they are entering from.

- **Different mouths** → both genuinely could go first → resolve by published priority.
- **Same mouth** → resolve by *distance to the mouth* first; priority breaks ties only when the distances are equal.

So position decides among robots that share a mouth, and priority decides only between robots arriving at different mouths.

### 7.4 The commit round, and its explicit limit

Nobody contesting a block "right now" is a statement about a peer table built from ≤5 Hz broadcasts. Two robots at opposite mouths can both read an empty block inside the same 200 ms window and both commit. The protocol therefore holds the winner stationary for one propagation round — `gate_commit_s = 0.45 s`, two heartbeat periods (`src/settings.py:158`, applied at `src/amr.py:1521`–`src/amr.py:1525` and `src/amr.py:1577`–`src/amr.py:1584`) — then latches admission in `_gate_committed` so a new gate does not restart on every 10 Hz tick.

The code states the limit rather than hiding it (`src/amr.py:1574`–`src/amr.py:1576`): *"This shrinks the race window; it does not close it. Over an asynchronous lossy channel no protocol can guarantee agreement (Fischer–Lynch–Paterson), which is precisely why the collision guarantee lives in Layer 0 and not here."* That sentence is the correct answer to a judge asking how collisions can be guaranteed at zero — see [07. Safety](07-SAFETY.md).

Two further lease rules complete the picture (`src/amr.py:2304`–`src/amr.py:2318`, `src/amr.py:2320`–`src/amr.py:2380`):

- **Physical presence outranks every remote claim.** Any peer whose reported cell is inside the zone *is* the owner, whatever the token table says (`src/amr.py:2311`–`src/amr.py:2313`).
- **Releases are an optimisation, never a requirement.** A `RELEASE` only helps early; TTL expiry repairs a lost one (`src/messages.py:41`, `src/messages.py:543`–`src/messages.py:545`).

---

## 8. Ageing must be in discrete buckets

There are two ageing schemes, and they are quantised differently. Both are quantised for the same reason.

| Key | Quantum | Step | Line |
|---|---|---|---|
| Scalar `pr` (non-PIBT decentralized policies) | **5.0 s** | +50.0 per bucket, on a base of 1000.0 when loaded | `src/amr.py:4996` |
| `PriorityKey.waiting_age` / `service_age` (PIBT policies) | **1.0 s** (`traffic.priority_age_quantum_s`) | +1 integer per bucket | `src/amr.py:5010`–`src/amr.py:5013`, `src/settings.py:174` |

**Why not continuous.** From `src/amr.py:4990`–`src/amr.py:4995`:

> Ageing is DISCRETE, in five-second steps, and that is not a detail. Continuous ageing makes two waiting robots swap rank several times a second, so neither ever holds the lead long enough to finish a commit round and both thrash at the mouth forever.

The arithmetic is the argument. A commit round is 0.45 s. Two robots whose priorities age continuously at the same rate cross over whenever a tiny perturbation — a lost heartbeat, a 3 ms latency jitter, a pose-noise cell flip — changes the ordering. If rank can flip faster than 0.45 s, no robot ever completes an admission, both keep re-entering the gate, and the aisle never drains. Stepping keeps the order stable for at least one whole bucket — 1.0 s or 5.0 s, i.e. 2.2× or 11× a commit round — while still guaranteeing that a long-suffering robot eventually outranks a loaded one and nobody starves.

This is a genuine ordering property, not a tuning preference: quantisation converts a dense order (where ties are measure-zero and crossings are continuous) into a discrete one where equality is common and the `robot_id` tiebreak, which is *stable*, does the deciding. Stability is what a commit round needs.

`priority_max_depth = 64` bounds the inheritance recursion defensively (`src/settings.py:175`), even though a physical conflict chain cannot exceed the fleet size.

---

## 9. Partition and dead-zone behaviour

### 9.1 What a robot does when it hears nobody

There is no special "isolated" mode. Degradation is a consequence of the peer table emptying, and every consumer of that table is written to be correct when it is empty.

| Time since last packet | State | Consequence |
|---|---|---|
| 0 | Normal | Full peer table |
| > `peer_stale_s` (1.0 s) | Intent expired | Stale peers keep their pose but contribute no reservations (`src/amr.py:5675`–`src/amr.py:5679`); freshness filters begin excluding them from bidding (`src/amr.py:3997`–`src/amr.py:3999`), charger scoring (`src/amr.py:842`) and conflict detection (`src/amr.py:672`) |
| > `central_timeout_s` (1.5 s) since a beacon | `mode = DEGRADED_P2P` | For decentralized policies this is a no-op: the mode was already `DEGRADED_P2P` from construction (`src/amr.py:217`). For central baselines it means *park* (`src/amr.py:2585`–`src/amr.py:2588`) |
| > `6 × peer_stale_s` (6.0 s) | Peer record deleted | The robot is now planning alone |

Alone, the robot is fully functional:

- **PIBT** runs on a one-element configuration `{self: cell}` (`src/amr.py:1861`–`src/amr.py:1866`) and trivially returns the preferred move.
- **Zone leases** find no owner — `_bios_lock` returns `None` when there are no peers and no unexpired claims (`src/amr.py:2311`–`src/amr.py:2318`) — so the robot claims, waits one 0.45 s commit round, and enters.
- **A* routing** is entirely local (`src/amr.py:2597` onward); the long-range route never depended on the network.
- **Layer 0** never depended on the network by construction (`src/settings.py:24`–`src/settings.py:25`).

So the degradation path is: *lose coordination efficiency, keep autonomy, keep safety.* The price is that PIBT is now resolving a configuration that omits real robots — which is exactly why the collision guarantee is Layer 0's and not the protocol's.

### 9.2 Anti-entropy: the machinery that makes rejoin converge

Three gossip channels heal state that was missed during an outage. All three deliberately **stay quiet when the network is demonstrably healthy** and turn themselves back on the moment any known peer goes stale.

| Channel | Period | Health suppression | Line |
|---|---|---|---|
| Task catalog (`TN`) | 1.0 s | Stops after 2 stale-windows of every known peer being fresh, and only when `loss == 0` and there are no dead zones | `src/amr.py:5145`–`src/amr.py:5187` |
| Completion catalog (`TD`) | 1.0 s | Same, plus a recovery grace so terminal facts sent *inside* an outage cross the heal | `src/amr.py:5189`–`src/amr.py:5237` |
| WMS re-announcement | 4.0 s | None — it is the bootstrap backstop, because if every robot loses the first multicast, peer gossip has no copy to repair from | `src/main.py:295`–`src/main.py:300` |

The `TASK_NEW` ingest path is written for exactly this: it is idempotent, generation-aware, and refuses to resurrect a task whose completion certificate it already holds (`src/amr.py:5320`–`src/amr.py:5330`), so anti-entropy cannot undo terminal state.

### 9.3 Recovery on rejoin

The scenario `partition_recovery` (`src/scenarios.py:543`–`src/scenarios.py:564`) splits a 4-robot fleet into two islands at t = 2 s and heals at t = 12 s, applied by the runner at `src/main.py:231`–`src/main.py:234`. On rejoin:

1. Heartbeats arrive; peer records repopulate within one heartbeat period.
2. Both catalogs resume gossiping because `_task_network_healthy_since` was reset while peers were stale (`src/amr.py:5168`, `src/amr.py:5218`).
3. Task claims held by an unreachable owner have already expired locally and restarted the auction at a new epoch (`src/amr.py:4690`–`src/amr.py:4729`); `_safe_incoming_epoch` prevents the returning island's stale epoch from winning.
4. Duplicate work is terminated by the completion certificate, which binds `(task, generation, descriptor_hash)` and is idempotent across epochs (`src/task_protocol.py:92`–`src/task_protocol.py:204`).

Pinned by `tests/test_resilience.py:41` — all 4 tasks completed, 0 robot-robot contacts — and, under 20 % uniform loss, by `tests/test_resilience.py:53`.

There is a `BIOS_PIBT.6`-only refinement worth knowing for questions: a fresh authenticated heartbeat naming the same active task extends an *existing* claim, but can never create ownership (`src/amr.py:4694`–`src/amr.py:4710`). That distinction is what lets a robot legitimately unreachable inside a mapped radio hole keep its task without letting a heartbeat mint an assignment.

---

## 10. Message budget

### 10.1 Measured, not estimated

`bytes_sent` is summed from `len(msg.encode(m))` on every emitted message (`src/amr.py:630`) and divided by robots × simulated seconds (`src/main.py:496`–`src/main.py:497`). It counts **robot traffic only** — the WMS injector and the manager are excluded from the aggregate (`src/main.py:390`–`src/main.py:391`).

From `artifacts/benchmarks/bios6-three-way-comparison.json`, `sih_acceptance_overlap`, 30 seeds per cell:

| Policy | Robots | msg/robot/s | bytes/robot/s (unauthenticated) |
|---|---:|---:|---:|
| `BIOS_PIBT.6` | 4 | 9.46 | 1 798 |
| `BIOS_PIBT.6` | 6 | 11.01 | 2 080 |
| `BIOS_PIBT.6` | 8 | 11.37 | 2 147 |
| `prioritized_space_time_astar` (central baseline) | 4 / 6 / 8 | 5.72 | ≈969 |
| `stop_and_wait_competition` | 4 / 6 / 8 | 4.72 | ≈884 |

From `artifacts/benchmarks/sih-acceptance.json`, the earlier `BIOS_PIBT.5` generation ran at 14.9–15.4 msg/robot/s — event-triggered communication in `BIOS_PIBT.6` cut per-robot traffic by roughly a quarter with no loss of the safety result. A live run at HEAD (`sih_acceptance_overlap`, 4 robots, 60 s, seed 0, `auction_bundle`) reproduces 13.39 msg/robot/s with 634 heartbeats sent against 494 suppressed and 424 intents against 177 suppressed.

Two honest observations:

1. **Per-robot rate grows slowly with fleet size, and does not grow at all for the baselines.** The baselines are periodic; ours is event-triggered, so more robots means more events. 9.46 → 11.37 across 4 → 8 robots is a 20 % rise for a 100 % fleet increase.
2. **Decentralisation costs bandwidth.** `BIOS_PIBT.6` uses about 2.2× the bytes of pure stop-and-wait. That is the price of sharing intent, and it should be quoted rather than hidden.

### 10.2 What that means for a real 802.11 link on a Pi

Multicast means each robot's packet is **one** transmission heard by all, so channel load is O(N) in the fleet while *receive-side* processing is O(N²) in aggregate. At 8 robots and 11.37 msg/robot/s:

| Quantity | Value | Derivation |
|---|---|---|
| Frames on the channel | ≈91 packets/s | 8 × 11.37 |
| Payload, unauthenticated | ≈17.2 kB/s | 8 × 2 147 |
| HMAC + session overhead | +8.9 kB/s | 91 × 98 bytes (measured constant, [§2.2](#22-every-message-type)) |
| UDP payload on the wire | **≈26.1 kB/s ≈ 209 kbit/s** | sum of the two above |
| Adding 28 B IPv4+UDP headers | ≈28.7 kB/s ≈ 230 kbit/s | 91 × (287 + 28) |

**Not verified:** actual 802.11 airtime. Multicast frames are transmitted at the basic rate, not the negotiated rate, and this repository contains no over-the-air measurement to pin that rate — the multi-process demo runs on loopback multicast. The correct statement to a judge is: *the payload rate is ~230 kbit/s at eight robots, which is small; whether that is small in airtime depends on the basic rate the AP is configured for, and we have not measured it.*

CPU, by contrast, **is** measured on real processes (`artifacts/benchmarks/bios6-distributed-demo.json`): mean control-loop time 0.083 ms, p99 0.389 ms, max 2.99 ms against a 20 ms budget, with 0 deadline misses over 6 000 ticks and 29.5 MB peak RSS. See [08. Edge Deployment](08-EDGE-DEPLOYMENT.md).

### 10.3 Per-type budget

Emission counts from the same 60 s, 4-robot run at HEAD:

| Class | Sent | Suppressed | Share of 3 214 |
|---|---:|---:|---:|
| Heartbeat | 634 | 494 | 20 % |
| Intent | 424 | 177 | 13 % |
| Auction (`TN`+`BD`+`AW`+`TD`) | 2 065 | — | 64 % |
| Coordination (`CL`/`RL`/`YD`/`EX`) | 91 | — | 3 % |

The dominant cost is **task allocation, not motion coordination** — largely certificate-bearing `TASK_DONE` (438 authenticated bytes) and lease-renewal `AWARD`s. Anyone optimising the radio budget should start there, not with the heartbeat.

---

## 11. Observing real packets — the `tcpdump` claim

**The multi-process UDP runner exists and is implemented.** `src/distributed_demo.py` spawns one OS process per robot via `multiprocessing` with the `spawn` context (`src/distributed_demo.py:176`), each owning an independent `AMRBrain`, monotonic clock epoch, task state, replay window, and real authenticated multicast socket (`src/distributed_demo.py:40`–`src/distributed_demo.py:62`). It refuses to run with fewer than three robots (`src/distributed_demo.py:164`–`src/distributed_demo.py:165`), which is requirement 1 enforced in code.

The parent process is a **physics and lidar referee only**: it sends each child a sensor frame over an OS pipe and applies the returned wheel command to `World` (`src/distributed_demo.py:239`–`src/distributed_demo.py:256`). It never forwards a peer message and never chooses a movement, priority, or auction winner. All peer traffic goes through the kernel's multicast stack.

To observe it:

```bash
export SIH_FLEET_PSK="$(python -c 'import secrets; print(secrets.token_hex(32))')"
python edge_demo.py --robots 3 --duration 5 --port 26231 \
  --allocation-policy auction --output artifacts/edge-demo-local.json
# in another terminal, on Linux:
sudo tcpdump -ni any udp port 26231
```

(`edge_demo.py` is a two-line entry point onto `src.distributed_demo.main`. The default group and port are 239.26.1.23:26123, `src/transport.py:44`–`src/transport.py:45`; the runbook overrides the port to avoid clashing with a concurrent default-port run.)

What the capture proves, and what it does not:

| Claim | Status | Evidence |
|---|---|---|
| Separate OS processes, distinct PIDs | Verified | `separate_processes` asserted at `src/distributed_demo.py:276`; `tests/test_edge_runtime.py:89` |
| Unrelated monotonic clock epochs | Verified | Offsets 10 000/20 000/30 000 s (`src/distributed_demo.py:190`–`src/distributed_demo.py:192`); asserted `tests/test_edge_runtime.py:93` |
| Every packet HMAC-authenticated, zero auth/malformed/replay failures | Verified | `src/distributed_demo.py:278`–`src/distributed_demo.py:282`; checked-in run reports all three counters at 0 |
| No missed control deadlines | Verified | `src/distributed_demo.py:283`–`src/distributed_demo.py:286`; 0/6 000 ticks in the artifact |
| Zero robot-robot contacts | Verified for this run | `src/distributed_demo.py:287`; 3 robots, 120 s, 12/12 tasks |
| Human-readable protocol in the capture | Verified | Compact JSON by design (`src/messages.py:143`) — the body is legible in `tcpdump -A` |
| "Peer messages observed" | ⚠️ **weak assertion** | `src/distributed_demo.py:277` checks `brain.msgs_recv > 0`, but `msgs_recv` is incremented *before* the self-source filter (`src/amr.py:5268`–`src/amr.py:5270`), so `IP_MULTICAST_LOOP` traffic counts. The checked-in artifact confirms this arithmetically: every node reports `recv = 4217 = 4205` (all three robots' sends, including its own) `+ 12` (WMS). The flag would pass for a node that heard only itself. The *real* proof of peer exchange in that run is that all three robots completed the same 12-task catalog with zero contacts, which is impossible without peer traffic. |
| Runs on Windows | Not verified | The checked-in artifact is macOS; `resource` is POSIX-only with a ctypes fallback (`src/distributed_demo.py:106`–`src/distributed_demo.py:151`). `tcpdump` guidance is Linux-only. |
| Real Raspberry Pi / Jetson hardware | Not verified in this repository | `archive/DEMO_AND_JUDGING.md:50` lists a Pi/Jetson JSON report as an outstanding evidence item |

---

## 12. Security posture

Stated briefly, because it is protocol surface even though it is not a stated requirement.

| Property | Mechanism | Status |
|---|---|---|
| Integrity and fleet-membership authenticity | HMAC-SHA256 over the canonical envelope, `hmac.compare_digest` | Implemented and tested (`tests/test_core.py:397`) |
| Replay / duplicate rejection | 64-wide sliding window keyed `(src, sid)`, tolerating reordering | Implemented and tested (`tests/test_core.py:412`) |
| Restart safety | Fresh random `sid` per boot means a restarted node's `seq=1` is not mistaken for a replay | `src/transport.py:222`, `src/messages.py:82`–`src/messages.py:84` |
| DoS-bounded state | Replay sessions capped at 1024 with TTL and LRU eviction | `src/transport.py:290`–`src/transport.py:306` |
| Malformed input never crashes the control loop | Every rejection is a return value, never an exception | `src/messages.py:214`–`src/messages.py:216` |
| Task identity binding | `TN` descriptor hash recomputed and checked on ingest; completion certificates self-verify a nonce and ownership proof | `src/messages.py:331`, `src/task_protocol.py:158`–`src/task_protocol.py:186` |
| Per-device identity, key rotation, secure provisioning | **Not implemented.** A single shared PSK authenticates membership, not role | admitted at `src/messages.py:148`–`src/messages.py:150` |
| Role authorisation | **Weak.** A directed `AWARD` is trusted on `src == "FM0"` alone, which any PSK holder can forge | `src/amr.py:5488` |

The batch simulator uses **no** authentication (`src/transport.py:145`). Every benchmark number in [12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md) is therefore measured on unauthenticated payloads; add 98 bytes per packet for the deployed form.

---

## 13. Defects and contradictions found

Recorded here because a judge who finds them first is worse than a team that found them itself. Each is reproducible at HEAD.

### D1 — An `INTENT` from an unheard peer creates a phantom robot at cell (0, 0)

**Severity: real, self-healing within one heartbeat period.**

`_ingest`'s `INTENT` branch does `self.peers.setdefault(m.src, Peer(m.src))` (`src/amr.py:5295`) and then sets `intent`, `windows`, `priority` and `last_seen` — but **not** `cell` or `pose`. The `Peer` dataclass defaults are `cell = (0, 0)` and `pose = (0, 0, 0)` (`src/amr.py:133`–`src/amr.py:134`). `HEARTBEAT` and `INTENT` are separate datagrams drawing independent loss samples, and under `BIOS_PIBT.6` they are gated separately, so a receiver can legitimately see an `INTENT` before it has ever seen a `HEARTBEAT` from that peer.

Reproduced at HEAD:

```
peer created by INTENT alone -> cell = (0, 0)  pose = (0.0, 0.0, 0.0)  state = idle
known_peer_ids = set()
after 2.0 s: present: True  intent cleared: True  cell still (0, 0)
after 7.0 s: present: False
```

Consequences while the phantom persists: it occupies (0, 0) in the PIBT configuration (`src/amr.py:1873`), in the unstick escape set (`src/amr.py:2248`), and in idle-parking exclusion (`src/amr.py:3217`); and `_bios_lock` will report it as the physical owner of whatever zone contains (0, 0) (`src/amr.py:2311`–`src/amr.py:2313`). It is also absent from `_known_peer_ids`, which is populated only in the heartbeat branch (`src/amr.py:5275`) and gates relayed-completion validation (`src/amr.py:5573`).

Bounded by: the next heartbeat corrects `cell` (≤0.6 s idle, ≤0.2 s in conflict under V6), and the record is deleted after 6.0 s of total silence. The suggested fix is a one-liner — do not create a peer record from `INTENT` alone, or mark an intent-only record as position-unknown and exclude it from every occupancy set.

### D2 — `archive/WIRE_PROTOCOL.md` omits the `EXPERIENCE` message type

The "Message types" list at `archive/WIRE_PROTOCOL.md:52`–`archive/WIRE_PROTOCOL.md:59` enumerates 12 of the 13 types in `ALL_TYPES` (`src/messages.py:52`–`src/messages.py:53`). `EX` is absent, although it is fully implemented, validated (`src/messages.py:430`–`src/messages.py:440`) and tested (`tests/test_core.py:671`). This document's [§2.2](#22-every-message-type) is the complete list.

### D3 — `backend/server.py:20` and `archive/DEMO_AND_JUDGING.md:33` describe a passive multicast dashboard listener that does not exist

Detailed in [§6.3](#63-what-happens-if-the-dashboard-is-switched-off). No component in the repository joins the multicast group except robot nodes and the WMS task source. The conclusion those documents draw (the dashboard cannot influence the fleet) is correct and in fact stronger than stated; the stated mechanism is not implemented.

### D4 — `archive/WIRE_PROTOCOL.md:56` describes "`CL`/`RL`: expiring claim or early release for a cell/block", but the cell wire form is never sent

`msg.claim` and `msg.release` (`src/messages.py:518`–`src/messages.py:527`) have no call site in `src/` or `tests/`, and the ingest branch matches only the block form (`src/amr.py:5624`). Per-cell leasing is real but is carried in the block form with a synthetic zone id ≥ 1 000 000 (`src/amr.py:1653`). The documentation is not wrong about the *capability*; it is wrong about the *encoding*.

### D5 — `YIELD` has no receiver

Emitted at `src/amr.py:1315` and `src/amr.py:2480`; no ingest branch exists anywhere. This is arguably by design (`src/messages.py:42` calls it a way to make deadlock-breaking observable) but `archive/WIRE_PROTOCOL.md:57`'s "observable yield decision" should not be read as "peers act on it".

### D6 — `MAX_PLAN_CELLS = 1024` is unreachable, and a long `PLAN_RSP` would raise inside the manager tick

`MAX_PLAN_CELLS` allows 1024 cells (`src/messages.py:60`), but `encode` raises above `MAX_DATAGRAM_BYTES = 2048`. Measured at HEAD, a timed `PLAN_RSP` crosses 2048 bytes between 160 and 180 cells, and crosses the 1400-byte simulated MTU near 118 cells. `_plan_fleet` applies no length cap (`src/fleet_manager.py:208`–`src/fleet_manager.py:217`), and `SimNetwork.send` calls `msg.encode` without a `try` (`src/transport.py:146`), so an over-long plan would raise out of the runner rather than being counted as a drop.

**Not reachable on the shipped maps.** The longest corner-to-corner A\* route measured across `dense_aisles`, `crossing_chokepoint`, `sih_acceptance_overlap` and `showcase_grand_challenge` is 51 cells, producing a 656-byte `PLAN_RSP`. This affects only the central baselines, which never run on the UDP transport. It is a latent bound, not a live bug — but a larger warehouse map would hit it.

### D7 — `archive/DECENTRALIZED_PRIORITY.md` results and conclusion are stale

That document reports a `BIOS_PIBT.1` development snapshot (`archive/DECENTRALIZED_PRIORITY.md:135`–`archive/DECENTRALIZED_PRIORITY.md:145`) and concludes at `archive/DECENTRALIZED_PRIORITY.md:169`–`archive/DECENTRALIZED_PRIORITY.md:171` that the project should **not** publish a 20 % completion-time result until a two-phase motion primitive is complete. The shipped default is now `BIOS_PIBT.6`, and `artifacts/benchmarks/sih-acceptance.json` exists. Whether the current evidence discharges requirement 20 is [12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md)'s call, not this document's — but the two documents currently disagree, and a judge who reads `archive/DECENTRALIZED_PRIORITY.md` will notice.

Two smaller stale points in the same file: `archive/DECENTRALIZED_PRIORITY.md:39` says "Every heartbeat carries a frozen `PriorityKey`", which is true only for `PIBT_POLICIES` — baselines send heartbeats with no `pk` at all (`src/amr.py:5041`–`src/amr.py:5043`); and `archive/DECENTRALIZED_PRIORITY.md:46`–`archive/DECENTRALIZED_PRIORITY.md:47` says ages are "quantized" without stating that the two ageing schemes use different quanta ([§8](#8-ageing-must-be-in-discrete-buckets)).

---

## 14. Verification status

| Claim | Status | Evidence |
|---|---|---|
| 13 message types encode, decode and round-trip | Implemented and tested | `tests/test_core.py:664`, `tests/test_core.py:671` |
| Malformed / non-finite / oversized / wrong-shape input is dropped, never raised | Implemented and tested | `tests/test_core.py:373`, `tests/test_core.py:379`, `tests/test_core.py:389` |
| HMAC rejects tampering and unsigned packets | Implemented and tested | `tests/test_core.py:397` |
| Replay window accepts reordering, rejects duplicates and old sequences | Implemented and tested | `tests/test_core.py:412` |
| All cross-node deadlines are receiver-local durations | Implemented and tested | `tests/test_core.py:421` |
| Block lease carries a local TTL and a frozen key | Implemented and tested | `tests/test_priority.py:110` |
| Radio model is deterministic per seed, and counterfactually fair | Implemented and tested | `tests/test_core.py:684`, `tests/test_core.py:697` |
| Dead zones kill peer traffic exactly as they kill server traffic under an AP relay | Implemented and tested | `tests/test_core.py:721` |
| Shipped policies build no fleet manager | Implemented and tested | `tests/test_core.py:465`, `tests/test_core.py:478`, with the converse at `tests/test_core.py:511` |
| Peer catalog gossip recovers a task missed with no manager present | Implemented and tested | `tests/test_priority.py:329` |
| Partition heals and catalogs converge | Implemented and tested | `tests/test_resilience.py:41`, and under 20 % loss `tests/test_resilience.py:53` |
| Three real OS processes exchange authenticated multicast with unrelated clocks | Implemented and tested | `tests/test_edge_runtime.py:80` |
| Message and byte budget | Measured | `artifacts/benchmarks/*.json`; metering at `src/amr.py:628`–`src/amr.py:640` |
| `PriorityKey` total order and PIBT collision-freedom on cell endpoints | Implemented and tested | `tests/test_priority.py:26`, `tests/test_priority.py:88` |
| 802.11 airtime on real hardware | **Not verified** | No over-the-air measurement in this repository; loopback multicast only |
| Raspberry Pi / Jetson execution of the UDP demo | **Not verified here** | Outstanding evidence item, `archive/DEMO_AND_JUDGING.md:50` |
| Windows execution of the multi-process demo | **Not verified** | Checked-in artifact is macOS |

---

## See also

[00. Problem Statement](00-PROBLEM-STATEMENT.md) · [01. Requirements Traceability](01-REQUIREMENTS-TRACEABILITY.md) · [02. Architecture](02-ARCHITECTURE.md) · [04. Path Planning](04-PATH-PLANNING.md) · [05. Coordination Policies](05-COORDINATION-POLICIES.md) · [06. Task Allocation](06-TASK-ALLOCATION.md) · [07. Safety](07-SAFETY.md) · [08. Edge Deployment](08-EDGE-DEPLOYMENT.md) · [09. Dashboard](09-DASHBOARD.md) · [10. API Reference](10-API-REFERENCE.md) · [11. Scenarios](11-SCENARIOS.md) · [12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md) · [13. Testing](13-TESTING.md) · [14. Findings](14-FINDINGS.md) · [15. Limitations](15-LIMITATIONS.md) · [16. Demo Runbook](16-DEMO-RUNBOOK.md)
