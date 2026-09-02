# Human Flow, Fleet Liveness, and Dashboard Audit

## Release decision

The screenshot defect is fixed and the change is suitable for the personal `main`
branch. Workers no longer spawn or patrol in the AMR staging row. The protected-worker
showcases now keep pedestrian work independent from logistics traffic, while BIOS 6
liveness no longer depends on a person accidentally perturbing a robot queue.

This is deterministic simulation evidence, not a physical safety certification.

## Root cause found in the reported screenshot

The old showcase accepted a row-1 pedestrian route and then applied a visual Y offset.
That offset did not create a real lane in the map; it moved the worker toward row 0,
which is the AMR staging lane. Layer 0 then correctly detected the person inside its
protective field and stopped the robots. The safety code was responding correctly to
bad scenario geometry.

The earlier regression test used only one row-1 AMR, so it missed the actual 10-AMR
row-0 launch configuration visible in the screenshot.

A second screenshot exposed a different presentation defect after the physics route
had been corrected. The 3D camera still fitted only the AMR grid, not the pedestrian
apron. At a near-horizontal orbit angle, perspective projected a worker on the front
apron directly over an AMR several metres behind the barrier. Contact metrics remained
zero because the world coordinates were separate, but the rendered evidence was
misleading. Numerical separation is therefore no longer accepted as visual QA.

## Implemented design

### Protected worker operations

- Human Interaction uses 3 workers and Grand Challenge uses 5 workers.
- Workers use a real, rendered one-way pedestrian apron around all four warehouse
  sides. It is separate from the AMR traffic surface instead of being a coordinate
  offset disguised as a lane.
- Eight inspection stations cover the corners and side midpoints. Each person receives
  a seeded phase on the loop, producing reproducible variation without head-on worker
  traffic.
- Workers repeatedly stop to perform work, resume walking, accumulate distance, and
  report `walking`, `working`, or `yielding` state.
- The general human model still validates rack-safe A-star routes, rejects invalid rack
  endpoints, avoids other people, gives way locally to nearby AMRs, and never consumes
  fleet messages. The separate `human_in_aisle` scenario remains the mixed-traffic
  test for the onboard safety layer.

### BIOS 6 liveness corrections exposed by the redesign

Removing human interference exposed two pre-existing robot-only liveness defects:

1. A cell lease stopped duplicate ownership but allowed a following AMR to brake too
   close to a leader turning through a merge. BIOS 6 now stages only turning traffic
   one cell before an occupied degree-3/4 junction. Straight convoys keep flowing.
2. Idle-vacate logic treated the requesting robot's complete future route as forbidden.
   This could leave no cell into which the blocker could move. Physical occupancy and
   unrelated reservations remain hard exclusions, but a bounded clearance step may
   use the requesting robot's corridor while Layer 0 revalidates motion every tick.
3. A loaded robot now preempts an idle AMR's long optional parking trip when it
   explicitly names that AMR as the blocker. A one-cell clearance move already under
   way is allowed to finish so it cannot be reset every control tick.

These rules remain local and peer-derived. The WMS still announces tasks only and does
not choose winners, routes, parking targets, or motor commands.

### Dashboard and 3D presentation

- The Three.js camera fits the complete pedestrian envelope, not only the AMR grid,
  and renders a wide amber walkway, exclusion buffer, and continuous guard rail.
- Orbit elevation is bounded so perspective cannot flatten the protected walkway onto
  the AMR staging lane. Tactical view uses the same full-envelope dimensions.
- Worker plaques use compact IDs instead of long `Hn - WORKER` labels that covered AMR
  labels in the original camera view.
- Green pause rings mean station work; amber rings mean yielding.
- The Evidence diagnostics report human work visits and aggregate walking distance.
- The 2D fallback fits and renders the same metric apron rather than clipping workers
  whose valid coordinates sit outside the AMR grid.
- Browser QA at 1280 x 720 confirmed camera fit, readable controls, no label pile-up,
  a scroll-contained Evidence panel, and no browser console warnings or errors.

## Measured evidence

All figures below use BIOS 6 with `auction_bundle`, identical scenario sizes and fixed
800-second Grand Challenge evidence windows.

### Screenshot-scale deterministic run

- Previous personal-main behavior, 10 AMRs, seed 1: 20/20 tasks in 504.4 s, zero
  contacts.
- Corrected implementation, 10 AMRs, seed 1: 20/20 tasks in 491.8 s, zero contacts,
  1.05 m closest observed separation, 115 worker station visits, and 2,588.5 m of
  aggregate worker travel.
- This is a 2.5% measured makespan reduction for that specific seed. It is not a
  universal speedup claim.

### Five-seed Grand Challenge gate

- 8 AMRs, seeds 0-4: 80/80 tasks completed, zero robot-robot, robot-human, and
  robot-rack contacts. Mean makespan is 561.7 s versus 574.1 s in the untouched prior
  baseline, a 2.2% reduction.
- 10 AMRs, seeds 0-4: 100/100 tasks completed with zero contacts. The untouched prior
  baseline completed 99/100 because seed 4 reached the 800 s evidence cutoff at 19/20.
- The result supports "100% completion across this defined feasible deterministic
  campaign." It does not support universal 100% completion.

### Complete jury showcase gate

- Open Floor: 8/8 tasks, zero contacts, 91.32 s.
- Chokepoint: 8/8 tasks, zero contacts, 259.72 s.
- Human Interaction: 10/10 tasks, zero contacts, 468.92 s, 66 station visits.
- Dead-Zone Mesh: 6/6 tasks, zero contacts, 379.00 s.
- Grand Challenge: 16/16 tasks, zero contacts, 484.30 s for the official 8-AMR seed.
- Combined: 48/48 tasks and zero observed contacts.

## Verification completed

- `214 passed` in the full Pytest suite.
- Ruff lint: all checks passed.
- Python bytecode compilation: passed.
- Git whitespace validation: passed.
- Browser visual QA: normal overview, tactical fit, and the lowest permitted orbit
  angle passed, with no console warnings or errors.
- Grand Challenge liveness is explicitly tested both with workers and with the worker
  list removed, so pedestrian motion cannot silently become the fleet's deadlock
  breaker again.

## Cleanup decisions

Deleted:

- `debug_mon.py`
- `debug_trace.py`

Both were unreferenced root-level debug programs that duplicated an obsolete simulation
loop and were not part of the supported CLI, tests, server, or documentation.

Retained intentionally:

- source artwork and alternate 2D/3D assets, because they are design inputs and useful
  fallbacks rather than executable dead code;
- benchmark evidence and reports;
- user-owned untracked presentation work folders and the nested project copy.

## Remaining improvement areas

These are follow-up validation opportunities, not blockers for this software prototype:

- Increase the deterministic campaign beyond five seeds per fleet size and add larger
  fleets to quantify tail latency.
- Validate the pedestrian-apron dimensions against the eventual warehouse layout and
  occupational-safety review; the current dimensions are a simulation operating model.
- Re-run the multi-process edge demonstration after any future transport or runtime
  change. This UI/human-flow change does not alter transport code.
- Test additional responsive breakpoints if the jury display differs materially from
  the verified 1280 x 720 and the supplied high-resolution desktop view.
