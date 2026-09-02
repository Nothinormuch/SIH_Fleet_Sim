# BIOS 6 Experimental Bounded Future Allocation

Status: **experimental; not a BIOS 6 default and not BIOS 7**

Branch: `auctionv2`

Untouched comparison point: `a7753c6a2e4c72c665c7abaceb6a0e4d55bf270d`

## Why this exists

The normal decentralized `auction` policy permits bids only from idle robots. That is
safe and simple, but it can be myopic when many related tasks arrive together: a robot
that is about to finish near the next pickup cannot express that fact.

The experimental `auction_bundle` policy keeps the executing task fixed and permits at
most one additional reservation:

```text
ACTIVE -> at most one FUTURE
```

It never constructs longer bundles and never reorders `ACTIVE`.

## Decision flow

```text
Task announced by WMS
        |
        v
Robot idle? ---- yes ----> normal BIOS 6 auction bid
        |
        no
        v
auction_bundle + BIOS_PIBT.6 + open topology + healthy/stable network?
        |
        no ----------------> no future bid; ordinary BIOS 6 continues
        |
        yes
        v
ACTIVE valid + future slot empty + no unresolved future bid?
        |
        no ----------------> no future bid
        |
        yes
        v
Evaluate every local FUTURE candidate as ACTIVE -> FUTURE -> charger
        |
        v
Payload, route, whole-sequence energy/reserve and hard deadlines valid?
        |
        no ----------------> reject candidate
        |
        yes
        v
Compare predicted completion cost with fresh idle-robot cost
        |
        v
Send exactly one bundle-version-bound bid for the best candidate
        |
        v
Bounded lease won? ---> reserve FUTURE, renew and revalidate
        |
        v
ACTIVE completes ---> transactionally validate owner/epoch/lease/task/energy/
                      deadline/route/charger, then promote FUTURE or release it
```

## Cost and feasibility

Hard constraints run before cost. A candidate must preserve payload capacity, a valid
route, the existing BIOS battery reserve, both hard deadlines, and a reachable charger
after the complete `ACTIVE -> FUTURE` sequence.

The ranking cost uses predicted completion burden, not pickup distance alone:

```text
time burden
+ configured energy weight * whole-sequence energy
+ configured deadline-risk weight * low-slack penalty
+ configured reserve-risk weight * low-reserve penalty
```

The time burden includes remaining active work, active drop to future pickup, future
handling, and future delivery. A genuinely faster idle robot therefore prevents a busy
robot's speculative bid. A 2 s retry cooldown prevents repeated no-benefit bidding.

## Ownership and recovery

- Maximum local capacity is two tasks total: one `ACTIVE`, one `FUTURE`.
- Maximum unresolved future bid is one.
- Future bids and awards bind task epoch, active task ID/epoch, and future-slot generation.
- Future ownership uses a bounded, renewable lease.
- Lease expiry, a newer owner, invalid energy/deadline assumptions, cancellation, or
  network uncertainty releases the future reservation.
- Promotion is transactional; a reservation is not permanent execution permission.
- Packet loss, configured dead zones, stale peers, controlled single-file blocks, and
  the network recovery hold disable new future planning and retain normal BIOS 6.

The topology gate is evidence-driven. Initial trials in chokepoints and human/dense
aisles were neutral-to-worse, so speculative future work is currently enabled only on
open layouts where the paired burst benchmark supports it.

## Security and correctness hardening

- Directed task awards are accepted only from `FM0` in the manager-backed allocation
  mode and are revalidated locally for payload, path, battery reserve, deadline,
  ownership, and epoch before execution.
- Task completion messages must identify the current logical task, an applicable epoch,
  and a consistent owner/source or locally valid relay provenance.
- Implausible epoch jumps are rejected and outbound epochs are bounded.
- Reordered bids for unknown tasks remain non-authoritative in a 128-entry, 2 s TTL
  cache; overflow and stale entries are discarded.
- Conflicting peer task descriptors cannot overwrite known task properties. A WMS
  descriptor can correct a provisional peer-gossiped descriptor once.
- UDP replay protection now has a 1,024-session global bound and one-hour stale-session
  expiry.

The current shared fleet PSK authenticates membership but is not Byzantine per-robot
identity. A malicious fleet member that knows the common PSK is outside this prototype's
security claim. Per-robot signing keys are required before stronger owner-proof claims.

## Verification

All existing and new tests pass:

```text
160 passed
Ruff: all checks passed
git diff --check: clean
```

New tests cover policy selection, ordinary idle-only auction behavior, one future bid,
capacity, best-candidate selection, waiting behind `ACTIVE`, idle-vs-busy comparison,
payload/energy/deadline/charger rejection, stale bundle awards, renewable/expiring
future leases, transactional promotion, cancellation without task resurrection,
network fallback/recovery hold, manager-award feasibility, unknown-bid bounds, epoch
poisoning, descriptor consistency, stale completion rejection, role authorization, and
bounded replay sessions.

Determinism was rechecked for burst seed 1 under `PYTHONHASHSEED=0`, `1`, and `42`.
Every non-wall-clock result was identical:

```text
15 tasks, 210.94 s, 10,052 messages, 3,210 auction messages,
151 bids, 58 future bids, 9 promotions, 0 contacts, 0 deadlocks
```

## Paired benchmark methodology

- Baseline ran from a detached worktree at the untouched release commit above.
- Candidate ran from `auctionv2`.
- Both used `BIOS_PIBT.6`, the same scenario builders, task descriptors, robot counts,
  seeds 0-2, network settings, simulation cutoffs, routing, PIBT, and safety layer.
- Task-stream hashes were checked per paired seed.
- Percentages below are measured simulation evidence, not physical certification.

### Sparse open workload

Five robots, one task per robot, three seeds. Both policies completed 15/15 tasks with
identical makespans, messages, contacts, and separation. The candidate sent no future
bids. This is the intended approximately-zero overhead result.

### Bursty open workload

Five robots, three tasks per robot, three seeds:

| Metric | Untouched BIOS 6 auction | `auction_bundle` | Measured change |
|---|---:|---:|---:|
| Tasks completed | 45/45 | 45/45 | equal |
| Mean paired makespan | 214.59 s | 176.28 s | **17.85% lower** |
| Total messages | 29,318 | 25,827 | **11.91% lower** |
| Auction messages | 8,382 | 8,171 | **2.52% lower** |
| Total auction bids | 355 | 293 | **17.46% lower** |
| Nonproductive wait ticks | 14,656 | 11,062 | **24.52% lower** |
| Future bids / promotions | 0 / 0 | 100 / 29 | observed |
| Robot/robot, robot/human, robot/rack contacts | 0 / 0 / 0 | 0 / 0 / 0 | equal |
| Deadlocks detected | 0 | 0 | equal |
| Worst separation | 0.867 m | 0.879 m | no regression observed |

Candidate makespans were 161.44 s, 210.94 s, and 156.46 s, versus baseline 223.76 s,
220.10 s, and 199.90 s. Allocation evaluation per-run mean was 1.35-2.38 ms; the
largest per-run P95 was 4.14 ms and the observed maximum was 5.97 ms on this machine.

The 5%, 10%, and 15% improvement-threshold sweep produced the same results on these
three burst seeds. The middle 10% setting remains the conservative default.

### Adaptive fallback scenarios

Chokepoint, human/dense aisle, and dead-zone scenarios produced zero future bids. Across
three seeds each, `auction_bundle` reproduced the untouched BIOS 6 task counts,
makespans, messages, contacts, deadlocks, and separation. This is intentional fallback,
not an allocator speedup.

### Combined fault challenge

The grand challenge includes dense aisles, humans, a dead zone, packet loss, a blocked
aisle, and robot failure/restart. Future planning was disabled, so this exposes the
security-hardening trade-off rather than a future-allocation gain.

At the fixed 480 s cutoff, untouched BIOS 6 completed 25 tasks across seeds 0-2
(6, 7, 12), while the hardened candidate completed 20 (6, 6, 8). Both timed out and
both observed zero robot/robot, robot/human, and robot/rack contacts and zero detected
deadlocks. Strict epoch/owner completion validation rejected stale completion gossip;
that prevented unsafe cancellation but delayed convergence and reduced progress.

## Verdict

The one-step allocator has **measured potential** for bursty open workloads and is
neutral where adaptive fallback applies. However, the combined hardened candidate does
not yet meet the universal acceptance condition because completion-proof handling
regresses liveness in the grand fault campaign.

Therefore:

- keep `auction_bundle` experimental on `auctionv2`;
- do not make it the default;
- do not merge it into the BIOS 6 release yet;
- do not claim a universal 17.85% improvement;
- next add identity-bound, replayable completion certificates or an equivalent bounded
  terminal-task proof that preserves stale-epoch safety and fault-campaign convergence,
  then repeat the full paired benchmark.

Actual travel, empty-travel, consumed-energy, and deadline-miss aggregates are not yet
exposed by the current `PolicyResult`; no improvement claim is made for those metrics.
Energy and deadline safety are covered here by hard feasibility checks, unit tests, and
rejection telemetry, not by an aggregate efficiency claim.
