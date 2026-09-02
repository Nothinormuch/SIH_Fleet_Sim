# 07. SAFETY AND COLLISION AVOIDANCE

> This document establishes what the collision-avoidance layer actually is, what physics it
> implements, exactly what "zero contacts" counts, and precisely how far the evidence for it
> reaches.

**Audience:** SIH judges and BEL evaluators assessing requirement 8 (collision avoidance) and
success criterion 19 (zero inter-robot collisions); teammates who must defend both live.
**Reads best after:** [02. Architecture](02-ARCHITECTURE.md)

## Requirements evidenced

| # | Requirement | Where | Evidence |
|---|---|---|---|
| 2 | Dynamic warehouse environment | [§7](#7-contact-accounting), [§8](#8-human-safety) | `src/world.py:754` — the safety layer's inputs are anonymous blobs with no identity, so people, pallets and peers are handled by one mechanism |
| 8 | Collision avoidance | [§2](#2-the-protective-field-derived), [§3](#3-closing-velocity), [§4](#4-the-360-guard) | `src/amr.py:868` (`_safety`), `src/settings.py:46` (`stop_field_m`), `src/settings.py:61` (`max_speed_for_clearance`) |
| 9 | Real-time conflict resolution | [§1](#1-the-layered-safety-argument) | `src/amr.py:603` — Layer 0 runs at 50 Hz after every coordination decision and can veto it |
| 15 | Edge / local execution | [§1.2](#12-what-layer-0-is-allowed-to-read) | `src/amr.py:868-982` — the whole Layer-0 closure reads only onboard sensor data and frozen config; no peer table, no inbox |
| 19 | **Zero inter-robot collisions** | [§7](#7-contact-accounting), [§9](#9-the-statistical-limit-of-the-claim) | `artifacts/benchmarks/sih-acceptance-2026-09-02.json` — 0 contacts of every kind over 180 runs / 268.54 robot-hours; `src/world.py:691` defines what was counted |

---

## 1. The layered safety argument

### 1.1 Where Layer 0 sits

The agent runs three loops at three rates (`src/amr.py:9-11`):

| Layer | Rate | Constant | What it decides |
|---|---|---|---|
| 0 — Safety | 50 Hz | `Rates.safety_hz = 50.0` (`src/settings.py:108`) | Whether the wheels may turn at all, and how fast |
| 1 — Local traffic | 10 Hz | `Rates.reactive_hz = 10.0` (`src/settings.py:109`) | Yield, hold, claim a block, reroute |
| 2 — Global route | 1 Hz | `Rates.route_hz = 1.0` (`src/settings.py:110`) | Which cells to traverse |

The ordering claim is one line of code. In `AMRBrain.step`, Layers 2 and 1 run, then the
waypoint follower produces a command, and then:

```python
act = self._follow(t, sensors)
act = self._safety(sensors, act)           # Layer 0 has the last word, always
```
— `src/amr.py:602-603`

`_safety` is the last transformation applied to the `Actuation` before it is returned to the
caller and handed to the physics (`src/main.py:343`, `src/main.py:348`). There is no code path
in `step()` that returns a command Layer 0 has not seen. `_follow` never sets
`Actuation.safety_stop` itself — the only seven assignments of that field in the entire agent
are the seven `return` statements inside `_safety` (`src/amr.py:915`, `:925`, `:927`, `:928`,
`:936`, `:947`, `:954`).

### 1.2 What Layer 0 is allowed to read

The strongest form of this claim is not architectural prose, it is the read-set of the
function. `_safety` (`src/amr.py:868-954`) calls exactly two helpers,
`_speed_limit_from_traffic` (`src/amr.py:1389-1412`) and
`_escape_motion_increases_clearance` (`src/amr.py:956-982`). Across all three functions, the
complete set of instance attributes touched is:

| Attribute | Kind | Can a coordination decision change it? |
|---|---|---|
| `self.cfg` | frozen `Config` dataclass (`src/settings.py:298`) | No — `frozen=True`, set at construction |
| `self.policy` | `str`, assigned once at `src/amr.py:161` | No — never reassigned anywhere in the file |
| `self.stats` | counter dict, **written only** (`self.stats["safety_stops"] += 1`) | No — it is an output, not an input |
| `self._creep_until` | `float`, initialised `-1e9` at `src/amr.py:359` | **Yes** — see §1.3 |

Everything else Layer 0 consults arrives in the `Sensors` object built by the world from that
one robot's onboard sensing (`src/world.py:739-789`). No peer table (`self.peers`), no inbox,
no `messages` symbol, no claim table, no priority key, no task lease. A packet cannot reach
Layer 0 because Layer 0 never looks anywhere a packet is stored.

**Consequence, and it is the point of the architecture:** total radio loss, a hostile peer, a
stale intent, a wrong priority, or a badly trained learned policy can make the fleet slow or
deadlocked. None of them can make it drive into something, except through the one channel in
§1.3.

### 1.3 The one exception, stated plainly

`self._creep_until` is a timestamp written by higher layers — sixteen assignment sites in
`src/amr.py`, including the BIOS unstick valve (`src/amr.py:2302`), the PIBT recentring paths
(`src/amr.py:1274`, `:1287`, `:1312`), and the V6 cluster unstick (`src/amr.py:2115`). While
`sensors.t < self._creep_until`, Layer 0 will permit motion inside the 360° guard that it
would otherwise forbid.

This is a real coordination-to-safety channel and it should be described as one rather than
denied. What bounds it:

- It can only *weaken the omni guard*, never the directional field: the creep branch still
  computes `v_allowed = self._speed_limit_from_traffic(sensors)` and returns a full stop if
  `v_allowed <= 0.02` (`src/amr.py:923-925`).
- The permitted speed is capped at 0.20 m/s, or 0.12 m/s in the separating-motion case
  (`src/amr.py:926`) — 17% and 10% of `v_max`.
- For the V3+ policy family — `BIOS_PIBT.3`, `BIOS_PIBT.5`, `BIOS_PIBT.6`
  (`src/amr.py:79-80`) — the timed window alone is **not sufficient**. `src/amr.py:909-915`
  rejects it unless `_escape_motion_increases_clearance` independently confirms that the
  commanded velocity is separating from *every* object currently inside the guard. That
  predicate reads only `sensors.detections` and `sensors.pose` (`src/amr.py:956-982`).
- `BIOS_1.0.0`, `BIOS_4`, `BIOS_PIBT.1` and `BIOS_PIBT.2` still get the unconditional timed
  window at 0.20 m/s. §5 documents what that costs.

Regression-tested at `tests/test_core.py:247` (an armed creep window still cannot move a
`decentralized` robot when `clearance_omni_m == 0.0`), `tests/test_core.py:262` and
`tests/test_core.py:279` (V3/V6 creep only while separating; the toward-peer case returns
`v == 0.0` with `safety_stop` set).

### 1.4 The import claim, corrected

A common way to phrase this is "Layer 0 does not import the protocol module." **That is not
literally true here and should not be said on stage.** `src/amr.py:46` is
`from . import messages as msg` at module scope, and Layer 0 lives in the same file as the
protocol handling. The defensible claim is the function-level one in §1.2: the Layer-0
closure never references `msg`, `self.peers`, or any received message, and that is
mechanically checkable by reading `src/amr.py:868-982`. On real hardware the separation would
be physical — a certified PLd/SIL2 scanner wired to the motor contactors, as the docstring at
`src/amr.py:878-880` says. In this repository it is a discipline about what one function
reads, not a module boundary.

---

## 2. The protective field, derived

### 2.1 The braking equation

A robot travelling at `v` cannot stop instantly. It first spends the sense-to-brake latency
`τ` still at speed `v`, then decelerates at `a`. The distance consumed is:

```
d_stop(v) = v·τ  +  v² / (2a)
```

Add the standstill clearance the robot never gives up, `margin`, and that is the protective
field:

```
stop_field(v) = v²/(2a) + v·τ + margin
```

Implemented verbatim at `src/settings.py:46-55`:

```python
def stop_field_m(self, v: float) -> float:
    v = abs(v)
    return v * v / (2 * self.a_max) + v * self.reaction_s + self.safety_margin_m
```

### 2.2 Every constant, with units and default

| Constant | Symbol | Default | Units | Where | Role |
|---|---|---|---|---|---|
| `radius_m` | `r` | 0.35 | m | `src/settings.py:18` | Chassis footprint radius; two robots touch at 0.70 m centre-to-centre |
| `v_max` | `v_max` | 1.2 | m/s | `src/settings.py:19` | Cruise ceiling |
| `a_max` | `a` | 0.8 | m/s² | `src/settings.py:20` | Braking authority — a fact about the chassis, not a tuning knob |
| `omega_max` | — | 1.6 | rad/s | `src/settings.py:21` | Turn-in-place rate (~92°/s) |
| `alpha_max` | — | 3.2 | rad/s² | `src/settings.py:22` | Angular acceleration limit |
| `reaction_s` | `τ` | 0.10 | s | `src/settings.py:30` | Sense-to-brake latency allowance |
| `safety_margin_m` | `margin` | 0.15 | m | `src/settings.py:31` | Standstill clearance never surrendered |
| `omni_stop_m` | — | 0.45 | m | `src/settings.py:32` | 360° standstill guard, measured as surface-to-surface gap |
| `safety_cone_rad` | — | 1.05 | rad (±60.2°) | `src/settings.py:33` | Directional field half-width for unexpected objects |
| `static_cone_rad` | — | 0.35 | rad (±20.1°) | `src/settings.py:34` | Narrow probe for mapped shelving |
| `sense_radius_m` | — | 4.0 | m | `src/settings.py:37` | Onboard lidar range for unlabelled obstacles |
| `cell_m` | — | 1.4 | m | `src/settings.py:305` | Lane pitch; chosen so `cell_m > 2r + omni_stop_m` |

The lane pitch is a safety constant, not a map constant. At the old 1.00 m pitch two correctly
centred neighbours sat exactly on the standstill threshold with no positive clearance budget
(`src/settings.py:300-304`); 1.40 m leaves 0.70 m between footprints. Asserted at
`tests/test_priority.py:926`: `DEFAULT.cell_m > 2 * spec.radius_m + spec.omni_stop_m`
(1.400 > 1.150).

### 2.3 The field as a function of speed

| `v` (m/s) | `stop_field_m(v)` (m) | `slow_field_m(v)` (m) |
|---:|---:|---:|
| 0.0 | 0.150 | 0.300 |
| 0.2 | 0.195 | 0.390 |
| 0.4 | 0.290 | 0.580 |
| 0.6 | 0.435 | 0.870 |
| 0.8 | 0.630 | 1.260 |
| 1.0 | 0.875 | 1.750 |
| 1.2 | **1.170** | 2.340 |

`slow_field_m` is simply `2 × stop_field_m` (`src/settings.py:57-59`); it is used only to
decide how far the ray-caster needs to resolve (`src/world.py:808`), because nothing beyond it
can change a control decision.

Tested at `tests/test_core.py:580`: the field at standstill equals `safety_margin_m` exactly,
and is strictly monotone in speed.

### 2.4 Why a fixed field is wrong in both directions

This was not a design preference; it was a bug in both directions.

**Too small at speed.** A field of, say, 0.8 m is comfortable at 0.6 m/s (needs 0.435 m) and
lethal at 1.2 m/s (needs 1.170 m). The robot detects, brakes correctly, and still arrives.

**Too large at rest.** The original implementation used a fixed 1.8 m cone. A stationary robot
one metre from shelving reads ~1.15 m of clearance, which is inside 1.8 m, so it protective-
stops and can never set off — and the pick stations are *exactly* the wall-adjacent cells
where this happens. Every robot froze permanently at t = 0
(`archive/FINDINGS.md:9-24`). The speed-dependent field collapses to the 0.15 m margin at
standstill, so a parked robot can pull away from a wall it was always going to drive past.

The associated fix is at `src/amr.py:928`: a protective stop kills translation but passes
`omega` through (damped to 30%). A robot that may not turn cannot face away from whatever
stopped it, and a stop it cannot recover from is a slower route to deadlock. At
`src/amr.py:936` and `:947` rotation survives undamped.

**This mirrors real hardware, it does not merely resemble it.** Certified AMR safety scanners
implement exactly this as *field-set switching* under ISO 3691-4 (driverless industrial
trucks) with the safety function rated per EN ISO 13849: the scanner holds several protective
field geometries in firmware and the vehicle controller selects among them by speed and
steering angle, so the field is always at least the current stopping distance. The named
analogue is recorded in the source at `src/settings.py:26-29`. What is implemented here is the
continuous limit of that switching — instead of choosing among discrete field sets, the field
is evaluated from the braking equation at every 50 Hz tick.

### 2.5 Two channels: mapped geometry vs unexpected objects

Collapsing "known wall" and "unknown object" into one number makes every pick station
unreachable (`archive/FINDINGS.md:26-38`). The sensor model therefore returns them separately
(`src/world.py:88-101`):

| Channel | Field | Cone | Computed by | Authority |
|---|---|---|---|---|
| Mapped geometry (racks, map boundary) | `clearance_static_m` | ±20.1° (`static_cone_rad`), 3 rays | Amanatides–Woo grid traversal, `src/world.py:817-823` | Crawl cap + hard backstop |
| Unexpected objects (peers, people, pallets) | `clearance_dynamic_m` | ±60.2° (`safety_cone_rad`) | Bearing test over detections, `src/world.py:825-828` | Full speed-scaled field |
| Unexpected objects, any bearing | `clearance_omni_m` | 360° | `min(range − r_self − r_det)`, `src/world.py:775` | Absolute standstill guard |

Mapped geometry gets a crawl cap of `0.35 × v_max = 0.42 m/s` inside 0.5 m
(`src/amr.py:952-953`) and a hard backstop at `safety_margin_m × 0.7 = 0.105 m`
(`src/amr.py:945-947`). The planner only routes through cells it knows are free, so shelving
is a wall to slow down beside, not a hazard to halt for. The backstop still exists because a
map can be wrong — and when it fires the world records a `robot-rack` contact
(`src/world.py:428-431`), so a wrong map shows up in the results instead of hiding.

Ray casting uses Amanatides–Woo voxel traversal (`src/world.py:831-871`) — one iteration per
cell crossed instead of one per fixed-length sample. At 50 Hz × N robots this is the
difference between a benchmark that runs in seconds and one that runs in minutes, and it is
the honest hot path to profile for the Pi (`src/world.py:795-800`; see
[08. Edge Deployment](08-EDGE-DEPLOYMENT.md)).

---

## 3. Closing velocity

### 3.1 The failure

Two robots meeting head-on in an aisle collided at **0.06 m separation while each was
individually braking correctly** (`archive/FINDINGS.md:54-60`).

Neither controller was wrong about itself. Each budgeted a field for its own 0.71 m/s. But
braking is the only tool either has, and **braking only slows you**. The other party keeps
coming for the entire time you are stopping. They closed at 1.42 m/s combined while each
reserved room for 0.71 m/s. The gap was consumed at twice the rate either had budgeted for.

At the configured `v_max`, a head-on pair closes at 2.4 m/s while each sizes a 1.17 m field.

### 3.2 The corrected inequality

The gap must already contain, before braking begins, everything both parties will travel:

```
gap  ≥  v·τ            our travel during the reaction delay
      + v²/(2a)        our braking distance
      + v_close·(τ + v/a)   THEIR travel over our whole reaction-plus-braking time
      + margin         standstill clearance
```

Recorded as a comment at `src/settings.py:82`. The `v_close·v/a` term is why this is not
merely a larger constant: the faster we go, the longer we take to stop, and the further they
get during it. The coupling is multiplicative.

### 3.3 Solved for `v`

Collecting terms in `v` gives a quadratic `A·v² + B·v + C ≤ 0`:

```
A = 1 / (2a)
B = τ + v_close / a
C = v_close·τ + margin − clearance
```

and the largest admissible root is `v = (−B + √(B² − 4AC)) / (2A)`. Implemented at
`src/settings.py:86-96`:

```python
a = self.a_max
A = 1.0 / (2.0 * a)
B = self.reaction_s + max(0.0, v_closing) / a
C = max(0.0, v_closing) * self.reaction_s + self.safety_margin_m - clearance_m
if C >= 0:
    return 0.0
disc = B * B - 4.0 * A * C
if disc <= 0:
    return 0.0
v = (-B + disc ** 0.5) / (2.0 * A)
return max(0.0, min(v, self.v_max))
```

`max(0.0, v_closing)` (`src/settings.py:88-89`) means receding traffic never earns extra
speed — a robot moving away cannot license us to go faster than the geometry alone allows.
`C >= 0` is the case where the clearance is already smaller than the irreducible margin plus
the other party's reaction-time travel: no positive speed is admissible, return zero.

### 3.4 The permitted speed, tabulated

| Clearance (m) | `v_close = 0` | `v_close = 0.6` | `v_close = 1.2` |
|---:|---:|---:|---:|
| 0.15 | 0.000 | 0.000 | 0.000 |
| 0.30 | 0.416 | 0.099 | 0.019 |
| 0.45 | 0.617 | 0.240 | 0.108 |
| 0.90 | 1.018 | 0.572 | 0.347 |
| 1.30 | **1.200** | 0.805 | 0.533 |
| 2.00 | 1.200 | 1.144 | 0.819 |
| 3.09 | 1.200 | 1.200 | **1.200** |

Read the last row: to travel at full speed toward something approaching at full speed, a robot
needs **3.09 m** of gap, against 1.17 m for the same speed toward something stationary. That is
2.6× the field, and it is within — but not comfortably within — the 4.0 m lidar range
(`src/settings.py:37`).

### 3.5 Where the estimate comes from

`Detection` carries `vx`, `vy` (`src/world.py:76-77`). This is not an unrealistic input: a
2D safety lidar with a tracker provides range rate directly, which is exactly what the
consumer needs. `_speed_limit_from_traffic` (`src/amr.py:1400-1411`) projects it onto the
line of sight:

```python
ux, uy = dx / rng, dy / rng
closing = -(det.vx * ux + det.vy * uy)          # component of THEIR velocity toward us
gap = rng - spec.radius_m - det.r
limit = min(limit, spec.max_speed_for_clearance(gap, max(0.0, closing)))
```

Only *their* velocity appears; our own is already inside the `v²/(2a)` term of the equation
being inverted. Objects outside ±60° are skipped (`src/amr.py:1405-1406`): braking does not
help against something overtaking from behind, and slowing for it would make the fleet timid
without making it safer.

Tested at `tests/test_core.py:594` — the permitted speed for a fixed 2.0 m gap is strictly
lower when the obstacle is closing at `v_max` than when it is stationary.

### 3.6 Why this is the argument *for* a coordination layer

Head-on traffic needs 3.09 m of gap for full speed, which in a 1.4 m-pitch aisle is more than
two cells. Layer 0 alone would therefore make an aisle almost single-file at cruise speed. The
coordination layer exists so that head-on geometries are *avoided* — by directed circulation,
block tokens and yielding (see [05. Coordination Policies](05-COORDINATION-POLICIES.md)) —
so that Layer 0 rarely has to fire at all. Safety and throughput are not in tension here;
messaging buys throughput specifically by keeping the safety layer idle.

---

## 4. The 360° guard

### 4.1 What a forward cone cannot see

The directional field covers ±60.2° (`safety_cone_rad = 1.05` rad). That leaves **240° of
bearing uncovered** — the 120° behind and a 60° wedge on each side.

Consider the merge geometry the problem statement calls out as requirement 11. Robot A
travels east and reaches the junction mouth; robot B approaches the same junction from the
south, travelling north. The bearing from A to B is −90°: squarely in A's blind wedge, and it
stays there for the whole approach, because B's motion is perpendicular to A's heading. B sees
A dead ahead and stops. A never sees B at all. Nothing in A's directional field triggers, and
they meet in the middle.

(`archive/FINDINGS.md:67-75` phrases this as "each sits outside the other's ±60° cone". For the
perfectly symmetric instant, where both are still an equal distance out, the bearing is ±45°
and both *are* covered. The blind case is the asymmetric one described above, which is the
configuration the pair actually passes through as they converge. The conclusion is the same;
the mechanism is worth stating exactly.)

### 4.2 The measured cost

`archive/FINDINGS.md:69` records the `central` policy logging **389 contacts at 0.09 m
separation** at a junction before an omnidirectional guard existed. That figure is a
historical measurement kept in the findings log; **it is not reproducible from any artifact
checked into this repository**, because no checked-in benchmark run predates the guard. Cite
it as the reason the guard exists, not as current evidence.

### 4.3 The mechanism

`Sensors.clearance_omni_m` is the minimum surface-to-surface gap to *any* detection regardless
of bearing (`src/world.py:775`):

```python
omni = min([d.range_m - spec.radius_m - d.r for d in dets], default=99.0)
```

and Layer 0 tests it *first*, before the directional field is even consulted
(`src/amr.py:890`):

```python
if sensors.clearance_omni_m <= spec.omni_stop_m:      # 0.45 m
```

The geometry for two AMRs:

| Quantity | Value |
|---|---|
| Contact (footprints touch) | 0.700 m centre-to-centre |
| Omni guard trigger | 0.450 m gap = **1.150 m centre-to-centre** |
| Margin the guard reserves | 0.450 m |
| Nominal adjacent-cell separation | 1.400 m centre-to-centre (0.700 m gap) |
| Slack before a correctly-centred neighbour trips the guard | 0.250 m |

Real AMRs carry 360° protective coverage for exactly this reason, and the constant's own
comment names it (`src/settings.py:32`).

### 4.4 A limit that must be stated

**The omni guard is a standstill field, not a stopping field.** It has no velocity term. At
0.45 m of gap a robot travelling at `v_max` still needs 1.170 m to stop. If an object entered
the blind sector at cruise speed and the guard were the only thing protecting against it, the
guard would fire too late.

It is not the only thing. The layered argument is:

1. The directional field, which *is* speed-scaled and closing-velocity-aware, handles
   everything roughly ahead — which is everything the robot is driving into.
2. The coordination layer keeps speeds low at junctions and chokepoints, so the blind sectors
   are entered slowly.
3. The omni guard is the last-resort standstill backstop for what (1) and (2) missed.

Presenting the omni guard as a general 360° stopping guarantee would be an overclaim. It is a
0.45 m standstill envelope, sized at three times the 0.15 m margin, and that is what it is.

---

## 5. The creep window

### 5.1 Why the exception exists

A protective field with no exception is a liveness trap. A chassis that finishes braking
slightly off-centre can leave a neighbour inside the 0.45 m omni guard. Both robots are then
permanently stopped by a guard neither can clear, because clearing it requires the motion the
guard forbids. Layer 0 is correct on every tick and the fleet never moves again. This is the
`central` policy failure recorded at `archive/FINDINGS.md:119-133`: PIBT returned a
collision-free next-cell configuration and two robots remained safety-stopped forever, because
discrete occupancy had not proved the swept trajectories executable.

### 5.2 The mechanism

When the omni guard fires, `_safety` evaluates two independent creep predicates
(`src/amr.py:892-907`):

```python
timed_creep = (
    self.policy in (POLICY_BIOS, POLICY_BIOS4, *PIBT_POLICIES)
    and sensors.t < self._creep_until
    and act.v > 0.0
)
separating_creep = (
    self.policy in V3_AUCTION_POLICIES
    and act.v > 0.0
    and self._escape_motion_increases_clearance(sensors, act)
)
```

`_escape_motion_increases_clearance` (`src/amr.py:956-982`) requires that the instantaneous
relative velocity along the line of sight be non-negative for **every** object inside the
guard, and strictly positive for at least one. It is a pure function of `sensors.detections`
and the commanded velocity; a single closing gap returns `False`.

The permitted speed is then (`src/amr.py:923-927`):

```python
v_allowed = self._speed_limit_from_traffic(sensors)
if v_allowed <= 0.02:
    return Actuation(v=0.0, omega=act.omega, safety_stop=True)
v = min(act.v, 0.12 if separating_creep else 0.20, v_allowed)
return Actuation(v=v, omega=act.omega, safety_stop=False)
```

The window is armed for 6.0 s at fifteen of the sixteen sites and 2.0 s at one
(`src/amr.py:1312`); `TrafficSpec.bios_unstick_s = 2.0` (`src/settings.py:168`) is the
*trigger* threshold — how long a robot must be stuck before the valve opens — not the window
length.

### 5.3 What the exception costs

Look at where the creep branch returns: `src/amr.py:927`, **before** the mapped-geometry
checks at `src/amr.py:944-953`. During a creep, the only protection against shelving is
whatever `clearance_static_m` contributes through `v_allowed`. And `clearance_static_m` is a
**three-ray probe over ±20.1°** (`src/world.py:817-823`), while the chassis that must clear
the shelf is a **full 0.35 m circle**, tested by the world against the entire 3×3 cell
neighbourhood (`src/world.py:635-650`).

That asymmetry is the whole failure mode. A shelf corner 40° off the heading at 0.25 m is
invisible to all three rays and squarely inside the footprint. And `omega` passes through the
creep branch unchanged (`src/amr.py:927`), so the chassis can rotate while creeping — sweeping
0.35 m of radius through space no ray ever looks at.

That mechanism is verifiable from the code as it stands today. The number usually quoted
alongside it is not.

### 5.4 Contradiction with `archive/FINDINGS.md` — read this before quoting a figure

`archive/FINDINGS.md:266-270` states that `BIOS_1.0.0` "drives into shelving and `BIOS_4` does
not — 15 rack contacts against zero", over four 420 s runs, attributing it to panic-on-stick
plus the creep window.

**That figure sits inside a section `archive/FINDINGS.md` itself marks as superseded.** The
correction block at `archive/FINDINGS.md:189-243` says "the table below is superseded — do not
quote it", and its own re-measurement on the *same* held-out seeds 8–11, same 420 s, same 4
robots (`archive/FINDINGS.md:212`) records `BIOS_1.0.0` with **0 rack contacts**, at a worst
separation of 0.736 m.

Independently: **every JSON artifact checked into `artifacts/benchmarks/` records zero
robot-rack contacts** — 1,030 policy-runs across thirteen files, verified by summing
`contacts_robot_rack` over all of them.

The honest position, and the one to defend live:

- The mechanism in §5.3 is real, is present in the code, and is the correct explanation of how
  a creep can reach shelving.
- The "15 rack contacts" number is **not supported by any current measurement or artifact**
  and should not be presented as evidence. `archive/FINDINGS.md` should be corrected.
- The trade-off is nonetheless real and should be stated as a design cost, not a measurement.

### 5.5 Is it still present, and still tuned that way?

Present: yes. Tuned that way: only for part of the fleet.

| Policy | Timed window alone permits motion? | Speed cap | Notes |
|---|---|---:|---|
| `BIOS_1.0.0` | **Yes** | 0.20 m/s | The panic-on-stick policy; unchanged |
| `BIOS_4` | **Yes** | 0.20 m/s | Learned Layer-1 policy over the same valve |
| `BIOS_PIBT.1`, `.2` | **Yes** | 0.20 m/s | — |
| `BIOS_PIBT.3`, `.5`, `.6` | **No** — also requires separating motion | 0.12 m/s | `src/amr.py:909-915` rejects a timed-only creep |

The acceptance-benchmark candidate is `BIOS_PIBT.5` and the showcase policy is
`BIOS_PIBT.6` (`src/amr.py:72-73`), so **the policies the submission's headline numbers come
from are in the gated row**. `BIOS_1.0.0` — the policy the superseded rack-contact figure was
measured on — is in the ungated row and remains so.

---

## 6. The linear slow-down zone mistake

### 6.1 The symptom nobody would have caught

Robots with **1.3 m of clear space ahead travelled at 0.04 m/s**
(`archive/FINDINGS.md:40-52`). The fleet was not deadlocked. It was moving at 1/30 of cruise
speed, which over a 400-second run is indistinguishable from deadlocked: zero tasks complete
either way, and the trace shows motion.

This is the measurement lesson, and it generalises past this bug. A liveness failure and a
severe throughput failure produce the *same* top-line result — `0/48 tasks` — and only an
instrument that reports speed, progress, and separation distributions separates them. It is
why `PolicyResult` carries `progress_cells`, `safety_stop_ticks`, `nonproductive_wait_ticks`
and `min_separation_m` alongside `tasks_completed` (`src/main.py:405-489`), and why
`min_separations` is a distribution rather than a boolean (`src/world.py:243-245`).

### 6.2 Why a linear taper is not physics

The original controller interpolated speed linearly between the stop field and an arbitrary
"warning distance". A linear taper between two distances has no physical meaning — nothing in
the braking dynamics is linear in clearance, because the braking term is quadratic in speed.

Worse, it was self-reinforcing. The *desired* speed sized the field, the field throttled the
robot, and the field never shrank because the robot never got to move
(`archive/FINDINGS.md:46-48`). A positive-feedback loop dressed as a safety margin.

### 6.3 The fix: invert the equation

`max_speed_for_clearance(gap)` (`src/settings.py:61-96`) does not taper. It answers the only
question that has a physical answer: *what is the fastest speed from which this gap is still
enough to stop?* At 1.3 m of clearance and no closing traffic the answer is 1.200 m/s — full
speed — and the robot still stops in time.

The invariant is asserted directly at `tests/test_core.py:586`:

```python
for gap in (0.2, 0.5, 1.0, 1.5, 3.0):
    v = spec.max_speed_for_clearance(gap)
    assert spec.stop_field_m(v) <= gap + 1e-6, gap
```

Whatever speed is permitted must be one the robot can still stop from — the inverse relation
checked in the forward direction.

**This single change took the first task from "never completes" to completing**
(`archive/FINDINGS.md:52`). An order of magnitude of throughput was sitting inside a function that
looked, on inspection, entirely reasonable.

---

## 7. Contact accounting

A judge must know exactly what "zero contacts" counts. This section is the definition.

### 7.1 Where it is evaluated

The world is the referee and it is deliberately ignorant of plans, tasks, priorities and
messages (`src/world.py:1-18`). `World.step` integrates one tick and returns the contact
events it found (`src/world.py:406`, `src/world.py:633`).

| Property | Value | Where |
|---|---|---|
| Tick length | `dt = 1/50 = 0.02 s` | `src/main.py:123`, `Rates.world_hz = 50.0` at `src/settings.py:107` |
| Contact evaluation rate | **50 Hz** — once per physics tick, no subsampling | `src/world.py:633` |
| Detection method | Swept, not endpoint | `src/geometry.py:68-77` |
| Per-pair debounce | 1.0 s | `src/world.py:726` |
| Event store | `World.contacts: list[ContactEvent]` | `src/world.py:240` |
| Separation store | `World._pair_min`, flushed to `min_separations` at `finalize()` | `src/world.py:245`, `:733-735` |

### 7.2 The three contact kinds

| Kind | Condition | Threshold at defaults | Where |
|---|---|---|---|
| `robot-robot` | swept min distance `< 2r` | **0.700 m** centre-to-centre | `src/world.py:700-707` |
| `robot-human` | swept min distance `< r + h.radius` | **0.650 m** (0.35 + 0.30) | `src/world.py:712-719` |
| `robot-rack` | the integrated pose would put the 0.35 m chassis circle inside a `RACK` cell, the map boundary, or a dynamic obstacle | contact, recorded with `separation = 0.0` | `src/world.py:428-431`, predicate at `src/world.py:635-659` |

`robot-rack` is accounted differently from the other two by design. It is detected on the
*proposed* pose during integration; when it fires the world records the contact and then sets
`v = 0.0` rather than advancing the position (`src/world.py:431-433`). The robot does not
tunnel through shelving — a simulator that lets robots clip through racking flatters every
policy — but the attempt is counted. Note that `_hits_rack` also covers the map boundary
(`src/world.py:652-655`) and dynamic obstacles (`src/world.py:656-658`), so a
`robot-rack` event is "the chassis reached mapped-impassable space", not strictly "hit a
shelf".

### 7.3 The swept test, and why endpoint checking is not acceptable

Two robots exchanging cells in one tick never share a position sample, yet certainly collide.
Endpoint-only checking is the single easiest way to accidentally report a perfect safety record
(`src/world.py:15-17`).

`segments_min_distance` (`src/geometry.py:68-77`) reduces two moving points to one:

```
r(t) = (a(t) − b(t)),  t ∈ [0,1]
```

Each robot sweeps `a0 → a1` and `b0 → b1` over the same tick. The relative motion is the single
segment from `(a0 − b0)` to `(a1 − b1)`, and the closest approach of the pair over the tick is
the closest approach of that segment to the origin — a point-to-segment distance
(`src/geometry.py:55-65`). Exact for constant velocity over the tick, which is what the
integrator produces.

Tested at `tests/test_core.py:42`: a straight position swap returns exactly 0.0, while two
parallel tracks 5 m apart return 5.0.

### 7.4 Debounce: one touch is one event

`World._record` (`src/world.py:722-731`) suppresses a repeated event for the same unordered
pair within 1.0 s. Without it, two robots resting in contact would log 50 events per second and
a single physical touch would appear as hundreds of collisions. **This means the reported count
is a count of distinct contact episodes, not of contact ticks.** For a zero-contact result the
distinction is immaterial; for any non-zero result it must be quoted correctly.

### 7.5 The near-miss distribution

Zero collisions over N runs is unfalsifiable on its own; absence over finitely many trials is
not evidence (`src/world.py:11-14`). The world therefore keeps every pairwise closest approach:
`_pair_min` holds the running minimum per pair over the whole run, and `finalize()` flushes
them into `min_separations` (`src/world.py:733-735`).

`PolicyResult.min_separation_m` is `min(min_separations)` and `p05_separation_m` is the 5th
percentile of the *per-pair minima* (`src/main.py:419-420`) — not a percentile over ticks. Note
that `min_separations` pools robot-robot **and** robot-human pairs (`src/world.py:703`,
`:715`), so `min_separation_m` in a run with pedestrians is the closest approach of either kind.

### 7.6 Where the counters end up

| Counter | Meaning | Where |
|---|---|---|
| `contacts_robot_robot` / `_robot_human` / `_robot_rack` | distinct contact episodes | `src/main.py:416-418` |
| `min_separation_m`, `p05_separation_m` | closest approach and its 5th percentile | `src/main.py:419-420` |
| `safety_stop_ticks` | sum over robots of Layer-0 interventions | `src/main.py:488`, incremented at `src/amr.py:891`, `:932`, `:946` |
| `human_yield_ticks` | sum over workers of ticks spent yielding | `src/main.py:489`, incremented at `src/world.py:623`, `:628` |
| `robot_hours` | `n_robots × sim_seconds / 3600` | `src/main.py:380` |

`safety_stop_ticks` is a more interesting number than the contact count, and it separates
policies that the contact count cannot. In the acceptance benchmark at 6 robots, seed 0:
`stop_and_wait` recorded **236,632** safety-stop ticks against `BIOS_PIBT.5`'s **82**
(`artifacts/benchmarks/sih-acceptance.json`). Divided by 6 robots at 50 Hz, the baseline fleet
spent roughly 789 of its 1200 seconds under protective stop and completed nothing. Both runs
report zero contacts. Only the intervention counter tells them apart.

---

## 8. Human safety

### 8.1 What a pedestrian is in this model

A `HumanState` (`src/world.py:120-148`) is a 0.30 m-radius walker at 1.15 m/s who publishes no
intent, receives no fleet messages, bids for no tasks and cannot be negotiated with
(`src/scenarios.py:407-412`). The AMRs see workers only as anonymous `Detection` blobs with no
identity field — asserted at `tests/test_core.py:632`. This is the case the problem statement's
shared-intent protocol is structurally blind to, and it is the reason Layer 0 must exist
independently of the radio.

Worker routes are not hand-authored splines. `World.add_human` (`src/world.py:256-371`) takes
the workstation cells and expands every segment, including the return leg, with the same A*
the AMRs use (`src/world.py:272-281`), so a worker walks only through passable space and cannot
clip a rack. A route with a rack endpoint raises rather than clipping through
(`tests/test_dashboard.py:234`). Initial placement rotates the circuit until the spawn point is
at least `r + 0.30 + omni_stop_m + 0.16 = 1.26 m` from every already-placed robot and worker
(`src/world.py:335-341`), so a valid route cannot materialise a person inside a staging queue at
frame zero.

### 8.2 The protected apron

`PEDESTRIAN_APRON_OFFSET_CELLS = 2.50`, `_WIDTH_CELLS = 0.86`, `_BOUND_CELLS = 2.98`
(`src/world.py:38-40`). A worker is mapped onto the apron *only* when their expanded route is a
complete four-sided perimeter loop hugging the map edge (`src/world.py:302-309`); the route is
then offset outside the vehicle boundary (`src/world.py:315-327`), which places its centre
beyond an outer-lane AMR's 4.0 m lidar range.

Status: **the apron is still implemented but is no longer the presentation model.**
`archive/HUMAN_FLOW_AUDIT.md:34-38` records why it was abandoned — it separated people from robots
but did not demonstrate humans doing warehouse work alongside AMRs. Current showcases place
workers in the same rack aisles as the fleet (`src/scenarios.py:276-300`), and
`tests/test_dashboard.py:73` asserts there is no hidden pedestrian apron in showcase telemetry.

### 8.3 Yield behaviour and yield-tick accounting

Each worker is evaluated once per 50 Hz tick (`src/world.py:456-630`):

1. **Awareness.** `protective_separation = h.radius + r + omni_stop_m + 0.16 = 1.26 m`;
   `awareness_distance = protective_separation + 1.10 = 2.36 m` (`src/world.py:459-462`). The
   look-ahead is deliberately larger than the AMR's omni field: a person who notices a robot
   only after entering that field has already made the robot stop
   (`src/world.py:450-454`).
2. **Candidate generation.** Forward along the route; reversed along the route; and, when
   avoidance is needed, bounded side-steps at 35°, 70° and 105° off the desired heading, on a
   deterministic preferred side first (`src/world.py:556-588`). Side-steps are rejected if they
   would take the worker more than `HUMAN_ROUTE_DEVIATION_M = 0.42 m` from the mapped route
   (`src/world.py:44`, `:572`) — without that bound, accumulated avoidance turns a person into
   an unintended new warehouse route.
3. **Acceptance.** `candidate_clearance` (`src/world.py:485-520`) rejects any candidate whose
   *swept* segment comes within `protective_separation` of a robot's swept segment, or within
   `0.72 m` of another worker's, unless the move is strictly escaping an already-violated gap.
4. **Fallback.** If no candidate survives, the worker stops (`src/world.py:625-628`).

Work pauses are interruptible: a worker abandons a 4.0 s shelf inspection the moment an AMR
approaches (`src/world.py:526-529`). Yield ticks are counted at `src/world.py:623` and
`:628`, surfaced per worker in `snapshot()` (`src/world.py:893`) and aggregated into
`human_yield_ticks` at `src/main.py:489`.

### 8.4 The honesty point a sharp judge will find first

**The worker now paths with A* and actively avoids robots. "Zero robot-human contacts" is
therefore a property of *both* parties avoiding each other, not evidence about the robots
alone.**

This is unambiguous in the code. `candidate_clearance` rejects any worker step whose swept
segment closes to within 1.26 m of a robot's swept segment (`src/world.py:498`) — a threshold
*larger* than the AMR's own 0.45 m omni guard. In an encounter where both parties are working
correctly, the worker's constraint binds first. The AMR's protective stop may never be the
mechanism that produced the separation.

Say this before a judge asks. The correctly scoped claims are:

- **Supported:** in the evaluated mixed-traffic runs, a fleet of AMRs and a crew of
  non-broadcasting workers sharing the same aisles produced zero contacts of any kind, with
  worst observed separations of 0.92 m (`showcase_human`, 6 runs) and 0.892 m
  (`showcase_grand_challenge`, 3 runs) — `artifacts/benchmarks/bios6-showcase-human.json`,
  `artifacts/benchmarks/bios6-grand-challenge.json`.
- **Supported:** Layer 0 has the *mechanism* to stop for a non-broadcasting pedestrian, and
  it is exercised — `human_yield_ticks` is non-zero in those runs (16 and 635 respectively),
  and `safety_stop_ticks` is non-zero throughout.
- **Not supported:** that the AMRs alone would have avoided a worker who did not avoid them.
  Nothing in the current evidence isolates that. The experiment that would isolate it — a
  pedestrian model that walks its route and ignores robots entirely — is not implemented.
  See [15. Limitations](15-LIMITATIONS.md).

### 8.5 And the acceptance benchmark had no pedestrians at all

`sih_acceptance_overlap` is built from `crossing_chokepoint` (`src/scenarios.py:577-595`),
which never passes a `humans=` argument (`src/scenarios.py:382-383`), so
`Scenario.humans` stays at its empty default (`src/scenarios.py:70`).

The `rh_upper95_per_1000_robot_hours` figures of 231.809 / 108.067 / 61.360 reported in
`artifacts/benchmarks/sih-acceptance.json` are therefore **vacuous**: they are identical to the
robot-robot bounds because both are the zero-event bound over the same exposure, and there
were no humans present to contact. Do not present them as human-safety evidence. The human
evidence is the mixed-traffic campaigns in §8.4 and `archive/HUMAN_FLOW_AUDIT.md`, and their
exposure is much smaller — 7.83 robot-hours combined, giving a 95% upper bound of **379.1**
robot-human contacts per 1000 robot-hours.

---

## 9. The statistical limit of the claim

### 9.1 Zero observed is not zero

Success criterion 19 asks for zero inter-robot collisions. The benchmark observed zero. Those
are different statements, and conflating them is the failure mode BEL evaluators are trained to
find.

Observing no event in finite exposure does not prove the event rate is zero. It *bounds* it.
The world module says so in its own docstring (`src/world.py:11-14`) and the metric carries the
caveat in the artifact itself (`src/metrics.py:240-241`):

> "zero observed contacts bound the rate, they do not prove zero; the upper bound falls only
> with more exposure"

### 9.2 Right-censoring, and where it applies

Two distinct censoring situations appear in this benchmark and they should not be confused.

**Censored throughput.** All 90 `stop_and_wait` baseline runs hit the 1200 s cutoff without
completing (`baseline_runs_completed: "0/30"` for every fleet). A timeout is not a makespan and
is never substituted for one. With true baseline makespan `B > D = 1200` and candidate makespan
`C`, `1 − C/B > 1 − C/D`, so the artifact reports `100 × (1 − C/D)` as a conservative lower
bound and refuses to publish a baseline mean or p95 at all
(`archive/SIH_ACCEPTANCE_BENCHMARK.md:31-46`; `baseline_makespan_mean_s: null` in the JSON). See
[12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md).

**Censored safety.** The contact process is censored in the other direction: the study ended
before any event occurred. The estimator for a Poisson rate with zero observations is a
one-sided upper bound.

### 9.3 The one-sided upper bound

For `k` events over exposure `T`, the exact one-sided upper limit at confidence `1 − α` is
`χ²_{1−α}(2k + 2) / (2T)`. At `k = 0` this reduces to the rule of three: the bound is
approximately `3 / T`. Implemented at `src/metrics.py:74-88`:

```python
upper = _chi2_quantile(conf, 2 * events + 2) / 2 / exposure
```

with the chi-square quantile from a Wilson–Hilferty approximation
(`src/metrics.py:61-71`), hand-rolled because the simulation core carries no third-party
dependencies and must run on a bare Raspberry Pi image. Tested at `tests/test_core.py:752`
(zero events give ≈ 3/T, and the bound is never zero — "an upper bound of zero would be a claim
we cannot make") and `tests/test_core.py:760` (more exposure tightens it).

### 9.4 The reported bounds

**Artifact used: `artifacts/benchmarks/sih-acceptance-2026-09-02.json`**, generated after this
section was first drafted. It records `generated_at: 2026-09-02T13:57:48Z`,
`git_commit: 7740efb03480155f76a8cdac4d146e47f544f024`, `verdict: "pass"`, and 0 contacts of
every kind across 268.54 robot-hours. Its `source_tree_dirty` flag is `true` because
documentation and frontend work was in progress; `git diff --name-only 07337e0..7740efb --
src/ backend/ benchmark.py` is empty, so no simulation code changed during the run.

The superseded August artifact `artifacts/benchmarks/sih-acceptance.json`
(`generated_at: 2026-08-25T09:43:34Z`, `git_commit: 781a4dfc…`, `source_tree_dirty: false`)
remains in the repository and agrees to within a percentage point. Bounds quoted below are from
the September run; where the two differ the difference is noted. Full comparison:
[12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md).

**Provenance discrepancy.** `archive/SIH_ACCEPTANCE_BENCHMARK.md:94-95` states that the
checked-in JSON records `git_commit` as `b1d3c82445cc32a8cbbf78331dfef462999a4e8a`. The
artifact actually records `781a4dfc2b3ae09e68768bd0453ad3443d56b520`. One of the two is stale.
The numbers below are read from the artifact, which is the authoritative object; the prose in
that document should be corrected before it is shown to an evaluator who checks it.

| Fleet | Runs | Candidate robot-hours | r-r contacts | 95% upper bound / 1000 robot-h | Worst separation |
|---:|---:|---:|---:|---:|---:|
| 4 | 30 | 12.8055 | 0 | **231.809** | 0.873 m |
| 6 | 30 | 27.4684 | 0 | **108.067** | 0.856 m |
| 8 | 30 | 48.3773 | 0 | **61.360** | 0.858 m |
| **Total** | **90** | **88.6512** | **0** | — | **0.856 m** |

All figures verified against the artifact's `fleets.<n>.candidate_safety` blocks; the exposures
sum to 88.6512 robot-hours exactly, matching `archive/SIH_ACCEPTANCE_BENCHMARK.md:73`.

Read the bounds correctly: at 4 robots the data are consistent with a true contact rate as high
as **one contact per 4.3 robot-hours**. At 8 robots, one per 16.3 robot-hours. These are not
impressive numbers, and that is the honest situation — 88.65 robot-hours is a small study. The
only thing that lowers them is more exposure. Restating "zero collisions" more forcefully does
not.

### 9.5 The separation distribution is the stronger evidence

The contact count is one bit per run. The separation distribution is continuous and it did not
come close to the threshold:

| | Value |
|---|---|
| Contact threshold (robot-robot) | 0.700 m centre-to-centre |
| Worst closest approach, 90 candidate runs | **0.856 m** |
| Margin over contact | **0.156 m** (22% of the threshold) |
| Median worst-separation per fleet | 1.184 / 1.035 / 0.914 m |

Not one of 90 runs produced a closest approach within 0.15 m of contact. That is a much
stronger statement than "the counter read zero", because it would have degraded gracefully and
visibly before any contact occurred. It is also why `safety_report` pools runs rather than
reporting per-run booleans (`src/metrics.py:213-218`).

### 9.6 A defect in the bound, found while writing this

`_chi2_quantile` uses the Wilson–Hilferty approximation and its docstring claims accuracy
"well under a percent for dof >= 2" (`src/metrics.py:62-66`). At the value that matters here it
is off by slightly more, **in the optimistic direction**:

| | Value |
|---|---|
| `_chi2_quantile(0.95, 2)` as implemented | 5.936870 |
| Exact χ²₀.₉₅(2) | 5.991465 |
| Relative error | **−0.91%** |

Because the quantile is in the numerator of the upper bound, every reported bound is ~0.91%
**lower** — that is, more favourable — than the exact one-sided limit. Corrected values:

| Fleet | Reported | Exact |
|---:|---:|---:|
| 4 | 231.809 | 233.941 |
| 6 | 108.067 | 109.061 |
| 8 | 61.360 | 61.924 |

The discrepancy is immaterial to any conclusion and does not change the verdict. It is recorded
here because the correct response to "your safety bound is approximate" is to have already said
by how much and in which direction. If corrected, prefer raising the reported bounds to the
exact values rather than adjusting the wording.

---

## 10. What this is not

Stated in full, because a safety argument that overclaims is worse than one that is narrow.

1. **This is simulation evidence under pinned assumptions. It is not a hardware safety
   certification.** No part of this repository has been assessed against ISO 3691-4,
   EN ISO 13849 or IEC 61508. §2.4 cites field-set switching as the engineering analogue the
   design mirrors, not as a conformance claim. On real hardware Layer 0 would be a certified
   scanner wired to the motor contactors; here it is Python
   (`src/amr.py:878-880`).

2. **Layer 0's sensor inputs are exact.** `pose_noise_m` (default 0.02 m, `src/scenarios.py:82`)
   perturbs only the reported `pose` and `cell` (`src/world.py:750-752`). Detection ranges and
   positions are read from ground truth (`src/world.py:758-772`), so `clearance_omni_m`,
   `clearance_dynamic_m` and `clearance_static_m` carry **no range noise**. Real lidar has range
   error, dropouts on dark or specular surfaces, and a minimum object size.

3. **There is no occlusion model.** `sense` returns every robot, worker and obstacle within
   4.0 m with no line-of-sight test (`src/world.py:754-772`). A peer behind a rack is detected
   through it. This is conservative for stopping — it produces *more* protective stops than
   reality — but it means the blind-corner case, where a real scanner sees nothing until the
   geometry opens, is not exercised at all. The 360° guard's blind-sector argument in §4 is
   about *bearing* coverage, not about occlusion.

4. **No sensor failure modes.** A failed chassis is modelled as a stopped physical obstacle
   with a silent radio (`src/main.py:330-335`), which is the *safe* failure. A scanner that
   reports clear while blinded, a stuck brake, a wheel-slip localisation divergence, and a
   partial detection dropout are all unmodelled. Requirement 19 is evidenced against
   coordination failure, not against component failure.

5. **The zero-contact result does not extend beyond the evaluated envelope.** The acceptance
   evidence is 4, 6 and 8 robots on one 13-cell chokepoint map, seeds 0–29, 1200 s cutoff, no
   packet loss, no dead zones, no pedestrians. Fleets, maps, seeds, radio conditions and fault
   injections outside that are stress tests, not acceptance evidence
   (`archive/HUMAN_FLOW_AUDIT.md:129-134`).

6. **It is not proof of behaviour during a permanent partition.** Partition recovery is tested
   (`tests/test_resilience.py:41`) and the fault campaign covers injected loss and dead zones
   (`artifacts/benchmarks/fault-campaign.json`, 180 runs, zero contacts of every kind). A
   *permanent* partition is a liveness question the benchmark does not answer. The safety claim
   under partition is architectural, from §1.2: a robot that hears nothing still has its
   protective field, because that field never read the radio.

7. **The human-safety result is a property of both parties.** §8.4. It is stated there rather
   than here so that it is impossible to miss.

8. **"Zero contacts" means zero distinct contact *episodes* as defined in §7**, with a 1.0 s
   per-pair debounce and thresholds of 0.700 m (robot-robot), 0.650 m (robot-human) and mapped-
   impassable-space entry (robot-rack). It does not mean "no near-miss": near-misses are
   reported separately as the separation distribution, and the worst one was 0.856 m.

---

## Related documents

- [02. Architecture](02-ARCHITECTURE.md) — the three-loop structure Layer 0 sits at the bottom of
- [03. Decentralized Protocol](03-DECENTRALIZED-PROTOCOL.md) — what Layer 0 deliberately does not read
- [04. Path Planning](04-PATH-PLANNING.md) — why the planner's discrete collision freedom is not continuous executability
- [05. Coordination Policies](05-COORDINATION-POLICIES.md) — the layer that keeps Layer 0 idle
- [11. Scenarios](11-SCENARIOS.md) — `human_in_aisle`, `crossing_chokepoint`, `sih_acceptance_overlap`
- [12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md) — the acceptance gate and the throughput lower bound
- [13. Testing](13-TESTING.md) — the safety regression gates cited throughout
- [14. Findings](14-FINDINGS.md) — the engineering-failure record, including the superseded table flagged in §5.4
- [15. Limitations](15-LIMITATIONS.md) — the full boundary of every claim
