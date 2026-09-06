# Timing diagnosis checkpoint, not release approval

Candidate code: `ac2ee3a2dc17c1e7256e509a3f2592f2ee4eee26`.
Controller fingerprint:
`5a0ca5656d1d71a40b880a358458c1c9064d3d12e56595dc5fa976f185c6fa22`.

Changes are bounded phase-by-phase control I/O timing witnesses, maximum wake
lateness, and a temporary macOS user-initiated thread QoS override. The override
is used only by the BIOS controller, driver bridge and simulation referee,
restored on exit, and explicitly reported as best-effort, not hard-real-time.
Windows and Linux retain their existing scheduler. No OS-wide settings change.
Atomic journal writes and their fsync durability remain synchronous and timed.
The 20 ms limit, stale-command checks, maps and workloads are unchanged.

The actual native macOS API probe applied and released the override successfully.
This demonstrates API operation, not a measured timing improvement. Tests inject
30 ms sensor-read, actuator-write and journal delays: all remain counted as
failed full cycles, with their phase identified. Tests also cover scoped cleanup,
platform fallback, rejected native requests, and unchanged failing result flags.

All **1,096 Python tests passed in 172.91 host seconds**. `full-suite.xml`
was produced in the isolated worktree at code commit `9002170`, then that exact
code was cherry-picked to `seven` as `ac2ee3a`. A Git comparison verified no
differences in `src/` or `tests/`. The full-suite run was separate from LAN
measurements; it is not a latency benchmark. Ruff and diff checks also passed.

A new, explicitly declared **choke10-only**, 240-second private LAN package was
prepared. It is a diagnostic subset, not a replacement for the seven-round gate.
Its results must be read from their own referee evidence once completed. No
improvement, release pass, personal-main merge or collaborator push is implied.

The previous candidate's complete 47/47 contact-free campaign and its remaining
timing failures are retained in
`../lan-campaign-20260906T132632Z-zql84vyj/REVIEW.md`.
