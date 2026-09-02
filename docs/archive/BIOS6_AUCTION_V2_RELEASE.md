# BIOS 6 Auction V2 release evidence

## Release decision

BIOS 6 with `allocation_policy=auction_bundle` satisfies the release condition:

> 100% completion across the checked-in feasible deterministic BIOS 6 Auction V2
> acceptance campaign.

That sentence is deliberately scoped. It is a finite simulation result over the exact
campaign below. It is not a universal termination proof, physical safety certification,
or Byzantine-security claim.

## What changed

- Hard feasibility deadlines now outrank soft business priority. Priority still orders
  tasks inside the deadline class.
- Launch waves keep two in-flight reservations per drop. After at least half of the
  replicated workload is terminal, a bounded three-reservation drain phase prevents an
  idle late-wave tail. The rule depends on workload state, not the scenario cutoff.
- Auction V2 retains at most one version-bound future task per robot. Promotion repeats
  path, payload, battery reserve, charger-return, active-task deadline, future deadline,
  lease, and ownership validation.
- Every immutable task generation has a canonical descriptor hash. A completion
  certificate binds task generation, descriptor, owner, auction epoch, completion time,
  result, nonce, and ownership proof hash.
- Terminal generations survive a process restart through a bounded, checksummed,
  atomically replaced journal. Corrupt, truncated, oversized, or tampered journals fail
  closed before the robot brain starts.
- The WMS observes valid terminal lifecycle only to stop repeating completed jobs. It
  never evaluates a bid, chooses a winner, or sends a peer-auction award.
- Reliable, fully fresh peer views become quiet after a recovery grace. Loss, dead zones,
  stale peers, partitions, and restarts retain task/completion anti-entropy.
- A held differential-drive chassis brakes its residual forward motion before turning;
  this prevents a recovery turn from sweeping the footprint into a side rack.
- Human local-yield and omnidirectional stop margins were restored to the checked
  simulation separation envelope. They are simulation controls, not certification.

The completion proof is deterministic integrity evidence carried over authenticated
transport; it is not a public-key signature. The security scope assumes a shared fleet
PSK and non-Byzantine fleet members. A malicious robot that already possesses that PSK is
outside the claim.

## Fixed acceptance campaign

- Seeds: 0 through 29, consecutive.
- Open burst: 5 robots, 15 tasks, existing 240 s cutoff.
- SIH overlap: 4 robots, 12 tasks, existing 1200 s cutoff at packet loss 0%, 5%, 10%,
  and 20%.
- Partition heal: 4 robots, 4 tasks, existing 240 s cutoff.
- Robot failure: 3 robots, 1 task, existing 180 s cutoff.
- Total: 210 runs and 1,680 announced tasks.
- Safety/liveness: zero robot/robot, robot/human, robot/rack contacts; zero detected
  deadlocks; zero deadline misses; zero rejected completion certificates.
- Communication: at most 15 messages per robot per simulated second.
- Computation: allocation P95 at most 25 ms on the current machine.
- Recovery coverage: future bids/promotions, terminal certificates, partition healing,
  and failed-owner reassignment must all be exercised.
- Determinism: semantic digests must match under `PYTHONHASHSEED=0`, `1`, and `42` for
  open burst, 20% loss, partition heal, and robot failure probes.

No cutoff was increased, no fault was removed, safety was not weakened, unfinished work
was not reclassified, and a timed-out run has no makespan.

## Measured result

| Gate | Result |
| --- | ---: |
| Complete runs | 210 / 210 |
| Completed tasks | 1,680 / 1,680 |
| All contact types | 0 |
| Detected deadlocks | 0 |
| Deadline misses | 0 |
| Rejected completion proofs | 0 |
| Open-burst median / P95 makespan | 199.24 s / 236.861 s |
| Maximum message rate | 12.14 messages/robot/s |
| Maximum measured allocation P95 | 17.6851 ms |
| Failed-owner reassignments exercised | 60 |
| Determinism probes | 4 / 4 passed across three hash seeds |

The maximum communication rate occurs in the lossy campaign; the healthy open burst
peaks at 9.16 messages/robot/s.

## Paired comparison with untouched BIOS 6

Baseline commit: `a7753c6a2e4c72c665c7abaceb6a0e4d55bf270d`.

Both variants use `showcase_open_floor(n_robots=5, tasks_per_robot=3)`, seeds 0–29,
the existing 240 s cutoff, BIOS 6 traffic/motion, and the same task inputs. The baseline
uses its untouched `auction`; the candidate uses `auction_bundle`.

| Metric | Untouched BIOS 6 auction | Auction V2 | Change |
| --- | ---: | ---: | ---: |
| Completed runs | 25 / 30 | 30 / 30 | +5 runs |
| Completed tasks | 438 / 450 | 450 / 450 | +12 tasks |
| Median makespan on 25 commonly completed seeds | 199.9 s | 195.6 s | -4.3 s |
| Median per-seed paired makespan delta | — | -14.6 s | faster |
| Total messages | 299,539 | 240,575 | -19.69% |
| Total wire bytes | 53,400,825 | 48,655,616 | -8.89% |
| Nonproductive wait ticks | 184,193 | 156,018 | -15.30% |
| Auction bids | 3,756 | 4,946 | +31.68% |
| Robot/robot, human, rack contacts | 0 / 0 / 0 | 0 / 0 / 0 | no regression observed |
| Detected deadlocks | 0 | 0 | no regression observed |

The increased bid count is expected: V2 evaluates bounded future work. Event-triggered
catalog and completion behavior more than offsets those bids, so both total messages and
wire bytes fall. Makespan is compared only on the 25 seeds where both variants finish;
the five baseline timeouts are not converted into fake completion times.

## Reproduce

```bash
source .venv/bin/activate
python -m pytest -q
ruff check .
python auction_v2_campaign.py --seeds 30 --jobs 8
```

The full raw acceptance artifact is
`artifacts/benchmarks/bios6-auction-v2-acceptance.json`. The paired summary is
`artifacts/benchmarks/bios6-auction-v2-paired.json`.
