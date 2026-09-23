# Independent review: idle-clearance release blocker

Candidate: `98f34333e44f5e14ad6828402c8fa5da920c1096`.
AMR SHA256: `2bbe49b506975a749a0c5eba488a37a0b63c127e4a69e0ad0f984d1d5a0ccda1`.
The proprietary Bugbot launcher was unavailable. One independent read-only review
agent was used as the disclosed fallback; the main agent independently reproduced
its finding. This is not a claim of proprietary Bugbot service execution.

## P1: validated idle-clearance waypoints are discarded on bidirectional maps

In `src/amr.py:3774`, `_vacate_if_in_the_way` installs the physically validated
recovery only inside the `self.circulation.enabled` branch. A bidirectional map
instead takes `_replan` at line 3808. An off-centre chassis can consequently attempt
the ordinary recentering motion which its nearby body prevents, despite having a
valid staged path into a free bay. The defect affects both current-source V6 and V7;
paired nonregression cannot detect a shared failure.

The captured-corner fixture, with one rack opened so neither endpoint belongs to
a controlled block, produced the valid path `(5.11, 5.32) -> (4.90, 6.30)`. After
60 simulated seconds the robot had not translated from
`(5.549068918095095, 4.927285187135282)`; its selected goal was `(3, 4)`, installed
recovery target was `None`, and its command was a protective zero-speed stop.
There were zero contacts. This is an independently reproduced liveness failure,
not a collision or a failed registered acceptance case.

Read-only reproduction from the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python - <<'PY'
import sys
sys.path.insert(0, 'tests')
from test_recovery_geometry import _fixture
from src.amr import AMRBrain, Peer, ST_IDLE, POLICY_BIOS_PIBT_V7
from src.environment import FREE, Warehouse
from src.geometry import to_cell
from src.settings import DEFAULT
old, world, target = _fixture()
grid = [list(row) for row in old.env.grid]
grid[4][2] = FREE
env = Warehouse(7, 7, tuple(map(tuple, grid)), ((0, 0),), ((6, 6),))
world.env = env
brain = AMRBrain('AMR24', env, DEFAULT, policy=POLICY_BIOS_PIBT_V7)
brain.state = ST_IDLE
for rid, body in world.robots.items():
    if rid != brain.rid:
        brain.peers[rid] = Peer(
            rid, cell=to_cell((body.x, body.y), DEFAULT.cell_m),
            pose=(body.x, body.y, body.theta), last_seen=0,
            blocked_on=brain.rid, task_id='T'+rid,
            goal=(3, 3), intent=[(3, 3), (3, 4)])
sensors = world.sense(brain.rid, pose_noise_m=0)
print('controlled blocks:', brain._controlled_block(sensors.cell),
      brain._controlled_block(target))
print('valid recovery:', brain._recovery_route(sensors, target))
for _ in range(3000):
    for peer in brain.peers.values():
        peer.last_seen = world.t
    sensors = world.sense(brain.rid, pose_noise_m=0)
    act, _ = brain.step(world.t, sensors, [])
    world.step(.02, {brain.rid: act})
print('position:', sensors.pose, 'goal:', brain.goal,
      'recovery:', brain._cell_repair_target,
      'contacts:', len(world.contacts), 'command:', act)
PY
```

## Preserved campaign scope and interruption

By 2026-09-06 10:06:12 UTC, only the owned holdout process group 55447 and capacity
process group 58375 were interrupted with SIGINT. Their child workers and scoped
caffeinate process had exited. No unrelated process or server was stopped. Neither
frozen source nor either raw report was modified to hide the interruption.

- Holdout: 66/120 measured workers, including 16/30 complete V7 cases (480/480
  tasks, zero contacts). Seed 2016 V7 was interrupted and is unmeasured; 53 further
  workers never started. This is not a full holdout pass or failure. Seeds 2000–2015
  have now been observed, and seed 2016's V6 controls have been observed.
- Capacity: 3/27 measured workers (scaled-floor 50-AMR triplet). Fixed-floor 100-AMR
  V7 was interrupted and is unmeasured; 23 further workers never started. There is
  no completed 100-AMR result from this candidate.
- Earlier complete regression50, stress, repeat and diagnostic reports remain
  available under their original source and scope. The full suite passed 976
  tests; that does not override this newly verified untested failure.
- The private seven-round LAN package was prepared only. No latest-source campaign
  was started, and that package must be regenerated after a controller change.

The raw files remained byte-for-byte identical before/after the scoped stop:

`bios7-release-98f3433-holdout.json` SHA256
`0520fad23cf9cccefa80ea25f908e1d2ea3347ad3f69d6dc20f99713a1ad75b9`.

`bios7-release-98f3433-capacity.json` SHA256
`f67efeb4d425f2682ced898afdbb52029d6cee53d3a889a4c63ce927dca85e42`.

No review finding has been fixed yet. The requested review workflow pauses before
applying findings pending the user's next instruction. BIOS 6 remains the default;
personal main and collaborator branches have not been pushed or merged.
