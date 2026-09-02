# 06. TASK ALLOCATION AND RE-ROUTING

> This document establishes how a task gets from the order source to a robot without a
> central assigner, how it comes back and is won again when the holder fails, and how a
> robot that meets a blocked aisle decides between routing around it and giving it up.

**Audience:** SIH judges and BEL evaluators reading the code for the first time, and
teammates who must defend these mechanisms live.
**Reads best after:** [05. Coordination Policies](05-COORDINATION-POLICIES.md)

Sub-problem 3 of SIH26123 is named in the problem statement as *"Task Allocation &
Re-routing: automatically re-assigning pickup points or changing paths if one robot
encounters a blocked aisle."* That sentence is three separate mechanisms — allocation,
re-routing, re-assignment — and they are implemented in three different places. This
document traces each one end to end.

## Requirements evidenced

| # | Requirement | Where | Evidence |
|---|---|---|---|
| 6 | No central coordination server | [§2](#2-the-decentralised-auction-in-mechanism-detail) | `src/main.py:272`, `src/amr.py:3558` |
| 9 | Real-time conflict resolution (allocation-side) | [§2.5](#25-admission-control-inside-the-matching) | `src/amr.py:3816` |
| 12 | Blocked aisle handling | [§6](#6-blocked-aisle--re-route--re-assign-requirements-12-13-14) | `src/amr.py:2849` |
| 13 | Re-routing | [§6.2](#62-step-2--re-route) | `src/amr.py:2867`, `src/amr.py:2698` |
| 14 | Task re-assignment | [§5](#5-task-re-assignment-requirement-14) | `src/amr.py:4690`, `src/amr.py:4727` |
| 15 | Edge / local execution | [§2.3](#23-closing-the-auction-without-an-auctioneer) | `src/amr.py:3806` |
| 18 | Battery status as a decision input | [§4](#4-battery-aware-bidding-requirement-18) | `src/amr.py:4114`, `src/amr.py:4061` |

Requirement 18 is claimed here rather than in [09. Dashboard](09-DASHBOARD.md) on
purpose: the dashboard readout is a *consequence* of battery being a live input to
allocation, and §4 is where that is established.

---

## 1. The four allocation policies

The policy names are constants in one module that deliberately contains no planning or
traffic code, so the allocation contract survives a change of motion policy
(`src/task_allocation.py:1`). The four values are declared at
`src/task_allocation.py:9-12`, and an unknown value is rejected rather than silently
defaulted (`src/task_allocation.py:21`).

| Policy | Mechanism | Central or decentralised | Allocation message cost | Appropriate when |
|---|---|---|---|---|
| `preassigned` | Static per-robot FIFO queue baked into the scenario; the robot pops the head when idle (`src/amr.py:3144`). Resolves to `None` internally (`src/main.py:65`). | Neither — no allocation happens at runtime | Zero allocation messages. `TASK_NEW`/`BID`/`AWARD` are never sent (`src/main.py:129`). | Route-policy comparisons. Every policy gets an identical workload, so a makespan difference is attributable to traffic handling and not to who got which job (`src/amr.py:232-235`). |
| `auction` | Peer contract-net. Every idle robot bids; every robot runs the same matching over the bids it heard; the self-winner accepts and broadcasts an `AWARD` (`src/amr.py:3480`). | Decentralised — no auctioneer exists | One `TASK_NEW` per task per 4 s from the injector (`src/settings.py:205`), plus up to 12 `BID`s per robot per 0.6 s round (`src/settings.py:185`, `src/settings.py:178`), plus one `AWARD` per win and one lease-renewal `AWARD` per refresh interval (`src/amr.py:5092`). | The honest decentralised baseline, and the frozen comparison point against which `auction_bundle` is measured. |
| `auction_bundle` | Everything `auction` does, plus: a *busy* BIOS 6 robot with an empty future slot may bid for exactly one additional task to execute after its current one (`src/amr.py:2885`, `src/amr.py:3574`). | Decentralised | `auction` plus one extra `BID` per busy robot per round and one extra lease `AWARD` per future reservation (`src/amr.py:5120`). Measured +31.68 % bids against `auction` in the paired release benchmark (`archive/BIOS6_AUCTION_V2_RELEASE.md:101`). | Bursty workloads on open layouts, where a robot about to finish near the next pickup can express that fact. Structurally disabled elsewhere — see [§3.4](#34-open-finding-auction_bundle-was-byte-identical-to-auction-on-showcase_chokepoint). |
| `hungarian` | Globally optimal min-cost assignment computed by the optional `FleetManager` over Manhattan robot→pickup cost, issued as directed `AWARD`s (`src/fleet_manager.py:126`). The solver is a dependency-free O(n³) primal-dual implementation (`src/assignment.py:35`). | **Centralised.** A `FleetManager` process is constructed for this policy even when the route policy is peer-to-peer (`src/main.py:166`). | One `TASK_NEW` per task per 4 s, zero `BID`s, one directed `AWARD` per assignment. Cheapest in messages, at the cost of a single point of failure. | The strong baseline. It answers the objection that decentralisation is being compared only against stop-and-wait; it quantifies what giving up global optimality costs. |

Two properties of this table matter under cross-examination.

**`hungarian` is centralised on purpose and is fenced off.** A robot accepts a directed
award only when it came from the manager id `FM0` *and* the robot's own allocation policy
is `None` or `hungarian` (`src/amr.py:5485-5492`); anything else increments
`rejected_directed_awards` and is dropped. The shared fleet pre-shared key authenticates
*membership*, not *role*, and the code says so at `src/amr.py:5486`. Conversely, the
manager's own task-assignment behaviour is gated by a single boolean set from the policy
(`src/fleet_manager.py:52`), so a manager present only for route advice never assigns a
task.

**The dashboard default is `auction_bundle`.** `POST /api/run` defaults
`allocation_policy` to `auction_bundle` (`backend/server.py:179`), `run_for_dashboard`
carries the same default in its signature (`src/main.py:569`), and the front-end
pre-selects it in the dropdown (`frontend/js/main.js:79`). This is the configuration a
judge sees unless they change it, which is why [§3.4](#34-open-finding-auction_bundle-was-byte-identical-to-auction-on-showcase_chokepoint)
is in this document rather than in a lab notebook.

---

## 2. The decentralised auction, in mechanism detail

This is the load-bearing mechanism for requirement 14 under requirement 6. There is no
auctioneer anywhere in the system. Below is every step, in execution order.

There are two implementations. `_run_auction` (`src/amr.py:3480`) is a serial,
one-task-at-a-time auction used by `decentralized`, `BIOS_1.0.0`, `BIOS_PIBT.1`,
`BIOS_PIBT.2` and `BIOS_4`. For the BIOS 3/5/6 family it immediately delegates to the
batch auction (`src/amr.py:3490-3492`), so **the dashboard's default policy
`BIOS_PIBT.6` never executes the serial path**. §2.1-§2.7 describe the batch auction
(`_run_v3_batch_auction`, `src/amr.py:3558`); §2.8 covers the serial one.

### 2.1 Who announces a task

A `WMS` pseudo-node injects `TASK_NEW` every 4 s for every task not yet known to be
terminal (`src/main.py:295-315`, period at `src/settings.py:205`). It is an *idempotent
catalog announcement, not a one-shot command* — the comment at `src/main.py:296-299`
states the reason: if every robot loses the first multicast, peer gossip has no copy to
repair from.

The injector is not a coordinator, and the code is explicit about the boundary
(`src/main.py:272-276`): it polls only `TASK_DONE` reports so it can stop repeating
completed jobs, and it validates the completion certificate before believing one
(`src/main.py:280-293`). It never evaluates a bid, chooses a winner, or sends an award.

Robots also re-announce. Each auction-enabled robot gossips one unfinished catalog entry
per period (`src/amr.py:5145-5187`), cycling through its open set with a cursor
(`src/amr.py:5175-5177`). On a healthy, fully fresh network this gossip goes quiet after
a recovery grace (`src/amr.py:5154-5168`); under loss, dead zones or a stale peer it
stays on. So the catalog survives the loss of the injector entirely.

### 2.2 Who bids, and what a bid is worth

Only a robot that owns no task and is idle may bid in the ordinary path
(`src/amr.py:3584-3587`). Tasks whose claim lease is still live are excluded from the
candidate set (`src/amr.py:3590-3595`).

Each candidate task is scored twice. First by **urgency**, a total order shared by every
participant (`src/amr.py:4073-4088`):

```
(deadline is None, deadline, -priority)
```

Hard deadlines therefore outrank soft business priority, and priority only breaks ties
*inside* a deadline class. The docstring at `src/amr.py:4076-4082` gives the reason:
reversing the two can starve a still-feasible low-priority deadline until every robot
must correctly reject it as impossible. No receiver-local arrival timestamp participates,
because edge nodes do not share a clock epoch.

Second by **bid cost** (`_v3_bid_cost`, `src/amr.py:4542`), which wraps the base cost
(`_bid_cost`, `src/amr.py:4043`). The base cost is not a straight-line distance — it is
the same A* model the robot navigates with:

- cells from the current pose to the pickup, plus cells from pickup to drop, both planned
  with the robot's own decaying contested-cell penalties (`src/amr.py:4046-4055`);
- plus accumulated learned edge delay for both legs, when BIOS 6 congestion experience is
  available (`src/amr.py:4056-4060`);
- plus a battery penalty of `max(0, 0.25 - battery_frac) * 20` cells
  (`src/amr.py:4061`);
- plus cargo-scaled handling time converted into equivalent cells, for the energy-auction
  policies (`src/amr.py:4063-4070`);
- and `1e9` — an effective refusal — when either leg has no path at all
  (`src/amr.py:4052-4053`).

`_v3_bid_cost` then adds a finite penalty of `20 + 4 × block_length` when the robot's
empty approach would consume a single-file block that the loaded trip also needs
(`src/amr.py:4557-4559`). Crossing a chokepoint empty and immediately coming back loaded
burns two scarce traversals against the admitted traffic phase. The penalty is finite, not
infinite, so the task remains serviceable if every surviving robot is on the wrong side
(`src/amr.py:4544-4548`).

The robot bids for its best `min(len(ranked), 12)` tasks
(`src/amr.py:3679-3682`; `auction_batch_bids` and `energy_bid_bundle` are both 12 at
`src/settings.py:185` and `src/settings.py:215`), records its own bid locally, and
broadcasts each as a `BID` message carrying task id, cost, auction epoch, task generation
and descriptor hash (`src/amr.py:3683-3693`, wire format at `src/messages.py:559`).

Under BIOS 6, a bid whose cost has not moved by more than a configured delta within the
refresh interval is suppressed rather than re-sent (`src/amr.py:689-700`), counted as
`bid_rebroadcasts_suppressed`.

### 2.3 Closing the auction without an auctioneer

Nobody declares the round closed. Each robot stamps its own round start
(`src/amr.py:3606-3607`) and refuses to evaluate until `auction_bid_window_s` (0.6 s,
`src/settings.py:178`) has elapsed on its own clock (`src/amr.py:3696-3697`).

It then assembles candidates from the bids it happens to hold, filtered hard
(`src/amr.py:3762-3789`):

- the bid's epoch must equal the task's current epoch (`src/amr.py:3765-3766`);
- the bid must be fresh — seen within the freshness window measured backwards from this
  robot's own round start (`src/amr.py:3756-3767`);
- for a peer's bid, that peer must still be fresh in the peer table
  (`src/amr.py:3771-3773`) **and** still advertise itself as idle with no goal
  (`src/amr.py:3774-3776`). A bid from a robot that has since taken work is discarded,
  not counted.

The last filter is what makes the result converge without an auctioneer: the heartbeat
stream continuously re-validates every bid in the pool, so a robot that won elsewhere
stops being a candidate everywhere within one stale window.

Matching is then deterministic and identical on every node. One winner per task is
selected first (`src/amr.py:3802-3807`), and the greedy pass takes candidates in the order
`(not an anchor task, urgency, cost, task id, robot id, epoch)` (`src/amr.py:3810-3815`),
skipping any robot or task already used (`src/amr.py:3816-3817`). Every field in that key
is derived from replicated message content; none of it is local state.

The design deliberately does **not** cascade a task to its second-best bidder when the
best bidder also won something else. The comment at `src/amr.py:3797-3801` explains why:
asynchronous peers holding slightly different bid sets would build different reassignment
chains from the same messages. Instead a robot takes at most one task it actually won, and
the rest return in the next 0.6 s round once that winner advertises itself busy.

### 2.4 How ties break

Two total orders do the work, and both are pure functions of replicated data.

Per task, the best bid is chosen by `(cost, robot_id)` (`src/amr.py:3806`), so an exact
cost tie is broken by lexicographic robot id — stable, and computable by every peer.

Between two competing *claims* for the same task, `_claim_wins`
(`src/amr.py:4668-4673`) compares `epoch` first (higher wins), then `(cost, owner_id)`
(lower wins). Lease expiry time is deliberately **not** part of that comparison, and the
comment at `src/amr.py:4584-4589` records the bug that forced the rule: a losing owner
whose self-renewed lease happened to outlive the winner's award kept working, and two
AMRs executed one task indefinitely after a partition healed.

### 2.5 Admission control inside the matching

Before a candidate pair is accepted, the matching applies physical constraints that every
peer computes identically:

- **Drop-cell capacity.** At most `auction_drop_capacity` (2, `src/settings.py:186`)
  in-flight tasks may target the same drop cell (`src/amr.py:3820-3821`). Once at least
  half the known workload is terminal, a bounded drain phase raises this to 3
  (`src/amr.py:4090-4103`). The trigger is workload state, not the scenario cutoff.
- **Corridor waves.** On maps with a bidirectional single-file block, tasks are admitted
  in directional waves derived from the pickup→drop A* path (`src/amr.py:4520-4540`), with
  at most `auction_corridor_capacity` (2, `src/settings.py:196`) live jobs per direction
  (`src/amr.py:3846-3848`) and no mixing of opposing phases (`src/amr.py:3841-3845`). Wave
  membership is re-derived every round from the canonical smallest unfinished task rather
  than cached, because under loss two peers can otherwise cache opposite waves forever
  (`src/amr.py:3716-3721`).
- **Empty approach.** A pair is rejected if reaching the pickup would send the robot
  through the block the loaded trip needs (`src/amr.py:3835-3840`).

### 2.6 Making the result consistent across peers

A win is not a fact; it is a **claim with a TTL**.

The self-winner records the claim locally, accepts the task, and broadcasts an `AWARD`
carrying epoch, cost, lease TTL, generation and descriptor hash
(`src/amr.py:3944-3966`; wire format `src/messages.py:578`). Lease length is
`auction_lease_s` = 20 s by default (`src/settings.py:179`), extended only by the owner
and only when its route physically crosses a mapped radio dead zone
(`src/amr.py:4617-4656`). The owner renews it on a timer while it holds the task
(`src/amr.py:5092-5118`), and a receiver re-bounds any incoming TTL before storing it
(`src/amr.py:5474-5482`, cap at `src/amr.py:4658-4666`).

Whether a peer's inferred win is recorded locally depends on link quality
(`src/amr.py:3866-3878`). On a strongly connected one-way map the replicated matching is
trusted and remote slots are recorded immediately for station backpressure. Under packet
loss or a dead zone it is **not**: the comment at `src/amr.py:3869-3877` states that a
locally inferred remote winner is a phantom reservation for a robot that may have picked a
different task in its own view. Only that robot's own `AWARD` establishes degraded-link
ownership. A self-win is always safe to claim.

That leaves one liveness hole, and it is closed explicitly. Independent auction windows
need not close on the same tick; if every robot's local view names *some other* robot, no
self-award ever exists and the fleet can idle forever. So under BIOS 6 on a non-circulation
map, a robot whose view has been stable for a full lease publishes its nominations
(`src/amr.py:3884-3925`). These are advisory: the nominee re-validates the epoch, cost,
owner, lease and its own battery feasibility against its *current* sensors before
accepting (`src/amr.py:3113-3143`), and drops the claim outright if it does not match
(`src/amr.py:3138-3143`).

Anti-entropy closes the remaining gaps. Task catalogs gossip (`src/amr.py:5145`),
completion certificates gossip (`src/amr.py:5189`), and reordered bids for a task this
robot has not yet heard of are parked in a bounded 128-entry / 2 s TTL cache
(`src/amr.py:2925-2954`) and admitted only once the task arrives and its generation and
descriptor hash match (`src/amr.py:2956-2980`). Implausible epoch jumps are refused
(`src/amr.py:2920-2923`).

### 2.7 When nobody bids or the winner vanishes

If a round produces no usable bids, or a lease expires, the task's epoch is incremented,
its bid window reopens, and all stale bid, claim, award and nomination state for it is
purged (`_restart_auction`, `src/amr.py:4675-4688`). The epoch is the mechanism that makes
a re-auction distinguishable from a replayed old one.

### 2.8 The serial auction (non-BIOS-3/5/6 policies)

`_run_auction` (`src/amr.py:3494-3556`) handles one task per round — the oldest unclaimed
one by `(announced_t, task_id)` (`src/amr.py:3507`). It opens the bid window on first
sight, broadcasts one bid, and returns (`src/amr.py:3511-3527`). On the tick after the
deadline it takes `min(bids)` by `(cost, robot_id)` (`src/amr.py:3541`), records the claim,
and if it is the winner accepts and broadcasts the award (`src/amr.py:3548-3556`). It is
simpler, sends fewer messages, and is strictly worse under load because it serialises.

---

## 3. `auction_bundle` — what bundling adds

### 3.1 The mechanism

`auction_bundle` is an **allocator**, not a motion policy (`src/amr.py:184-186`). It is
enabled only for `BIOS_PIBT.6` (`src/amr.py:2885-2888`) and adds exactly one capability:

```
ACTIVE  →  at most one FUTURE
```

A robot that is executing a task, is not idle or charging, has an empty future slot, has
no unresolved future bid, is past its retry cooldown and passes the network gate may
evaluate a single future candidate (`src/amr.py:3574-3583`). It never builds longer
bundles and never reorders the active task.

Candidate evaluation is a whole-sequence simulation, not a distance heuristic
(`src/amr.py:3610-3628`). For each open task it estimates the complete
`ACTIVE → FUTURE → charger` chain: remaining active work, active drop to future pickup,
future loaded leg, cargo-scaled handling, and the unladen trip to the nearest reachable
dock (`_future_sequence_estimate`, `src/amr.py:4204-4262`). Hard constraints run before
cost (`src/amr.py:4292-4313`): payload capacity, a valid route, the 15 % battery reserve
across the *whole* sequence, the active task's deadline, and the future task's deadline —
each with its own rejection counter.

The ranking cost is `equivalent_cells + 4·energy + 2·deadline_risk + 1·reserve_risk`
(`src/amr.py:4315-4334`, weights at `src/settings.py:292-294`).

The anti-greed rule is the important one. Before bidding, the busy robot computes what the
best currently-idle peer would bid for the same task (`_best_fresh_idle_cost`,
`src/amr.py:4336-4361`) and abandons its own bid if it is not at least 10 % better
(`src/amr.py:3618-3624`, threshold `bundle_reassignment_threshold` at
`src/settings.py:291`), incrementing `future_hysteresis_prevented`. A genuinely faster
idle robot therefore beats a speculating busy one.

Ownership is a lease like any other, but version-bound: the bid, award and reservation all
carry `(active_task_id, active_epoch, future_slot_generation)` (`src/amr.py:3640-3656`,
wire fields `future`/`active`/`ae`/`bv` at `src/messages.py:568-574`). A reservation whose
context does not match the robot's current state is refused as a version mismatch
(`src/amr.py:4370-4374`). The slot generation is incremented on every loss, release,
expiry or invalidation (`src/amr.py:4403`), so a delayed award for a superseded slot cannot
land.

Promotion is transactional. When the active task completes, `_promote_future`
(`src/amr.py:4421-4460`) re-checks network health, that the task is still open and not
completed, that the local claim is still the current epoch, still owned by this robot,
still unexpired, and that the energy estimate still holds — and releases the reservation
back to auction if any check fails (`src/amr.py:4441-4447`). A reservation is a lease, not
execution permission.

The reservation is also revalidated every second while held (`src/amr.py:3011-3024`) and
released if the task was cancelled or completed, or the sequence became infeasible.

### 3.2 Complexity

Per candidate task, `_future_sequence_estimate` runs up to `3 + len(docks)` A* searches
(`src/amr.py:4234-4247`), and the ranking loop runs it once per open task, then
`_future_bid_cost` runs it again for the survivors. This is why the evaluation is timed
and sampled into `allocation_compute_ms` (`src/amr.py:3629-3632`) with a bounded 2048-entry
ring, and why the release campaign gates allocation P95 at 25 ms
(`src/auction_v2_campaign.py:47`). Measured maximum in the campaign was 17.69 ms
(`archive/BIOS6_AUCTION_V2_RELEASE.md:77`). One measurement taken while writing this document
— `showcase_open_floor`, 4 robots, seed 0, 120 s, `BIOS_PIBT.6` — reported an allocation
P95 of **17.10 ms**, and the same run under plain `auction` reported **0.0 ms**, because
plain `auction` never enters the timed block at all. The bundle's compute cost is real and
is entirely in this one path.

### 3.3 What bundling buys, measured

The paired release benchmark on `showcase_open_floor` (5 robots, 3 tasks each, seeds 0-29)
reports `auction` at 25/30 complete runs and 438/450 tasks against `auction_bundle` at
30/30 and 450/450, with total messages down 19.69 % and bids up 31.68 %
(`archive/BIOS6_AUCTION_V2_RELEASE.md:92-103`).

A smaller reproduction run for this document, at the dashboard's own defaults
(`showcase_open_floor`, `BIOS_PIBT.6`, 4 robots, seed 0, duration 120 s):

| Allocation | Tasks | Makespan | Future bids | Promotions | Min separation |
|---|---:|---:|---:|---:|---:|
| `auction` | 7 / 8 | timed out at 120 s | 0 | 0 | 1.177 m |
| `auction_bundle` | 8 / 8 | 115.66 s | 6 | 4 | 1.198 m |

Both are collision-free. The bundle finished the workload inside the window; plain
`auction` did not. **The plumbing works and it is doing something real.**

### 3.4 OPEN FINDING: `auction_bundle` was byte-identical to `auction` on `showcase_chokepoint`

**The observation.** On `showcase_chokepoint` with server defaults — 4 robots, seed 0,
120 s, `BIOS_PIBT.6` — `auction` and `auction_bundle` produced identical results:

| Allocation | Tasks | Makespan | Min separation | Bids sent | Robot/robot contacts |
|---|---:|---:|---:|---:|---:|
| `auction` | 3 / 8 | timed out | 1.309 m | 2058 | 0 |
| `auction_bundle` | 3 / 8 | timed out | 1.309 m | 2058 | 0 |
| `preassigned` | 0 / 0 | timed out | 9.899 m | 0 | 0 |
| `hungarian` | 0 / 8 | timed out | 1.799 m | 0 | 0 |

Not merely similar — identical to the last digit on every field. `preassigned` and
`hungarian` separated cleanly, so the allocation switch is genuinely reaching the runner;
the two auction variants were indistinguishable.

**The diagnosis, and where the working hypothesis was wrong.** The initial hypothesis was
that `POST /api/run` truncates the scenario: it defaults to 120 s, 4 robots and seed 0
regardless of scenario (`backend/server.py:173-197`), and `run_for_dashboard` overwrites
the scenario's own duration with the request value (`src/main.py:661-662`). That
truncation is real — `showcase_chokepoint` is authored for 420 s and seed 7
(`src/scenarios.py:740`, duration set at `src/scenarios.py:383`), and its task deadlines
are anchored at 420 s (`src/scenarios.py:694`), so under a 120 s run no deadline ever
binds and the late-wave drain phase is never reached.

But truncation is **not the cause**. The actual cause is a documented, deliberate topology
gate:

```python
# src/amr.py:2900
if (self.blocks.members or self.cfg.net.loss > 0.0
        or self.cfg.net.dead_zones):
    self._future_network_candidate_since = None
    return False
```

`_future_network_healthy` returns `False` on any map that has a controlled single-file
block, which disables every future-allocation entry point:
`_run_v3_batch_auction`'s busy-future branch (`src/amr.py:3582`), the revalidation loop
(`src/amr.py:3007`), and the mid-task bundle round (`src/amr.py:3155`). Verified directly:
`corridors(chokepoint_warehouse(13))` returns 1 block of 13 cells, while
`open_floor(22, 15)` returns 0. On `showcase_chokepoint`, `auction_bundle` therefore
*reduces exactly to `auction`* — every code path that differs is switched off. Identical
output is the correct output.

The existing documentation already predicts this. `archive/BIOS6_EXPERIMENTAL_BOUNDED_FUTURE_AUCTION.md:96-97`
lists "controlled single-file blocks" among the conditions that disable new future
planning, and lines 189-192 report that chokepoint, human/dense-aisle and dead-zone
scenarios produce zero future bids and reproduce untouched BIOS 6 exactly — "intentional
fallback, not an allocator speedup." The finding is therefore a **documentation and
dashboard-UX defect, not a code defect**.

**Why it still matters for the demo.** `auction_bundle` is the dashboard default
(`backend/server.py:179`, `frontend/js/main.js:79`), and `Chokepoint` is the marquee
showcase scenario (`src/scenarios.py:801`). A judge who picks the headline scenario and
leaves the default allocator sees the bundle allocator's name on screen while the bundle
allocator is switched off, with nothing in the UI saying so.

**Open questions this leaves.**

1. Should the dashboard surface `future_bids_sent == 0` as an explicit "bundle inactive on
   this topology" badge rather than leaving the label to imply otherwise? Not implemented.
2. Should `/api/run` default `duration` and `seed` to the scenario's authored values
   instead of 120 s and 0? Currently a showcase authored for 420 s and seed 7 is silently
   run at 120 s and seed 0. Not implemented.
3. Is the corridor gate still justified? It was set from evidence — trials in chokepoints
   were "neutral-to-worse"
   (`archive/BIOS6_EXPERIMENTAL_BOUNDED_FUTURE_AUCTION.md:99-101`) — but that evidence
   predates the corridor-wave admission control in `src/amr.py:3699-3753`. Not re-measured.

---

## 4. Battery-aware bidding (requirement 18)

Battery is a hard admission constraint on allocation, not a dashboard decoration. Four
distinct mechanisms consume it.

**Battery on the wire.** Every heartbeat carries the sender's battery fraction as field
`b` (`src/messages.py:462`), and every receiver stores it on its peer record
(`src/amr.py:5283`, field declared at `src/amr.py:143`). Peer battery is therefore an input
to *other robots'* decisions, not only to the owner's.

**Bid cost.** `_bid_cost` adds `max(0, 0.25 - battery_frac) * 20` equivalent cells
(`src/amr.py:4061`). Below 25 % state of charge a robot's bids get progressively worse and
it loses auctions to better-charged peers without any explicit rule.

**Feasibility gate — energy bid suppression.** `_energy_feasible`
(`src/amr.py:4114-4136`) is the hard gate. It rejects a task outright when cargo weight
exceeds the 100 kg identical-model payload capacity (`src/amr.py:4123-4125`,
`src/settings.py:44`), when there is no path, when the projected battery reserve *after*
completing the task and reaching the nearest dock falls under `energy_reserve_frac` = 15 %
(`src/amr.py:4132-4133`, `src/settings.py:208`), or when the predicted completion time
would miss a hard deadline (`src/amr.py:4134-4135`).

The energy model behind it (`_task_estimate`, `src/amr.py:4144-4202`) is explicit: approach
leg plus a 1.35× multiplier on the loaded leg, times a cargo-type factor and a weight
factor of `1 + 0.35 × weight/100 kg`, plus an unladen post-drop charger leg, plus idle draw
across a cargo-scaled 12 s handling allowance, plus a 10 % uncertainty margin
(`src/amr.py:4190-4198`; constants at `src/settings.py:208-224`). A failed gate increments
`energy_bids_suppressed` and the robot stays silent for that task
(`src/amr.py:3662-3668`; serial path at `src/amr.py:3514-3517`).

The docstring at `src/amr.py:4119-4121` and `archive/BIOS_PIBT_5_ENERGY_AUCTION.md:26-28`
both state the design reason for not using a fixed `battery > 80%` rule: the same task
costs different energy for robots in different places.

**Candidate reduction.** Even among feasible robots, only the three nearest fresh, idle,
sufficiently charged peers bid (`_energy_candidate`, `src/amr.py:4490-4518`,
`energy_candidate_bids` = 3 at `src/settings.py:212`). Membership is live rather than
age-expanded: busy, charging, failed and stale peers leave the set, admitting the next
robot automatically (`src/amr.py:4514-4516`). Missing peer state *widens* participation
rather than suppressing work (`src/amr.py:4493-4494`). If no task is feasible at all, the
robot backs off for at least 5 s and counts `energy_no_eligible_rounds`
(`src/amr.py:3673-3678`).

**Charge-dock behaviour.** An idle robot below `energy_charge_trigger_frac` = 15 %
(`src/settings.py:216`) sets a dock as its goal and enters `charging`
(`src/amr.py:2984-2994`), and rejoins the auction only above `energy_rejoin_frac` = 45 %
(`src/amr.py:2996-3002`, `src/settings.py:217`). BIOS 6 does not simply take the nearest
dock: `_v6_select_charger` (`src/amr.py:826-864`) scores each reachable dock by route
length plus penalties for peers already charging there and peers intending to, and counts
`charger_contentions_avoided` when it picks a farther but less contended one. No robot owns
a global charger schedule; stale peer information simply disappears
(`src/settings.py:278`).

**A precise limitation, stated rather than hidden.** The charge trigger is evaluated only
when `self.task is None` (`src/amr.py:2986`). **A robot that already holds a task will not
abandon it for low battery.** Energy safety is a pre-commitment gate, not a mid-task abort.
The design intent is coherent — `_energy_feasible` already includes the return-to-dock
reserve before the commitment is made — but a robot whose energy estimate turns out
optimistic mid-task has no in-flight escape hatch. See
[15. Limitations](15-LIMITATIONS.md).

---

## 5. Task re-assignment (requirement 14)

A task returning to the pool and being won by another robot is a *lease* mechanism, not a
handover protocol. There is no "release" message in the wire protocol. Ownership simply
stops being renewed and the task becomes claimable again.

### 5.1 The four triggers

| Trigger | Condition | Code | Effect |
|---|---|---|---|
| **Lease expiry** (owner crashed, radio lost, robot failed) | `claim.lease_until <= t` | `src/amr.py:4690-4692` | Claim removed, task epoch incremented, auction reopened (`src/amr.py:4723-4729`). Counted as `task_reassignments` when the lost owner was a peer (`src/amr.py:4727-4728`). |
| **Superseded by a better claim** | An incoming claim wins `_claim_wins` against the local one and this robot was the old owner | `src/amr.py:4590-4592` | `_drop_current_task()` — the robot stops mid-task, clears goal, path and state, and returns to idle (`src/amr.py:4731-4741`). Any held future reservation is invalidated too (`src/amr.py:4593-4600`). |
| **Peer completed the same job** | A valid completion certificate arrives whose `(task, generation, descriptor_hash)` matches the active task | `src/amr.py:515-521` | Active task cancelled and `_needs_duplicate_vacate` set, so the cancelled duplicate leaves the lane before bidding again (`src/amr.py:3027-3037`). |
| **Future reservation released** | Revalidation, promotion or feasibility check fails | `src/amr.py:4392-4419` | The robot re-announces the task itself with a fresh epoch (`src/amr.py:4406-4416`) — a peer, not the WMS, restarts the auction. |

The exact re-assignment threshold is therefore **temporal, not spatial**: default
`auction_lease_s` = 20 s (`src/settings.py:179`), renewed by the owner on a timer while it
works (`src/amr.py:5092-5118`).

One refinement guards against premature re-auction. Under BIOS 6, if a fresh authenticated
heartbeat from the claim's owner still names the same active task and the owner is not
idle or charging, the lease is extended instead of expired (`src/amr.py:4694-4710`). The
comment is explicit that this is *evidence of liveness*, not a source of ownership: a
heartbeat can never create a claim, only extend an existing one. Once the owner is
genuinely gone the evidence goes stale and bounded expiry resumes.

### 5.2 The messages exchanged

For a crashed owner, in order:

1. The owner stops sending. Its `AWARD` lease renewals cease
   (`src/amr.py:5092-5118`), and its `HEARTBEAT` stops
   (`src/amr.py:624-626`).
2. Every peer ages it out of the peer table (`src/amr.py:5671-5681`).
3. On the first tick after `lease_until`, each surviving robot independently expires the
   claim and calls `_restart_auction`, bumping the epoch (`src/amr.py:4675-4678`).
4. The next `TASK_NEW` — from the injector every 4 s (`src/main.py:295`) or from peer
   catalog gossip (`src/amr.py:5145`) — or simply the already-open local record makes the
   task available again (`src/amr.py:3590-3595`).
5. Idle robots bid `BID` at the new epoch (`src/amr.py:3689-3693`). Bids at the old epoch
   are refused (`src/amr.py:5452-5453`).
6. The new self-winner accepts and broadcasts `AWARD` (`src/amr.py:3944-3966`).

No message says "release", "reassign" or "takeover". Requirement 14 is satisfied by
absence of renewal plus a monotonic epoch.

### 5.3 The demonstrating scenario

`robot_failure_reassignment` (`src/scenarios.py:520-540`) is purpose-built: three robots on
a 16×10 open floor, one task, and `robot_fail_at={"AMR01": 2.0}`. The comment at
`src/scenarios.py:537` records that AMR01 is deliberately placed closest to `T000` so that
it *wins* the first auction and is then killed holding it. The scenario carries its work in
`unassigned` and leaves `assignments` empty (`src/scenarios.py:534-536`), so it exercises
the auction path only.

It is covered by a release test asserting exactly one robot failure, at least one
reassignment, full completion and zero robot/robot contacts
(`tests/test_resilience.py:27-38`).

Measured for this document — `robot_failure_reassignment`, seed 0, `BIOS_PIBT.6`:

| Allocation | Tasks | Makespan | `task_reassignments` | Robot/robot contacts | Min separation |
|---|---:|---:|---:|---:|---:|
| `auction` | 1 / 1 | 47.06 s | 2 | 0 | 1.282 m |
| `auction_bundle` | 1 / 1 | 47.06 s | 2 | 0 | 1.282 m |

Status: **implemented and tested**. (The 47.06 s figure is under `BIOS_PIBT.6`;
`archive/BIOS_PIBT_5_ENERGY_AUCTION.md:95` quotes 46.82 s for the same scenario under
`BIOS_PIBT.5`. Both are correct for their policy.)

---

## 6. Blocked aisle → re-route → re-assign (requirements 12, 13, 14)

This is the chain the problem statement names. It has three stages and two decision
points, and the honest answer to "when does it re-route versus give the task up" is that
**those are independent mechanisms on different timescales**, not two branches of one
test.

### 6.1 Step 1 — detect the blockage

Lidar returns are anonymous. `_observe_dynamic_obstacles`
(`src/amr.py:2774-2876`) turns a persistent, stationary, unidentified return into map
state:

- returns moving faster than 0.08 m/s are handled by the prediction path instead
  (`src/amr.py:2797`);
- a return within 0.35 m of a fresh peer pose is a robot, not a pallet, and is discarded
  (`src/amr.py:2794-2796`, `src/amr.py:2845-2848`);
- an unmatched stationary return must be seen at least 3 times across at least 0.3 s before
  it becomes map state (`src/amr.py:2852-2860`) — one heartbeat interval, so a peer has a
  chance to identify itself first;
- it is then recorded as blocked for 2.0 s and counted as `dynamic_obstacles_detected`
  (`src/amr.py:2861-2863`).

Two properties matter for the defence. The layer expires — cells are cleared as soon as
their TTL passes (`src/amr.py:2776-2778`) — so a temporarily blocked aisle does not become
permanently unusable. And it is **never learned from peer messages**: the comment at
`src/amr.py:307-310` states that a total radio failure cannot erase the physical
blocked-aisle response, because this layer comes from the robot's own sensor.

### 6.2 Step 2 — re-route

If a newly blocked cell lies on the robot's remaining path and is not its goal, it sets
`route_blocked` (`src/amr.py:2864-2865`) and replans immediately, counting
`dynamic_reroutes` only if the path actually changed (`src/amr.py:2867-2871`).

`_replan` (`src/amr.py:2693-2772`) passes the live blocked set to A* — with the current
cell and the goal excluded, so a robot standing on a flagged cell or delivering to one is
never made unroutable (`src/amr.py:2698-2701`). It also layers in decaying contested-cell
penalties, learned edge delays and short-horizon predictions
(`src/amr.py:2702-2712`).

The key design decision is at `src/amr.py:294`: contested cells become *expensive, never
impassable* — "marking them impassable is how a jam turns into an unsolvable map." Only
the sensed-obstacle layer is a hard block, and it expires in 2 s.

If A* returns nothing — the blockage has no detour — the robot keeps its goal and an empty
path. It does not give up. The route loop calls `_replan` again on every route tick
(`src/amr.py:2684-2685`), and after `livelock_progress_s` = 12 s without progress it clears
its learned penalties entirely and replans from scratch (`src/amr.py:2686-2691`).

### 6.3 Step 3 — re-assign, if and only if the lease runs out

**There is no code path from "blocked" to "abandon the task".** Searching the task loop
confirms it: the only calls to `_drop_current_task` are lease expiry
(`src/amr.py:4711-4713`), a superseding claim (`src/amr.py:4590-4592`) and a peer's
completion certificate (`src/amr.py:519-520`). A blockage never abandons a task directly.

The chain therefore reads: a robot re-routes as often as necessary for free, and only if it
is stuck long enough that it stops renewing its 20 s lease — or physically fails — does the
task return to the pool and get won by someone else. The threshold is
`auction_lease_s` = 20 s (`src/settings.py:179`), with the BIOS 6 heartbeat-evidence
extension of §5.1 protecting a robot that is stuck but demonstrably alive and still working
(`src/amr.py:4694-4710`).

That last clause is worth stating plainly to a judge, because it cuts both ways: a robot
that is alive, heartbeating and still nominally holding a task it cannot finish will keep
its lease renewed under BIOS 6 and will **not** be preempted. Re-assignment triggers on
*node failure and radio loss*, not on *slow progress*. A slow-progress preemption rule is
**not implemented**.

### 6.4 The demonstrating scenario

`blocked_aisle` (`src/scenarios.py:498-517`) drops a pallet at `(5, y)` on the middle
robot's straight-line route one second into the run, on a 14-cell-wide open floor where an
alternate route exists (`src/scenarios.py:512-513`). It runs in `preassigned` mode — its
work lives in `assignments`, not `unassigned` — which is deliberate: it isolates re-routing
from allocation.

Covered by `tests/test_resilience.py:13-24`, which asserts full completion, at least one
detection, at least one reroute, and zero robot/robot and robot/rack contacts.

Measured for this document, seed 0, `BIOS_PIBT.6`:

| Allocation | Tasks | Makespan | Obstacles detected | Reroutes | Contacts | Min separation |
|---|---:|---:|---:|---:|---:|---:|
| `preassigned` | 3 / 3 | 23.98 s | 3 | 1 | 0 | 1.401 m |
| `auction` | 3 / 3 | 24.40 s | 3 | 1 | 0 | 1.400 m |

Status: **implemented and tested** for detection and re-route (requirements 12, 13);
**implemented and tested** for failure-driven re-assignment (requirement 14, §5.3);
**not implemented** for blockage-driven task abandonment, which is a deliberate design
choice rather than a gap.

---

## 7. Task protocol integrity

When there is no central authority, "this task is finished" is a claim made by one robot
to peers who cannot check it against a book of record. `src/task_protocol.py` supplies the
binding that lets a peer check it against arithmetic instead.

### 7.1 Two separate identities

The module's opening comment (`src/task_protocol.py:3-6`) draws the distinction the rest of
the system depends on:

- **`auction_epoch`** is a transient allocation attempt. It increments on every re-auction
  (`src/amr.py:4676-4677`).
- **`generation`** identifies the warehouse job itself. Only a genuinely new WMS job may
  advance it (`src/amr.py:119-121`).

Keeping them separate is what lets a valid completion terminate later re-auctions of the
same job, without letting an old completion suppress a genuinely new job at the same task
id.

### 7.2 The descriptor hash

`task_descriptor_hash` (`src/task_protocol.py:44-71`) is a SHA-256 over a canonical JSON
encoding — sorted keys, no whitespace, `allow_nan=False`, floats rounded to 6 places
(`src/task_protocol.py:30-37`) — of exactly the immutable WMS fields: version, task id,
generation, pick, drop, cargo type, cargo weight, priority, and the workload-relative
deadline.

The exclusion is as important as the inclusion. The docstring at
`src/task_protocol.py:56-58` states that runtime messages carry a *decreasing TTL* but that
TTL is deliberately not part of task identity — because independent edge nodes have
unrelated monotonic clock epochs and a receiver-local timestamp would make the same job
hash differently on every node.

**What this buys.** Two robots that disagree about a task's pickup, drop, cargo or priority
produce different hashes and can detect the disagreement without a referee. `TASK_NEW`
carries the hash (`src/messages.py:489`) and a conflicting descriptor at the same
generation is rejected with `rejected_task_conflicts` (`src/amr.py:5369-5376`) — unless it
comes from the `WMS`, which is allowed exactly one correction to a provisionally
peer-gossiped descriptor (`src/amr.py:5418-5430`). `BID` and `AWARD` also carry the hash
(`src/messages.py:566-567`, `src/messages.py:587-588`), and a bid or award whose hash does
not match the local descriptor is dropped (`src/amr.py:5444-5448`,
`src/amr.py:5498-5502`). A stale message about a superseded version of a task cannot
influence the current auction.

### 7.3 The completion certificate

`CompletionCertificate` (`src/task_protocol.py:92-134`) binds task id, generation,
descriptor hash, owner, auction epoch, completion time, result and a derived nonce, plus an
`ownership_proof_hash` domain-separated under `"BIOS-OWNERSHIP-v1"`
(`src/task_protocol.py:74-89`).

Validation (`src/task_protocol.py:136-186`) parses the wire mapping, rejects malformed
fields, then **recomputes** both the ownership proof and the nonce and compares. A
certificate whose owner, epoch, generation or descriptor was altered no longer matches its
own proof and is refused. Combined with the receiver-side checks at
`src/amr.py:5560-5590` — the certificate must name the current logical task identity, and
must come either directly from its owner or as a relay where both the relayer and the owner
are known fleet members — this is what lets a robot accept "task T is finished" from a peer.

Accepting one installs a tombstone (`src/amr.py:500-543`): the certificate is stored under
its `(task, generation, descriptor)` key, a per-task high-water generation is raised, the
active or future task is cancelled if it matches, and *all* transient auction state for
that task — open record, claim, bids, bid timestamps, awards, nominations — is purged in
one place.

**Resurrection is the attack this stops.** A delayed or replayed `TASK_NEW` for a
generation at or below a verified terminal generation is refused and counted as
`task_resurrections_suppressed` (`src/amr.py:5320-5328`). Without the generation/descriptor
binding, an old announcement re-entering the fleet after a partition heal would restart an
auction for work that is already done.

### 7.4 Surviving a restart

`TerminalJournal` (`src/terminal_journal.py:49-153`) persists only the newest verified
terminal generation per task id. It writes an fsync-and-rename snapshot
(`src/terminal_journal.py:132-144`), guards the file with a SHA-256 checksum over the
canonical record list (`src/terminal_journal.py:109-110`), and re-validates every record as
a certificate on load (`src/terminal_journal.py:68-87`). A corrupt, truncated, oversized or
tampered journal raises rather than returning partial data
(`src/terminal_journal.py:96-110`) — it **fails closed**, so an edge node never executes a
task whose terminal status is unknown.

In the simulation, a restarted robot is reconstructed with the terminal records exported
from its predecessor (`src/main.py:243-249`, restore at `src/amr.py:451-470`).

### 7.5 What this does *not* guarantee

Stated plainly because a BEL evaluator will ask. These are deterministic
*application-level* bindings carried over authenticated transport; they are **not**
per-device signatures. Fleet membership is established by a shared pre-shared key, which
authenticates membership and not identity (`src/task_protocol.py:8-12`,
`archive/BIOS6_AUCTION_V2_RELEASE.md:39-42`). A malicious robot that already holds the fleet
PSK can forge a valid-looking certificate for any owner, and is explicitly outside the
prototype's security claim. The objects are structured to be signed by a per-device key in
a later phase (`src/task_protocol.py:10-12`). Status: **implemented and tested** for
integrity against loss, reordering and replay; **not implemented** for Byzantine fleet
members.

---

## 8. The `assignments` + `unassigned` double-listing

A `Scenario` carries both a per-robot queue list `assignments` and a flat `unassigned` list
(`src/scenarios.py:86`). For custom scenarios built from the dashboard's map editor, every
generated task is placed in **both**: round-robin into `assignments`
(`src/main.py:622-625`) and wholesale into `unassigned` (`src/main.py:627`). This reads
like double-counting. It is not, and the guard that makes it correct is worth reading
before anyone "fixes" it.

**Exactly one of the two lists is ever used per run.** `_announced_tasks`
(`src/main.py:70-82`) is the single arbiter:

```python
# src/main.py:78
if allocation_policy in ACTIVE_ALLOCATION_POLICIES:
    if sc.unassigned:
        return list(sc.unassigned)
    return [task for queue in sc.assignments for task in queue]
return []
```

- For `auction`, `auction_bundle` or `hungarian`, it returns `unassigned` when non-empty,
  and otherwise flattens `assignments` so an old scenario that predates `unassigned` still
  works.
- For `preassigned` — which `_resolve_allocation_policy` has already normalised to `None`
  (`src/main.py:65-66`) — it returns an empty list.

Its result then sets one boolean, `uses_allocation` (`src/main.py:129`), and **both**
queue-seeding sites are guarded by it:

- initial seeding: `b.queue = [] if uses_allocation else sc.assignments[i]`
  (`src/main.py:155-156`);
- restart seeding after a robot failure: `if not uses_allocation: brains[rid].queue = ...`
  (`src/main.py:253-254`), with the comment explaining that an auction node reconstructs
  its catalog from peer gossip while pre-assigned work has no distributed owner record.

`uses_allocation` also gates the WMS injector (`src/main.py:295`) and the task total
(`src/main.py:175`). So an auction run announces each task exactly once and no robot holds
a private queue; a `preassigned` run announces nothing and every robot holds its own queue.
`assignments` serves the route-only comparison modes; `unassigned` serves the auction
modes. **Never both.**

This is covered by test: on `dense_aisles` at 2 robots, `auction` and `hungarian` both
report exactly 8 announced tasks, with `BID` traffic present under `auction` and absent
under `hungarian` (`tests/test_core.py:491-513`).

### 8.1 REAL BUG: `preassigned` is vacuous on every `showcase_*` scenario

The invariant above holds for custom and classic scenarios. It does **not** hold for the
showcase family, and the consequence is a live defect.

`_showcase_profile` moves all work into `unassigned` and then **clears** `assignments`
(`src/scenarios.py:717-718`):

```python
sc.unassigned = profiled
sc.assignments = [[] for _ in range(sc.n_robots)]
```

For `preassigned`, `_announced_tasks` returns `[]`, `uses_allocation` is `False`, and every
robot is seeded from an empty `assignments[i]`. The run has **no tasks at all**. Verified:
`showcase_chokepoint(seed=0, n_robots=4)` reports `len(unassigned) == 8`,
`assignments == [0, 0, 0, 0]` and `n_tasks == 0`; the corresponding run in §3.4 reports
`tasks_announced = 0`, `tasks_completed = 0`, `auction_bids_sent = 0` and a min separation
of 9.899 m — four robots that never moved.

The failure mode is the dangerous kind: the run *succeeds*, reports zero collisions, and
looks like a clean baseline. A judge comparing `preassigned` against `auction_bundle` on a
showcase scenario would be comparing a real run against an empty one. The comparison in
§3.4 is only trustworthy because both auction rows announce 8 tasks.

Suggested fix, **not implemented**: have `_showcase_profile` populate `assignments` with a
round-robin of `profiled` instead of clearing it — matching what `run_for_dashboard`
already does for custom scenarios at `src/main.py:622-625` — or have the dashboard refuse
`preassigned` on showcase scenarios. Either preserves the double-listing invariant that
`_announced_tasks` was written against.

---

## 9. Status summary

| Capability | Status | Primary evidence |
|---|---|---|
| Four selectable allocation policies | Implemented and tested | `src/task_allocation.py:9`, `tests/test_core.py:491` |
| Decentralised auction with no auctioneer | Implemented and tested | `src/amr.py:3558`, `tests/test_core.py:478` |
| Deterministic tie-breaking and total order | Implemented and tested | `src/amr.py:3806`, `src/amr.py:4668` |
| Expiring leases and epoch-based re-auction | Implemented and tested | `src/amr.py:4690`, `tests/test_core.py:451` |
| `auction_bundle` one-step future reservation | Implemented and tested | `src/amr.py:3574`, `tests/test_auction_bundle.py:54` |
| Battery as a hard bid-admission constraint | Implemented and tested | `src/amr.py:4114`, `tests/test_auction_bundle.py:107` |
| Charge-dock selection under contention | Implemented | `src/amr.py:826` |
| Task re-assignment after owner failure | Implemented and tested | `src/amr.py:4690`, `tests/test_resilience.py:27` |
| Blocked-aisle detection and re-route | Implemented and tested | `src/amr.py:2849`, `tests/test_resilience.py:13` |
| Descriptor hashes and completion certificates | Implemented and tested | `src/task_protocol.py:44`, `tests/test_task_protocol.py` |
| Terminal journal survives restart | Implemented and tested | `src/terminal_journal.py:49`, `tests/test_terminal_journal.py` |
| Mid-task abort on low battery | **Not implemented** | `src/amr.py:2986` (`self.task is None` guard) |
| Slow-progress preemption of a live owner | **Not implemented** | `src/amr.py:4694` extends the lease of a live owner |
| Per-device signed ownership proofs | **Not implemented** | `src/task_protocol.py:8-12` |
| `preassigned` on showcase scenarios | **Broken** — announces zero tasks | `src/scenarios.py:718` |

Everything above is **simulation evidence**. The identical `AMRBrain` runs on real UDP
sockets (`src/amr.py:32-37`), and hardware execution is covered in
[08. Edge Deployment](08-EDGE-DEPLOYMENT.md), but no allocation result in this document
was measured on physical robots.

---

## 10. Contradictions with the existing BIOS documents

Recorded rather than smoothed over, because a judge may read those files too.

1. **`auction_bundle` default status.** `archive/BIOS6_EXPERIMENTAL_BOUNDED_FUTURE_AUCTION.md:3`
   still reads "Status: **experimental; not a BIOS 6 default**", and its verdict at lines
   213-218 says "do not make it the default" and "do not merge it into the BIOS 6 release
   yet". It **is** now the default in three places (`backend/server.py:179`,
   `src/main.py:569`, `frontend/js/main.js:79`), and `archive/BIOS6_AUCTION_V2_RELEASE.md:5`
   declares the release condition met. The release document supersedes the experimental
   one; the experimental one is not marked superseded. Anyone quoting it will contradict
   the running system.

2. **Task ordering rule.** `archive/BIOS_PIBT_5_ENERGY_AUCTION.md:39` and `:64` state
   "Declared priority orders tasks first, then earliest hard deadline". The current code
   does the opposite: `_task_urgency` (`src/amr.py:4084-4088`) orders by deadline class,
   then deadline, then priority. `archive/BIOS6_AUCTION_V2_RELEASE.md:16-17` records the
   change explicitly ("Hard feasibility deadlines now outrank soft business priority"), so
   the BIOS 5 document is stale rather than wrong-at-the-time. This document follows the
   code.

3. **Default policy.** `archive/BIOS_PIBT_5_ENERGY_AUCTION.md:5` states "`BIOS_PIBT.5` is the
   default software policy". The server default is `BIOS_PIBT.6`
   (`backend/server.py:174`).

4. **Chokepoint fallback is documented but not surfaced.** The zero-future-bid fallback in
   §3.4 is correctly predicted by
   `archive/BIOS6_EXPERIMENTAL_BOUNDED_FUTURE_AUCTION.md:96-97` and `:189-192`. Nothing in the
   dashboard or the run summary tells the operator it is in effect, which is what turned a
   documented behaviour into an open finding during this review.

---

## See also

- [01. Requirements Traceability](01-REQUIREMENTS-TRACEABILITY.md) — the full 20-requirement matrix
- [03. Decentralized Protocol](03-DECENTRALIZED-PROTOCOL.md) — `TASK_NEW`/`BID`/`AWARD`/`TASK_DONE` on the wire
- [04. Path Planning](04-PATH-PLANNING.md) — the A* used by `_bid_cost` and `_replan`
- [05. Coordination Policies](05-COORDINATION-POLICIES.md) — which route policy pairs with which allocator
- [07. Safety](07-SAFETY.md) — Layer 0, which no allocation decision can override
- [09. Dashboard](09-DASHBOARD.md) — where battery and auction events are rendered
- [10. API Reference](10-API-REFERENCE.md) — `POST /api/run` and its defaults
- [11. Scenarios](11-SCENARIOS.md) — `blocked_aisle`, `robot_failure_reassignment`, `showcase_chokepoint`
- [12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md) — the Auction V2 acceptance campaign
- [13. Testing](13-TESTING.md) — `tests/test_auction_bundle.py`, `tests/test_resilience.py`
- [14. Findings](14-FINDINGS.md) — cross-cutting findings
- [15. Limitations](15-LIMITATIONS.md) — what is not implemented
- [16. Demo Runbook](16-DEMO-RUNBOOK.md) — which scenario to select to show re-assignment live
