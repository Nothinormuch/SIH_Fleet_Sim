"""The headless simulation runner: one scenario, one policy, one seed, one result.

This is the batch path. It runs the identical `AMRBrain` objects that the distributed
UDP demo runs, against a seeded network model instead of real sockets, at a few hundred
times realtime. That speed is not a convenience - it is what makes the safety claim
expressible as a rate with a confidence interval instead of "we watched it for a while
and nothing happened".

The loop is a fixed-step 50 Hz integration, and the ordering inside each tick matters:

    1. scripted world events   (kill the manager, split the network)
    2. WMS announcements       (allocation policies, or auction workloads)
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
import sys
from dataclasses import replace

from . import messages as msg
from .amr import (AMRBrain, POLICIES, POLICY_HIERARCHICAL, POLICY_CENTRAL,
                  POLICY_STOP_WAIT, POLICY_BIOS, POLICY_BIOS_PIBT,
                  POLICY_BIOS_PIBT_V2, POLICY_BIOS_PIBT_V3, POLICY_BIOS_PIBT_V5,
                  POLICY_DECENTRALIZED, POLICY_BIOS4,
                  PIBT_POLICIES, Task)
from .fleet_manager import FleetManager, MANAGER_ID
from .metrics import PolicyResult, compare, safety_report
from .scenarios import SCENARIOS, Scenario, workload_fingerprint
from .settings import Config, DEFAULT
from .task_allocation import (ALLOCATION_AUCTION, ALLOCATION_HUNGARIAN,
                               ALLOCATION_POLICIES, ALLOCATION_PREASSIGNED,
                               ACTIVE_ALLOCATION_POLICIES,
                               validate_allocation_policy)
from .transport import SimNetwork
from .world import Actuation, World

WMS_ID = "WMS"
AUCTION_MESSAGE_TYPES = (msg.TASK_NEW, msg.BID, msg.AWARD, msg.TASK_DONE)


def _resolve_allocation_policy(sc: Scenario,
                               allocation_policy: str | None) -> str | None:
    """Resolve legacy scenario behavior while keeping the public choice separate."""
    if allocation_policy is None and sc.use_auction:
        allocation_policy = ALLOCATION_AUCTION
    validate_allocation_policy(allocation_policy)
    if allocation_policy == ALLOCATION_PREASSIGNED:
        return None
    return allocation_policy


def _announced_tasks(sc: Scenario, allocation_policy: str | None) -> list[Task]:
    """Return the workload that this policy must allocate.

    Scenarios normally contain round-robin queues so route policies can be compared
    without task allocation affecting the result. An explicit allocation policy ignores
    those queues and announces the same tasks to the WMS/robots instead. The old
    `use_auction` scenario flag is supported only as a backwards-compatible default.
    """
    if allocation_policy in ACTIVE_ALLOCATION_POLICIES:
        if sc.unassigned:
            return list(sc.unassigned)
        return [task for queue in sc.assignments for task in queue]
    return []


def _auction_event(message: msg.Message) -> dict:
    """Expose the actual auction datagram in dashboard telemetry."""
    body = message.body
    event = {
        "type": message.type,
        "src": message.src,
        "seq": message.seq,
        "t": round(message.t, 3),
    }
    for key in ("task", "cost", "e", "dl", "u", "dst", "winner"):
        if key in body:
            event[key] = body[key]
    return event

# Which policies are served by a fleet manager at all. Kept here as one tuple because
# two places need the answer - the runner, which decides whether to build a manager,
# and the dashboard payload, which has to tell the UI whether a missing manager is a
# failure or the design. A policy absent from this tuple is peer-to-peer by intent.
MANAGED_POLICIES = (POLICY_CENTRAL, POLICY_HIERARCHICAL)


def run_scenario(sc: Scenario, policy: str, seed: int = 0,
                 cfg: Config | None = None, trace: list | None = None,
                 verbose: bool = False,
                 allocation_policy: str | None = None, policy_model=None) -> PolicyResult:
    """Run one (scenario, policy, seed) and return everything it produced.

    Pass a list as `trace` to collect per-frame snapshots for the dashboard; leave it
    None for benchmark runs, where appending 30 000 frames would dominate the runtime.
    """
    if policy not in POLICIES:
        raise ValueError(f"unknown policy {policy!r}")
    allocation_policy = _resolve_allocation_policy(sc, allocation_policy)

    cfg = cfg or DEFAULT
    cfg = replace(cfg, net=sc.net, seed=seed)
    workload_id = workload_fingerprint(sc, cfg, allocation_policy)
    dt = 1.0 / cfg.rates.world_hz

    world = World(sc.env, cfg, seed=seed)
    net = SimNetwork(cfg, seed=seed)
    net.register(WMS_ID)
    announced_tasks = _announced_tasks(sc, allocation_policy)
    uses_allocation = bool(announced_tasks)

    brains: dict[str, AMRBrain] = {}
    for i, start in enumerate(sc.starts):
        rid = f"AMR{i + 1:02d}"
        robot_state = world.add_robot(rid, start, 0.0)
        if i < len(sc.initial_battery_fracs):
            robot_state.battery_wh = (
                max(0.0, min(1.0, sc.initial_battery_fracs[i]))
                * cfg.robot.battery_full_wh)
        b = AMRBrain(rid, sc.env, cfg, policy=policy, home=start,
                     allocation_policy=allocation_policy, policy_model=policy_model)
        b.queue = ([] if uses_allocation else
                   list(sc.assignments[i]) if i < len(sc.assignments) else [])
        if hasattr(b, 'use_auction'):
            b.use_auction = sc.use_auction
        brains[rid] = b
        net.register(rid)

    for j, walk in enumerate(sc.humans):
        world.add_human(f"H{j + 1}", walk)

    # The route policy may need a manager for space-time plans, while Hungarian needs
    # one only for task assignment. These responsibilities are intentionally separate.
    manager = None
    if policy in (POLICY_CENTRAL, POLICY_HIERARCHICAL) \
            or allocation_policy == ALLOCATION_HUNGARIAN:
        manager_allocation = (ALLOCATION_HUNGARIAN
                              if allocation_policy == ALLOCATION_HUNGARIAN else None)
        manager = FleetManager(sc.env, cfg,
                               allocation_policy=manager_allocation,
                               route_planning=policy in (
                                   POLICY_CENTRAL, POLICY_HIERARCHICAL))
        net.register(MANAGER_ID)

    total_tasks = len(announced_tasks) if uses_allocation else sc.n_tasks
    next_wms_announcement = 0.0
    makespan = None
    steps = int(sc.duration_s / dt)
    seq = 0
    auction_events: list[dict] = []
    failed_nodes: set[str] = set()
    triggered_failures: set[str] = set()
    activated_obstacles: set[str] = set()
    cleared_obstacles: set[str] = set()

    def capture_trace_frame() -> None:
        """Append one self-consistent telemetry frame, including unique progress."""
        nonlocal auction_events
        if trace is None:
            return
        snap = world.snapshot()
        snap["fleet"] = [
            {"id": rid, "state": brain.state, "mode": brain.mode,
             "task": brain.task.tid if brain.task else None,
             "goal": list(brain.goal) if brain.goal else None,
             "pick": list(brain.task.pick) if brain.task else None,
             "drop": list(brain.task.drop) if brain.task else None,
             "cargo_type": brain.task.cargo_type if brain.task else None,
             "cargo_weight": brain.task.cargo_weight if brain.task else None,
             "task_priority": brain.task.priority if brain.task else None,
             "deadline": brain.task.deadline if brain.task else None,
             "carry": (brain.task.tid
                       if brain.task and brain.goal == brain.task.drop else None),
             "path": [list(cell) for cell in
                      brain.path[brain.pidx:brain.pidx + 8]],
             "peers": sorted(brain.peers.keys()),
             "blocked_on": brain.blocked_on,
             "priority_key": (brain._pub_priority_key.to_wire()
                              if brain.policy in PIBT_POLICIES else None),
             "done": len(brain.completed), "failed": rid in failed_nodes}
            for rid, brain in sorted(brains.items())
        ]
        snap["manager_alive"] = bool(manager and manager.alive)
        snap["tasks_completed"] = len({
            tid for brain in brains.values()
            for tid, _started, _finished in brain.completed
        })
        snap["auction_events"] = auction_events
        auction_events = []
        trace.append(snap)

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
        for rid, fail_at in sc.robot_fail_at.items():
            if t >= fail_at and rid not in triggered_failures:
                failed_nodes.add(rid)
                triggered_failures.add(rid)
                if verbose:
                    print(f"  [t={t:6.1f}] {rid} failed", file=sys.stderr)
        for rid, restart_at in sc.robot_restart_at.items():
            if t >= restart_at and rid in failed_nodes and rid in brains:
                index = int(rid.removeprefix("AMR")) - 1
                failed_nodes.remove(rid)
                brains[rid] = AMRBrain(
                    rid, sc.env, cfg, policy=policy,
                    home=sc.starts[index], allocation_policy=allocation_policy)
                # An auction node reconstructs its catalog from peer gossip after a
                # restart. Pre-assigned work has no distributed owner record, so its
                # static queue is restored explicitly for that comparison mode.
                if not uses_allocation:
                    brains[rid].queue = list(sc.assignments[index])
                if verbose:
                    print(f"  [t={t:6.1f}] {rid} restarted", file=sys.stderr)
        for event in sc.obstacles:
            if event.oid not in activated_obstacles and t >= event.appear_at:
                world.add_obstacle(event.oid, event.cell, event.radius_m)
                activated_obstacles.add(event.oid)
            if (event.clear_at is not None and event.oid not in cleared_obstacles
                    and t >= event.clear_at):
                world.remove_obstacle(event.oid)
                cleared_obstacles.add(event.oid)

        if uses_allocation and t >= next_wms_announcement:
            # TASK_NEW is an idempotent catalog announcement, not a one-shot command.
            # Repeating it is essential: if every robot loses the first multicast,
            # peer gossip has no copy from which to repair the missing task. Existing
            # or completed tasks ignore the same-epoch repeat.
            next_wms_announcement = t + 2.0 * cfg.traffic.task_gossip_period_s
            for tk in announced_tasks:
                seq += 1
                announcement = msg.task_new(
                    WMS_ID, seq, t, tk.tid, tk.pick, tk.drop, epoch=0,
                    bid_until=t + cfg.traffic.auction_bid_window_s,
                    cargo_type=tk.cargo_type, cargo_weight=tk.cargo_weight,
                    priority=tk.priority, deadline=tk.deadline)
                net.send(t, WMS_ID, announcement)
                if trace is not None:
                    auction_events.append(_auction_event(announcement))

        if manager is not None:
            out = manager.step(t, net.poll(t, MANAGER_ID))
            for m in out:
                net.send(t, MANAGER_ID, m)
                if trace is not None and m.type in AUCTION_MESSAGE_TYPES:
                    auction_events.append(_auction_event(m))

        cmds = {}
        for rid in sorted(brains):
            st = world.robots[rid]
            net.set_position(rid, (st.x / cfg.cell_m, st.y / cfg.cell_m))
            if rid in failed_nodes:
                # A failed chassis remains a stopped physical obstacle, while its
                # process and radio are silent. Packets due during downtime are lost.
                net.poll(t, rid)
                cmds[rid] = Actuation(v=0.0, omega=0.0, safety_stop=True)
                st.carrying = None
                continue
            sensors = world.sense(rid, pose_noise_m=sc.pose_noise_m)
            act, outbox = brains[rid].step(t, sensors, net.poll(t, rid))
            for m in outbox:
                net.send(t, rid, m)
                if trace is not None and m.type in AUCTION_MESSAGE_TYPES:
                    auction_events.append(_auction_event(m))
            cmds[rid] = act
            # Payload is physically on the chassis ONLY after arrival at pick location (in to_drop state)
            b = brains[rid]
            st.carrying = b.task.tid if (b.task and b.goal == b.task.drop) else None

        world.step(dt, cmds)

        if trace is not None and k % int(cfg.rates.world_hz /
                                         cfg.rates.telemetry_hz) == 0:
            capture_trace_frame()

        # A lossy peer auction may temporarily create duplicate executors. Completion
        # is a property of a task ID, not of an executor, so replicated completions
        # count once. Summing per-robot lists could declare 16/16 while a distinct
        # task was still visibly in flight.
        done = len({tid for b in brains.values() for tid, _start, _end in b.completed})
        if total_tasks and done >= total_tasks:
            if makespan is None:
                makespan = t
            # A playback run must include the completion frame. Otherwise the summary
            # says 16/16 while the final visible frame still shows 15/16 and a carried
            # task. Capture it immediately, including when completion lands on the
            # final physics tick of the configured evidence window.
            if trace is not None and (
                    not trace or trace[-1].get("tasks_completed", 0) < total_tasks):
                capture_trace_frame()
            break

    world.finalize()
    return _summarize(sc, policy, allocation_policy, seed, cfg, world, net,
                      brains, manager, total_tasks, makespan, workload_id)


def _summarize(sc, policy, allocation_policy, seed, cfg, world, net, brains,
               manager, total_tasks, makespan, workload_id) -> PolicyResult:
    sim_s = world.t
    n = len(brains)
    robot_hours = n * sim_s / 3600.0
    completion_times: dict[str, float] = {}
    for brain in brains.values():
        for tid, started, finished in brain.completed:
            duration = finished - started
            completion_times[tid] = min(completion_times.get(tid, duration), duration)
    task_times = list(completion_times.values())
    done = len(completion_times)
    seps = sorted(world.min_separations)

    def agg(key: str) -> float:
        return sum(b.stats[key] for b in brains.values())

    plan_cpu = agg("plan_cpu_s") + (manager.stats["plan_cpu_s"] if manager else 0.0)
    plan_calls = int(agg("plan_calls")) + (manager.stats["plans"] if manager else 0)
    plan_max = max([b.stats["plan_cpu_max_s"] for b in brains.values()] +
                   [manager.stats["plan_cpu_max_s"] if manager else 0.0])

    return PolicyResult(
        policy=policy, allocation_policy=allocation_policy,
        scenario=sc.name, seed=seed, workload_id=workload_id,
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
        progress_cells=int(agg("progress_cells")),
        bios4_unstick=int(agg("bios4_unstick")),
        deadlocks_detected=int(agg("deadlocks_detected")),
        retreats=int(agg("retreats")),
        yields=int(agg("yields")),
        replans=int(agg("replans")),
        dynamic_obstacles_detected=int(agg("dynamic_obstacles_detected")),
        dynamic_reroutes=int(agg("dynamic_reroutes")),
        task_reassignments=int(agg("task_reassignments")),
        auction_bids_sent=int(agg("auction_bids_sent")),
        energy_bids_suppressed=int(agg("energy_bids_suppressed")),
        energy_no_eligible_rounds=int(agg("energy_no_eligible_rounds")),
        safety_stop_ticks=int(agg("safety_stops")),
        human_yield_ticks=sum(h.yield_ticks for h in world.humans.values()),
        seconds_degraded=round(agg("seconds_degraded") / max(1, n), 1),
        msgs_sent=int(agg("msgs_sent")),
        bytes_sent=int(agg("bytes_sent")),
        msgs_per_robot_s=round(agg("msgs_sent") / n / sim_s, 2) if sim_s else 0.0,
        bytes_per_robot_s=round(agg("bytes_sent") / n / sim_s, 1) if sim_s else 0.0,
        plan_cpu_total_s=round(plan_cpu, 4),
        plan_calls=plan_calls,
        plan_cpu_mean_ms=round(plan_cpu / plan_calls * 1000, 3) if plan_calls else 0.0,
        plan_cpu_max_ms=round(plan_max * 1000, 3),
        priority_decisions=int(agg("priority_decisions")),
        priority_inheritances=int(agg("priority_inheritances")),
        priority_backtracks=int(agg("priority_backtracks")),
        priority_forced_moves=int(agg("priority_forced_moves")),
        priority_waits=int(agg("priority_waits")),
        net_loss=cfg.net.loss,
        manager_killed_at=sc.kill_manager_at,
        robot_failures=len(sc.robot_fail_at),
    )


def run_for_dashboard(scenario: str, policy: str, robots: int | None = None,
                      seed: int = 0, duration: float | None = None,
                      allocation_policy: str = ALLOCATION_AUCTION, policy_model=None,
                      **extra) -> dict:
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
    if scenario.startswith("custom_"):
        from src.scenarios import Scenario
        from src.geometry import Cell
        # Read injected custom info from the request (set by backend server)
        custom_env = extra.get("_custom_env")
        custom_starts_raw = extra.get("_custom_starts", [])
        custom_duration = extra.get("_custom_duration")
        custom_seed = extra.get("_custom_seed", seed)
        starts = [Cell(s[0], s[1]) for s in custom_starts_raw]
        if custom_env is None:
            raise ValueError(f"custom scenario {scenario} missing environment data")
        sc = Scenario(
            name=custom_env.name,
            env=custom_env,
            starts=starts,
            assignments=[[] for _ in starts],
            duration_s=float(custom_duration or 180.0),
            seed=custom_seed,
        )
    else:
        sc = SCENARIOS[scenario](**kw)
    if duration is not None and not scenario.startswith("custom_"):
        sc.duration_s = float(duration)

    frames: list = []
    result = run_scenario(sc, policy, seed=seed, trace=frames,
                          allocation_policy=allocation_policy, policy_model=policy_model)
    return {
        "map": sc.env.to_json(),
        "meta": {
            "scenario": sc.name, "policy": policy,
            "allocation_policy": allocation_policy, "seed": seed,
            "robots": sc.n_robots, "duration_s": sc.duration_s,
            "tasks": len(_announced_tasks(sc, allocation_policy)) or sc.n_tasks,
            "humans": len(sc.humans),
            "kill_manager_at": sc.kill_manager_at,
            "cell_m": DEFAULT.cell_m,
            "pose_units": "metres",
            "robot_diameter_m": 2.0 * DEFAULT.robot.radius_m,
            "has_manager": policy in (POLICY_CENTRAL, POLICY_HIERARCHICAL),
            "dead_zones": [list(zone) for zone in sc.net.dead_zones],
            "energy_reserve_frac": DEFAULT.traffic.energy_reserve_frac,
            "energy_uncertainty_frac": DEFAULT.traffic.energy_uncertainty_frac,
            "tasks_catalog": [
                {
                    "id": task.tid,
                    "pick": list(task.pick),
                    "drop": list(task.drop),
                    "cargo_type": task.cargo_type,
                    "cargo_weight": task.cargo_weight,
                    "priority": task.priority,
                    "deadline": task.deadline,
                }
                for task in _announced_tasks(sc, allocation_policy)
            ],
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
    alloc = r.allocation_policy or "preassigned"
    return (f"route={r.policy:<14} alloc={alloc:<10} {r.scenario:<20} seed={r.seed} "
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
    ap.add_argument("--policy", default=POLICY_BIOS_PIBT_V5,
                    choices=sorted(POLICIES) + ["all"],
                    help="route/traffic policy")
    ap.add_argument("--allocation-policy", choices=sorted(ALLOCATION_POLICIES),
                    default=None,
                    help=("task allocator: decentralized auction, Hungarian "
                          "comparison, or preassigned workload"))
    ap.add_argument("--robots", type=int, default=None)
    ap.add_argument("--duration", type=float, default=None,
                    help="override the scenario duration in simulated seconds")
    ap.add_argument("--seed", type=int, default=0,
                    help="first deterministic seed (default: 0)")
    ap.add_argument("--seeds", type=int, default=1,
                    help="number of consecutive seeds to run and pool")
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
        for seed in range(args.seed, args.seed + args.seeds):
            kw = {"seed": seed}
            if args.robots is not None:
                kw["n_robots"] = args.robots
            sc = SCENARIOS[args.scenario](**kw)
            if args.duration is not None:
                sc.duration_s = args.duration
            if args.loss is not None:
                sc.net = replace(sc.net, loss=args.loss)
            r = run_scenario(sc, policy, seed=seed, verbose=args.verbose,
                             allocation_policy=args.allocation_policy)
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
        for cand in (POLICY_CENTRAL, POLICY_HIERARCHICAL, POLICY_BIOS,
                     POLICY_DECENTRALIZED, POLICY_BIOS_PIBT,
                     POLICY_BIOS_PIBT_V2, POLICY_BIOS_PIBT_V3,
                     POLICY_BIOS_PIBT_V5, POLICY_BIOS4):
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
