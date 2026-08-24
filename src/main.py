"""The headless simulation runner: one scenario, one policy, one seed, one result.

This is the batch path. It runs the identical `AMRBrain` objects that the distributed
UDP demo runs, against a seeded network model instead of real sockets, at a few hundred
times realtime. That speed is not a convenience - it is what makes the safety claim
expressible as a rate with a confidence interval instead of "we watched it for a while
and nothing happened".

The loop is a fixed-step 50 Hz integration, and the ordering inside each tick matters:

    1. scripted world events   (kill the manager, split the network)
    2. WMS announcements       (auction scenarios only)
    3. fleet manager tick      (advice, never commands)
    4. every robot tick        (sense -> brain -> actuate, in sorted id order)
    5. world integration       (physics, then collision checks)

Robots are stepped in sorted id order rather than dict order so a run is reproducible
across Python versions, and every robot reads sensors from the *same* world state before
any of them acts - stepping robot A into a new position before robot B senses would give
A a physically impossible information advantage.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import replace

from . import messages as msg
from .amr import (AMRBrain, POLICIES, POLICY_HIERARCHICAL, POLICY_CENTRAL,
                  POLICY_STOP_WAIT, POLICY_BIOS, POLICY_BIOS4, Task)
from .fleet_manager import FleetManager, MANAGER_ID
from .metrics import PolicyResult, compare, safety_report
from .scenarios import SCENARIOS, Scenario
from .settings import Config, DEFAULT
from .transport import SimNetwork
from .world import World

WMS_ID = "WMS"

# Which policies are served by a fleet manager at all. Kept here as one tuple because
# two places need the answer - the runner, which decides whether to build a manager,
# and the dashboard payload, which has to tell the UI whether a missing manager is a
# failure or the design. A policy absent from this tuple is peer-to-peer by intent.
MANAGED_POLICIES = (POLICY_CENTRAL, POLICY_HIERARCHICAL)


def run_scenario(sc: Scenario, policy: str, seed: int = 0,
                 cfg: Config | None = None, trace: list | None = None,
                 verbose: bool = False, policy_model=None) -> PolicyResult:
    """Run one (scenario, policy, seed) and return everything it produced.

    Pass a list as `trace` to collect per-frame snapshots for the dashboard; leave it
    None for benchmark runs, where appending 30 000 frames would dominate the runtime.
    """
    if policy not in POLICIES:
        raise ValueError(f"unknown policy {policy!r}")

    cfg = cfg or DEFAULT
    cfg = replace(cfg, net=sc.net, seed=seed)
    dt = 1.0 / cfg.rates.world_hz

    world = World(sc.env, cfg, seed=seed)
    net = SimNetwork(cfg, seed=seed)
    net.register(WMS_ID)

    brains: dict[str, AMRBrain] = {}
    for i, start in enumerate(sc.starts):
        rid = f"AMR{i + 1:02d}"
        world.add_robot(rid, start, 0.0)
        # One model instance is SHARED by the whole fleet, and that is the claim:
        # every robot flashes the same BIOS. Sharing is safe because PolicyNet.act is
        # pure - it reads weights and returns an index, holding no per-robot state.
        b = AMRBrain(rid, sc.env, cfg, policy=policy, home=start,
                     policy_model=policy_model)
        b.queue = list(sc.assignments[i]) if i < len(sc.assignments) else []
        b.use_auction = sc.use_auction
        brains[rid] = b
        net.register(rid)

    for j, walk in enumerate(sc.humans):
        world.add_human(f"H{j + 1}", walk)

    # stop_and_wait has no fleet manager by definition - it is the no-coordination
    # baseline. BIOS_1.0.0 also runs without one, by design: its chokepoint
    # admission and unstick logic are wholly peer-to-peer. `central` depends on the
    # manager, `hierarchical` merely prefers it.
    manager = None
    if policy in MANAGED_POLICIES:
        manager = FleetManager(sc.env, cfg)
        net.register(MANAGER_ID)

    total_tasks = sc.n_tasks if not sc.use_auction else len(sc.unassigned)
    announced = False
    makespan = None
    steps = int(sc.duration_s / dt)
    seq = 0

    for k in range(steps):
        t = k * dt

        if sc.kill_manager_at is not None and manager is not None \
                and manager.alive and t >= sc.kill_manager_at:
            manager.kill()
            if verbose:
                print(f"  [t={t:6.1f}] fleet manager killed", file=sys.stderr)
        if sc.partition_at is not None and t >= sc.partition_at and net.partition is None:
            net.set_partition([set(g) for g in sc.partition_groups])
        if sc.heal_at is not None and t >= sc.heal_at and net.partition is not None:
            net.set_partition(None)

        if sc.use_auction and not announced:
            announced = True
            for tk in sc.unassigned:
                seq += 1
                net.send(t, WMS_ID, msg.task_new(WMS_ID, seq, t, tk.tid,
                                                 tk.pick, tk.drop))

        if manager is not None:
            out = manager.step(t, net.poll(t, MANAGER_ID))
            for m in out:
                net.send(t, MANAGER_ID, m)

        cmds = {}
        for rid in sorted(brains):
            st = world.robots[rid]
            net.set_position(rid, (st.x / cfg.cell_m, st.y / cfg.cell_m))
            sensors = world.sense(rid, pose_noise_m=sc.pose_noise_m)
            act, outbox = brains[rid].step(t, sensors, net.poll(t, rid))
            for m in outbox:
                net.send(t, rid, m)
            cmds[rid] = act
            st.carrying = brains[rid].task.tid if brains[rid].task else None

        world.step(dt, cmds)

        if trace is not None and k % int(cfg.rates.world_hz /
                                         cfg.rates.telemetry_hz) == 0:
            snap = world.snapshot()
            snap["fleet"] = [
                {"id": r, "state": b.state, "mode": b.mode,
                 "task": b.task.tid if b.task else None,
                 "goal": list(b.goal) if b.goal else None,
                 # The intent horizon, i.e. exactly what this robot is broadcasting.
                 # The dashboard draws these as reservation cones so the coordination
                 # is visible rather than implied - decentralisation is invisible on
                 # screen unless the messages are drawn.
                 "path": [list(c) for c in b.path[b.pidx:b.pidx + 8]],
                 "peers": sorted(b.peers.keys()),
                 "blocked_on": b.blocked_on,
                 "done": len(b.completed)}
                for r, b in sorted(brains.items())
            ]
            snap["manager_alive"] = bool(manager and manager.alive)
            trace.append(snap)

        done = sum(len(b.completed) for b in brains.values())
        if makespan is None and total_tasks and done >= total_tasks:
            makespan = t
            break

    world.finalize()
    return _summarize(sc, policy, seed, cfg, world, net, brains, manager,
                      total_tasks, makespan)


def _summarize(sc, policy, seed, cfg, world, net, brains, manager,
               total_tasks, makespan) -> PolicyResult:
    sim_s = world.t
    n = len(brains)
    robot_hours = n * sim_s / 3600.0
    task_times = [d - s for b in brains.values() for _, s, d in b.completed]
    done = sum(len(b.completed) for b in brains.values())
    seps = sorted(world.min_separations)

    def agg(key: str) -> float:
        return sum(b.stats[key] for b in brains.values())

    plan_cpu = agg("plan_cpu_s") + (manager.stats["plan_cpu_s"] if manager else 0.0)
    plan_calls = int(agg("plan_calls")) + (manager.stats["plans"] if manager else 0)
    plan_max = max([b.stats["plan_cpu_max_s"] for b in brains.values()] +
                   [manager.stats["plan_cpu_max_s"] if manager else 0.0])

    return PolicyResult(
        policy=policy, scenario=sc.name, seed=seed,
        sim_seconds=round(sim_s, 2), robots=n,
        tasks_completed=done, tasks_announced=total_tasks,
        # A run that did not finish has no makespan. Recording the wall-clock cutoff as
        # if it were one would silently turn a failure into a merely-slow result.
        makespan_s=round(makespan, 2) if makespan is not None else round(sim_s, 2),
        completed_all=makespan is not None,
        task_times=[round(x, 2) for x in task_times],
        throughput_per_robot_hr=round(done / robot_hours, 2) if robot_hours else 0.0,
        contacts_robot_robot=sum(1 for e in world.contacts if e.kind == "robot-robot"),
        contacts_robot_human=sum(1 for e in world.contacts if e.kind == "robot-human"),
        contacts_robot_rack=sum(1 for e in world.contacts if e.kind == "robot-rack"),
        min_separation_m=round(seps[0], 3) if seps else 0.0,
        p05_separation_m=round(seps[max(0, int(0.05 * len(seps)))], 3) if seps else 0.0,
        robot_hours=round(robot_hours, 5),
        deadlocks_detected=int(agg("deadlocks_detected")),
        retreats=int(agg("retreats")),
        yields=int(agg("yields")),
        replans=int(agg("replans")),
        safety_stop_ticks=int(agg("safety_stops")),
        seconds_degraded=round(agg("seconds_degraded") / max(1, n), 1),
        msgs_sent=int(agg("msgs_sent")),
        bytes_sent=int(agg("bytes_sent")),
        msgs_per_robot_s=round(agg("msgs_sent") / n / sim_s, 2) if sim_s else 0.0,
        bytes_per_robot_s=round(agg("bytes_sent") / n / sim_s, 1) if sim_s else 0.0,
        plan_cpu_total_s=round(plan_cpu, 4),
        plan_calls=plan_calls,
        plan_cpu_mean_ms=round(plan_cpu / plan_calls * 1000, 3) if plan_calls else 0.0,
        plan_cpu_max_ms=round(plan_max * 1000, 3),
        net_loss=cfg.net.loss,
        manager_killed_at=sc.kill_manager_at,
    )


def run_for_dashboard(scenario: str, policy: str, robots: int | None = None,
                      seed: int = 0, duration: float | None = None,
                      policy_model=None) -> dict:
    """One run, packaged for the web dashboard: map, every frame, and the summary.

    Playback rather than a live stream, deliberately. The sim runs far faster than
    realtime, so streaming it would mean throttling it back down to wall-clock for no
    benefit; and a recorded run can be scrubbed, paused on the interesting frame and
    replayed against a different policy, which is what anyone evaluating this actually
    wants to do.
    """
    kw: dict = {"seed": seed}
    if robots is not None:
        kw["n_robots"] = robots
    sc = SCENARIOS[scenario](**kw)
    if duration is not None:
        sc.duration_s = float(duration)

    frames: list = []
    result = run_scenario(sc, policy, seed=seed, trace=frames,
                          policy_model=policy_model)
    return {
        "map": sc.env.to_json(),
        "meta": {
            "scenario": sc.name, "policy": policy, "seed": seed,
            "robots": sc.n_robots, "duration_s": sc.duration_s,
            "tasks": sc.n_tasks if not sc.use_auction else len(sc.unassigned),
            "humans": len(sc.humans),
            "kill_manager_at": sc.kill_manager_at,
            "cell_m": DEFAULT.cell_m,
            "has_manager": policy in MANAGED_POLICIES,
            # So the dashboard can say WHICH model produced a run. A BIOS_4 result with
            # no model behind it is an untrained control, not a policy, and the two must
            # never be confused on screen.
            "model": (policy_model.to_dict().get("meta") if policy_model is not None
                      else None),
        },
        "frames": frames,
        "summary": result.to_dict(),
    }


# ---------------------------------------------------------------------- CLI


def _fmt(r: PolicyResult) -> str:
    ok = "done" if r.completed_all else "TIMEOUT"
    return (f"{r.policy:<14} {r.scenario:<20} seed={r.seed} "
            f"{ok:>7} makespan={r.makespan_s:7.1f}s "
            f"tasks={r.tasks_completed}/{r.tasks_announced} "
            f"rr={r.contacts_robot_robot} rh={r.contacts_robot_human} "
            f"minsep={r.min_separation_m:.2f}m "
            f"dead={r.deadlocks_detected} msg/rob/s={r.msgs_per_robot_s:.1f}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="sih-fleet-sim",
        description="Headless AMR fleet simulation (SIH26123).")
    ap.add_argument("--scenario", default="crossing_chokepoint",
                    choices=sorted(SCENARIOS))
    ap.add_argument("--policy", default="all",
                    choices=sorted(POLICIES) + ["all"])
    ap.add_argument("--robots", type=int, default=None)
    ap.add_argument("--seeds", type=int, default=1,
                    help="run seeds 0..N-1 and pool them for the safety statistics")
    ap.add_argument("--loss", type=float, default=None,
                    help="override uniform packet loss probability")
    ap.add_argument("--json", metavar="PATH", default=None,
                    help="write full results as JSON")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    policies = list(POLICIES) if args.policy == "all" else [args.policy]
    by_policy: dict[str, list[PolicyResult]] = {}

    for policy in policies:
        runs = []
        for seed in range(args.seeds):
            kw = {"seed": seed}
            if args.robots is not None:
                kw["n_robots"] = args.robots
            sc = SCENARIOS[args.scenario](**kw)
            if args.loss is not None:
                sc.net = replace(sc.net, loss=args.loss)
            r = run_scenario(sc, policy, seed=seed, verbose=args.verbose)
            runs.append(r)
            print(_fmt(r))
        by_policy[policy] = runs

    print()
    for policy, runs in by_policy.items():
        rep = safety_report(runs)
        print(f"SAFETY  {policy:<14} {rep['robot_robot_contacts']} robot-robot and "
              f"{rep['robot_human_contacts']} robot-human contacts in "
              f"{rep['robot_hours']:.3f} robot-hours")
        print(f"        upper 95% bound: {rep['rr_upper95_per_1000_robot_hours']} "
              f"robot-robot per 1000 robot-hours "
              f"(worst separation {rep['worst_separation_m']} m)")

    if POLICY_STOP_WAIT in by_policy:
        print()
        for cand in (POLICY_CENTRAL, POLICY_HIERARCHICAL, POLICY_BIOS, POLICY_BIOS4):
            if cand in by_policy:
                c = compare(by_policy[POLICY_STOP_WAIT], by_policy[cand])
                print(f"VS STOP-AND-WAIT  {cand}: {json.dumps(c)}")
    if POLICY_CENTRAL in by_policy and POLICY_HIERARCHICAL in by_policy:
        c = compare(by_policy[POLICY_CENTRAL], by_policy[POLICY_HIERARCHICAL])
        print(f"VS CENTRAL RESERVATION  hierarchical: {json.dumps(c)}")

    if args.json:
        payload = {p: [r.to_dict() for r in runs] for p, runs in by_policy.items()}
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
