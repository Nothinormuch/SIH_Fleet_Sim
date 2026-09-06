# BIOS 7 release checks and evidence boundaries

Status: candidate verification in progress. This checklist does not approve a
release or change the default policy. Personal `main` and collaborator `main` are
not modified by running tests.

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

The fixed `--phase release` plan cannot accept easier replacement seeds, smaller
task catalogs or custom fleet sizes. It declares 78 candidate cases:

1. `regression50`: repeat the exact failed fixed-floor, 50-AMR, seed-0 workload first.
2. `capacity`: historical smaller fleets, scaled 50 and fixed/scaled 100. Expansion
   requires a passing regression50 report from the same frozen candidate sources.
3. `holdout`: 30 SIH ten-AMR seeds, 2000 through 2029, not previously tuned seeds.
4. `stress`: humans, blocked aisle, controller failure, chokepoint, crossed paths
   and open floor; 3/10 AMRs and three seeds each.
5. `repeat`: identical input repeated twice, with equal semantic result counters.

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
