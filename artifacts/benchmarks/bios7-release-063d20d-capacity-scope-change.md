# Capacity queue stopped at the user's request

On 2026-09-06 at approximately 10:51 UTC, the user explicitly requested that
100-AMR testing be deferred for the imminent demonstration. This changes the
release scope; it is not a passing result for the former full capacity gate.

The owned capacity coordinator (PID/process group 64199) and its active worker
(PID 64902) were interrupted with SIGINT. Their exit and the end of their scoped
sleep inhibitor were verified. No 100-AMR worker started in this candidate's
capacity queue. The separate registered ten-AMR SIH campaign continued untouched.

The raw capacity artifact is preserved without rewriting its plan or results:
`bios7-release-063d20d-capacity.json`, SHA256
`a96e1d9af2b48f1eb6da5dc6a2fa961f929811bacf523366ba28e58b40ec8001`.
It contains one completed worker out of the originally declared 27. That worker
was the immutable original V6, scaled-floor 50-AMR control: 100/100 tasks, 884.28
simulated seconds, and 19 rack-contact events. It is unsafe and excluded from
valid performance claims. The scaled-floor current-source V6 worker was active
when interrupted and has no measured result. Twenty-five workers never started.
The capacity stage is incomplete, not passed, and provides no current-candidate
scaled-floor or 100-AMR completion result.

The already completed, separate fixed-floor regression50 evidence remains valid:
BIOS7 and current-source V6 each completed 100/100 tasks with 50 robots in 379.24
simulated seconds and zero contacts. It is not a 100-robot test.

The immediate release target is a validated smaller-fleet demonstration, including
ten-AMR mixed traffic and two-host independent-controller execution. Remaining
registered SIH, safety, communication nonregression, determinism and live timing
gates are not relaxed. Larger-fleet scalability stays explicitly unvalidated;
neither architectural intent nor available map space proves 100+ AMR operation.
