# BIOS 7 release checks and evidence boundaries

Status: candidate verification in progress. This checklist does not approve a
release or change the default policy. Personal `main` and collaborator `main` are
not modified by running tests.

## Approved software-prototype scope (September 6, 2026)

The user explicitly approved publication as a **software-prototype release after
the latest-source SIH checks pass**, while retaining failed Mac live-timing results.
This supersedes the earlier requirement that every live 20 ms deadline must pass
before *software-prototype* publication. It does not turn any failed report into
a pass, relax the live timing measurement, or approve physical deployment.

Mandatory headless completion, zero-contact, SIH time-reduction, current-V6
nonregression, stress/coverage, repeatability and regression checks below remain
unchanged. The current two-host evidence must be reported separately, including
any timing/freshness failures. The physical/deployment timing qualification stays
unapproved until its own gates pass. Do not describe the prototype as hard-real-time,
Raspberry Pi-tested, certified, universally compatible or universally successful.

If the software gates pass, promote personal `main` and publish collaborator
branch `seven` only. Keep collaborator `main` untouched. Until then publication
remains pending. This explicit scope decision is not a retroactive benchmark pass.

Scope update, September 6 at approximately 10:51 UTC: the user requested that
100-AMR tests be deferred for the imminent demonstration. The active capacity
queue was stopped before reaching any 100-AMR worker; its original raw plan and
partial results are preserved with a separate scope-change audit. The former
78-case full-capacity release must not be called passed. A smaller-fleet demo
release still requires the complete registered ten-AMR SIH campaign, stress,
repeatability, causal and latest-source two-host timing gates below. Existing
fixed-floor 50-AMR evidence is additional finite evidence, not proof of 100+
scalability. No default has changed merely because this scope was reduced.

## What is being released

BIOS 7 retains decentralized task bidding and peer traffic coordination. Its new
policy mechanism releases corridor-related auction admission pressure only after
a verified passage; it does not release an occupied physical cell or override
another robot's lease. Shared V6/V7 reliability changes validate continuous recovery
geometry and preserve bounded, authenticated independent-controller execution.

The collision repair checks a swept chassis footprint, not only free destination
cells. It includes staged local recovery, bounded speed through recovery completion,
and a short actuator/braking check against the map. This follows the general
footprint-projection approach described in [Nav2's regulated path follower](https://docs.nav2.org/rolling/configuration_and_development/configuration_guide/controller_plugins/configuring_regulated_pp/).
It is not an implementation of Nav2 or proof of safety certification. As Nav2 also
explains for its [CPU-based collision monitor](https://docs.nav2.org/jazzy/configuration_and_development/configuration_guide/core_servers/collision_monitor/configuring_collision_monitor_node/),
software collision avoidance is distinct from a certified physical safety chain.

PIBT's published reachability result has graph assumptions; it is not a guarantee
for arbitrary warehouse geometry, arbitrary failures or continuous physical motion.
See the [original PIBT paper](https://arxiv.org/abs/1901.11282). Our finite acceptance
evidence must state its layouts, robot counts, task catalogs, seeds and cutoffs.

## Mandatory headless stages

The September 6 reliability candidate adds recovery regression tests and exact
top-k auction eligibility pruning. In a development diagnostic covering only the
first two simulated seconds of the fixed-floor ten-AMR, seed-0 workload, pruning
reduced A* calls from 16,210 to 5,650. Separate unprofiled subprocess measurements
were 0.55961 and 0.23199 host seconds. Semantic results, excluding measured host
compute timing, matched. These are short cold-start measurements, not a full-run
speedup or a live-control deadline claim. The shared helper benefits both current
V6 and V7; it is not credited as a V7-only policy advantage.

The frozen `5cce5de` candidate's complete unit/integration suite passed 723 tests. This includes
continuous recovery geometry, real sensor-TTL expiry, task-identity revalidation
and equivalence tests for the original versus pruned eligibility predicate. Its
subsequent fixed-floor 50-AMR test nevertheless stopped at 99/100 tasks with zero
contacts, so that candidate was not released. Later cache and idle-tail fixes
require their own complete regression and acceptance reruns. Test counts do not
replace the larger-fleet and independent-controller gates below.

The later bounded-cache change further reduced the same two-second diagnostic
from 5,650 to 2,690 A* calls, with matching semantic results before the separate
charger-reachability correction. These are two sequential short diagnostics, not
evidence of full-run speed or a live timing budget. Configured but unreachable
chargers are now rejected intentionally; this is a safety correction, not an
equivalence optimization. Existing no-charger tasks retain their legacy behavior,
which must not be described as proof of return-to-dock feasibility.

The fixed `--phase release` plan cannot accept easier replacement seeds, smaller
task catalogs or custom fleet sizes. It declares 78 candidate cases:

1. `regression50`: repeat the exact failed fixed-floor, 50-AMR, seed-0 workload first.
2. `capacity`: historical smaller fleets, scaled 50 and fixed/scaled 100. Expansion
   requires a passing regression50 report from the same frozen candidate sources.
3. `holdout`: the registered 30 SIH ten-AMR seeds, 2000 through 2029. Seeds
   2000–2004 were observed during the stopped `5cce5de` campaign; a later run of
   those seeds is a retest, not untouched holdout evidence. Keep the full registered
   range and disclose this distinction rather than replacing observed cases.
4. `stress`: humans, blocked aisle, controller failure, chokepoint, crossed paths
   and open floor; 3/10 AMRs and three seeds each.
5. `repeat`: identical input repeated twice, with equal semantic result counters.

Subsequent frozen candidate `83e22ca` failed the registered holdout on seed 2012:
BIOS 7 completed 16/30 tasks while both V6 controls completed 30/30. Its source-pinned
raw report is retained. At that point 2000–2012 had been observed and
2013–2029 remained unobserved; the later campaign described below expanded that
observed range. Fixing the verified idle-corridor continuation defect
requires a new complete fixed-range campaign, not relabeling the failed campaign
or its completed-pairs-only statistics as a release pass.

Candidate `98f3433` subsequently completed seeds 2000–2015 before an independent
review found a different, shared V6/V7 idle-clearance defect on bidirectional maps.
Its remaining holdout and capacity workers were intentionally interrupted; seed
2016's V6 controls had also run. Those partial reports are retained, not promoted.
The approved repair is frozen at `063d20d` with 1,006 passing tests. It preserves
validated escape waypoints while retaining normal bidirectional traffic admission.
Its new source-pinned release campaign must stand on its own; the prior completed
stages are not substituted as new-source results.

Every candidate case must complete all declared tasks and report zero contacts.
Human/fault cases must actually exercise the named behavior. Missing evidence is
not a pass. Each SIH case must show at least the specified 20% time reduction
against stop-and-wait. A baseline simulation timeout gives a conservative lower
bound, not an exact speedup; a worker/resource error gives no performance score.

Two V6 comparators are kept distinct:

- `bios6`: immutable original commit `8e4ab51727e4fcd7f372de8b55b99c38cb213512`.
- `bios6_safety_fixed`: V6 policy from the current candidate source, including shared
  safety/runtime changes. This is not a claim that its source has only a safety patch.

BIOS 7 must not increase per-case completion time, cumulative messages or cumulative
bytes versus the current-source V6 comparator. An incomplete comparator can only
prove these inequalities when the completed candidate is already below all relevant
observed lower bounds. Unknown inequalities fail the gate. Unsafe original results
remain visible and are excluded from valid safety/performance comparisons.

Run stages from a frozen candidate with `python bios7_acceptance.py --execute
--phase release --release-stage STAGE --control-root /absolute/path/to/bios6-control
--output /new/evidence/path.json`. Supply the regression50 report with
`--prerequisite` before the separate capacity stage. Existing evidence is never
overwritten. Never edit the candidate or control tree while workers execute.

The regression50 worker wall-clock allowance is 1,800 seconds. Before executing
any expanded capacity case, its separate stage declares `--worker-timeout 3600`
because the historical 50-robot worker already required about eight host minutes.
This is a host resource allowance, not a change to the fixed 1,200-second simulated
workload window. Other stages retain 1,800 seconds per worker. External timeouts
are errors, never successful simulated runs or completion-time denominators.

## Independent-controller and presentation gates

- Full unit/integration suite, geometry reproductions, ownership/security checks,
  and JavaScript parsing/rendering checks must pass on the final code.
- Repeat the source-pinned Mac/Windows proof with overlapping paths and ten
  independent controller processes, using the passive dashboard publisher.
- Exercise sensor loss separately from shared traffic, blocked aisles and process
  failure. Failure recovery needs a confirmed process exit observed before a
  surviving owner's authenticated task-completion event.
- Preserve all raw host/referee reports. Process exit zero alone is not a success.
  Check completion, contacts, cross-host authenticated peers, control/scheduler
  timing, stale-command rejection, scenario coverage and source identity.
- A campaign manifest contains only pinned local config/key/output paths. Each round
  has its own key/session and TCP listener port. Private keys are never committed.

## Default and publication gate

Only after the required evidence passes should the default selectors, labels and
run instructions switch to BIOS 7. Recheck default-selection and smoke tests before
merging into personal `main`. Publish collaborator branch `seven`; do not alter
collaborator `main`. Include exact tested source identities and any remaining
deployment limitations in the handoff. Preserve earlier failed evidence.

## What this cannot certify

Laptop processes are real distributed software execution, not Raspberry Pi CPU
emulation. Simulated contacts and sensor-stop commands are not physical safety
certification. A real AMR requires a supported driver/vendor adapter, localization
and map conventions, measured chassis/braking parameters, networking commissioning,
and the manufacturer's independent safety chain. No claim of universal compatibility,
universal completion, or superiority over every centralized planner is supported.
