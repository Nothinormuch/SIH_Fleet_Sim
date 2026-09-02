# 09. FLEET DASHBOARD

> This document establishes that the browser dashboard in `frontend/` satisfies requirements 16, 17 and 18 — a lightweight monitoring UI showing every AMR's real-time position and battery status — and that it does so as a passive reader of a recorded run rather than as the central coordinator the problem statement forbids.

**Audience:** SIH judges and BEL evaluators reading the frontend for the first time, and teammates who have to defend it in front of a laptop.
**Reads best after:** [02. Architecture](02-ARCHITECTURE.md)

**Baseline.** Everything below describes the committed tree. HEAD is `7740efb`, two commits ahead of the suite baseline `07337e0`; the differences are the boot screen, covered in [§11](#11-the-boot-screen--landed-after-the-suite-baseline), and the hit-testing fixes covered in [§12](#12-hit-testing-fixes-in-bioscss-committed-as-7740efb).

## Requirements evidenced

| # | Requirement | Where | Evidence |
|---|---|---|---|
| 16 | Fleet dashboard | [§1](#1-what-the-dashboard-is-and-what-it-is-not), [§2](#2-three-layers-and-nothing-outside-them) | `frontend/index.html:27`, served by `backend/server.py:790` |
| 17 | Real-time positions | [§3](#3-real-time-positions--requirement-17) | `frontend/js/main.js:922`, `frontend/js/digital-twin.js:1189` |
| 18 | Battery status | [§4](#4-battery-status--requirement-18) | `src/world.py:881`, `frontend/js/hud.js:206`, `frontend/js/main.js:1092` |
| 3 | Decentralized communication | [§5.2](#52-live-peer-links) | `frontend/js/network.js:65`, `frontend/js/digital-twin.js:1305` |
| 4 | Position sharing | [§5.2](#52-live-peer-links) | `src/main.py:205` (`peers`), `frontend/js/network.js:72` |
| 5 | Intent sharing | [§5.1](#51-published-intent-horizons) | `src/main.py:204` (`path`), `frontend/js/network.js:28` |
| 6 | No central coordination server | [§1.2](#12-why-a-passive-reader-is-the-honest-answer) | `backend/server.py:15-24` |
| 10 | Deadlock resolution | [§5.3](#53-wait-for-arrows) | `src/main.py:207` (`blocked_on`), `frontend/js/network.js:106` |
| 11 | Chokepoint handling | [§5.4](#54-single-file-blocks-under-block-control) | `frontend/js/environment.js:266` |
| 12 | Blocked aisle handling | [§6.1](#61-the-scene-graph) | `frontend/js/digital-twin.js:1145`, `:1253` |
| 19 | Zero inter-robot collisions | [§4.2](#42-and-five-other-places-a-number-is-shown) | `frontend/js/main.js:1432`, `frontend/js/shell.js:221` |

Requirements 7, 8, 9, 13, 14, 15 and 20 are established elsewhere in the suite; the dashboard renders their evidence but is not itself the evidence. See [01. Requirements Traceability](01-REQUIREMENTS-TRACEABILITY.md).

**Status vocabulary used below:** *implemented and tested* (code plus an automated test that exercises it), *implemented* (code, verified by reading and by running the server, no automated test), *simulated only*, *not implemented*.

---

## 1. What the dashboard is, and what it is not

### 1.1 Playback, not a live socket

The dashboard does not hold a socket open to a running fleet. It POSTs a run request to `/api/run` (`frontend/js/main.js:518`), receives a complete recorded run as one JSON document, and plays it back locally (`frontend/js/main.js:874`, `tick`). The payload is `{map, meta, frames, summary, demo_evidence}` (`src/main.py:681-721`).

This is a design decision with a stated rationale, not a shortcut. The reasoning is written into the server itself at `backend/server.py:7-13` and again at `src/main.py:571-577`:

1. **The simulation is much faster than wall-clock.** Streaming would mean throttling it back down to realtime for no benefit. Measured on the development machine for the opening scenario (`showcase_chokepoint`, 4 AMRs, seed 7, 320 s requested): the run reached `t = 254.0 s` of simulated time in 49.7 s and 72.3 s of wall-clock across two consecutive warm runs — roughly 3.5× to 5.9× realtime. A faster demo machine will do better; `frontend/js/boot-screen.js:71-73` records a measured opening of 6–9 s on the machine that comment was written on, which would be 28–42×. **The specific figure "≈22× realtime" that circulates in project notes is not verified anywhere in this repository**; the two anchored numbers are the ones above.
2. **A recorded run can be interrogated; a stream cannot.** Playback can be paused on the exact frame where two robots negotiate a chokepoint (`step()`, `frontend/js/main.js:840`, deliberately frame-accurate rather than time-accurate), scrubbed backwards (`frontend/js/main.js:100`), and replayed against a different policy on the same seed by changing one select and pressing Launch (`frontend/js/main.js:499-506`). That is what evaluating a coordination policy actually requires.
3. **A live stream can only ever be watched once.** A judge who missed the moment cannot ask for it back.

Status: *implemented and tested*. `tests/test_dashboard.py:416` asserts the recorded frames carry a monotone, bounded, unique task-completion count including the final frame; `tests/test_dashboard.py:396` asserts every recorded metric pose lands inside a non-rack cell.

### 1.2 Why a passive reader is the honest answer

Requirement 6 forbids a central coordination server. Requirement 16 asks for a UI that visualises *the entire fleet*. Taken naively these conflict: a fleet-wide aggregator is a central aggregator, with the same single point of failure the problem statement is trying to eliminate.

The project's answer is to make the dashboard structurally incapable of coordinating, and to say so plainly. From `backend/server.py:15-24`:

> In the distributed runner the dashboard is a **passive multicast listener**: it joins the group and reads the same datagrams the robots send each other. It cannot command anything, and switching it off changes nothing about how the fleet behaves. Here in the batch runner it is downstream of a completed simulation, which is even further from being a coordinator.

Two distinct claims, and they should be defended separately:

- **In the batch runner (this dashboard).** The dashboard is downstream of a *finished* simulation. There is no channel from the browser into a running fleet at all: `/api/run` is POST-only (`backend/server.py:285-288` returns 405 for GET) and returns only after `run_for_dashboard` has completed inside `_SIM_LOCK` (`backend/server.py:645-647`). Nothing the operator does during playback can reach a robot, because by then there are no robots. *Implemented and tested* — `tests/test_dashboard.py:249` asserts POST-only and the security headers.
- **In the real edge runner.** `src/distributed_demo.py` starts one OS process per AMR with its own clock epoch and authenticated multicast socket; the dashboard's role there is to join the group and read. That claim belongs to [08. Edge Deployment](08-EDGE-DEPLOYMENT.md) and is *not* exercised by this browser dashboard.

### 1.3 What happens to the fleet if the dashboard is closed

Nothing.

In the batch runner the question is trivially answered: the fleet does not exist while you are watching it, so closing the tab cannot affect it. The run is already recorded and the summary is already computed (`src/main.py:719`).

In the edge runner the answer is the one that matters to a judge, and it is architectural rather than incidental: the dashboard is a listener on a multicast group, so its absence removes a reader, not a participant. `archive/DEMO_AND_JUDGING.md:43` states the evidence for it — a packet capture showing multicast peer traffic continuing with the dashboard shut down. That capture is listed as an evidence-checklist item, not as a checked-in artifact; treat it as *implemented, demonstration pending* until the capture is produced.

### 1.4 What the dashboard is *not*

- It is **not** a WMS. Task announcement happens inside the simulation (`TN` events, `frontend/js/main.js:1170`); the dashboard only renders the announcements.
- It is **not** a safety system. The 50 Hz protective-stop layer is onboard and independent (`src/settings.py:110`). The dashboard's "Safety margin" vital is a *readout* of separation, computed client-side over the current frame (`frontend/js/hud.js:182`), and it never gates anything.
- It is **not** responsive to phone widths in the way the stale `archive/UI_UX_HUD_AUDIT.md` claims. See [§13](#13-contradictions-with-existing-docs).

---

## 2. Three layers, and nothing outside them

The whole interface is three stacked layers, stated in the markup at `frontend/index.html:11-26` and again in the stylesheet header at `frontend/css/bios.css:7-13`:

| Layer | Element | What it is | Cited at |
|---|---|---|---|
| Tier 1 · the world | `#world` | The 3D twin (`#twinCanvas`) or the 2D diagnostic floor (`#floor`), full-bleed, never cropped by a panel | `frontend/index.html:69-73`, `frontend/css/bios.css:126-131` |
| Tier 2 · the HUD | `#hud` | Always on, corners only, nothing that needs reading | `frontend/index.html:76-167`, `frontend/css/bios.css:159-164` |
| Tier 3 · the menu | `#menu` + `#sheet` | Summoned with Tab; two stages | `frontend/index.html:170-206`, `:210-436` |

The admission test for the HUD is written down at `frontend/js/hud.js:10`: **"what survives here is only what answers a question at a glance."** Three vitals passed it — Workload, Fleet charge, Safety margin (`frontend/js/hud.js:12-14`). Nine floating cards that used to sit on the warehouse did not, and moved into the menu (`frontend/js/hud.js:3-8`).

### 2.1 The stacking constraint

`#menu` is `z-index: 8` (`frontend/css/bios.css:690`), **below** `#hud`'s `z-index: 10` (`frontend/css/bios.css:163`). `#sheet` is `z-index: 45` (`frontend/css/bios.css:919`) and is a **sibling** of `#menu`, not a child — `#menu` closes at `frontend/index.html:206` and `.sheet` opens at `frontend/index.html:210`.

This is a constraint, not a style preference, and it has a specific failure mode. The reason is stated at `frontend/css/bios.css:682-686`:

> The rails sit BELOW the HUD (z-index 8 against its 10) [because] the health bars stay lit on top of the menu's gradient, and they cannot do that if the gradient is painted over them — the vitals just go grey. The sheet is a sibling rather than a child for exactly this reason: it needs to be above the HUD, and a child cannot escape its parent's stacking context.

**If someone "tidies up" by nesting `#sheet` inside `#menu`, the sheet inherits `#menu`'s stacking context and can no longer paint above `#hud` at any z-index.** The visible symptom is that the vitals in the top-left go grey behind the sheet's gradient — which reads as a colour bug, sends the next person hunting through the palette tokens, and has nothing to do with colour. Keep the sibling relationship.

### 2.2 Five flat categories

The menu has exactly five categories and no nesting (`frontend/js/shell.js:28`, markup at `frontend/index.html:180-184`):

| Key | Category | Panel | Needs a run? |
|---|---|---|---|
| `1` | Deployment | scenario gallery, mission parameters, Launch | no |
| `2` | Fleet | roster + selected-agent inspector | yes |
| `3` | Coordination | auction summary, allocation log, decision trace | yes |
| `4` | Evidence | run summary, contacts, separation, diagnostics | yes |
| `5` | System | render mode, jury mode, controls, BIOS_4 training | no |

`Q` and `E` step between them (`frontend/js/shell.js:369-374`), skipping categories that have nothing in them yet rather than opening an empty sheet (`frontend/js/shell.js:151-159`). Deployment and System are always reachable because "one of them is how you get a run in the first place, and the other is how you get out of trouble" (`frontend/js/shell.js:48-51`).

The rule that keeps the count at five is at `frontend/index.html:18-21`: *if it is not one of five categories it is not a category — it is a detail inside one.*

### 2.3 `shell.js` and `main.js`: the exact published surface

`frontend/js/shell.js` is a **classic script** and owns the keyboard, the menu, the toast and the verdict. It owns no simulation state and reads none (`frontend/js/shell.js:4-7`). `frontend/js/main.js` is an **ES module** and owns playback, telemetry and the twin.

They meet at exactly one object, published once in `boot()` at `frontend/js/main.js:145-161`:

| Member | Kind | Purpose |
|---|---|---|
| `app` | read-only handle | the `App` object; the shell reads `app.data`, `app.cameraMode`, `app.selectedRobotId` and calls `app.currentFrame()` / `app.robotColour()` |
| `togglePlay` | fn | Space |
| `cycleCameraMode` | fn | F5 / V |
| `setCameraMode(mode)` | fn | quick-camera slots |
| `cycleTargetRobot` | fn | C |
| `selectRobot(id)` | fn | quick-fleet slots |
| `adjustZoom(delta)` | fn | Z / X |
| `togglePresentationMode` | fn | J |
| `toggleFullscreen` | fn | F11 |
| `step(direction)` | fn | ← / → |
| `openBuilder` / `closeBuilder` | fn | Esc while the builder is up |

Two further read-only handles are attached to `App` immediately afterwards rather than to `window.BIOS`, because they are properties of the app rather than commands (`frontend/js/main.js:165-170`):

- `App.robotColour(id)` — so the right-hand rail draws each slot in the same colour the twin and the roster use.
- `App.currentFrame()` — returns the **interpolated frame at the playhead**, not `frames[0]`. This is what makes the rail's battery bars correct; see [§4.3](#43-read-at-the-playhead-not-at-frame-zero).

In the other direction, the shell is reached only through optional calls: `window.Shell?.verdict(...)`, `?.toast(...)`, `?.syncRail()`, `?.renderQuick()`, `?.clearVerdict()`, `?.cameraToast(...)`, `?.closeMenu()` (ten call sites, `frontend/js/main.js:107`–`:862`). Every one is optional-chained, so a page with `shell.js` removed still plays back.

**The property this buys:** the interface can be reworked, re-skinned or thrown away without touching a line of playback or telemetry code (`frontend/js/shell.js:5-7`). The suite's earlier single-file dashboard did not have it.

Two members of these public surfaces are currently dead: `Shell.showTab` is exported at `frontend/js/shell.js:414` with a comment saying "main.js reaches for it", and `main.js` does not; `Hud.dispose` is exported at `frontend/js/hud.js:265` and never called. `frontend/js/main.js:576` claims "init() disposes any previous instance" — `Hud.init` (`frontend/js/hud.js:240-248`) does not call `dispose()`, it performs the equivalent reset inline. The behaviour is correct; the comment and the two exports are not.

### 2.4 Load order and the module / classic split

`frontend/index.html:503-509` loads six classic scripts and then one module:

```
boot-screen.js  environment.js  amr.js  hud.js  network.js  shell.js   (classic)
main.js                                                                (type=module)
```

`main.js` consumes globals defined by the classic scripts — `View`, `Hud`, `loadAssets`, `buildStaticLayer`, `renderStaticFloor`, `drawFleet`, `drawNetwork`, `robotColour`. This is safe because a `type="module"` script is deferred by definition and therefore always executes after every classic script in the document has run. `boot-screen.js` is deliberately first and deliberately classic, so `window.BiosBoot` exists before `main.js` starts reporting stages into it (`frontend/index.html:499-502`).

`main.js` imports only one thing through the module graph: `DigitalTwin` from `./digital-twin.js` (`frontend/js/main.js:1`), which in turn imports Three.js.

---

## 3. Real-time positions — requirement 17

### 3.1 The telemetry frame

One frame is produced by `World.snapshot()` (`src/world.py:875-906`) and decorated with per-robot coordination state by `capture_trace_frame()` (`src/main.py:186-220`). Verified by running `run_for_dashboard('showcase_chokepoint', 'BIOS_PIBT.6', robots=4, seed=7, duration=320)` and reading the keys off the result:

**`frame.robots[]` — the pose channel (requirement 17):**

| Field | Type | Meaning | Cited at |
|---|---|---|---|
| `id` | string | `AMR01`… | `src/world.py:879` |
| `x`, `y` | float, **metres** | world pose; `+Y` is north | `src/world.py:879` |
| `th` | float, radians | heading, maths convention | `src/world.py:880` |
| `v` | float, m/s | speed (not read by the frontend; the PiP shows a state-derived constant instead — see below) | `src/world.py:880` |
| `batt` | float, 0–1 | state of charge, `battery_wh / battery_full_wh` | `src/world.py:881` |
| `carry` | task id or null | what is on the deck | `src/world.py:882` |

**`frame.fleet[]` — the coordination channel**, one row per robot, carrying `state`, `mode`, `task`, `goal`, `pick`, `drop`, `cargo_type`, `cargo_weight`, `task_priority`, `deadline`, `carry`, `path` (the next 8 cells of the intent horizon), `peers`, `blocked_on`, `priority_key`, `decision`, `done`, `failed` (`src/main.py:191-213`).

Also per frame: `humans[]`, `obstacles[]`, `contacts`, `manager_alive`, `tasks_completed`, `auction_events` (`src/world.py:885-905`, `src/main.py:214-219`).

`meta` carries the units contract — `pose_units: "metres"`, `cell_m`, `robot_diameter_m` (`src/main.py:694-696`) — so the renderer never has to guess whether a coordinate is a cell index or a metre. `tests/test_dashboard.py:400` asserts `pose_units == "metres"` and that every pose floors into a free cell.

### 3.2 10 Hz telemetry against 50 Hz physics

Telemetry is emitted at `telemetry_hz = 10.0` (`src/settings.py:112`) while the world integrates at `world_hz = 50.0` and the onboard safety layer runs at `safety_hz = 50.0` (`src/settings.py:108-109`). The gate is `src/main.py:351`. Measured on the opening run: 2,541 frames, with the first twelve inter-frame deltas all exactly `0.1 s`.

The comment at `src/settings.py:101-105` is the argument the whole project rests on and is worth quoting when a judge asks why the dashboard's rate is the slow one: *the three control loops the problem statement conflates into one are kept separate, because only the slowest one was ever a candidate for a central server, and it is the one least hurt by latency.* Telemetry is the slowest loop and it is explicitly annotated "passive listener, not a coordinator" (`src/settings.py:112`).

### 3.3 Interpolation, and the shortest-arc heading

Drawing 10 Hz frames verbatim at 60 fps would look like stop-motion of something that is actually smooth. So playback interpolates (`frontend/js/main.js:5-9`).

`bracket(t)` (`frontend/js/main.js:892-911`) binary-searches the recorded timestamps for the two frames straddling the playhead and returns `[f0, f1, u, idx]`. It **binary-searches rather than dividing by 0.1** on purpose: a run may append a completion frame between two scheduled samples, so the index-to-time mapping is not perfectly uniform (`frontend/js/main.js:899-901`).

`interpolate(f0, f1, u)` (`frontend/js/main.js:922-962`) then blends:

- `x`, `y`, `batt` — linear (`lerp`, `frontend/js/main.js:913`).
- `th` — **shortest-arc**, via `lerpAngle` (`frontend/js/main.js:915-920`):

```js
function lerpAngle(a, b, u) {
  let d = b - a;
  while (d >  Math.PI) d -= Math.PI * 2;
  while (d < -Math.PI) d += Math.PI * 2;
  return a + d * u;
}
```

Without the wrap, a robot crossing ±π — heading `+3.10 rad` on one frame and `−3.10 rad` on the next — would be interpolated as a **−6.20 rad sweep**, i.e. a full spin the long way round, compressed into a tenth of a second, once per pass. The unwrap turns that into the +0.08 rad it actually was. The same function is applied to human headings (`frontend/js/main.js:946`), where `Number.isFinite` guards cover a worker whose heading is absent (`frontend/js/main.js:940-941`).

Discrete fields — `carry`, `mode`, `paused`, `obstacles`, `fleet`, `tasks_completed` — are **snapped at the midpoint** (`u >= 0.5`) rather than blended (`frontend/js/main.js:933`, `:947-957`), because half a carry flag is meaningless.

Status: *implemented*. The interpolation math has no dedicated automated test; the coordinate transform underneath it does (`tests/test_dashboard.py:279`).

### 3.4 Where the interpolated pose is drawn

`draw()` (`frontend/js/main.js:966-1019`) brackets and interpolates once, then hands the same frame to every consumer, so nothing on screen can disagree with anything else about where a robot is:

| Consumer | Call site |
|---|---|
| 3D twin | `App.twin.update(frame, …)` — `frontend/js/main.js:975` |
| 2D fallback | `drawFleet(ctx, App.view, frame, …)` — `frontend/js/main.js:997` |
| PiP viewfinder | `renderPiP(frame, selectedRobot)` — `frontend/js/main.js:1006` |
| Scrubber + clock | `frontend/js/main.js:1008-1009` |
| Fleet roster, inspector, auction, decisions, spotlight | `frontend/js/main.js:1010-1017` |
| HUD vitals and event rail | `Hud.render(frame, …)` — `frontend/js/main.js:1018` |

In the twin, the pose lands at `frontend/js/digital-twin.js:1189-1190`:

```js
group.position.copy(this._toWorld(robot.x, robot.y, 0));
group.rotation.y = -robot.th - Math.PI / 2;
```

---

## 4. Battery status — requirement 18

### 4.1 Where the value comes from

`batt` is produced by the simulation as a fraction, `battery_wh / cfg.robot.battery_full_wh`, rounded to three places (`src/world.py:881`). It rides on `frame.robots[]`, **not** on `frame.fleet[]` — a distinction that matters, and that has already caused one bug (see [§4.3](#43-read-at-the-playhead-not-at-frame-zero)).

Battery is not just a readout: it is a decision input. The energy gate refuses a bid whose task-plus-charger-return reserve is infeasible, against `meta.energy_reserve_frac` (`src/main.py:697`), and the reserve floor is surfaced in the inspector as "Reserve floor ≥ 15%" (`frontend/js/main.js:1078`, `:1093`). For how battery enters the auction, see [06. Task Allocation](06-TASK-ALLOCATION.md).

### 4.2 …and five other places a number is shown

Requirement 18 is satisfied at a glance by one element and in depth by five more:

| # | Where | What it shows | Cited at |
|---|---|---|---|
| 1 | HUD vital "Fleet charge" (always on) | fleet **mean** SoC, with `is-low` below 2× reserve and `is-crit` below reserve | markup `frontend/index.html:84-87`; logic `frontend/js/hud.js:206-216` |
| 2 | Menu → Fleet → roster row | per-robot bar, class `crit` under 15%, `low` under 35% | `frontend/js/main.js:1271-1272`, `:1293` |
| 3 | Menu → Fleet → selected-agent inspector | per-robot percentage + bar + reserve floor | `frontend/js/main.js:1077`, `:1092` |
| 4 | Menu right-hand rail, quick-fleet slot | per-robot mini-bar and tooltip | `frontend/js/shell.js:183-190` |
| 5 | PiP live viewfinder, "Bat" | selected robot's percentage | markup `frontend/index.html:160`; logic `frontend/js/main.js:1064`, `:1068` |
| 6 | 2D fallback, on-canvas pip | drawn **only** below 35%, red below 15% | `frontend/js/amr.js:270-275` |

Number 6 is a deliberate omission rather than a gap: *"a permanent bar over every robot is noise"* (`frontend/js/amr.js:269`). In the 3D twin the equivalent cue is the state halo and the charging badge (`frontend/js/digital-twin.js:1197`, `:1202`), not a number.

The related safety headline — contacts, the number requirement 19 rests on — is pinned in the menu's corner counter (`frontend/js/shell.js:221-235`) precisely because it would otherwise live three clicks deep in Evidence (`frontend/js/shell.js:217-220`), and in the Evidence panel itself (`frontend/js/main.js:1431-1438`).

Status: *implemented*. Every path above was read; the underlying values are covered by the simulation's own tests.

### 4.3 Read at the playhead, not at frame zero

This is the sharpest thing to know about the battery display, and it is written down at `frontend/js/shell.js:173-175`:

> Battery lives on the robots array, not on the fleet rows, and it has to be read at the playhead rather than at frame zero or every slot shows 100%.

The right-hand rail is rendered by `shell.js`, which by design holds no simulation state. The naive way for it to get a fleet list is `app.data.frames[0]` — the first recorded frame — which is a perfectly good source for *identity* and a useless one for *state*: at `t = 0` every robot is at its starting charge and every slot renders the same bar for the whole run.

The fix is the read-only accessor `App.currentFrame()` (`frontend/js/main.js:166-170`), which re-brackets and re-interpolates at the current `simTime`. The rail asks for it and falls back to frame zero only if no run is loaded (`frontend/js/shell.js:175`):

```js
const frame = app?.currentFrame?.() || app?.data?.frames?.[0];
```

The same discipline applies everywhere else: the HUD vital, the roster, the inspector and the PiP all read from the frame `draw()` just interpolated, not from `App.data`.

A worked check on the opening run: at frame zero `AMR01` reports `batt: 0.48` (scenarios seed initial charges, `src/main.py:149-152`), so a frame-zero read would peg that robot at 48% for 254 s regardless of what it charged to.

---

## 5. Visualizing the invisible

This is the strongest design argument in the project, and `frontend/js/network.js:1-13` states it better than a paraphrase:

> This file exists because decentralisation is invisible. A warehouse of robots moving around looks identical whether they are coordinating peer-to-peer, following a central schedule, or blundering past each other on luck. The messages are the whole claim, so the messages get drawn.

The constraint that makes it evidence rather than decoration is the last line of that block: **"Nothing is synthesised for the picture."** Every overlay below is rendered from a field the robots actually publish.

### 5.1 Published intent horizons

**What is drawn.** The next cells this robot has told its peers it is about to occupy, as a run of tinted squares in the robot's own colour, fading along the horizon; the immediate next cell — the one under contention — is outlined; the goal is a dashed outline (`frontend/js/network.js:28-60`).

**Where the data comes from.** `fleet[].path`, which is `brain.path[brain.pidx : brain.pidx + 8]` — literally the payload of the robot's `INTENT` message, not a reconstruction (`src/main.py:204-205`).

**Why it fades.** `a = 0.30 * (1 - i / (path.length + 1))` (`frontend/js/network.js:37`). Commitment decays with distance along the horizon, which is what the time windows in the message encode (`frontend/js/network.js:26-27`). A cell two steps out is a promise; a cell eight steps out is a plan.

**In 3D** the same field becomes a polyline plus the first five cells as translucent lease pads (`frontend/js/digital-twin.js:1287-1303`).

**What a judge should look for:** two coloured horizons approaching the same cell from opposite ends of an aisle, and one of them retracting before contact. That retraction *is* the negotiation.

### 5.2 Live peer links

**What is drawn.** One dashed cyan line per pair of robots that can currently hear each other, drawn once per pair and under the chassis so a beam reads as plugging into a robot rather than cutting it in half (`frontend/js/network.js:62-93`). Alpha falls off with on-screen distance so a dense fleet does not become a ball of string (`frontend/js/network.js:82`).

**Where the data comes from.** `fleet[].peers` — `sorted(brain.peers.keys())`, the sender's own peer table (`src/main.py:206`).

**In 3D**, the same table becomes dashed lines at 0.72 m height (`frontend/js/digital-twin.js:1305-1321`).

**What a judge should look for:** links thinning out and vanishing as a robot enters a mesh dead zone (drawn as a rose cylinder, `frontend/js/digital-twin.js:541-567`), and the `comms_lost` glyph appearing over a robot whose `mode` has degraded to `DEGRADED_P2P` (`frontend/js/network.js:123-128`). That is requirement 3 failing gracefully rather than requirement 3 working in ideal conditions.

### 5.3 Wait-for arrows

**What is drawn.** A red arrow from a waiting robot to the robot it is waiting on, stopping short of both sprites so it points *between* them (`frontend/js/network.js:106-110`, `arrow()` at `:135-160`).

**Where the data comes from.** `fleet[].blocked_on` — the field the distributed deadlock detector runs on (`src/main.py:207`).

**Why it is the strongest single frame in the demo**, from `frontend/js/network.js:103-105`:

> This is the wait-for graph the distributed deadlock detector searches for cycles, made visible: when you can see two arrows pointing at each other, you are looking at the cycle.

**What a judge should look for:** two arrows pointing at each other. That mutual pair is a 2-cycle in the wait-for graph — a distributed deadlock, on screen, in the frame before it is broken. Pause on it with `←`/`→` (frame-accurate stepping, `frontend/js/main.js:837-849`) and step forward to watch priority admission break it. This is requirement 10 shown rather than asserted.

The separate case `blocked_on === 'gate'` is not a peer dependency and is drawn differently — a dashed amber ring, meaning the robot is waiting at the mouth of a single-file block for a commit round (`frontend/js/network.js:113-121`).

### 5.4 Single-file blocks under block control

**What is drawn.** Maximal connected runs of passable cells with at most two exits, filled amber and outlined on the perimeter only (`frontend/js/environment.js:353-377`).

**How they are found.** `findBlocks(map, 6)` — a flood fill over cells where `degree(x, y) <= 2` (`frontend/js/environment.js:266-291`), recomputed client-side with the same rule the agent uses.

**Why only long ones.** The threshold is 6 cells (`frontend/js/environment.js:359`), and the reason is at `frontend/js/environment.js:264-265`: *only long ones are the ones the agent actually applies block control to — showing the short gaps too would imply a constraint that is not being enforced.* Drawing an unenforced constraint would be exactly the kind of decoration this file forbids.

**What a judge should look for:** a robot stopping at the amber boundary with a dashed ring around it while another traverses the block, then entering as the block clears. That is requirement 11.

### 5.5 The summary a defender can give in one sentence

Every overlay on this dashboard is a rendering of a published field — `path`, `peers`, `blocked_on`, `state` — so if the coordination were fake, the overlays would be empty. There is no synthesised layer to hide behind.

---

## 6. The 3D digital twin

`frontend/js/digital-twin.js` (1,362 lines), rendered into `#twinCanvas`, is the default view (`frontend/js/main.js:52`, `App.viewMode = '3d'`).

### 6.1 The scene graph

Four groups hang off the scene, rebuilt or refreshed on different cadences (`frontend/js/digital-twin.js:157-160`):

| Group | Contents | Rebuilt |
|---|---|---|
| `world` | floor slab, grid helper, pedestrian apron + guard rails, rack uprights / shelves / stock, station pads, charge docks, dead-zone cylinders, boundary edges, task cargo markers | once per run, in `load()` → `_buildWarehouse()` + `_buildTasks()` (`:198-226`) |
| `routes` | intent polylines, lease pads, peer link lines | throttled to ~11 Hz — `simTime - lastRouteRefresh > .09` (`:1266-1269`) |
| `dynamic` | robot groups, human billboards, obstacle pallets | populated lazily by `_ensureRobot` / `_ensureHuman` / `_ensureObstacle` (`:914`, `:1074`, `:1145`); transforms updated every frame in `update()` (`:1181`) |
| lights | hemisphere + key (shadow-casting, 2048² map) + cyan rim | once, in the constructor (`:181-196`) |

One robot group is 20-odd meshes: chassis cylinder, deck box, deck plate, two emissive strips, four wheels, bumper, sensor bar, mast, lidar, beacon, two deck rails, payload carton, heading cone, state halo ring, selection ring, name label sprite, and two badge sprites (`:920-1068`). `group.userData` publishes the handles `update()` needs — `{robotId, colour, halo, selection, label, beacon, wheels, payload, badgeCharging, badgeBlocked}` (`:1067`).

Disposal is careful about one thing: `disposeObject` skips any material map tagged `userData.shared`, because the texture cache outlives the scene and disposing a cached map would throw away a texture the next build is about to request by name (`:19-35`, tagged at `:108`).

**Blocked aisles (requirement 12)** are `frame.obstacles[]`, rendered as a pallet with slats, a rotating rose warning ring and a "BLOCKED AISLE" label (`:1145-1179`). They are hidden rather than destroyed when they clear (`:1260-1262`), so an obstacle that is removed and re-promoted reuses its group.

### 6.2 Coordinate conventions

Two flips, each confined to one function.

**2D (`environment.js`).** The simulation uses `+Y = north`; canvas uses `+Y = down`. The flip lives in `cellToScreen` alone (`frontend/js/environment.js:201-204`), and `worldToScreen` is a thin metres→cells wrapper over it (`:209-212`), so every draw call inherits the same convention:

```js
cellToScreen(x, y) {
  return [this.ox + x * this.cell,
          this.oy + (this.map.height - y) * this.cell];
}
```

Sprites are authored facing `+Y` (up), so a robot at heading `θ` is drawn rotated by **`PI/2 - θ`** (`frontend/js/environment.js:239`, applied again at `frontend/js/amr.js:70` and `:227`). The negation of the maths-convention angle is not a typo: the Y flip turns counter-clockwise in the world into clockwise on screen (`frontend/js/environment.js:9-11`).

Status: *implemented and tested*. `tests/test_dashboard.py:279` asserts `worldToScreen(2.5·cell, 1.5·cell)` lands exactly on the centre of `cellRect(2, 1)` at a 1.4 m cell pitch — the regression that catches anyone treating one cell as one metre.

**3D (`digital-twin.js`).** Three.js is Y-up, so the warehouse floor is the XZ plane and the flip is `+Y (north) → −Z`, centred on the map, in `_toWorld` alone (`frontend/js/digital-twin.js:377-381`):

```js
return new THREE.Vector3(xMetres - widthM / 2, height, heightM / 2 - yMetres);
```

Heading follows: `group.rotation.y = -robot.th - Math.PI / 2` (`:1190`).

### 6.3 Baked textures

Five textures, all local, all loaded through one function so every map gets the same treatment (`frontend/js/digital-twin.js:98-111`): `SRGBColorSpace` (a texture loaded as linear reads washed out under ACES tone mapping) and `anisotropy = 8` (the floor turns to mush at grazing angles otherwise, which is most of the frame at a 45° camera).

| File | Used for | Bytes |
|---|---|---|
| `assets/twin/floor_panel.jpg` | floor slab, tiled one texture per two cells (`:395-398`) | 26,087 |
| `assets/twin/carton.jpg` | rack stock and the carried payload (`:757`, `:1019`) | 10,319 |
| `assets/twin/worker_hi/orange/blue.png` | pedestrian cross-billboards (`:1092-1093`) | 80,310 |
| `assets/twin/badge_charging/deadlock/complete.png` | state badges (`:854`, `:1060-1061`) | 120,311 |

They are *baked* rather than dropped in: the sources are 7 MB presentation renders with plinths and labels in them, cut by `tools/bake_twin_textures.py` (`frontend/js/digital-twin.js:76-78`).

The rule that decided what does **not** get a texture is at `frontend/js/digital-twin.js:936-940` and is worth repeating, because it is the difference between a twin and a diorama:

> A MATERIAL texture transfers (the floor panelling, the corrugated card — they describe a surface), a DEPICTION does not (it describes an object, and the object is wrong).

The robot's top-down render was mapped onto the deck and removed: it is a picture of a *different* robot, with a mast and gantry this chassis does not have, stretched from a 1.59:1 source onto a nearly square face (`:928-935`). Panel lines and a lit strip in the robot's own colour replaced it. The charge dock and the pick/drop station are modelled for the same reason (`:636-647`, `:577-586`).

The payload carton keeps its texture *and* its colour coding, because a map multiplies the material tint rather than replacing it — so the box reads as card and still says what the cargo is (`:1014-1016`, recoloured per frame at `:1218`).

### 6.4 `InstancedMesh`, and its two traps

Three instanced meshes carry the warehouse's repeated geometry: rack uprights (`4 × rackCells`), shelves (`3 × rackCells`) (`:512-513`), and rack stock (`12 × rackCells` — 3 beams × 4 slots) (`:754-760`).

**Trap 1 — the constructor count is a hard cap.** `new THREE.InstancedMesh(geometry, material, count)` allocates the matrix buffer once. Writing `setMatrixAt(i, …)` for `i >= count` does not grow it and does not throw in a way anyone notices during a demo; those instances simply **never draw**. The counts here are computed from `rackCells.length` at build time (`:512-513`, `:759`) rather than guessed, which is what keeps them correct for any map.

**Trap 2 — unfilled instances stack at the world origin.** Every slot in the buffer starts as an identity matrix, which places that instance at `(0, 0, 0)`. So any instance past the fill point is drawn — at the centre of the floor, on top of every other unfilled instance, as a solid lump of geometry in the middle of the warehouse. The rack stock loop *deliberately skips* top-beam slots already claimed by a task marker (`:770`), so `index` finishes below the allocated ceiling, and the fix is one line at `:783-785`:

```js
// Instances past the fill point keep an identity matrix and would all stack
// at the origin; trimming the draw count is what keeps them off screen.
stock.count = index;
```

`uprights` and `shelves` do not need the trim because their loops fill every allocated slot exactly (`:520-531`); the invariant to preserve if anyone adds a skip condition there is that **any instanced mesh with a conditional fill must set `mesh.count = filled`.**

### 6.5 `frameFloor()` measures the projection instead of modelling it

`frameFloor(fill = 0.94)` (`frontend/js/digital-twin.js:269-318`) places the orbit camera so the whole operational volume — including the pedestrian apron — occupies 94% of the frame.

The interesting part is what it replaced. The previous version solved the fit with trigonometry: rotate the floor's bounding box by the azimuth, foreshorten the depth axis by `sin(elevation)`, take the binding axis. That is correct in principle, and it got the sign of every term right, and it still cropped — because a hand-derived model of a perspective projection is one approximation away from the projection itself. It was tuned on a 22 × 15 floor; Chokepoint is 25 × 9, and its far corner landed **22 px off the right edge and 28 px below the bottom** (`:255-262`).

The replacement does not model the projection. It *uses* it:

1. Choose elevation (45°) and azimuth (≈20° off the short axis) — chosen, not fitted (`:280-281`). Corner-on is what the old camera was stuck in, and it is why it needed to sit so far back: seen from a corner, a 31 × 21 m floor presents its 37 m diagonal (`:277-279`).
2. Build the eight corners of the operational bounding box, at both floor level and rack height (`:289-294`).
3. Put the camera at a trial distance, `project()` all eight corners into normalised device coordinates, and take the worst `max(|ndc.x|, |ndc.y|)` — where `1` is the frame edge (`:298-306`).
4. Scale the distance by `extent / fill` and repeat. Three passes converge well inside a pixel (`:307-312`).

The result is exact for any map shape, because the thing doing the foreshortening is the projection matrix rather than a model of it.

`_commitCamera()` (`:239-244`) is required after any camera placement. With damping on, `OrbitControls` keeps a decaying rotation delta and re-applies it on every `update()`; assigning `camera.position` is not enough, because the controls re-derive their spherical coordinates from the new position, add the leftover delta, and spend the next second dragging the camera off toward wherever the last gesture was heading — *which looks exactly like the reframe silently failing*. Running one `update()` with damping switched off is the branch where OrbitControls zeroes the delta.

**Verification snippet.** Derived from the algorithm above, this can be pasted into the browser console with the twin in Orbit mode immediately after a run loads. It re-runs step 3 against the live camera:

```js
// Expect ≈0.94 (the `fill` argument), and never > 1.0.
const t = window.BIOS.app.twin;
const w = t.map.width  * t.meta.cell_m;
const d = t.map.height * t.meta.cell_m;
const m = t.map.pedestrian_apron ? 3.5 : 0.6;   // pedestrianEnvelope margin, digital-twin.js:113
let worst = 0;
for (const x of [-m, w + m])
  for (const z of [-m, d + m])
    for (const h of [0, 3]) {
      const ndc = t._toWorld(x, z, h).project(t.camera);
      worst = Math.max(worst, Math.abs(ndc.x), Math.abs(ndc.y));
    }
console.log('worst corner extent:', worst.toFixed(4));
```

This snippet is **derived from the code, not executed during this documentation pass** — treat the expected value as a prediction from `digital-twin.js:298-312`, not as a measurement. Any value above `1.0` means a corner is off-screen. Note it must be run before the operator drags the orbit camera, since dragging is allowed to reframe.

### 6.6 Camera modes

Four modes, cycled with `F5` or `V` and set from the HUD cluster or the menu's quick-camera slots:

| Mode | Behaviour | Cited at |
|---|---|---|
| `overview` ("Orbit") | free `OrbitControls`, damped, polar angle capped at `0.35π` so the floor is never flattened into a strip | `frontend/js/digital-twin.js:146-155`, `:337` |
| `tactical` | straight down, a few degrees off plumb, fitted by the same aspect-aware maths rather than a fixed multiple of the span | `:342-361` |
| `follow` ("Chase") | third-person, lerped at 0.085 toward 5.5 m behind and 3.7 m above the selected robot | `:1337-1343` |
| `pov` | first-person, lerped at 0.18 to 0.55 m ahead at 0.82 m height, looking 7 m down the robot's heading | `:1332-1336` |

`controls.enabled` is true only in `overview` and `tactical` (`:337`), so a mouse drag cannot fight the chase camera. Returning to Orbit from a robot-locked camera re-frames the floor (`:341`) — without it, the free camera reappears wherever the chase cam abandoned it, *which feels like the view broke rather than like it returned*.

The polar cap has a substantive reason, not an aesthetic one (`:149-151`): at a near-horizontal angle a worker on the protected perimeter projects on top of an AMR several metres inside the barrier — physically safe, visually a near-miss. The camera is not permitted to manufacture a safety incident that did not happen.

Robot picking is a raycast against the robot groups only, walking up the parent chain to find `userData.robotId` (`:1346-1361`), wired to `selectRobot` + `setCameraMode('follow')` in the constructor callback (`frontend/js/main.js:63-66`).

At ten or more AMRs, permanent name labels obscure the traffic the audience is trying to inspect, so labels are kept only for the selected robot and genuine exceptions — failed, blocked, retreating, or a robot-locked camera (`:1206-1211`).

---

## 7. The 2D diagnostic fallback

`#floor` is a plain 2D canvas, hidden at load (`class="is-hidden"`, `frontend/index.html:71`) and reachable from Menu → System → Render mode → "2D diag" (`frontend/index.html:364-365`, handler `frontend/js/main.js:128`).

**When it is used.** As a fallback when the 3D twin is unavailable or unhelpful — a machine without a usable WebGL context, a projector that renders the twin badly, or an operator who wants the flat evidence view. The System panel describes it as exactly that (`frontend/index.html:362`).

**What it shows.** Everything the twin shows except the third dimension, from the same interpolated frame: the baked floor and rack tiles (`renderStaticFloor`, `frontend/js/environment.js:296-378`), the single-file blocks and pedestrian apron, the full network layer — intent horizons, peer links, wait-for arrows, gate rings, comms-lost glyphs (`drawNetwork`, `frontend/js/network.js:15-23`) — and the fleet with halos, cargo, pick/drop badges, the selection reticle and the low-battery pip (`drawFleet`, `frontend/js/amr.js:41-98`).

**Two implementation details worth knowing.**

- The floor and furniture never change during playback, so they are rendered once to an offscreen canvas and blitted per frame in overview and tactical modes (`buildStaticLayer`, `frontend/js/environment.js:380-393`; blit at `frontend/js/main.js:989-991`).
- Switching *to* 2D re-measures and rebuilds that cache before drawing (`frontend/js/main.js:384-391`). The reason is a real Chromium failure: the diagnostic canvas is `display: none` while the twin is active, so its startup `resize()` legitimately measures 0 × 0, and **drawing a zero-size cached canvas raises `InvalidStateError`**. The guard at `frontend/js/main.js:990` (`App.staticLayer?.width > 0`) is the second half of the same fix.

Click-to-select is wired to `#floor` only (`frontend/js/main.js:139`, `onCanvasClick` at `:467-491`); in 3D the twin does its own raycast. The 2D hit radius is `max(0.9, robot_diameter_m × 1.5)` metres (`frontend/js/main.js:476`).

The PiP viewfinder is always 2D, whichever main view is active — it re-uses the same `View` class against `#pipCanvas` (`frontend/js/main.js:62`, `renderPiP` at `:1021-1070`).

One inaccuracy to be aware of before a judge spots it: the PiP "Spd" readout is **not** the published `robots[].v`. It is a two-valued constant derived from `fleet[].state` — `'0.00'` when idle, charging or blocked, `'0.85'` otherwise (`frontend/js/main.js:1063`). The real speed is in the telemetry and simply is not read. Everything else on that strip (heading, battery, state) is genuine.

---

## 8. The scenario builder

Paint a floor, POST it, run it. Opened from Deployment → "Build a floor" (`frontend/index.html:227`, handler `frontend/js/main.js:1616`), it is its own full-screen overlay above the sheet that launched it, *because a grid you paint on wants the whole screen* (`frontend/index.html:439-440`).

**The flow.** A 22 × 14 grid at 28 px per cell (`frontend/js/main.js:1513-1515`). Click to place, drag to paint a run of cells (`:1579-1593`), or drag a component from the palette with a live preview under the cursor (`:1596-1614`). Each placement clears the cell from all four coordinate lists first, so painting floor over a station does not leave the station in the payload and build a warehouse whose stations sit inside racks (`:1643-1650`). Save POSTs to `/api/scenarios/custom` and then **re-reads the whole library** rather than splicing the new entry in by hand, which keeps the gallery, the hidden `<select>` and `App.showcase` consistent by construction (`:1799-1804`, `refreshScenarioLibrary` at `:1817-1823`).

### 8.1 The tile encoding must stay in step with `src/environment.py`

```js
const TILE_CODE = {FREE: 0, RACK: 1, STATION: 2, DOCK: 3, AMR: 0, HUMAN: 0};
```
— `frontend/js/main.js:1526`

This is **not an arbitrary local enum.** `0/1/2/3` are `FREE/RACK/STATION/DOCK` imported from `src/environment.py` by the server (`backend/server.py:55`), validated against `CUSTOM_CELL_VALUES` (`backend/server.py:151`), and fed straight into `Warehouse()`. The warning is written at `frontend/js/main.js:1507-1509`:

> The tile encoding is not arbitrary: 0/1/2/3 are FREE/RACK/STATION/DOCK from `src/environment.py`, and the server feeds this grid straight into `Warehouse()`. Changing a number here silently builds a different warehouse.

"Silently" is the operative word. Swap 1 and 2 and the builder still paints, the POST still validates, the run still succeeds — and every rack you drew is now a pick station and every station is a wall. There is no error to read. **If `src/environment.py` ever renumbers, `frontend/js/main.js:1526` must change in the same commit.**

`AMR` and `HUMAN` both map to `0` because they are not tile types: they are markers stored in parallel lists (`BUILDER.starts`, `BUILDER.humans`, `:1652-1655`) and sent as coordinate arrays alongside the grid (`:1789-1792`).

### 8.2 Validation happens twice, on purpose

Client-side, `syncBuilderTally` disables Save and names what is missing *before* the button is pressed rather than after the server says no — at least two AMR starts, one station, one dock (`frontend/js/main.js:1737-1743`).

Server-side, the same minima are re-checked, plus one the client cannot cheaply do: every start, station, dock and worker must lie in the same navigable connected component (`backend/server.py:556-570`). Without it *"the floor saves correctly but can only ever end in a timeout"* — a failure that looks like a coordination bug and is a topology mistake.

There is no `alert()` anywhere in the builder, deliberately: a modal dialog blocks every event in the page until dismissed, *which on this dashboard means the playback loop and the browser automation both stop dead* (`frontend/js/main.js:1491-1494`).

### 8.3 Custom scenarios live in an in-memory dict — restart loses them

```python
CUSTOM_SCENARIOS: dict[str, dict] = {}
```
— `backend/server.py:76`, written at `:575`, read at `:591` and `:636`

There is no file, no database and no serialisation. **Restarting `backend/server.py` discards every floor anyone has built.** During a demo the symptoms are: the gallery loses its custom cards on the next `/api/scenarios` fetch, and a Launch against a stale id returns `404 unknown custom scenario` (`backend/server.py:638`). Uploaded BIOS_4 models have the same lifetime and say so in their error hint (`backend/server.py:629-632`).

Practical guidance for a live demo: **build custom floors after the last planned restart, not before.** If a floor must survive, it belongs in `src/scenarios.py` as a builder, not in the builder UI.

Status: *implemented*. There is no automated test of the builder UI; the server-side custom-scenario endpoint is exercised only through its validation paths.

---

## 9. Keyboard reference

All shortcuts are owned by `frontend/js/shell.js`'s single `keydown` listener (`:404`, handler `:286-382`). Two global gates apply first:

- **A keystroke aimed at a text field is not a shortcut.** Everything except `Escape` and `Tab` is gated on `isTyping(event.target)` (`:280-284`, applied at `:311`), so typing a seed never scrubs the timeline.
- **While the builder is open, nothing below applies except `Escape`** (`:299-301`).

| Key | Action | Context | Cited at |
|---|---|---|---|
| `Tab` | Open / close the menu | not while typing (stays a focus key in a field) | `frontend/js/shell.js:305-309` |
| `Esc` | Back out one stage: sheet → rails → closed | innermost first: builder, then jury mode, then menu | `:290-297` |
| `1`–`5` | Jump straight to a category (opens the menu if closed) | anywhere | `:375-380` |
| `Q` / `E` | Previous / next category, skipping empty ones | menu open | `:369-374`, `:151-159` |
| `↑` / `↓`, `W` / `S` | Move the rail cursor | menu open, sheet closed | `:315-317` |
| `Enter` | Open the category under the cursor | menu open, sheet closed | `:318-322` |
| `Space` | Play / pause | anywhere | `:326-329` |
| `←` / `→` | Step one telemetry frame back / forward | anywhere; pauses playback | `:361-368`, `frontend/js/main.js:840` |
| `F5` | Cycle perspective (**not** reload) | anywhere | `frontend/js/shell.js:330-335` |
| `V` | Cycle perspective (alias) | anywhere | `:336-338` |
| `C` | Next AMR | anywhere | `:339-342` |
| `Z` / `X` | Zoom out / in | anywhere | `:343-348` |
| `J` | Toggle jury (presentation) mode | anywhere | `:349-351` |
| `F1` | Hide / show the HUD | anywhere | `:352-356` |
| `F11` | Fullscreen | anywhere | `:357-360` |
| `F12` | Left to the browser (devtools) | — | `frontend/js/boot-screen.js:221` |
| any key / any click | Dismiss the boot screen | only while it is up and armed | `frontend/js/boot-screen.js:214-235` |

`F5` is rebound because *"reload is not what anyone wants from a running simulation, and this is the key every player already associates with changing the view"* (`frontend/js/shell.js:331-332`) — and because a reload costs a full re-simulation.

**The on-screen list is incomplete.** Menu → System → Controls (`frontend/index.html:384-397`) lists twelve bindings and omits `V`, `Enter`, and the `↑↓`/`WS` rail cursor. The table above is the complete set as implemented. This is a documentation gap in the UI, not a bug.

Nothing tells a first-time viewer the menu exists except one button, by design: `<kbd>Tab</kbd> Command menu`, bottom of the HUD (`frontend/index.html:165-166`).

---

## 10. Performance and load

### 10.1 What ships

| Item | Bytes | Note |
|---|---|---|
| `frontend/vendor/three/` (module + core + OrbitControls) | 793,437 | **vendored, not CDN** — see below |
| 2D sprite atlas fetched by `loadAssets()` (24 files) | 1,946,167 | `frontend/js/environment.js:24-61`, `:66-82` |
| Twin textures (8 files, lazily fetched by name) | 237,027 | `frontend/js/digital-twin.js:98-111` |
| Builder tile art (6 files, fetched on first builder open) | 57,401 | `frontend/js/main.js:1531-1533`, `:1539-1549` |
| Boot logo | 112,424 | `frontend/index.html:46` |
| `frontend/js/*` (8 files) | ~7,675 lines | no build step, no bundler, no transpile |
| `frontend/css/bios.css` | 2,122 lines | the **only** stylesheet |
| `frontend/assets/` on disk, total | ~27 MB | most of it is never requested at runtime |

**Three.js and OrbitControls are vendored under `frontend/vendor/`, so the demo does not depend on venue Wi-Fi or a CDN** (`README.md:328-329`, MIT licence retained at `frontend/vendor/three/LICENSE`). The same reasoning is why there is no webfont: the type stack is system serif and system mono (`frontend/css/bios.css:57-60`), so nothing on the critical path is a third-party fetch. A CDN outage or a captive portal at the venue turns a CDN-loaded twin into a blank canvas five minutes before a demo; this one renders offline.

Everything is served by the stdlib `http.server` in `backend/server.py` with `Cache-Control: no-store` and a strict CSP of `default-src 'self'` (`backend/server.py:252-263`) — which, incidentally, would *block* a CDN even if someone added one.

### 10.2 The run payload

Measured on the opening run (`showcase_chokepoint`, 4 AMRs, seed 7, 320 s requested, 254 s simulated): **2,541 frames, 7,538,318 bytes of JSON**, roughly 2.97 kB per frame. Responses are not compressed — `_send` sets `Content-Length` and no `Content-Encoding` (`backend/server.py:246-267`) — which is acceptable because the transfer is over localhost. It would not be over a network.

Larger scenarios scale roughly with `frames × robots`: Grand Challenge is 10 AMRs over 800 s (`src/scenarios.py:819-824`), so expect a payload several times this one.

The whole run is held in memory in the browser as `App.data` (`frontend/js/main.js:527`) plus two derived arrays flattened at load — `App.auctionEvents` and a de-duplicated `App.decisionEvents` (`frontend/js/main.js:528-537`) — and again inside `Hud` as a merged, time-sorted event list (`frontend/js/hud.js:71-104`).

### 10.3 Rendering cost

- Renderer pixel ratio is capped at **1.75** (`frontend/js/digital-twin.js:137`, re-applied on every resize at `:330`). Re-applying it is not redundant: moving the window to a display with a different scale factor changes `devicePixelRatio` without changing the CSS size, and a renderer holding the old ratio draws a buffer smaller than the canvas it is stretched across — *which looks like a soft, badly rendered 3D view and reads as a weak engine*.
- One shadow-casting light, `2048 × 2048` map (`:186-191`).
- The `routes` group — the most expensive per-frame rebuild, since it disposes and recreates every intent line and lease pad — is throttled to roughly 11 Hz rather than running at display rate (`:1266-1269`).
- The 2D fallback caches the entire static floor to an offscreen canvas and blits it (`frontend/js/environment.js:380-393`), and skips off-screen tiles in zoomed camera modes (`:305-307`).
- The HUD event rail **reconciles against row keys instead of rewriting `innerHTML`** (`frontend/js/hud.js:108-162`). This is a correctness fix as much as a performance one: rows arrive several times a second, every rebuild restarts the 400 ms entry fade on every row, and nothing ever reaches full opacity — *the rail ends up permanently at about 45% and reads as a rendering fault rather than as text*. Re-inserting a node restarts its CSS animation, so even `replaceChildren` with the same nodes is as destructive as `innerHTML`.

### 10.4 Load time

The dominant cost at load is not the assets: it is the opening simulation. `boot()` ends with `run()` (`frontend/js/main.js:185`), which POSTs a 320 s Chokepoint run and waits. Measured here at **49.7 s and 72.3 s** across two warm runs on the development machine; `frontend/js/boot-screen.js:71-73` records 6–9 s on the machine that comment was written on. That range is machine-dependent and worth measuring on the actual demo laptop before the presentation. See [§11.2](#112-one-number-to-check-on-the-demo-machine).

---

## 11. The boot screen — landed after the suite baseline

**Status: committed, in `4a8186e`, one commit ahead of the `07337e0` baseline this suite otherwise documents.** It was uncommitted work-in-progress when this document was commissioned; it is now in the tree. Treated separately here because it is newer than everything above and has not been through the same review.

It comprises `frontend/js/boot-screen.js` (292 lines), markup at `frontend/index.html:29-66`, roughly 400 lines added to `frontend/css/bios.css`, 14 lines of reporting calls in `frontend/js/main.js`, `tools/bake_brand_assets.py` (132 lines), and `frontend/assets/brand/`.

### 11.1 What it does and why

It covers the window described in [§10.4](#104-load-time). Before it existed, the page was *fully painted* for the whole opening run: an empty warehouse, a HUD reading `0 / 0`, three greyed rail categories. That reads as broken rather than busy, and it was the first thing a judge saw on every load and every reload (`frontend/js/boot-screen.js:3-7`).

The screen is a power-on self-test — the one loader this project has a claim to, since the team is called BIOS. The design rule that makes it defensible rather than decorative: **every line in the readout is a real stage stamping its real elapsed time; nothing is a timer pretending to be progress** (`:9-13`). Five stages, weighted because they are not the same size — the simulation is 56% of the bar, because otherwise it sits at 30% for the whole wait and then jumps to done (`:48-57`).

Failure is handled by admitting it: `fail(id, msg)` marks the line `FAIL`, still counts it toward the bar (*the bar tracks how far the boot got, not how well it went — the line above says how well it went*, `:133-134`), and **arms the way in anyway** (`:124-139`). `main.js` calls it on both failure paths (`frontend/js/main.js:85`, `:590`).

The boundary is the same one `shell.js` keeps: `boot-screen.js` owns no simulation state and reads none; `main.js` reports into `window.BiosBoot` and never reads back, and every call is a no-op once the screen is down (`frontend/js/boot-screen.js:20-33`, no-op guards at `:112`, `:125`, `:161`). Published surface: `stage(id)`, `fail(id, msg)`, `ready()`, `show()`, `dismiss()`, `isUp()` (`:291`).

Three details that are easy to get wrong and were not:

- **It owns the keyboard in the capture phase and calls `stopImmediatePropagation`** (`:202-205`, `:256`), so `shell.js` never sees the keystroke that dismisses it. Without this, Space would dismiss the boot screen *and* toggle playback, and F11 would dismiss it and go fullscreen (`:15-18`).
- **It swallows the whole input burst for 700 ms after dismissal** (`:212`, `:223`, `:233`). One press produces `pointerdown`, `mousedown`, `mouseup`, `click`; only the first is consumed while the screen is up, and the rest would land on the world the moment it is uncovered — so the gesture that dismissed the screen would also pick a robot or swing the camera.
- **It listens for `pointerdown`, `mousedown`, `mouseup`, `click` and `touchstart`, not just `pointerdown`** (`:257`), because a presentation clicker, an accessibility tool, or a synthetic dispatch may produce only a plain `click` — and those input paths were unable to get past the screen at all. `touchstart` is registered `passive: false` explicitly, since `preventDefault` inside a passive listener is ignored with a console warning rather than honoured (`:258-260`).

If the markup is missing, the module returns an all-no-op object rather than throwing (`:42-46`) — a missing boot screen must not take the page down with it.

### 11.2 One number to check on the demo machine

`FAILSAFE_MS = 40000` (`frontend/js/boot-screen.js:75`). If `ready()` never arrives, the screen arms itself after 40 s with *"taking longer than expected · press any key to enter"* (`:286-289`).

The comment above it justifies 40 s against a *measured opening of 6–9 s* (`:71-73`). **On the machine used for this documentation pass, the opening run took 49.7 s and 72.3 s** — i.e. the failsafe would fire *before* the run finished, showing "taking longer than expected" on a boot that is proceeding normally, and offering entry to a warehouse that is not yet built. It would not crash anything: `stage('warehouse')` and `ready()` are no-ops once armed, and `draw()` populates the world as soon as the payload lands.

This is machine-dependent and may be entirely fine on the demo laptop. **Time one cold load on the actual demo machine before the presentation.** If it exceeds ~35 s, raise `FAILSAFE_MS` or reduce the opening scenario's duration (`src/scenarios.py:805`, currently 320 s).

---

## 12. Hit-testing fixes in `bios.css` (committed as `7740efb`)

Committed as `7740efb` ("Fix: closed panels kept eating the clicks meant for the menu"), **+31 / −6** in `frontend/css/bios.css`. It is not the boot screen; it is a set of `pointer-events` corrections. Documented here because it changes behaviour and the reasoning is worth preserving:

1. **`#hud > *` became `:where(#hud) > *`** (`frontend/css/bios.css:172`). Written the obvious way, `#hud > *` carries ID specificity that no class-level rule can outrank, so every "step aside" rule below it lost silently: `opacity: 0` applied, `pointer-events: none` did not, and **the HUD cluster went invisible while still swallowing clicks meant for the menu underneath**. `:where()` contributes zero specificity, which is the entire point. Invisible and clickable is the worst of both.
2. **Hiding a parent is not enough.** A child that declares `pointer-events: auto` remains hit-testable inside a `none` parent, so `body.hud-hidden` and `body.sheet-open` now name the children too (`:177-178`, `:194-195`).
3. **`#menu` is `pointer-events: none` with the rails opting back in** (`:722-726`), because the rails kept catching clicks meant for the world after the menu was dismissed. This is also what makes `shell.js:400-402`'s click-outside-to-dismiss correct: only the sheet's own backdrop area can produce that event.
4. **`.sheet` drives hit-testing from state, never from the animation** (`:921-929`, `:946`). The `visibility` transition is delayed (`0s linear var(--t)`) so the panel stays hit-testable through the fade — and if that delayed transition fails to land, the panel stays hit-testable *forever*: invisible, `opacity: 0`, still swallowing every click aimed at the rails underneath. That is exactly the bug where opening a category and pressing Esc left the mouse dead and only the arrow keys working. `pointer-events` is not a transitioned property, so it flips with the class and cannot get stuck.

All four fix bugs that leave no trace on screen, which makes them exactly the kind of change a later "cleanup" reverts by accident. Do not simplify `:where(#hud) > *` back to `#hud > *`, and do not move `pointer-events` onto the `visibility` transition — both revert a real, invisible bug.

---

## 13. Contradictions with existing docs

### 13.1 `archive/UI_UX_HUD_AUDIT.md` is entirely stale — do not show it to a judge

It describes an interface that no longer exists. Every load-bearing term in it returns **zero matches** across `frontend/index.html`, `frontend/css/bios.css` and `frontend/js/`:

| Claimed by the audit | Reality |
|---|---|
| "Core HUD" / "Tactical HUD" modes with an `aria-expanded` disclosure button | no such control; the HUD is three vitals, a rail and a mission bar (`frontend/js/hud.js:10-14`) |
| "persistent side panel", "360 px operator panel" | replaced by the summoned two-stage menu (`frontend/js/shell.js:9-24`) |
| "legend" that "scrolls within its own row" | no legend element exists |
| "Tactical HUD and PiP are mutually exclusive" | no tactical HUD to be exclusive with; PiP is toggled independently (`frontend/js/main.js:421-425`) |
| phone/tablet breakpoint behaviour, "compact PiP/HUD labels" | the only surviving breakpoint logic is one line closing the PiP below 1081 px (`frontend/js/main.js:135`) |
| "The final matrix reported zero intersections … at every size" | measured against a layout that has since been replaced |

The current interface's own rationale is in the file headers (`frontend/index.html:11-26`, `frontend/css/bios.css:1-26`, `frontend/js/hud.js:1-24`, `frontend/js/shell.js:1-25`) and in this document. **`archive/UI_UX_HUD_AUDIT.md` should be rewritten against the current layers or deleted.** Its WCAG references (reflow, target size, focus-not-obscured) are still worth keeping; its findings are not.

### 13.2 `archive/DEMO_AND_JUDGING.md` is consistent

Checked line by line against the frontend. Its live sequence, its passive-dashboard mermaid diagram and its evidence checklist all match the code. Its instruction *"do not say zero collisions are guaranteed; say zero observed contacts in the stated exposure"* is honoured by the UI itself: the Evidence panel closes with *"Simulation evidence · N observed contacts in this run · not a physical safety certification"* (`frontend/js/main.js:1461`).

### 13.3 Deleted stylesheets — no stale references found

`css/style.css`, `css/hud.css` and `css/digital-twin.css` were deleted. A repository-wide search across `.html`, `.js`, `.py` and `.md` returns **zero references** to any of them. `frontend/index.html:9` links exactly one stylesheet, `/css/bios.css`, and that is the only one on disk. This one is clean.

---

## 14. `el('id')` consumer-vs-provider audit

A recurring live bug in this repo is a `document.getElementById` in the JS with no matching `id=` in the markup: it returns `null`, and the failure surfaces as a `TypeError` on the first frame that touches it. Two-line diff, whole-dashboard outage.

**The check, run for this document:**

```sh
cd frontend
grep -ohE "el\('[A-Za-z0-9_-]+'\)"            js/*.js | sed -E "s/.*el\('([^']+)'\)/\1/" | sort -u > consumers.txt
grep -ohE "getElementById\('[A-Za-z0-9_-]+'\)" js/*.js | sed -E "s/.*getElementById\('([^']+)'\)/\1/" | sort -u >> consumers.txt
sort -u consumers.txt -o consumers.txt
grep -ohE 'id="[A-Za-z0-9_-]+"' index.html | sed -E 's/id="([^"]+)"/\1/' | sort -u > providers.txt
comm -23 consumers.txt providers.txt   # consumed but never provided  <-- the bug class
comm -13 consumers.txt providers.txt   # provided but never consumed  <-- dead markup
```

**Result: 107 consumed ids, 128 provided ids, and no live mismatch.**

*Consumed but not in `index.html` — 2, both accounted for:*

| id | Verdict |
|---|---|
| `progressTasks` | **Not a bug.** Created by `renderSummary()`'s `innerHTML` at `frontend/js/main.js:1410`, read by `updateSummaryProgress()` at `:1472` under an `if (taskElement)` guard (`:1475`). `renderSummary` runs at `frontend/js/main.js:566`, before the first `draw()` at `:580`. |
| `progressTime` | Same, `frontend/js/main.js:1411` / `:1473` / `:1479`. |

*Provided but never literally `el('…')`-ed — 23, of which 21 are false positives of the regex:*

- 17 are read through `Hud`'s cached-node loop, which calls `el(key)` over a variable (`frontend/js/hud.js:40-45`): `vitalTasks`, `vitalTasksVal`, `vitalTasksFill`, `vitalTasksGhost`, `vitalCharge`, `vitalChargeVal`, `vitalChargeFill`, `vitalChargeGhost`, `vitalMargin`, `vitalMarginVal`, `vitalMarginFill`, `vitalMarginGhost`, `missionFill`, `messages` — plus `messages` again in `dispose`/`init`.
- 4 are set through the local `set(id, value)` helper in `syncBuilderTally` (`frontend/js/main.js:1731-1735`): `tallyStations`, `tallyDocks`, `tallyStarts`, `tallyHumans`.
- 2 are CSS-only hooks with no JS consumer by design: `#world` (`frontend/css/bios.css:126`), `#hud` (`:159`), `#linkState` (`:295`).

*Genuinely unreferenced — 2, harmless:*

| id | Verdict |
|---|---|
| `builderPool` | `frontend/index.html:452`. Zero references in JS or CSS; the element is styled through its `.pool-list` class and its children are selected with `document.querySelectorAll('.pool-tile')` (`frontend/js/main.js:1562`). Dead attribute, not a bug. |
| `railCounterLabel` | `frontend/index.html:203`. Zero references; styled as `.rail-counter small` (`frontend/css/bios.css:911`). Its sibling `railCounter` *is* consumed (`frontend/js/shell.js:222`). Dead attribute, not a bug. |

**Recommendation: put the two `comm` lines in CI**, next to the existing `node --check` sweep at `.github/workflows/ci.yml:32`, with `progressTasks` and `progressTime` allow-listed. The check is cheap and it catches the exact class of failure that has bitten this dashboard before.

---

## 15. Test coverage of the frontend

Six tests in `tests/test_dashboard.py` touch the dashboard; three of them execute frontend JavaScript under Node. **All six pass** (`python -m pytest tests/test_dashboard.py -k "canvas or cargo or post_only or completion or metric_frames"` → `6 passed`).

| Test | Line | What it pins |
|---|---|---|
| `test_canvas_converts_metric_poses_to_grid_cells` | `tests/test_dashboard.py:279` | `View.worldToScreen` maps a metric pose onto the exact centre of the right cell at a 1.4 m pitch — the regression against treating one cell as one metre |
| `test_canvas_fits_the_real_pedestrian_apron_instead_of_clipping_workers` | `:345` | `View.resize` frames the pedestrian apron, not only the AMR grid |
| `test_3d_cargo_lifecycle_tracks_pickup_carry_and_delivery` | `:307` | `DigitalTwin._buildTaskTimeline` / `_updateTaskCargo` reconstruct cargo state at any playhead **without mutating the simulation data** — the property scrubbing depends on |
| `test_dashboard_run_is_post_only_and_security_headers_are_present` | `:249` | `/api/run` rejects GET; CSP and the security headers are served |
| `test_dashboard_metric_frames_remain_inside_free_map_cells` | `:396` | every recorded pose floors into a non-rack cell, and `pose_units == "metres"` |
| `test_dashboard_completion_uses_unique_tasks_and_includes_the_final_frame` | `:416` | task counts are unique, bounded and present on the final frame — the single-source-of-truth rule the HUD depends on |

CI additionally runs `node --check` over every file in `frontend/js` (`.github/workflows/ci.yml:32`), which catches syntax errors but not behaviour.

**Not covered by any automated test:** the interpolation math (`lerpAngle` in particular), the keyboard map, the menu state machine, `frameFloor`, the network overlay, the builder UI, and the boot screen. These are *implemented*, verified by reading and by running the server, not *implemented and tested*.

---

## See also

- [02. Architecture](02-ARCHITECTURE.md) — where the frontend sits in the whole system
- [03. Decentralized Protocol](03-DECENTRALIZED-PROTOCOL.md) — the messages [§5](#5-visualizing-the-invisible) draws
- [06. Task Allocation](06-TASK-ALLOCATION.md) — battery as an admission criterion, behind [§4](#4-battery-status--requirement-18)
- [07. Safety](07-SAFETY.md) — what the "Safety margin" vital reads out and why it gates nothing
- [08. Edge Deployment](08-EDGE-DEPLOYMENT.md) — the passive-multicast-listener claim in [§1.2](#12-why-a-passive-reader-is-the-honest-answer)
- [10. API Reference](10-API-REFERENCE.md) — `/api/run`, `/api/scenarios`, `/api/scenarios/custom`, `/api/train*`, `/api/model`
- [11. Scenarios](11-SCENARIOS.md) — the five showcase floors the gallery offers
- [12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md) — the numbers the Evidence panel renders
- [13. Testing](13-TESTING.md) — the full suite behind [§15](#15-test-coverage-of-the-frontend)
- [15. Limitations](15-LIMITATIONS.md) — including the stale UI audit and the PiP speed readout
- [16. Demo Runbook](16-DEMO-RUNBOOK.md) — the keyboard table in [§9](#9-keyboard-reference) is the one to have open
