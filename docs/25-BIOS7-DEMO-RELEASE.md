# BIOS7 smaller-fleet demo release candidate

## Current user-approved mixed demo release

The user explicitly authorized a scenario-specific merge after reviewing the
comparison below: BIOS7 for Open Floor, Chokepoint, Human Interaction and Dead-Zone
Mesh; BIOS6 for Grand Challenge. Auction V2 remains the default allocator.
Dashboard scenario selection and HTTP/CLI requests with omitted policies apply
this mapping. Explicit overrides remain available and recording labels retain
their actual executed policy. Other scenarios retain the BIOS7 fallback; no new
acceptance claim is made for them.

The five measured selected configurations completed 52/52 tasks with zero contacts.
Only Chokepoint showed a V7 improvement over current-source V6 (8.50%); three
presets tied and Grand Challenge intentionally retains V6. These results are from
the unchanged controller source be01a30; the release edits select defaults only.
The failed seed-2017 report and three strict LAN timing failures remain visible.
This approval supersedes the earlier publication block **for the mixed demo only**,
not for universal V7 performance, hardware deployment or hard-real-time claims.

### Historical decision before mixed-profile authorization

Status: **not promoted: latest-source seed 2017 failed completion, and the
subsequent demo-only comparison regressed Grand Challenge by 13.75% versus V6**.

The user stopped the broad campaign and conditionally requested a demo release
if V7 performs better. All five unchanged demo presets completed without contacts
for both V6 and V7. V7 improved Chokepoint by 8.50%, tied three presets and was
slower on Grand Challenge, so no demo-wide upgrade was established. See the
[latest comparison](../artifacts/benchmarks/bios7-demo-be01a30-REVIEW.md).
Personal main remains unchanged; live timing qualification also remains failed.

The user approved software-prototype publication after the latest-source SIH
gates pass, with failed live-timing results retained. This supersedes the earlier
timing prerequisite for prototype publication only; it does not certify deployment.
The latest frozen `be01a30` durable-worker candidate completed **47/47 tasks with
zero contacts** across seven Mac/Windows rounds. **Four of seven** passed all
strict gates; chokepoint, human crossing and failure recovery still failed timing.
No stale motion was accepted and no unexpected stale motion was rejected.
See [the latest LAN audit](../artifacts/deployment/lan-campaign-20260906T142520Z-mq62vkk7/REVIEW.md)
and [the approved scope](24-BIOS7-RELEASE-CHECKLIST.md).
The following checkpoints are historical, not substitutes for current-source
acceptance evidence.

Recovery checkpoint: the later command-contract and stationary-queue repairs
passed 1,083 Python tests, including the two mixed-warehouse regression failures
discovered during verification. The earlier single-host overlap and human runs
also demonstrated the original live-adapter mismatch repair. These are separate
source-pinned checkpoints, not a passing rerun of the failed LAN campaign. See
[the complete recovery audit](../artifacts/deployment/contract-repair-20260906/README.md).
The subsequent exact-source two-host campaign completed **47/47 declared jobs**
with zero contacts, but only **3/7 rounds passed all gates**. Ten-AMR overlap,
chokepoint, human crossing and failure-recovery rounds still failed timing;
the failure round also rejected 47 unexpected stale motion frames (none applied).
See [the repaired-candidate LAN audit](../artifacts/deployment/lan-campaign-20260906T132632Z-zql84vyj/REVIEW.md).
That checkpoint did not pass its strict deployment timing gates. The latest
candidate still needs its registered software performance gates; deployment timing
qualification remains separate under the approved scope above.

The `seven` branch prepares `BIOS_PIBT.7` with `auction_bundle` as the public
launch defaults. The earlier frozen-source SIH headless campaign passed. The
first Mac/Windows campaign passed only two of seven sessions, before the command
and queue repairs above. This file is not release approval. See the
[first LAN evidence and audit](../artifacts/deployment/lan-campaign-20260906T114030Z-sje2va1r/REVIEW.md).

That first LAN test completed 32/47 declared tasks with zero contacts across all seven
sessions. Three-AMR sensor-loss and overlapping-path sessions passed. Ten-AMR
overlap completed 2/10 jobs and human crossing completed 3/10. Chokepoint and
blocked-aisle completed 10/10 each, and the one-job failure scenario recovered
its job, but every ten-AMR session failed at least one timing gate. None of
these failures may be overridden by the successful headless results.

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
The complete registered range now passed: 900/900 candidate jobs, zero contacts,
and per-case current-source V6 time/message/byte nonregression. Median exact task
time reduction against current-source V6 was 8.3198%. The minimum stop-and-wait
reduction was a 27.475% lower bound because that control timed out; it is not an
exact completed-baseline speedup. This does not approve the failed live campaign.
See [the release checklist](24-BIOS7-RELEASE-CHECKLIST.md) and
[controller execution notes](../artifacts/benchmarks/bios7-release-063d20d-execution-notes.md).

Only after those gates pass: merge/push personal `main`, and publish collaborator
branch `seven`. Collaborator `main` is outside the authorized publication scope.
