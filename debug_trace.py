"""Debug: trace robots on crossing_chokepoint."""
import sys
from dataclasses import replace
sys.path.insert(0, '.')

from src.scenarios import crossing_chokepoint
from src.settings import DEFAULT, Config
from src.world import World
from src.transport import SimNetwork
from src.amr import AMRBrain, POLICY_STOP_WAIT, POLICY_HIERARCHICAL, POLICY_BIOS
from src.fleet_manager import FleetManager, MANAGER_ID
from src.metrics import PolicyResult

def run(policy, robots=4, seed=0, duration=120.0, trace_every=1.0):
    sc = crossing_chokepoint(robots, 3, seed)
    sc.duration_s = duration
    cfg = DEFAULT
    dt = 1.0 / cfg.rates.world_hz
    world = World(sc.env, cfg, seed=seed)
    net = SimNetwork(cfg, seed=seed)
    net.register('WMS')
    brains = {}
    for i, start in enumerate(sc.starts):
        rid = f'AMR{i+1:02d}'
        world.add_robot(rid, start, 0.0)
        b = AMRBrain(rid, sc.env, cfg, policy=policy, home=start)
        b.queue = list(sc.assignments[i]) if i < len(sc.assignments) else []
        b.use_auction = sc.use_auction
        brains[rid] = b
        net.register(rid)
    manager = None
    if policy in ('central', 'hierarchical'):
        manager = FleetManager(sc.env, cfg)
        net.register(MANAGER_ID)
    _entered = {}
    for k in range(int(sc.duration_s / dt)):
        t = k * dt
        if manager is not None:
            out = manager.step(t, net.poll(t, MANAGER_ID))
            for m in out:
                net.send(t, MANAGER_ID, m)
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
            if 6 <= c[0] <= 18 and c[1] == 4:
                if rid not in _entered.get(policy, set()):
                    _entered.setdefault(policy, set()).add(rid)
                    print(f'ENTER {policy} t={t:6.1f} {rid} cell={c}')
        if sum(len(b.completed) for b in brains.values()) >= sc.n_tasks:
            print(f'{policy} COMPLETED ALL at t={t}')
            break

if __name__ == '__main__':
    p = sys.argv[1] if len(sys.argv)>1 else 'hierarchical'
    run(p)