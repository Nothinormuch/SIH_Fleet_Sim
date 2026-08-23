"""Monitor BIOS for stalls and progress over a longer run."""
import sys
sys.path.insert(0, '.')
from src.scenarios import crossing_chokepoint
from src.settings import DEFAULT
from src.world import World
from src.transport import SimNetwork
from src.amr import AMRBrain, POLICY_BIOS

policy = sys.argv[1] if len(sys.argv) > 1 else POLICY_BIOS
sc = crossing_chokepoint(4, 3, 0)
sc.duration_s = 600.0
cfg = DEFAULT
dt = 1.0 / cfg.rates.world_hz
world = World(sc.env, cfg, seed=0)
net = SimNetwork(cfg, seed=0)
net.register('WMS')
brains = {}
for i, start in enumerate(sc.starts):
    rid = f'AMR{i+1:02d}'
    world.add_robot(rid, start, 0.0)
    b = AMRBrain(rid, sc.env, cfg, policy=policy, home=start)
    b.queue = list(sc.assignments[i]) if i < len(sc.assignments) else []
    brains[rid] = b
    net.register(rid)

last_change = {r: 0.0 for r in brains}
max_stall = {r: 0.0 for r in brains}
last_cell = {r: None for r in brains}
done_at = None
for k in range(int(sc.duration_s / dt)):
    t = k * dt
    cmds = {}
    for rid in sorted(brains):
        st = world.robots[rid]
        net.set_position(rid, (st.x / cfg.cell_m, st.y / cfg.cell_m))
        sensors = world.sense(rid)
        act, outbox = brains[rid].step(t, sensors, net.poll(t, rid))
        for m in outbox:
            net.send(t, rid, m)
        cmds[rid] = act
        st.carrying = brains[rid].task.tid if brains[rid].task else None
    world.step(dt, cmds)

    for rid in brains:
        c = (round(world.robots[rid].x), round(world.robots[rid].y))
        if c != last_cell[rid]:
            max_stall[rid] = max(max_stall[rid], t - last_change[rid])
            last_change[rid] = t
            last_cell[rid] = c
    if done_at is None and sum(len(b.completed) for b in brains.values()) >= sc.n_tasks:
        done_at = t
        print(f'ALL TASKS DONE at t={t:.1f}')
for rid in brains:
    max_stall[rid] = max(max_stall[rid], sc.duration_s - last_change[rid])
print(f'policy={policy} done_at={done_at}')
print(f'max_stall_per_robot={ {r: round(v,1) for r,v in max_stall.items()} }')