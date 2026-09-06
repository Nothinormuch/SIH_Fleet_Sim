# BIOS7 smaller-fleet demo release candidate

Status: **verification in progress; personal main has not been promoted**.
The `seven` branch prepares `BIOS_PIBT.7` with `auction_bundle` as the public
launch defaults. The final SIH campaign and latest-source Mac/Windows timing
evidence are still required before publication. This file is not release approval.

## Scope

The immediate demonstration uses three to ten independent robot controllers.
At the user's request, 100-AMR testing is deferred. The interrupted expansion
report stays incomplete; it is not replaced with a passing label. Successful
fixed-floor 50-AMR evidence is additional simulation evidence from a separate
55-by-31-cell warehouse, with no humans, not the standard smaller dashboard map.
No 100+ scalability, physical Raspberry Pi, universal compatibility, or certified
safety claim is made.

## What changes for an operator

Public CLI entrypoints, the dashboard API, Edge Lab, the distributed runner and
the service example share BIOS7/Auction V2 defaults through `src/release_profile.py`.
The dashboard reads those defaults from its scenario API. Explicit BIOS6, earlier
policies, plain auction, Hungarian and preassigned comparisons stay selectable
where supported. Existing recordings keep the policy that actually produced them.

The five normal showcase presets retain their existing maps, workload catalogs,
fleet sizes, seeds and observation windows. All five passed the frozen-controller
checks. Four additional ten-AMR settings were also exercised. Human Interaction,
Open Floor and Dead-Zone Mesh completed within their original windows; ten-AMR
Chokepoint did not finish within the original four-AMR preset's 320-second window.

That original result remains visible: BIOS7 completed 12/20 tasks versus BIOS6's
11/20, both contact-free. A separately labelled 800-second diagnostic completed
20/20 in 555.50 seconds with BIOS7 and 590.70 seconds with current-source BIOS6.
Messages were 49,859 versus 53,850 and serialized bytes 9,676,901 versus 10,363,094.
There were zero contacts for either policy. This extended observation is not a
passing result for the original 320-second cutoff.

The UI now offers an explicitly visible **Scale demo window with fleet** setting.
It starts from the selected preset's existing window and scales the allowance
with fleet size, subject to the existing API resource limits. For Chokepoint,
four AMRs retain 320 seconds; ten AMRs visibly select 800 seconds before launch.
This is a presentation allowance, not an estimated or guaranteed completion time.
Typing a duration switches to a fixed cutoff that later fleet edits do not change.
Seed99 retains its fixed six-AMR/180-second proof. Custom maps and registered
headless benchmark windows are not changed. This UI adjustment is not credited
as an algorithmic speed improvement.

Actual browser checks verified the visible allowance, preservation of a manually
typed 320-second cutoff, retained V6 labels on an old recording, and the new V7
ten-AMR Chokepoint recording's 20/20 final evidence panel with no browser errors.

The updated candidate passed all **1,023 Python tests** in 246.69 host seconds,
including 120 focused public-default/API/UI/runtime checks. Five JavaScript
live-twin tests and Ruff also passed. These regression results do not replace the
separate quiet-host live timing campaign.

## Source identities and release gates

Headless controller evidence is frozen at commit `063d20d4b2bf75f0e2d0d3d8634eb747680356c4`,
with AMR SHA256 `c19cea1422c93e68527304083bcf51ea967a012cb8dc02d77f3830b3cd32d7dc`.
The subsequent candidate launch-default/UI changes do not modify the AMR algorithm,
physical configuration or acceptance harness. They receive a separate full test
run and source fingerprint; old headless artifacts are not relabelled as results
from a later commit. The LAN package must contain the final candidate entrypoint
bytes, so earlier prepared packages are withdrawn rather than bypassing checks.

The prepared launch-default commit is `6545a238b63b9a9b11608a01109be708a9af3ce5`.
Its complete private LAN package has controller fingerprint
`8578c87abdc2e09189a22c4f576690dbcf007742d3ace36ba4c86bc97cda900a`.
No session key is part of the repository. The candidate was also rerun on the
ten-AMR SIH development seed0 with immutable V6, current-source V6 and BIOS7;
each semantic result matched its corresponding frozen-063d20d result exactly.
BIOS7 again completed 30/30 in 833.08 seconds versus 968.12 for both V6 controls.
The default HTTP request, with policy/allocation omitted, selected BIOS7/Auction V2
and completed the standard Open Floor eight-task profile in 91.32 seconds with
zero contacts. These are entrypoint/compatibility checks, not extra holdout seeds.

Required before promotion: complete registered 30-seed SIH range, zero candidate
contacts and full task completion, per-case time/message/byte nonregression against
current-source V6, the SIH stop-and-wait bound, stress/coverage and repeat gates,
causal checks, full regression tests, and latest-source two-host live evidence.
See [the release checklist](24-BIOS7-RELEASE-CHECKLIST.md) and
[controller execution notes](../artifacts/benchmarks/bios7-release-063d20d-execution-notes.md).

Only after those gates pass: merge/push personal `main`, and publish collaborator
branch `seven`. Collaborator `main` is outside the authorized publication scope.
