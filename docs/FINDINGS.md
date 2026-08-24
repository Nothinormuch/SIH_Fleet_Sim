# Engineering findings

Bugs that were real, cost time, and are worth not rediscovering. Several of them are
also the most defensible content in the submission: they are the difference between a
simulation that *looks* like it works and one whose numbers mean something.

---

## 1. A fixed protective field is wrong in both directions

**Symptom:** every robot froze permanently at t = 0.

A stationary robot one metre from shelving had 1.15 m of clearance against a fixed 1.8 m
protective field, so it could never set off — and it could not turn away either, because
the stop also killed rotation.

**Fix:** size the field from the braking equation, `v²/(2a) + v·τ + margin`. At `v_max`
that is ~1.17 m; at standstill it collapses to the 0.15 m margin. A parked robot can pull
away from a wall; a robot at full speed cannot drive into one. This is what real AMR
safety scanners do (ISO 3691-4 field-set switching) and it is not an optimisation.

Also: **rotation must survive a protective stop.** A robot that may not turn cannot face
away from whatever stopped it, and a stop it cannot recover from is just a slower way to
deadlock.

## 2. Mapped geometry and unexpected objects need different fields

**Symptom:** with the field fixed, robots still crawled at wall-adjacent pick cells.

A ±60° cone at a robot hugging shelving always reads a few centimetres — and picks
happen exactly there. Collapsing "known wall" and "unknown object" into one number makes
every pick station unreachable.

**Fix:** two channels. Mapped racks get a narrow ±20° cone and a crawl limit, because the
planner only routes through cells it knows are free. Unexpected objects get the full
speed-scaled field and absolute authority. The hard backstop against mapped geometry
stays, and when it fires the world records a rack contact — so a wrong map shows up in
the results instead of hiding.

## 3. A linear "slow-down zone" is not physics, and it costs an order of magnitude

**Symptom:** robots with 1.3 m of clear space ahead travelled at **0.04 m/s**. The fleet
was not deadlocked; it was moving at 1/30 speed, which is indistinguishable from
deadlocked over a 400-second run.

A linear taper between the stop field and an arbitrary warning distance has no physical
meaning. Worse, it self-reinforced: the *desired* speed sized the field that then
throttled the robot, and the field never shrank because the robot never got to move.

**Fix:** invert the braking equation — `max_speed_for_clearance(gap)` returns the fastest
speed from which that gap is still enough to stop. At 1.3 m the answer is full speed.
This single change took the first task from "never completes" to completing.

## 4. The field must account for *closing* speed, not just own speed

**Symptom:** two robots meeting head-on collided at 0.06 m separation despite both
braking correctly.

They closed at 1.42 m/s combined while each budgeted for its own 0.71 m/s. Braking only
slows *you*; the other party keeps coming for the entire time you are stopping.

**Fix:** solve `gap ≥ v·τ + v²/(2a) + v_close·(τ + v/a) + margin` for `v`. Detections
carry an estimated velocity (a real 2D safety lidar plus tracker provides range rate).
Head-on traffic now needs a much larger gap — which is precisely the argument for a
coordination layer: messaging exists so the safety layer rarely has to fire.

## 5. A forward cone cannot see a side merge

**Symptom:** the `central` policy logged 389 contacts at 0.09 m separation.

Two robots converging on a junction at ninety degrees each sit outside the other's ±60°
cone. Nothing triggers, and they meet in the middle.

**Fix:** a 360° guard with a hard stop at 0.30 m, in addition to the directional field.
Real AMRs carry 360° protective coverage for exactly this reason.

## 6. Coordination bugs that had nothing to do with the radio

These are the ones worth presenting, because each is a way a plausible protocol fails
while every component looks correct in isolation.

**Priority inversion at a queue.** A robot at a corridor mouth yielded to a peer queued
*behind* it, because ageing had given the blocked peer a higher priority. The robot in
front stops for the robot it is itself blocking, and the queue never moves. Ageing makes
this certain rather than unlikely: whoever is stuck at the back accrues priority fastest.
*Fix:* position decides between robots entering by the same mouth; priority only decides
between robots arriving at different mouths, where both genuinely could go first.

**Symmetric yielding from a stale key.** Each robot compared its own *live* priority
against the peer's *published* one. Both did the same, so inside one heartbeat period
both could conclude they were the loser. Both yield, forever, and the wait-for graph
shows a mutual block that no cycle-breaker can fix because neither robot is wrong.
*Fix:* arbitrate on published-vs-published, which makes the relation antisymmetric.

**Continuous ageing thrashes any commit round.** Two waiting robots swapped rank several
times a second, so neither ever held the lead long enough to complete a 0.45 s commit
round. *Fix:* discrete five-second ageing steps — stable far longer than a round takes,
while still guaranteeing nobody starves.

**Right of way inside a block.** A robot already inside a single-file aisle deferred to a
higher-priority peer waiting outside it. The aisle can then never empty and the waiting
robot can never enter. *Fix:* whoever is committed inside a block outranks anyone still
outside it, regardless of priority.

**Giving way while blocked.** The give-way manoeuvre was itself subject to traffic holds,
so it waited for the robot it was giving way to. *Fix:* retreats are exempt from Layer 1
(Layer 0 still protects them), and they are time-boxed so they cannot latch.

**Waiting in the doorway.** A robot queued at a corridor mouth stands exactly where the
robot inside must drive to leave. No cycle to detect, no rule violated, and neither moves.
*Fix:* pull off the axis — sideways, not backwards, since reversing just relocates the
obstruction down the lane the other robot needs.

**Block control everywhere is worse than none.** Applying full block exclusion to all 59
short gaps in a racking layout turned the warehouse into a series of toll gates and made
throughput *worse than doing nothing*. Block control is now scoped to runs of ≥ 6 cells,
where per-cell yielding genuinely cannot recover.

## 7. Discrete collision freedom is not continuous executability

**Symptom:** PIBT returned a collision-free next-cell configuration, yet two robots
remained safety-stopped forever.

The grid endpoints were valid, but a leader turning west while its follower entered
from the south initially reduced the physical chassis gap. A similar straight-convoy
failure occurred when the leader was laterally off-center and the follower had already
crossed half a cell. Layer 0 was correct to reject both commands; discrete occupancy had
not proved their swept trajectories executable.

**Fix:** stage a follower at its own cell centre when the leader turns, or when measured
separation is already inside the standstill guard margin. The leader then receives a
bounded recovery command only while relative velocity increases every close-peer gap.
Straight, well-spaced convoys still flow without serialization.

## 8. Idle parking must avoid the route, not just the destination

**Symptom:** an idle robot noticed that it blocked a peer, stepped aside, then selected
another cell in the same advertised intent horizon. It oscillated between two cells at
the corridor mouth while the task-owning robot was repeatedly displaced away from its
goal.

**Fix:** parking candidates exclude peer cells, peer goals and every cell in the current
peer intent horizon. The idle AMR therefore selects a genuine side bay. Idle heartbeats
also clear the peer's previous active intent, and a robot with no goal never publishes
the tail of an old path.

## 9. Priority permission needs bounded hysteresis

**Symptom:** two merge contenders alternated permission every control tick. The winner's
blocked timer—and therefore its waiting priority—was cleared as soon as PIBT authorized
motion, before the chassis had time to turn and enter the next cell.

Keeping the age forever solved that race but starved other traffic whenever the winner
could not physically translate.

**Fix:** retain the accumulated waiting rank only for the six-second verified recovery
window. Cell progress resets it immediately; failure to progress lets it expire. This
is long enough to execute one admitted transition and bounded enough not to become a
permanent priority lease.

---

## Simulation-harness lessons

- **Sample start cells without replacement.** Two robots spawned in one cell overlap for
  the entire run, and the contact counter reports ~400 "collisions" that are one broken
  initial condition. A benchmark that starts in an impossible state cannot measure
  anything.
- **A schedule is an *earliest-entry* bound, not a pace.** Timestamps computed from a
  nominal cruise speed slower than the robot can actually drive leave every robot
  permanently "ahead of schedule", holding at every single cell. The fleet crawled at one
  cell per replan.
- **Do not re-issue an unchanged plan every second.** It resets the robot to the head of
  a fresh schedule each time, so it never gets far enough into one to benefit.
- **Compress space-time plans carefully.** Waiting is encoded as "stay in the same cell
  for k steps". Hand the robot only the cell list and it collapses the repeats and sails
  straight through the conflict the wait was avoiding — and the central baseline silently
  stops being conflict-free.
- **Per-destination delivery queues.** One shared queue drained and re-pushed per poll is
  quadratic in fleet size; it dominated runtime before anything else did.
- **Ray-cast with grid traversal, not fixed steps.** Amanatides–Woo costs one iteration
  per cell crossed instead of eighty per ray. At 50 Hz × N robots it decides whether the
  benchmark runs in seconds or minutes.

---

## BIOS_4 — what a learned coordination policy actually bought

`BIOS_4` picks one of five verbs at the 10 Hz traffic layer — proceed, hold, yield to a
passing bay, respect the block token, replan — from a 549-parameter network trained by an
evolution strategy. It does not drive the wheels. Trained on seeds 0–2, **reported here on
held-out seeds 8–11 at 420 s, which is longer than any episode it ever saw:**

| policy | tasks (4 seeds) | per seed | progress | r-r | r-h | rack | min sep | replans |
|---|---|---|---|---|---|---|---|---|
| `stop_and_wait` | 0/48 | 0,0,0,0 | 59 | 0 | 0 | 0 | 0.855 | 366 |
| `central` | 0/48 | 0,0,0,0 | 54 | 0 | 0 | 0 | 0.849 | 32 |
| `hierarchical` | 0/48 | 0,0,0,0 | 31 | 0 | 0 | 0 | 0.851 | 974 |
| `BIOS_1.0.0` | 7/48 | 4,3,**0,0** | 99 | 0 | 0 | **15** | 0.732 | 1556 |
| **`BIOS_4`** | **13/48** | 3,3,3,4 | 129 | 0 | 0 | **0** | 0.865 | 729 |

**The per-seed column is the result, not the total.** `BIOS_1.0.0` scores well on two
seeds and *completely fails* on the other two; `BIOS_4` delivers on all four. A
best-of-one-seed comparison would have shown `BIOS_1.0.0` winning (4 vs 3) and hidden the
thing that actually matters. This is the same reason `safety_report()` pools runs, applied
to throughput.

Three secondary results worth more than the headline:

- **`BIOS_1.0.0` drives into shelving and `BIOS_4` does not** — 15 rack contacts against
  zero, and a worse worst-separation (0.732 m vs 0.865 m). Panic-on-stick edges into "any
  free adjacent cell" and Layer 0's creep window lets it through the omni guard; over
  28 robot-minutes that finds the racking. The learned policy reaches the same liveness
  without needing the valve nearly as often.
- **The backstop is not doing the work.** The unstick valve fired 23 times across four
  420 s runs. If the learned policy were merely riding on panic-on-stick, that number
  would be in the hundreds — a genome that always holds triggers it roughly 120 times per
  robot per run. It is instrumented (`PolicyResult.bios4_unstick`) precisely so this claim
  can be checked rather than asserted.
- **The reroute cooldown halved the churn.** 729 replans against `BIOS_1.0.0`'s 1556 and
  `hierarchical`'s 974. Replanning was left rate-limited at 3 s deliberately: unlimited
  replanning is a known pathology here, and evolution would otherwise have rediscovered it
  and called it a strategy.

### The guarantees do not depend on the training working

Tested rather than claimed. An always-hold model still moves the fleet (the valve fires
above the model, on its own timer); an always-proceed model — the most reckless verb,
ignoring block ownership and peer intent entirely — still produces zero contacts, because
Layer 0 is below all of it. Twelve *arbitrary* 549-parameter genomes over 12 episodes
likewise produced zero robot-robot and zero robot-human contacts. A badly trained BIOS_4 is
slow; it is not unsafe and it does not deadlock.

### Training lessons

- **A sparse reward has no slope.** Over a 120 s episode the fleet completes 0–3 of 12
  tasks, so a fitness of "tasks completed" is almost all zeros and evolution has nothing to
  climb. `progress_cells` — cells of *net* approach to a goal — is the dense companion.
  Monotone on purpose: crediting every goalward step would pay a robot to oscillate, and
  the fitness would be maximised by twitching in place.
- **An argmax policy starts on a plateau.** Initialised at zero, every logit is equal, the
  first legal verb always wins, and small updates never flip the decision. The ES iterate
  sat at exactly the same fitness for **thirteen generations** before escaping. The elite
  still improved throughout, because it comes from the sampled population rather than from
  the iterate — which is the only reason the run produced anything. Ship the best genome
  ever *scored*, never the final iterate.
- **Cost per episode is a random variable over seeds.** The same 90 s episode ranges from
  2.07 s to 5.96 s of wall clock depending on the seed. A training budget estimated from
  one seed was wrong by 3.4×.
- **Population variance dwarfs the gradient.** Generation best swung between 8 and 3039
  while the mean climbed steadily from −4784 to roughly +150. The mean is the signal that
  the search is working; the best is mostly luck.

### What this does not show

One training run, one scenario, one fleet size. 13/48 is 27% of the task set — no policy
here completes it. There is no variance estimate over *training* seeds, so "BIOS_4 beats
BIOS_1.0.0" is one sample of a training process, not a distribution. And none of it has
touched real hardware: the sim-to-real claim rests on the observation space being
restricted to what a robot can actually sense and receive, which is an argument, not a
measurement.
