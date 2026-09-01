"""Deterministic three-way comparison for SIH problem statement 26123.

The incoming collaboration branch exposed two labels, but only its competition
stop-and-wait changes executed in the Python simulator. This campaign compares three
real execution paths in the same 50 Hz physics/world model:

* BIOS 6 with the released decentralized Auction V2 allocator;
* enhanced, non-cooperative stop-and-wait with static preassignment;
* centralized prioritized space-time A* with Hungarian task allocation.

Each architecture uses its intended allocator. Because allocator choice is part of
the independent variable, comparisons are paired by a separate task-catalog digest
rather than pretending their normal workload fingerprints are equal. A timeout is a
right-censored lower bound, never a fabricated makespan.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .amr import (POLICY_BIOS_PIBT_V6, POLICY_PRIORITIZED_SPACE_TIME,
                  POLICY_STOP_WAIT_COMPETITION)
from .main import run_scenario
from .metrics import PolicyResult, percentile
from .scenarios import (blocked_aisle, human_in_aisle, partition_recovery,
                        robot_failure_reassignment, showcase_open_floor,
                        sih_acceptance_overlap)
from .task_allocation import (ALLOCATION_AUCTION_BUNDLE, ALLOCATION_HUNGARIAN,
                              ALLOCATION_PREASSIGNED)

POLICY_CONFIGS = {
    "bios6_auction_v2": (POLICY_BIOS_PIBT_V6, ALLOCATION_AUCTION_BUNDLE),
    "stop_wait_competition": (
        POLICY_STOP_WAIT_COMPETITION, ALLOCATION_PREASSIGNED),
    "prioritized_space_time": (
        POLICY_PRIORITIZED_SPACE_TIME, ALLOCATION_HUNGARIAN),
}
HASH_SEEDS = (0, 1, 42)


def _build_case(case: str, robots: int, seed: int):
    if case == "sih_overlap":
        return sih_acceptance_overlap(
            n_robots=robots, tasks_per_robot=3, seed=seed)
    if case == "open_floor":
        return showcase_open_floor(
            n_robots=robots, tasks_per_robot=3, seed=seed)
    if case == "blocked_aisle":
        return blocked_aisle(n_robots=robots, tasks_per_robot=1, seed=seed)
    if case == "human_aisle":
        return human_in_aisle(n_robots=robots, tasks_per_robot=2, seed=seed)
    if case == "partition_heal":
        return partition_recovery(
            n_robots=robots, tasks_per_robot=1, seed=seed)
    if case == "robot_failure":
        return robot_failure_reassignment(
            n_robots=robots, tasks_per_robot=1, seed=seed)
    raise ValueError(f"unknown comparison case {case!r}")


def _catalog_tasks(scenario) -> list:
    by_id = {}
    for queue in scenario.assignments:
        for task in queue:
            by_id[task.tid] = task
    for task in scenario.unassigned:
        by_id[task.tid] = task
    return [by_id[tid] for tid in sorted(by_id)]


def _catalog_digest(scenario) -> str:
    payload = {
        "map": scenario.env.to_json(),
        "starts": [list(cell) for cell in scenario.starts],
        "tasks": [
            {
                "id": task.tid,
                "pick": list(task.pick),
                "drop": list(task.drop),
                "cargo_type": task.cargo_type,
                "cargo_weight": task.cargo_weight,
                "priority": task.priority,
                "deadline": task.deadline,
                "generation": task.generation,
            }
            for task in _catalog_tasks(scenario)
        ],
        "network": scenario.net.__dict__,
        "duration_s": scenario.duration_s,
        "failures": scenario.robot_fail_at,
        "restarts": scenario.robot_restart_at,
        "partition_at": scenario.partition_at,
        "heal_at": scenario.heal_at,
        "partition_groups": [sorted(group) for group in scenario.partition_groups],
    }
    wire = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(wire).hexdigest()


def _worker(spec: tuple[str, int, int, str]) -> tuple[str, int, str, str, PolicyResult]:
    case, robots, seed, config_name = spec
    scenario = _build_case(case, robots, seed)
    catalog = _catalog_digest(scenario)
    policy, allocation = POLICY_CONFIGS[config_name]
    if allocation == ALLOCATION_PREASSIGNED and scenario.unassigned:
        # The collaboration baseline declares static ownership. Auction-native
        # scenarios store the same catalog in ``unassigned`` instead of per-robot
        # queues, so deterministically preassign that catalog before execution.
        queues = [[] for _ in scenario.starts]
        for index, task in enumerate(sorted(
                scenario.unassigned, key=lambda item: item.tid)):
            queues[index % len(queues)].append(task)
        scenario.assignments = queues
        scenario.unassigned = []
        scenario.use_auction = False
    result = run_scenario(
        scenario, policy, seed=seed, allocation_policy=allocation)
    return case, robots, config_name, catalog, result


def _summary(rows: list[tuple[str, PolicyResult]]) -> dict:
    results = [result for _catalog, result in rows]
    completed = [result for result in results if result.completed_all]
    makespans = [result.makespan_s for result in completed]
    return {
        "runs": len(results),
        "completed_runs": len(completed),
        "completion_rate": round(len(completed) / len(results), 6),
        "tasks_completed": sum(result.tasks_completed for result in results),
        "tasks_announced": sum(result.tasks_announced for result in results),
        "median_makespan_s": (
            round(statistics.median(makespans), 3) if makespans else None),
        "p95_makespan_s": (
            round(percentile(makespans, 0.95), 3) if makespans else None),
        "robot_robot_contacts": sum(r.contacts_robot_robot for r in results),
        "robot_human_contacts": sum(r.contacts_robot_human for r in results),
        "robot_rack_contacts": sum(r.contacts_robot_rack for r in results),
        "deadlocks_detected": sum(r.deadlocks_detected for r in results),
        "deadline_misses": sum(r.deadline_misses for r in results),
        "task_reassignments": sum(r.task_reassignments for r in results),
        "dynamic_reroutes": sum(r.dynamic_reroutes for r in results),
        "messages_sent": sum(r.msgs_sent for r in results),
        "bytes_sent": sum(r.bytes_sent for r in results),
        "max_messages_per_robot_second": round(max(
            (r.msgs_per_robot_s for r in results), default=0.0), 3),
        "nonproductive_wait_ticks": sum(
            r.nonproductive_wait_ticks for r in results),
        "auction_bids_sent": sum(r.auction_bids_sent for r in results),
        "max_plan_cpu_ms": round(max(
            (r.plan_cpu_max_ms for r in results), default=0.0), 4),
        "max_allocation_p95_ms": round(max(
            (r.allocation_compute_p95_ms for r in results), default=0.0), 4),
        "raw_runs": [
            {"catalog_digest": catalog, **result.to_dict()}
            for catalog, result in sorted(rows, key=lambda row: row[1].seed)
        ],
    }


def _paired_architecture_comparison(
    baseline_rows: list[tuple[str, PolicyResult]],
    candidate_rows: list[tuple[str, PolicyResult]],
) -> dict:
    base = {result.seed: (catalog, result) for catalog, result in baseline_rows}
    cand = {result.seed: (catalog, result) for catalog, result in candidate_rows}
    if set(base) != set(cand):
        return {"verdict": "invalid", "reason": "seed sets differ"}

    pairs = []
    reductions = []
    invalid = []
    candidate_timeouts = []
    for seed in sorted(base):
        base_catalog, baseline = base[seed]
        cand_catalog, candidate = cand[seed]
        if base_catalog != cand_catalog:
            invalid.append(seed)
            continue
        if not candidate.completed_all:
            candidate_timeouts.append(seed)
            continue
        if baseline.completed_all:
            value = (
                (baseline.makespan_s - candidate.makespan_s)
                / baseline.makespan_s * 100.0
            )
            kind = "exact"
            baseline_value = baseline.makespan_s
        else:
            value = (
                (baseline.sim_seconds - candidate.makespan_s)
                / baseline.sim_seconds * 100.0
            )
            kind = "right_censored_lower_bound"
            baseline_value = baseline.sim_seconds
        reductions.append(value)
        pairs.append({
            "seed": seed,
            "kind": kind,
            "baseline_completed": baseline.completed_all,
            "baseline_time_or_cutoff_s": baseline_value,
            "candidate_makespan_s": candidate.makespan_s,
            "reduction_or_lower_bound_pct": round(value, 3),
            "catalog_digest": base_catalog,
        })

    verdict = "pass"
    reason = None
    if invalid:
        verdict, reason = "invalid", f"catalog mismatch at seeds {invalid}"
    elif candidate_timeouts:
        verdict, reason = "fail", f"candidate timeouts at seeds {candidate_timeouts}"
    elif not reductions:
        verdict, reason = "invalid", "no completed candidate pairs"
    elif min(reductions) < 20.0:
        verdict, reason = "fail", "minimum paired reduction is below 20%"
    return {
        "verdict": verdict,
        "reason": reason,
        "baseline_policy": baseline_rows[0][1].policy,
        "baseline_allocation": baseline_rows[0][1].allocation_policy,
        "candidate_policy": candidate_rows[0][1].policy,
        "candidate_allocation": candidate_rows[0][1].allocation_policy,
        "paired_runs": len(base),
        "baseline_completed_runs": sum(row[1].completed_all for row in baseline_rows),
        "candidate_completed_runs": sum(row[1].completed_all for row in candidate_rows),
        "minimum_reduction_or_bound_pct": (
            round(min(reductions), 3) if reductions else None),
        "median_reduction_or_bound_pct": (
            round(statistics.median(reductions), 3) if reductions else None),
        "pairs": pairs,
    }


_DETERMINISTIC_FIELDS = (
    "policy", "scenario", "seed", "allocation_policy", "sim_seconds",
    "robots", "tasks_completed", "tasks_announced", "makespan_s",
    "completed_all", "contacts_robot_robot", "contacts_robot_human",
    "contacts_robot_rack", "deadlocks_detected", "replans", "msgs_sent",
    "bytes_sent", "nonproductive_wait_ticks", "plan_calls",
    "task_reassignments", "dynamic_reroutes", "deadline_misses",
)


def _semantic_digest(result: PolicyResult) -> str:
    row = result.to_dict()
    payload = {field: row[field] for field in _DETERMINISTIC_FIELDS}
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _probe_subprocess(config_name: str, hash_seed: int) -> dict:
    environment = dict(os.environ)
    environment["PYTHONHASHSEED"] = str(hash_seed)
    completed = subprocess.run(
        [sys.executable, "-m", "src.baseline_comparison", "--probe", config_name],
        check=True, capture_output=True, text=True, timeout=180,
        env=environment,
    )
    return json.loads(completed.stdout)


def _determinism_gate() -> dict:
    rows = []
    passed = True
    for config_name in POLICY_CONFIGS:
        variants = [
            _probe_subprocess(config_name, hash_seed)
            for hash_seed in HASH_SEEDS
        ]
        case_passed = len({variant["digest"] for variant in variants}) == 1
        passed = passed and case_passed
        rows.append({
            "configuration": config_name,
            "hash_seeds": list(HASH_SEEDS),
            "digests": [variant["digest"] for variant in variants],
            "passed": case_passed,
            "semantic_result": variants[0]["result"],
        })
    return {"passed": passed, "probes": rows}


def _git_commit() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], check=True, capture_output=True,
            text=True, timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _source_tree_dirty() -> bool | None:
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain", "--", "src", "tests",
             "baseline_comparison.py", "backend", "frontend", "pyproject.toml"],
            check=True, capture_output=True, text=True, timeout=5,
        ).stdout
        return bool(status.strip())
    except (OSError, subprocess.SubprocessError):
        return None


def run_comparison_campaign(
    *, seeds: int = 30, fault_seeds: int = 10, first_seed: int = 0,
    jobs: int = 1, check_determinism: bool = True,
) -> dict:
    if seeds <= 0 or fault_seeds <= 0 or jobs <= 0:
        raise ValueError("seeds, fault_seeds and jobs must be positive")
    specs: list[tuple[str, int, int, str]] = []
    for robots in (4, 6, 8):
        for seed in range(first_seed, first_seed + seeds):
            for config_name in POLICY_CONFIGS:
                specs.append(("sih_overlap", robots, seed, config_name))
    supporting = (
        ("open_floor", 5),
        ("blocked_aisle", 3),
        ("human_aisle", 4),
        ("partition_heal", 4),
        ("robot_failure", 3),
    )
    for case, robots in supporting:
        for seed in range(first_seed, first_seed + fault_seeds):
            for config_name in POLICY_CONFIGS:
                specs.append((case, robots, seed, config_name))

    started = time.perf_counter()
    if jobs == 1:
        completed = [_worker(spec) for spec in specs]
    else:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            completed = list(pool.map(_worker, specs))

    grouped: dict[tuple[str, int, str], list[tuple[str, PolicyResult]]] = {}
    for case, robots, config_name, catalog, result in completed:
        grouped.setdefault((case, robots, config_name), []).append(
            (catalog, result))

    cases = {}
    comparisons = {}
    evaluated = (("sih_overlap", 4), ("sih_overlap", 6),
                 ("sih_overlap", 8), *supporting)
    for case, robots in evaluated:
        key = f"{case}:{robots}"
        cases[key] = {
            name: _summary(grouped[(case, robots, name)])
            for name in POLICY_CONFIGS
        }
        comparisons[key] = {
            "bios_vs_stop_wait_competition": _paired_architecture_comparison(
                grouped[(case, robots, "stop_wait_competition")],
                grouped[(case, robots, "bios6_auction_v2")]),
            "bios_vs_prioritized_space_time": _paired_architecture_comparison(
                grouped[(case, robots, "prioritized_space_time")],
                grouped[(case, robots, "bios6_auction_v2")]),
        }

    determinism = (
        _determinism_gate() if check_determinism else
        {"passed": None, "probes": [], "skipped": True}
    )
    failures = []
    for key in [f"sih_overlap:{robots}" for robots in (4, 6, 8)]:
        comparison = comparisons[key]["bios_vs_stop_wait_competition"]
        bios = cases[key]["bios6_auction_v2"]
        if comparison["verdict"] != "pass":
            failures.append(f"{key}: 20% completion-time gate did not pass")
        if bios["completed_runs"] != bios["runs"]:
            failures.append(f"{key}: BIOS did not complete every run")
        for field in ("robot_robot_contacts", "robot_human_contacts",
                      "robot_rack_contacts", "deadlocks_detected"):
            if bios[field] != 0:
                failures.append(f"{key}: BIOS {field}={bios[field]}")
    if determinism["passed"] is False:
        failures.append("deterministic replay gate failed")

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "problem_statement_id": 26123,
        "git_commit": _git_commit(),
        "source_tree_dirty": _source_tree_dirty(),
        "seeds_per_sih_fleet": seeds,
        "seeds_per_supporting_case": fault_seeds,
        "first_seed": first_seed,
        "jobs": jobs,
        "policy_configurations": {
            name: {"route_policy": policy, "allocation_policy": allocation}
            for name, (policy, allocation) in POLICY_CONFIGS.items()
        },
        "methodology": {
            "same_simulator": True,
            "same_catalog_digest_required_for_pairs": True,
            "allocator_is_part_of_architecture_under_test": True,
            "timeouts_are_right_censored": True,
            "physical_safety_certification": False,
        },
        "verdict": "pass" if not failures else "fail",
        "failures": failures,
        "wall_time_s": round(time.perf_counter() - started, 3),
        "determinism": determinism,
        "cases": cases,
        "comparisons": comparisons,
    }


def write_json(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=30)
    parser.add_argument("--fault-seeds", type=int, default=10)
    parser.add_argument("--first-seed", type=int, default=0)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--skip-determinism", action="store_true")
    parser.add_argument(
        "--output", type=Path,
        default=Path("artifacts/benchmarks/bios6-three-way-comparison.json"),
    )
    parser.add_argument(
        "--probe", choices=sorted(POLICY_CONFIGS), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.probe:
        _case, _robots, _config, catalog, result = _worker(
            ("open_floor", 5, 0, args.probe))
        print(json.dumps({
            "catalog_digest": catalog,
            "digest": _semantic_digest(result),
            "result": {field: getattr(result, field)
                       for field in _DETERMINISTIC_FIELDS},
        }, sort_keys=True))
        return 0

    payload = run_comparison_campaign(
        seeds=args.seeds, fault_seeds=args.fault_seeds,
        first_seed=args.first_seed, jobs=args.jobs,
        check_determinism=not args.skip_determinism,
    )
    write_json(payload, args.output)
    print(json.dumps({
        "verdict": payload["verdict"],
        "failures": payload["failures"],
        "wall_time_s": payload["wall_time_s"],
        "output": str(args.output),
    }, indent=2))
    return 0 if payload["verdict"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
