# Shared Human-AMR Flow and Motion Audit

## Scope and release status

This audit covers the local mixed-traffic redesign for the Human Interaction and
Grand Challenge showcases. It is deterministic simulation evidence, not a physical
safety certification. The change must remain local until visual review is accepted;
it is not authorization to push or merge.

## Defects reproduced

### Repeated AMR rotation

After braking, an AMR could stop slightly beyond the centre of a straight-path cell.
The waypoint follower still considered that centre unvisited, so it turned 180 degrees,
drove back to the centre, then turned another 180 degrees to continue. The route and
reserved cells were valid; the controller was chasing a point behind the chassis.

The follower now consumes a crossed waypoint only when the incoming and outgoing grid
edges are the same straight direction. It never applies this shortcut at a corner,
where reaching the cell centre is still required for rack clearance.

### Unexecutable temporary detour

A stopped anonymous object could temporarily disconnect the one-way circulation graph.
The old replan fallback installed an undirected route, but the directed traffic gate
correctly rejected its first reverse edge forever as `v2-direction`. The robot therefore
held a non-empty route that could never be executed.

Directed BIOS policies now retain an empty temporary plan and retry after the short-lived
local obstacle expires. They never install a route that their own traffic gate forbids.

### Decorative rather than collaborative workers

The previous presentation routed workers around a protected outer apron. That separated
people from robots, but it did not demonstrate humans doing warehouse work alongside
AMRs. Earlier mixed-aisle attempts overcorrected by making every worker cross the entire
floor for every stop, creating unrealistic congestion and time-box failures.

## Implemented mixed-traffic model

- Human Interaction uses three workers; Grand Challenge uses five.
- Workers are assigned seeded, non-overlapping two-aisle rack zones distributed across
  the left, centre, right, and intermediate warehouse regions.
- Workstations are discovered from rack geometry rather than fixed screen coordinates.
- Every route segment, including its return, is expanded by A* through passable cells.
- Workers remain on the physical warehouse floor and cannot enter racks, obstacles,
  AMRs, or one another.
- A worker pauses for four seconds at a shelf inspection point. Starts are offset by
  two seconds per worker so the full crew does not enter traffic simultaneously.
- When local motion predicts a conflict, a worker evaluates forward, reverse, and
  bounded preferred-side steps. Unsafe swept segments are rejected.
- Work pauses are interruptible when an AMR approaches.
- AMRs still receive anonymous onboard detections only. Humans do not broadcast intent,
  consume fleet messages, bid for tasks, or depend on the WMS.
- The 3D worker now displays a tablet/inspection pose during `working`; green indicates
  inspection and amber indicates yielding.

## Measured deterministic evidence

### Controller-only spin regression

On the same 8-AMR, seed-1 Grand Challenge input before the shared-worker redesign:

- Previous controller: 16/16 tasks in 484.3 s; individual AMRs spent approximately
  103-140 s turning in place.
- Crossed-straight-waypoint fix: 16/16 tasks in 329.12 s; individual turn-in-place time
  fell to 24-49 s.
- Both runs recorded zero robot-robot, robot-human, and robot-rack contacts.

Remaining turns correspond to route corners, yielding, parking, or recovery and are not
removed merely for visual smoothness.

### Final shared-worker acceptance campaign

Configuration: BIOS 6, `auction_bundle`, 8 AMRs, 5 workers, 16 tasks, 800 s evidence
window, Grand Challenge seeds 0-4.

| Seed | Completion | Makespan | Closest separation | All contacts |
|---:|---:|---:|---:|---:|
| 0 | 16/16 | 429.26 s | 1.220 m | 0 |
| 1 | 16/16 | 402.00 s | 1.007 m | 0 |
| 2 | 16/16 | 383.16 s | 0.917 m | 0 |
| 3 | 16/16 | 416.08 s | 1.016 m | 0 |
| 4 | 16/16 | 507.52 s | 0.991 m | 0 |

Campaign result: 80/80 tasks, zero observed robot-robot, robot-human, and robot-rack
contacts, with a mean makespan of 427.60 s. This supports completion and safety only
for the defined deterministic campaign; it does not establish universal completion or
physical certification.

## Regression gates

The codebase includes targeted tests for:

- skipping a crossed waypoint on a straight path;
- retaining a crossed corner waypoint for rack clearance;
- rejecting unexecutable reverse edges in directed replans;
- reciprocal worker clearance in a shared aisle;
- warehouse-wide worker coverage, rack safety, safe spawn placement, work visits, and
  no hidden pedestrian apron in showcase telemetry;
- complete Grand Challenge execution with and without workers.

## Remaining validation boundary

Eight AMRs are the defined showcase acceptance fleet. Ten-AMR mixed-traffic runs are a
higher-density overload stress case and must be reported separately until their own
multi-seed completion gate passes. No universal 100% completion or certified human
safety claim should be made from this simulation.
