# 14. ENGINEERING FINDINGS

> This document is a catalogue of things that were measured and turned out to contradict
> the obvious design. Each entry states the intuition that was held, the number that
> contradicted it, the mechanism that explains the number, and what changed in the code
> as a result. Several entries record a retraction: a result that was published inside
> this repository, then withdrawn when a better measurement was taken.

**Audience:** SIH judges and BEL evaluators assessing whether the numbers in
[12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md) mean anything, and teammates
who must explain under questioning why a particular constant has the value it has.
**Reads best after:** [07. Safety](07-SAFETY.md) and
[05. Coordination Policies](05-COORDINATION-POLICIES.md).

Every claim about behaviour carries a `file:LINE` citation against commit `07337e0`.
Where a claim in `archive/FINDINGS.md` no longer survives reading the code, this document
corrects it and says so — see [§1.4](#14-correction-the-omni-guard-is-045-m-not-030-m)
and [§8](#8-throughput-measurement-is-seed-sensitive-and-one-result-was-retracted).

Findings marked **OPEN** are unresolved. They are listed here rather than in
[15. Limitations](15-LIMITATIONS.md) only when the diagnosis is itself the finding.

---

## Requirements evidenced

| # | Requirement | Findings that bear on it |
|---|---|---|
| 8 | Collision avoidance | [§1](#1-a-fixed-protective-field-is-wrong-in-both-directions), [§2](#2-a-linear-slow-down-zone-is-not-physics-and-it-cost-an-order-of-magnitude), [§3](#3-the-protective-field-must-budget-for-closing-speed), [§4](#4-a-forward-cone-cannot-see-a-side-merge) |
| 9 | Real-time conflict resolution | [§6](#6-four-coordination-failures-that-had-nothing-to-do-with-the-radio), [§11](#11-discrete-collision-freedom-is-not-continuous-executability) |
| 10 | Deadlock resolution | [§6](#6-four-coordination-failures-that-had-nothing-to-do-with-the-radio), [§7](#7-an-idle-robot-is-a-permanent-wall) |
| 11 | Narrow intersection / chokepoint handling | [§5](#5-block-control-everywhere-is-worse-than-block-control-nowhere), [§11](#11-discrete-collision-freedom-is-not-continuous-executability) |
| 19 | Zero inter-robot collisions | [§1](#1-a-fixed-protective-field-is-wrong-in-both-directions)–[§4](#4-a-forward-cone-cannot-see-a-side-merge), [§9](#9-a-trained-policy-expires-when-the-simulator-changes) |
| 20 | ≥ 20 % task-time reduction | [§8](#8-throughput-measurement-is-seed-sensitive-and-one-result-was-retracted), [§10](#10-a-saturated-benchmark-configuration-cannot-discriminate-between-policies), [§13](#13-open-two-configurations-that-should-differ-and-do-not) |

---

## 1. A fixed protective field is wrong in both directions

**The intuition.** A safety scanner has a protective field. Pick a field large enough to
stop from full speed — say 1.8 m — and the robot can never hit anything.

**What was measured.** Every robot froze permanently at `t = 0`. Not slowly: at the first
tick, and forever.

**The mechanism.** A stationary robot parked one metre from shelving has 1.15 m of
clearance against a 1.8 m field. The field is violated, so the robot is stopped. It is
stopped, so it cannot move away. Nothing in the loop can ever change the input that
caused the stop. The same fixed number that is *too large* at rest is *too small* at
speed: 1.8 m is generous at 1.2 m/s only by coincidence, and would be wrong on any
chassis with different braking authority.

**What changed.** The field is now computed from the braking equation on every tick:

```python
# src/settings.py:55
return v * v / (2 * self.a_max) + v * self.reaction_s + self.safety_margin_m
```

`RobotSpec.stop_field_m` (`src/settings.py:46`) evaluates to about 1.17 m at
`v_max = 1.2 m/s` (`src/settings.py:19`, `a_max = 0.8 m/s²` at `src/settings.py:20`,
`reaction_s = 0.10 s` at `src/settings.py:30`) and collapses to the 0.15 m standstill
margin at rest (`safety_margin_m`, `src/settings.py:31`). A parked robot can pull away
from a wall; a robot at full speed cannot drive into one.

**The engineering analogue.** This is field-set switching, which is what a certified
2D safety laser scanner does under ISO 3691-4 and EN ISO 13849: the scanner holds several
protective field sets and the vehicle controller selects among them by speed and steering
angle. Speed-dependent field sizing is a requirement of the standard, not an
optimisation. The rationale is recorded in the code at `src/settings.py:24-29`.

### 1.1 Rotation must survive a protective stop

The first implementation killed rotation along with translation. A robot that may not
turn cannot face away from whatever stopped it, so a protective stop it cannot recover
from is a slower path to the same gridlock. Every stop return in `_safety`
(`src/amr.py:868`) now preserves the commanded angular velocity:

```python
# src/amr.py:933-936
# Rotation survives the stop. A robot that may not turn cannot face away
# from what stopped it, and a protective stop it cannot recover from is
# just a slower way to gridlock.
return Actuation(v=0.0, omega=act.omega, safety_stop=True)
```

The same holds at `src/amr.py:915`, `src/amr.py:925` and `src/amr.py:947`. The
omnidirectional branch damps rotation to 30 % rather than zeroing it
(`src/amr.py:928`), because a close 360° contact is the one case where turning quickly
can itself reduce clearance.

### 1.2 Mapped geometry and unexpected objects need different fields

With the field sized correctly, robots still crawled at wall-adjacent pick cells. A
±60° cone at a robot hugging shelving always reads a few centimetres — and picks happen
exactly there. Collapsing "known wall" and "unknown object" into one number makes every
pick station unreachable.

The sensor model therefore reports two channels, computed separately in
`World.sense` (`src/world.py:739`): `clearance_static_m` from a three-ray cast inside the
narrow mapped cone (`src/world.py:817-823`, half-angle `static_cone_rad = 0.35 rad ≈ ±20°`
at `src/settings.py:34`) and `clearance_dynamic_m` from the wide cone
(`safety_cone_rad = 1.05 rad ≈ ±60°`, `src/settings.py:33`, applied at
`src/world.py:827`). Mapped racking gets a crawl limit rather than a stop:

```python
# src/amr.py:952-953
if stat < 0.5:
    v = min(v, 0.35 * spec.v_max)
```

That is 0.42 m/s, and it is justified because the planner only routes through cells it
already knows are free. Unexpected objects keep the full speed-scaled field and absolute
authority.

### 1.3 The mapped-geometry backstop is instrumented, not silent

A hard backstop against mapped geometry survives at `src/amr.py:944-947`, firing at
`safety_margin_m * 0.7 = 0.105 m`. When the chassis nevertheless reaches racking, the
world records it as a contact rather than letting the robot clip through:

```python
# src/world.py:428-431
if self._hits_rack((nx, ny)):
    # Physically blocked. Record it and stop dead rather than tunnelling -
    self._record(self.t, "robot-rack", rid, "rack", 0.0)
    st.v = 0.0
```

A wrong map therefore shows up in the results as `contacts_robot_rack` instead of hiding.
This is what made [§9](#9-a-trained-policy-expires-when-the-simulator-changes)'s
"BIOS_1.0.0 drives into shelving" result observable at all.

### 1.4 Correction: the omni guard is 0.45 m, not 0.30 m

`archive/FINDINGS.md` records the 360° hard stop as 0.30 m. The code's constant is 0.45 m:

```python
# src/settings.py:32
omni_stop_m: float = 0.45       # 360 deg guard with relative-motion reserve
```

checked at `src/amr.py:890`. The extra 0.15 m is the relative-motion reserve added by
[§3](#3-the-protective-field-must-budget-for-closing-speed). A stale 0.30 m also survives
in a comment at `src/settings.py:300`; there is no 0.30 m stop constant anywhere in the
code. Quote 0.45 m.

---

## 2. A linear "slow-down zone" is not physics, and it cost an order of magnitude

**The intuition.** Between the stop field and some larger warning distance, taper the
speed linearly. The robot decelerates smoothly instead of slamming to a halt.

**What was measured.** Robots with **1.3 m of clear space ahead travelled at 0.04 m/s** —
1/30 of `v_max`. The fleet was not deadlocked. Over a 400 s run that distinction is
invisible: no task completed, and every symptom read as deadlock.

**The mechanism.** A linear taper between a stop field and an arbitrary warning distance
has no physical meaning, and here it self-reinforced. The *desired* speed sized the field
that then throttled the robot; the field never shrank, because the robot never got to
move. The loop had a stable fixed point at approximately zero.

**What changed.** The braking equation is inverted instead of tapered.
`RobotSpec.max_speed_for_clearance` (`src/settings.py:61`) solves
`v²/(2a) + v·τ + margin = clearance` for `v` and returns the fastest speed from which
that clearance is still enough to stop (`src/settings.py:86-96`). With 1.3 m ahead the
honest answer is full speed, and the robot still stops in time. Braking authority is a
fact about the chassis, not a tuning knob.

**This single change took the first task from "never completes" to completing.** It is
the largest single-commit effect measured in this project.

**What to take from it.** The tell is not "the fleet is slow"; it is "the fleet is slow
and the throttle is a function of the thing the throttle controls". Any control law where
the commanded output feeds back into the constraint that produced it needs a fixed-point
check before it needs tuning.

---

## 3. The protective field must budget for *closing* speed

**The intuition.** Each robot brakes correctly for its own speed. Two correct robots
cannot collide.

**What was measured.** Two robots meeting head-on **collided at 0.06 m separation**,
each having braked correctly the whole way in.

**The mechanism.** Braking slows *you*. The other party keeps coming for the entire time
you are stopping. Two robots at `v_max` close at 1.42 m/s combined while each budgets for
its own 0.71 m/s. Each was individually correct; the pair was not. This is the difference
between a per-agent invariant and a pairwise one, and it is the sharpest illustration in
this project that composing two correct components does not produce a correct system.

**What changed.** The solved inequality now carries the peer's approach speed
explicitly. The derivation is stated in the code:

```python
# src/settings.py:82
#   gap >= v*tau + v^2/(2a) + v_close*(tau + v/a) + margin
```

realised by the `v_closing` terms at `src/settings.py:88` and `src/settings.py:89`. The
`v_close · v/a` term is why this is not simply a larger constant: the faster you go, the
longer you take to stop, and the further the other party travels while you do it.

Closing speed is estimated per detection from the range rate, projected onto the bearing:

```python
# src/amr.py:1409-1411
closing = -(det.vx * ux + det.vy * uy)
gap = rng - spec.radius_m - det.r
limit = min(limit, spec.max_speed_for_clearance(gap, max(0.0, closing)))
```

in `_speed_limit_from_traffic` (`src/amr.py:1389`). `Detection` carries `vx`/`vy`
(`src/world.py:76-77`), populated for peers at `src/world.py:760-762` and for humans at
`src/world.py:767`, and zeroed for static obstacles at `src/world.py:771-772`. A real 2D
safety lidar with an object tracker supplies exactly this quantity, so the observation is
one a physical robot can make.

**The consequence that matters for the submission.** Head-on traffic now requires a much
larger gap, which makes the safety layer expensive if it is the only mechanism. That is
precisely the argument for a coordination layer: messaging exists so that Layer 0 rarely
has to fire. Across the 90-run acceptance campaign the candidate recorded
`safety_stop_ticks` low enough that the coordination layer, not the brake, is doing the
work — see [12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md).

---

## 4. A forward cone cannot see a side merge

**The intuition.** Obstacles that matter are in front of you. A ±60° forward cone covers
the dangerous directions.

**What was measured.** The `central` policy logged **389 contacts at 0.09 m separation**,
all at junctions.

**The mechanism.** Two robots converging on a four-way junction at ninety degrees each
sit *outside* the other's ±60° cone for the entire approach. Neither cone triggers.
They meet in the middle, at speed, with no braking on either side. The cone is not a
sensor limitation; it is a decision about which detections to act on, and the decision
was wrong for the one geometry that a warehouse grid produces constantly.

**What changed.** A 360° guard was added *in addition to* the directional field:

```python
# src/settings.py:32
omni_stop_m: float = 0.45       # 360 deg guard with relative-motion reserve
```

checked unconditionally at `src/amr.py:890` and fed by an all-directions minimum computed
in the world at `src/world.py:775`. The directional field still governs speed; the omni
guard is a hard floor that no bearing can escape.

**The engineering analogue.** Commercial AMRs carry 360° protective coverage — typically
two diagonally-opposed scanners, or a scanner plus a rear guard — for exactly this
reason. A single forward scanner is a fork-truck configuration, not an AMR one.

---

## 5. Block control everywhere is worse than block control nowhere

**The intuition.** Single-file segments cause head-on conflicts. Apply mutual exclusion
to every single-file segment and the conflicts disappear.

**What was measured.** Applying full block exclusion to **all 59 short gaps** in the
standard racking layout made throughput **worse than doing nothing at all**. The
breakdown is recorded in the code: 24 four-cell picking aisles and 35 two-cell rack gaps
(`src/amr.py:1707-1709`).

**The mechanism.** A two-cell gap between racks is not a corridor; it is a doorway. Token
acquisition costs a round of announce-observe-commit, and the segment is traversed in
less time than the round takes. Applying exclusion to all 59 turned the warehouse into a
series of toll gates: every robot spent more time acquiring permission to cross a gap
than crossing it, and the gaps are dense enough that a route crosses many of them. The
comment records the measurement:

```python
# src/amr.py:1694-1695
gap in a racking layout made the fleet markedly WORSE than doing nothing: 59
blocks on the standard map ...
```

**What changed.** Block control is scoped by length:

```python
# src/settings.py:152
min_controlled_block: int = 6   # cells; shorter gaps use plain per-cell yielding
```

enforced in `_controlled_block` at `src/amr.py:1713-1716`. Shorter gaps fall back to
per-cell yielding, which is cheap and sufficient when the segment is short enough that a
robot inside it clears quickly. Blocks are identified by scanning for connected runs of
degree ≤ 2 free cells in `corridors` (`src/environment.py:165`, scan at
`src/environment.py:183`, single cells dropped at `src/environment.py:202`).

**What to take from it.** A coordination mechanism has a fixed cost per invocation and a
benefit proportional to the contention it removes. Below some segment length the cost
dominates. The threshold is not a taste parameter; it is where those two curves cross,
and it has to be measured on the actual map.

---

## 6. Four coordination failures that had nothing to do with the radio

These are the most valuable entries in this catalogue, because each is a way a plausible
protocol fails while every component looks correct in isolation. None of them involves a
dropped packet, a corrupted message or a timeout. The radio worked perfectly throughout.

### 6.1 Priority inversion at a queue

**The intuition.** Ageing prevents starvation: the longer a robot waits, the higher its
priority climbs, so nobody waits forever.

**What was measured.** A robot at a corridor mouth yielded to a peer queued *behind* it.
The queue never moved.

**The mechanism.** Ageing had given the blocked peer a higher priority — and it does so
*reliably*, not occasionally, because whoever is stuck at the back of a queue accrues
priority fastest. The robot in front therefore stops for the robot it is itself blocking.
Ageing, the mechanism intended to guarantee liveness, was the direct cause of a permanent
stall.

**What changed.** Priority is no longer the tiebreak when both robots are entering by the
same mouth:

```python
# src/amr.py:1554-1556
# So position decides among robots entering by the same mouth, and priority
# only decides between robots arriving at *different* mouths, where both
# genuinely could go first.
```

Different mouths take the priority path (`src/amr.py:1557-1562`); the same mouth takes
the position path (`src/amr.py:1563-1566`), which compares Manhattan distance to the
entry cell returned by `nearest_end` (`src/environment.py:226`). The general rule: a
tiebreak is only meaningful between alternatives that are both physically achievable.

### 6.2 Symmetric yielding from comparing a live key against a published one

**The intuition.** Each robot computes its own priority, compares it against the peer's
priority as most recently heard, and the loser yields. Antisymmetric by construction.

**What was measured.** Four AMRs stood motionless for 400 simulated seconds, each
politely blocked on a peer that was blocked on it. No error, no dropped message, no
detected cycle.

**The mechanism.** Each robot compared its own **live** priority against the peer's
**published** one. Ageing advances continuously, so within a single broadcast period a
robot's live key exceeds the key it last published. Both sides do this, so both can
conclude they lost — using different, individually correct, numbers. The relation is not
antisymmetric, so it is not an order, and everything built on top of it inherits the
defect.

**What changed.** The key is latched at the moment it is transmitted:

```python
# src/amr.py:5037-5039
# Latch the key at the moment we publish it, so peers and we are comparing
# the same number for the whole heartbeat period.
self._pub_priority = self._priority(t)
```

with the richer PIBT key latched alongside at `src/amr.py:5042`. Arbitration reads only
the latched value — `_arbitration_key` (`src/amr.py:4964`) returns
`(self._pub_priority, self.rid)` at `src/amr.py:4978`, and `_peer_outranks`
(`src/amr.py:1819`) compares published against published at `src/amr.py:1824`. The
invariant is documented on the key type itself at `src/priority.py:27-29`.

**The general rule, stated so it transfers.** *Any peer tiebreak decided from broadcast
state must compare PUBLISHED against PUBLISHED, latching the value at the moment you
send it.* Comparing a live local value against a remote published value is not a
comparison of two things; it is a comparison of a thing against a stale photograph of a
different thing.

**The diagnostic tell, which is the part worth memorising.** The system was **idle and
consistent**. No error. No timeout. No cycle in any resource graph. Every node was
defensibly waiting, and every node could produce a correct justification for waiting. A
deadlock detector looks for a cycle in *who-holds-what*; this was a cycle in
*who-believes-what*. No wait-for-graph cycle-breaker can fix it, because neither robot is
wrong — they are reasoning from different snapshots of a value that is supposed to be
one value.

**The engineering analogue.** This is why two-phase commit and Paxos-family protocols
vote on a *proposal identifier that was transmitted*, never on a locally recomputed one,
and why vector-clock comparison is defined over received timestamps rather than current
ones. The failure mode is the distributed-systems equivalent of comparing `now()` on two
machines and concluding both are later.

### 6.3 Continuous ageing thrashes every commit round

**The intuition.** Smooth, continuous ageing is fairer than discrete steps — no
quantisation artefacts, no ties.

**What was measured.** Two waiting robots swapped rank several times a second. A commit
round takes 0.45 s. Neither robot ever held the lead long enough to complete
announce-observe-commit, so no round ever finished and neither ever moved.

**The mechanism.** Continuous ageing makes rank a continuous function of time, so two
robots with near-equal base priority cross over repeatedly at whatever rate their
accumulators differ. The protocol requires the leader to *remain* the leader for the
duration of a round. Continuous ageing guarantees it will not.

**What changed.** Ageing advances in discrete five-second steps:

```python
# src/amr.py:4996
return base + 50.0 * float(int(waited / 5.0))
```

with the rationale at `src/amr.py:4990-4995`. Five seconds is stable an order of
magnitude longer than a 0.45 s round takes, while still guaranteeing that nobody starves.
The separate PIBT key uses its own named quantum, `priority_age_quantum_s = 1.0`
(`src/settings.py:174`), applied at `src/amr.py:5010-5013`.

**What to take from it.** A value that a consensus round depends on must be stable for
longer than the round. That is a constraint relating two timescales, and it is invisible
if you only reason about the fairness property the value was introduced to provide.

### 6.4 A give-way manoeuvre blocked by the robot it was giving way to

**The intuition.** Give-way is just another movement. It goes through the same traffic
layer as every other movement.

**What was measured.** The give-way manoeuvre waited for the robot it was giving way to.

**The mechanism.** Layer 1 holds a robot when a higher-ranked peer wants the cell it is
moving into. A retreat moves *out of the way of* that same peer, so it was held by the
peer it existed to serve. The politeness was structurally self-defeating.

**What changed.** Retreats are exempt from Layer-1 holds and time-boxed:

```python
# src/amr.py:1139-1143
if self.state == ST_RETREAT:
    # A give-way manoeuvre must never be blocked by the robot it is giving way
    # to. That is a deadlock dressed as politeness ...
```

Layer 0 still validates every retreat step, so the exemption cannot cause a contact. The
time-box is at `src/amr.py:2631` (`if done or retreat_age > 6.0:`), because an exempt
state that can latch is worse than the hold it replaced. Note that both this timeout and
the hysteresis window in [§6.6](#66-priority-permission-needs-bounded-hysteresis) are
inline literals rather than named settings constants — see
[15. Limitations](15-LIMITATIONS.md).

### 6.5 Waiting in the doorway

A robot queued at a corridor mouth stands exactly where the robot inside must drive to
leave. There is no cycle to detect and no rule is violated; both robots are behaving
correctly and neither moves.

The fix is to pull off the *axis*, and the direction matters:

```python
# src/amr.py:2528-2532
# Prefer stepping SIDEWAYS. Reversing along the same axis just relocates
# the obstruction one cell down the lane the other robot is trying to use ...
```

`_passing_bay` (`src/amr.py:2510`) ranks candidates with `perpendicular` as the primary
key (`src/amr.py:2533`, ranking tuple at `src/amr.py:2540`). Reversing is not a
solution — it moves the obstruction without removing it. The same perpendicular-first
ranking is reused by the liveness valve `_bios_unstick` at `src/amr.py:2281`. The trigger
is a wait longer than `yield_aside_s = 2.0` (`src/settings.py:144`), at
`src/amr.py:1333-1337`.

### 6.6 Priority permission needs bounded hysteresis

Two merge contenders alternated permission every control tick. The winner's blocked timer
— and therefore its waiting priority — was cleared as soon as PIBT authorised motion,
*before the chassis had physically turned* and entered the next cell. It then lost the
next arbitration to the contender whose timer had kept running.

Retaining the age forever fixed the race and created starvation: a winner that could not
physically translate held its rank indefinitely. The resolution is a bounded lease:

```python
# src/amr.py:2135-2137
self._priority_grace_since = self.blocked_since
self._priority_grace_until = max(
    self._priority_grace_until, t + 6.0)
```

in `_track_block` (`src/amr.py:2120`), consumed at `src/amr.py:2123-2128` and when
building the published key at `src/amr.py:5001-5003`. Cell progress resets it
immediately; failure to progress lets it expire. Six seconds is long enough to execute
one admitted transition and short enough not to become a permanent priority lease. The
regression gate is `tests/test_benchmark.py:277`.

### 6.7 Right of way inside a block

A robot already inside a single-file aisle deferred to a higher-priority peer waiting
outside it. The aisle can then never empty, and the waiting robot can never enter — the
higher priority produced a strictly worse outcome for its holder. Whoever is committed
inside a block now outranks anyone still outside it, regardless of priority:

```python
# src/amr.py:1499-1500
if self.blocks.id_of(here) == cid:
    return None                     # already committed inside this block
```

with the legacy per-cell path at `src/amr.py:1251-1255` and the doctrine stated at
`src/amr.py:1246-1250`. Regression gate: `tests/test_priority.py:958`.

---

## 7. An idle robot is a permanent wall

**The intuition.** A robot that has finished its work is out of the way. It is not
competing for anything.

**What was measured.** A robot that finished its queue stopped on a drop station and the
peer that needed that cell never completed. Every symptom read as a planner deadlock.

**The mechanism.** Yielding, block control and deadlock breaking all assume both parties
are trying to go somewhere. None of them has any way to prompt a robot that has already
arrived and has no reason to move again. The failure is silent and total. The docstring
states it:

```python
# src/amr.py:3254-3261
def _vacate_if_in_the_way(self, t: float, sensors: Sensors) -> None:
    """Parked on somebody's destination? Move.

    Without this an idle robot is a permanent wall. Every other mechanism here -
    yielding, block control, deadlock breaking - assumes both parties are trying to
    go somewhere; none of them can prompt a robot that has already arrived and has
    no reason to move again. ...
```

**What changed, and its current status.** Two things, and both are in the shipped code.

First, heartbeats carry the sender's goal. The parameter is `goal` on `msg.heartbeat`
(`src/messages.py:448-451`), serialised as the single-character wire field `"g"`
(`src/messages.py:469`) with the justification at `src/messages.py:465-468`. It is
populated at `src/amr.py:5060` and parsed into `Peer.goal` at `src/amr.py:5281`. Without
it there is no way for a parked robot to know it is in someone's way, because a robot
that has arrived publishes no intent tail.

Second, `_vacate_if_in_the_way` (`src/amr.py:3254`) detects the condition at
`src/amr.py:3264-3267` — any peer whose goal is this cell, or whose intent horizon
contains it — and moves.

**A second measured failure inside the fix.** The first version selected another cell in
the same advertised intent horizon and oscillated between two cells at the corridor mouth
while the task-owning robot was repeatedly displaced away from its goal. Parking
candidates now exclude peer cells, peer goals *and* every cell in the current peer intent
horizon:

```python
# src/amr.py:3284-3291
taken = {p.cell for p in self.peers.values()} | {
    p.goal for p in self.peers.values()
    if p.goal and (p.task_id is not None or p.state == ST_CHARGING)} | {
    cell
    for p in self.peers.values()
    if p not in explicit_blockers
    for cell in p.intent}
```

with a deliberate carve-out at `src/amr.py:3277-3283` for the requester's own intent,
because the idle chassis may need to move one cell *forward* along that corridor before
any side bay exists.

**Did it change the measured outcome?** Yes, and the evidence is specific rather than
general. `archive/HUMAN_FLOW_AUDIT.md` records that making idle parking yield locally to
active task traffic is one of four fixes exercised by the 10-AMR Grand Challenge
acceptance campaign, which completes 100/100 tasks across seeds 0–4 with zero contacts of
every recorded kind, and it names a **seed-4 parking-tail regression** specifically. Two
regression gates hold the behaviour: `tests/test_dashboard.py:193`
(`test_loaded_robot_preempts_idle_parking_cycle_in_grand_challenge`) and
`tests/test_priority.py:811`. What has *not* been done is an A/B measurement of the same
campaign with the mechanism disabled, so the honest statement is that the campaign passes
with the fix and named seeds failed without it — not that a controlled experiment
isolated its contribution.

---

## 8. Throughput measurement is seed-sensitive, and one result was retracted

This entry is a finding about measurement discipline. It is included because the
retraction is one of the more credible things in this repository, and because the
withdrawn claim is exactly the kind that a hackathon submission normally ships.

**The claim that was made.** Profiling showed per-tick simulation cost rising over the
course of a run. The natural reading — and the one that was written down — was that
*episodes get slower as they run*, which would imply a compounding cost with congestion
and would have been a genuine scaling result.

**Why it was withdrawn.** It came from profiling **seed 0 alone**. Pooled over seeds 0–3
the cost is flat at approximately **22× realtime**. There was no trend to explain.

**What made the error possible.** Episode cost is a random variable over seeds, and the
spread is large. The same 90 s episode ranges from 2.07 s to 5.96 s of wall clock
depending only on the seed — a **2.9× spread**, with a training budget estimated from one
seed wrong by 3.4×. A single-seed profile cannot distinguish a trend within a run from
the position of that seed inside a wide distribution.

**What survived the retraction, and it is the more useful result.** Per-tick cost tracks
the number of peers in sensor range, and it **saturates**. The measurement is recorded in
the training configuration, where it drives a design decision:

```python
# src/evolve.py:142-146
# (sim seed, episode length). The mixed lengths are deliberate: at 120 s the fleet
# is still dispersed (1.6-2.0 detections/tick) and at 240 s it is saturated at 3.0,
# so training on short episodes alone would teach a policy about a regime that is
# not the one it exists to fix. One long episode per genome buys that exposure at
# roughly twice the cost of a short one.
```

The consequence is a real one for every experiment in this project: **the first 120 s of
a run is a different regime from the one the fleet actually fails in.** At 1.6–2.0
detections per tick the robots are still dispersed and coordination is barely exercised;
at 3.0 the scenario is saturated and coordination is the whole problem. A short run does
not measure a scaled-down version of the long run — it measures a different system. The
training episode set at `src/evolve.py:148` mixes 120 s and 240 s episodes for exactly
this reason, and [§13](#13-open-two-configurations-that-should-differ-and-do-not) is a
live instance of the same trap.

**What to take from it.** Report cost pooled over seeds, or report the distribution. A
per-seed number is an anecdote about a random variable with a 2.9× spread. The same
discipline is applied to safety in `safety_report` (`src/metrics.py:213`), whose
docstring says it directly: "one run is an anecdote".

---

## 9. A trained policy expires when the simulator changes

**The intuition.** A model file is a self-describing artefact. If the format check
passes, the features match and the seeds are the same, the model is the model.

**What was measured.** Identical weights, identical features, identical held-out seeds
8–11 at 420 s and 4 robots: **13/48 tasks before, 0/48 after.** Not degraded — zero.

**The mechanism.** Teammates rewrote the Layer-1 machinery the policy's verbs act on.
BIOS_4 selects one of five verbs — proceed, hold, yield to a passing bay, respect the
block token, replan — at the 10 Hz traffic layer (`src/amr.py:2417-2418`). It does not
drive the wheels. When the meaning of those verbs changed, a 549-parameter network fitted
to their old semantics was left issuing well-formed instructions with different effects.
Two contributing failures compounded it: a merge left `POLICY_BIOS4` selectable while
silently dropping every call site, so the policy produced plausible numbers while
discarding the uploaded model; and `progress_cells`, the dense reward `src/evolve.py`
optimises, lost its accumulator and read 0 for every policy.

**The control that made the diagnosis possible.** The hand-written `BIOS_1.0.0` baseline
scored **7/48 before AND after**. That is the entire argument: an unchanged baseline
across the same simulator change proves the simulator did not regress, and isolates the
failure to the one policy whose behaviour was *fitted* to the old dynamics. Without a
non-learned control in the comparison set, the only available conclusion would have been
"something broke", and the obvious suspect would have been the wrong one.

**Re-measured on the same held-out seeds 8–11 at 420 s, 4 robots:**

| policy | tasks | progress | r-r | r-h | rack | min sep |
|---|---|---|---|---|---|---|
| `stop_and_wait` | 0/48 | 59 | 0 | 0 | 0 | 0.900 |
| `central` | 0/48 | 56 | 0 | 0 | 0 | 0.860 |
| `hierarchical` | 0/48 | 33 | 0 | 0 | 0 | 0.850 |
| `BIOS_1.0.0` | **7/48** | 97 | 0 | 0 | 0 | 0.736 |
| `BIOS_4` (2026-08-24 weights) | **0/48** | 52 | 0 | 0 | 0 | 0.855 |
| `BIOS_4` (retrained 2026-08-25) | **6/48** | 90 | 0 | 0 | 0 | **0.869** |

Retraining against the current code recovers 0/48 → 6/48, at the best worst-separation of
any policy measured. It still does not beat `BIOS_1.0.0` (6 vs 7), and the honest reading
is "matches the hand-written baseline at better separation", not "beats it".

**Safety never degraded.** Zero robot-robot, zero robot-human and zero rack contacts
across every row, including the expired model. The guarantees live in ordinary Python
below the network — `_safety` (`src/amr.py:868`) is applied to the output of every policy
on every tick and reads only `Sensors`. **A stale model is slow, not dangerous.** That is
the specific claim the layered architecture was built to be able to make, and this is the
measurement that tests it rather than asserting it.

**What changed, and what did not.** Retraining restored throughput. The provenance gap
that made the expiry silent is **still open**: a model file records its features, its
actions, its training seeds and its withheld evaluation seeds, but **nothing about the
code version it was trained against**. Inspecting `models/bios4.json` shows a `meta`
block with `trained_by`, `algorithm`, `fitness`, `generations`, `train_seeds` and
`eval_seeds_withheld` — and no commit hash. A simulator change therefore expires a model
with every format check still passing. See
[15. Limitations](15-LIMITATIONS.md#2-verified-defects).

**The engineering analogue.** This is training-serving skew, and the standard mitigation
is the one missing here: pin the environment version in the model artefact and refuse to
load a model whose environment version does not match. A learned policy is a function of
the dynamics it was evolved against; the dynamics are part of the model, and an artefact
that does not record them is incomplete.

### 9.1 Two secondary results worth more than the headline

**Panic-on-stick can drive into shelving, and the mechanism is real even though the
headline number is not.** Layer 0's creep window returns before the mapped-geometry
checks, so while creeping, shelving is protected only by a 3-ray ±20° probe while the
chassis is a full 0.35 m circle — and `omega` passes through unchanged. Panic-on-stick
edges into "any free adjacent cell", so over enough robot-minutes that combination can
find the racking. This is only visible because the world records rack contacts rather than
blocking silently ([§1.3](#13-the-mapped-geometry-backstop-is-instrumented-not-silent)).

> **The "15 rack contacts against zero" figure once quoted here is refuted — do not use
> it.** It originates in a section of the superseded `archive/FINDINGS.md` that the file itself
> marks as superseded, and that correction's re-measurement on the same seeds and
> configuration records **0**. Summing `contacts_robot_rack` across all 1,030 runs in all
> 13 checked-in artifacts gives zero everywhere. The *mechanism* above is derived from the
> code and stands; the *measurement* does not, and quoting it to a judge invites a
> falsifiable claim. See [15. Limitations](15-LIMITATIONS.md) and
> [07. Safety](07-SAFETY.md).

**The backstop is not doing the work.** The unstick valve fired 23 times across four
420 s runs. A genome that always holds triggers it roughly 120 times per robot per run,
so if the learned policy were merely riding on panic-on-stick that number would be in the
hundreds. It is instrumented as `PolicyResult.bios4_unstick` precisely so the claim can be
checked rather than asserted.

### 9.2 Three training lessons

**A sparse reward has no slope.** Over a 120 s episode the fleet completes 0–3 of 12
tasks, so a fitness of "tasks completed" is almost all zeros and evolution has nothing to
climb. `progress_cells` — cells of *net* approach to a goal — is the dense companion, and
it is monotone on purpose: crediting every goalward step would pay a robot to oscillate.

**An argmax policy starts on a plateau.** Initialised at zero, every logit is equal, the
first legal verb always wins, and small updates never flip the decision. The ES iterate
sat at identical fitness for **thirteen generations** before escaping. The elite still
improved throughout, because it comes from the sampled population rather than from the
iterate — which is the only reason the run produced anything. **Ship the best genome ever
scored, never the final iterate.** The reasoning is preserved at `src/evolve.py:134-139`,
along with the decision to leave `init_scale = 0.0` (`src/evolve.py:140`) rather than
change a default that no run has yet measured — "an unvalidated fix is worse than a
documented limitation".

**Population variance dwarfs the gradient.** Generation best swung between 8 and 3039
while the mean climbed steadily from −4784 to roughly +150. The mean is the signal that
the search is working; the best is mostly luck. This is the same conclusion as
[§8](#8-throughput-measurement-is-seed-sensitive-and-one-result-was-retracted), reached
independently in a different measurement.

---

## 10. A saturated benchmark configuration cannot discriminate between policies

**The intuition.** A harder configuration is a better test. If a policy is genuinely
better, it will show up more clearly under more load.

**What was measured.** The first re-measurement in
[§9](#9-a-trained-policy-expires-when-the-simulator-changes) was run at **12 robots**.
There, `stop_and_wait`, `central`, `hierarchical` and *both* BIOS_4 models all score 0,
and `BIOS_1.0.0` scores 2/144.

**The mechanism.** At that fleet size the scenario is in near-total gridlock. Every
difference between policies is inside the noise of a system that is failing for reasons
none of them address. The configuration looks like a valid comparison — same map, same
seeds, same task catalog, every policy run identically — and it measures nothing. It
would have supported any conclusion the reader arrived with, including the wrong one
about BIOS_4 that it nearly produced.

**The rule.** **A config where the baselines all read zero is not a hard test; it is a
broken instrument.** Before reading your own result into a benchmark, check that it
separates policies you *already know* differ. Here `BIOS_1.0.0` at 7/48 and
`stop_and_wait` at 0/48 on the 4-robot held-out configuration is that check: the
instrument demonstrably resolves a known difference, so a new zero is informative.

**The engineering analogue.** This is a floor effect, and it is the reason a measurement
instrument is characterised against known standards before being used on an unknown. A
benchmark with no dynamic range at the operating point is not a strict benchmark; it is
an uncalibrated one.

**Where the same trap is live in this repository.**
[§13](#13-open-two-configurations-that-should-differ-and-do-not) is a ceiling-effect
instance of the identical error, found while writing this document.

---

## 11. Discrete collision freedom is not continuous executability

PIBT returned a collision-free next-cell configuration and two robots nevertheless
remained safety-stopped forever. The grid endpoints were valid; the *swept trajectories*
between them were not. A leader turning west while its follower entered from the south
initially *reduced* the physical chassis gap, even though the two destination cells were
distinct. Layer 0 was correct to reject both commands.

The gap between the two representations is the finding: discrete occupancy proves that no
two robots want the same cell. It does not prove that the continuous motion connecting
one configuration to the next is executable at the chassis's turning radius and braking
authority. The `sep < 2 * r` swept test at `src/world.py:704-705`, over
`segments_min_distance` (`src/geometry.py:68`), works in the continuous representation and
is not fooled by valid endpoints.

The fix stages a follower one cell before an occupied junction when the leader is turning:

```python
# src/amr.py:1600-1605
# ... it does not by itself leave enough continuous braking room when the
# leader is turning through a merge. Stage one cell before an occupied
# junction so the queue cannot compact into an omnidirectional safety latch.
```

in `_v3_staging_conflict` (`src/amr.py:1586`), wired ahead of block arbitration at
`src/amr.py:1214-1217`. Straight, well-spaced convoys still flow without serialisation —
the carve-out at `src/amr.py:1623-1628` declines to treat a leader turning *elsewhere* as
a conflict. Regression gate: `tests/test_priority.py:794`.

---

## 12. Simulation-harness lessons

Six measurement bugs, each of which produced numbers that looked like results.

**Sample start cells without replacement.** Two robots spawned in one cell overlap for the
entire run, and the contact counter reports ~400 "collisions" that are one broken initial
condition. A benchmark that starts in an impossible state cannot measure anything, and it
fails *loudly*, which is worse — the number is large enough to look like a real safety
result.

**A schedule is an earliest-entry bound, not a pace.** Timestamps computed from a nominal
cruise speed slower than the robot can actually drive leave every robot permanently
"ahead of schedule", holding at every single cell. The fleet crawled at one cell per
replan. Same class of error as
[§2](#2-a-linear-slow-down-zone-is-not-physics-and-it-cost-an-order-of-magnitude): a
constraint derived from a number that was never meant to be a constraint.

**Do not re-issue an unchanged plan every second.** It resets the robot to the head of a
fresh schedule each time, so it never gets far enough into one to benefit. The reroute
cooldown is rate-limited at 3 s deliberately, and it halved replan churn: 729 replans for
BIOS_4 against `BIOS_1.0.0`'s 1556 and `hierarchical`'s 974. It was left rate-limited
during training because unlimited replanning is a known pathology here, and evolution
would otherwise have rediscovered it and called it a strategy.

**Compress space-time plans carefully.** Waiting is encoded as "stay in the same cell for
k steps". Hand the robot only the cell list and it collapses the repeats and sails
straight through the conflict the wait was avoiding — and the central baseline silently
stops being conflict-free. A baseline that has quietly lost its defining property is the
most dangerous object in a comparison.

**Per-destination delivery queues.** One shared queue drained and re-pushed per poll is
quadratic in fleet size; it dominated runtime before anything else did.

**Ray-cast with grid traversal, not fixed steps.** Amanatides–Woo costs one iteration per
cell crossed instead of eighty per ray. At 50 Hz × N robots it decides whether the
benchmark runs in seconds or minutes — and per
[§8](#8-throughput-measurement-is-seed-sensitive-and-one-result-was-retracted), simulation
speed is what buys the robot-hours that a safety upper bound is computed from. Speed here
is not convenience; it is statistical power.

---

## 13. OPEN: two configurations that should differ, and do not

Found while writing this document, by running the dashboard's own defaults.

**What was measured.** On `showcase_chokepoint` at the server's default request shape —
4 robots, seed 0, 120 s — all four combinations of `{BIOS_PIBT.5, BIOS_PIBT.6} ×
{auction, auction_bundle}` return a **byte-identical summary**: 3/8 tasks,
`makespan_s = 120.0`, `min_separation_m = 1.309`, `replans = 26`, `completed_all: false`.

**Half of it is a measurement artefact, and the mechanism is
[§6](#6-four-coordination-failures-that-had-nothing-to-do-with-the-radio)-flavoured but
much simpler.** `showcase_chokepoint` is natively a 420 s scenario with 8 tasks.
`parse_run_request` supplies `duration = 120` unconditionally
(`backend/server.py:197`), and `run_for_dashboard` applies it at
`src/main.py:661-662`. The run is truncated before the scenario reaches the congested
regime that distinguishes the policies at all — which is exactly the 120 s-versus-240 s
regime split measured in
[§8](#8-throughput-measurement-is-seed-sensitive-and-one-result-was-retracted). Re-run at
the scenario's native 420 s, the policies do separate: `BIOS_PIBT.5` sends 12,130
messages and `BIOS_PIBT.6` sends 8,387, a 30.9 % reduction consistent with V6's
documented message-suppression result.

**The other half is not an artefact and remains open.** At the native 420 s,
`auction` and `auction_bundle` are still identical on **every** metric including message
count — 8/8 tasks, 258.66 s makespan, 0.911 m minimum separation, 40 replans. The
diagnosis is that `auction_bundle`'s distinguishing mechanism never fires on this
scenario: `future_candidates_evaluated`, `future_bids_sent`, `future_bids_won` and
`future_promotions` are all **0** over the full 420 s run. With no future bids, the
bundled allocator degenerates exactly to the frozen one. That is defensible behaviour —
`archive/BIOS6_EXPERIMENTAL_BOUNDED_FUTURE_AUCTION.md` reports the same near-zero-overhead
result on a sparse workload — but it means the dashboard's default allocator is
indistinguishable from the frozen one on the dashboard's own showcase scenario, and no
demonstration on this scenario can evidence requirement 14.

**Why it matters more than a cosmetic bug.** Two false comparison conclusions have
already been drawn from truncated dashboard runs. The failure is the ceiling-effect twin
of [§10](#10-a-saturated-benchmark-configuration-cannot-discriminate-between-policies):
there, every baseline read zero and the benchmark could not discriminate; here, every
configuration reads the same non-zero value and the benchmark still cannot discriminate.
Both look like valid comparisons. **The check is the same in both directions — confirm
your instrument separates two configurations you already know differ, before reading a
new result into it.**

**Status.** The truncation is a known defect with a known fix (honour the scenario's own
duration and robot count unless the request overrides them explicitly). The
`auction_bundle` equivalence on this scenario is understood but unresolved: it needs
either a showcase scenario whose workload actually triggers future bidding, or an honest
statement on the dashboard that the two allocators coincide here. Tracked in
[15. Limitations](15-LIMITATIONS.md#212-post-apirun-silently-truncates-long-scenarios-to-defaults)
and [§7 there](15-LIMITATIONS.md#3-two-things-that-are-not-limitations).

---

## What this catalogue is evidence of

Ten of the thirteen entries above are cases where a mechanism that was individually
correct produced a system that was wrong: a correct protective field that could not be
escaped, two correctly braking robots that collided, a correct ageing rule that caused the
starvation it prevents, a correct give-way that waited for its beneficiary, a correct
PIBT configuration that was not executable, a correctly formatted model that had expired,
and two correctly constructed benchmarks that could not measure anything. That is the
pattern worth taking away, and it is why the safety layer in this system is deliberately
the *least* clever component: it is the only one whose correctness is local enough to
reason about in isolation.

---

**Related documents:** [05. Coordination Policies](05-COORDINATION-POLICIES.md) ·
[07. Safety](07-SAFETY.md) · [12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md) ·
[13. Testing](13-TESTING.md) · [15. Limitations](15-LIMITATIONS.md)
