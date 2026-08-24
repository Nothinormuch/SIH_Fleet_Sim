"""Reproducible SIH success-criterion benchmark and release gate.

This runner pairs policies by fleet size, seed and a cryptographic workload identity.
It refuses partial/mismatched comparisons and treats a stop-and-wait timeout as a
right-censored lower bound rather than as a fabricated makespan.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from .amr import POLICIES, POLICY_BIOS_PIBT_V3, POLICY_STOP_WAIT
from .main import run_scenario
from .metrics import PolicyResult, compare_paired, safety_report
from .scenarios import SCENARIOS
from .task_allocation import (ALLOCATION_AUCTION, ALLOCATION_POLICIES,
                              ALLOCATION_PREASSIGNED)


def parse_robot_counts(value: str) -> list[int]:
    counts = []
    for part in value.split(","):
        try:
            count = int(part.strip())
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"invalid robot count {part!r}") from exc
        if count < 3:
            raise argparse.ArgumentTypeError(
                "SIH acceptance fleets must contain at least three robots")
        if count not in counts:
            counts.append(count)
    if not counts:
        raise argparse.ArgumentTypeError("at least one robot count is required")
    return counts


def _git_commit() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], check=True, capture_output=True,
            text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _source_tree_dirty() -> bool | None:
    """Whether benchmark-relevant source/docs differ from the recorded HEAD."""
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal", "--",
             "src", "tests", "benchmark.py", "README.md", "docs"],
            check=True, capture_output=True, text=True, timeout=5).stdout
        return bool(status.strip())
    except (OSError, subprocess.SubprocessError):
        return None


def _allocation_arg(value: str) -> str | None:
    return None if value == ALLOCATION_PREASSIGNED else value


def _run_policy_pair(args: tuple) -> tuple[PolicyResult, PolicyResult]:
    """Pickle-safe worker: build fresh scenarios and run one paired seed."""
    (scenario_name, robots, seed, duration_s, baseline_policy,
     candidate_policy, allocation_policy) = args
    baseline_scenario = SCENARIOS[scenario_name](n_robots=robots, seed=seed)
    candidate_scenario = SCENARIOS[scenario_name](n_robots=robots, seed=seed)
    if duration_s is not None:
        baseline_scenario.duration_s = float(duration_s)
        candidate_scenario.duration_s = float(duration_s)
    normalized_allocation = _allocation_arg(allocation_policy)
    baseline = run_scenario(
        baseline_scenario, baseline_policy, seed=seed,
        allocation_policy=normalized_allocation)
    candidate = run_scenario(
        candidate_scenario, candidate_policy, seed=seed,
        allocation_policy=normalized_allocation)
    return baseline, candidate


def run_acceptance_benchmark(
    *,
    scenario_name: str = "sih_acceptance_overlap",
    robot_counts: list[int] | None = None,
    seeds: int = 30,
    first_seed: int = 0,
    duration_s: float | None = None,
    baseline_policy: str = POLICY_STOP_WAIT,
    candidate_policy: str = POLICY_BIOS_PIBT_V3,
    allocation_policy: str = ALLOCATION_AUCTION,
    threshold_pct: float = 20.0,
    bootstrap_samples: int = 5000,
    jobs: int = 1,
    verbose: bool = True,
) -> dict:
    """Run and return the complete versioned acceptance evidence payload."""
    if scenario_name not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario_name!r}")
    if baseline_policy not in POLICIES or candidate_policy not in POLICIES:
        raise ValueError("baseline and candidate must be registered route policies")
    if allocation_policy not in ALLOCATION_POLICIES:
        raise ValueError(f"unknown allocation policy {allocation_policy!r}")
    if seeds <= 0:
        raise ValueError("seed count must be positive")
    if bootstrap_samples <= 0:
        raise ValueError("bootstrap sample count must be positive")
    if jobs <= 0:
        raise ValueError("jobs must be positive")
    robot_counts = robot_counts or [4, 6, 8]
    if any(count < 3 for count in robot_counts):
        raise ValueError("every fleet must contain at least three robots")

    started = time.perf_counter()
    fleets: dict[str, dict] = {}
    all_pass = True

    for robots in robot_counts:
        baseline_runs: list[PolicyResult] = []
        candidate_runs: list[PolicyResult] = []
        worker_args = [
            (scenario_name, robots, seed, duration_s, baseline_policy,
             candidate_policy, allocation_policy)
            for seed in range(first_seed, first_seed + seeds)
        ]

        if jobs == 1:
            completed_pairs = [
                _run_policy_pair(arguments) for arguments in worker_args
            ]
            iterator = enumerate(completed_pairs, start=1)
        else:
            executor = ProcessPoolExecutor(max_workers=jobs)
            future_to_seed = {
                executor.submit(_run_policy_pair, arguments): arguments[2]
                for arguments in worker_args
            }

            def completed():
                count = 0
                try:
                    for future in as_completed(future_to_seed):
                        count += 1
                        yield count, future.result()
                finally:
                    executor.shutdown(wait=True, cancel_futures=True)

            iterator = completed()

        for completed_count, (baseline, candidate) in iterator:
            baseline_runs.append(baseline)
            candidate_runs.append(candidate)
            if verbose:
                base_state = (f"{baseline.makespan_s:.1f}s" if baseline.completed_all
                              else f"censored@{baseline.sim_seconds:.1f}s")
                cand_state = (f"{candidate.makespan_s:.1f}s"
                              if candidate.completed_all
                              else f"timeout {candidate.tasks_completed}/"
                                   f"{candidate.tasks_announced}")
                print(
                    f"fleet={robots:>2} seed={baseline.seed:>3} "
                    f"baseline={base_state} candidate={cand_state} "
                    f"[{completed_count}/{seeds}]",
                    flush=True)
        baseline_runs.sort(key=lambda result: result.seed)
        candidate_runs.sort(key=lambda result: result.seed)

        comparison = compare_paired(
            baseline_runs, candidate_runs, threshold_pct=threshold_pct,
            bootstrap_samples=bootstrap_samples)
        all_pass = all_pass and comparison.get("verdict") == "pass"
        fleets[str(robots)] = {
            "comparison": comparison,
            "baseline_safety": safety_report(baseline_runs),
            "candidate_safety": safety_report(candidate_runs),
            "baseline_runs": [result.to_dict() for result in baseline_runs],
            "candidate_runs": [result.to_dict() for result in candidate_runs],
        }

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "source_tree_dirty": _source_tree_dirty(),
        "scenario": scenario_name,
        "robot_counts": robot_counts,
        "first_seed": first_seed,
        "seeds_per_fleet": seeds,
        "baseline_policy": baseline_policy,
        "candidate_policy": candidate_policy,
        "allocation_policy": allocation_policy,
        "duration_override_s": duration_s,
        "threshold_pct": threshold_pct,
        "bootstrap_samples": bootstrap_samples,
        "jobs": jobs,
        "verdict": "pass" if all_pass else "fail",
        "wall_time_s": round(time.perf_counter() - started, 3),
        "fleets": fleets,
    }


def write_json(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_csv(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "robots", "role", "policy", "allocation_policy", "scenario", "seed",
        "workload_id", "completed_all", "tasks_completed", "tasks_announced",
        "makespan_s", "sim_seconds", "contacts_robot_robot",
        "contacts_robot_human", "contacts_robot_rack", "robot_hours",
        "deadlocks_detected", "retreats", "yields", "replans",
        "msgs_per_robot_s", "bytes_per_robot_s", "plan_cpu_mean_ms",
        "plan_cpu_max_ms", "net_loss",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for robots, fleet in payload["fleets"].items():
            for role in ("baseline", "candidate"):
                for result in fleet[f"{role}_runs"]:
                    row = {name: result.get(name) for name in fields}
                    row["robots"] = int(robots)
                    row["role"] = role
                    writer.writerow(row)


def _print_summary(payload: dict) -> None:
    print("\nSIH ACCEPTANCE SUMMARY")
    for robots, fleet in payload["fleets"].items():
        comparison = fleet["comparison"]
        bound = comparison.get("minimum_reduction_lower_bound_pct")
        bound_text = f"{bound:.2f}%" if isinstance(bound, (int, float)) else "n/a"
        print(
            f"  robots={robots:<3} verdict={comparison.get('verdict'):<10} "
            f"candidate={comparison.get('candidate_runs_completed')} "
            f"baseline={comparison.get('baseline_runs_completed')} "
            f"minimum reduction bound={bound_text}")
        if comparison.get("reason"):
            print(f"    reason: {comparison['reason']}")
    print(f"\nOVERALL: {payload['verdict'].upper()}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sih-acceptance-benchmark",
        description="Strict paired SIH completion-time and safety release gate")
    parser.add_argument("--scenario", default="sih_acceptance_overlap",
                        choices=sorted(SCENARIOS))
    parser.add_argument("--robots", type=parse_robot_counts, default=[4, 6, 8],
                        help="comma-separated fleet sizes (default: 4,6,8)")
    parser.add_argument("--seeds", type=int, default=30)
    parser.add_argument("--first-seed", type=int, default=0)
    parser.add_argument("--duration", type=float, default=None,
                        help="override the pinned scenario cutoff")
    parser.add_argument("--baseline", default=POLICY_STOP_WAIT,
                        choices=sorted(POLICIES))
    parser.add_argument("--candidate", default=POLICY_BIOS_PIBT_V3,
                        choices=sorted(POLICIES))
    parser.add_argument("--allocation-policy", default=ALLOCATION_AUCTION,
                        choices=sorted(ALLOCATION_POLICIES))
    parser.add_argument("--threshold", type=float, default=20.0)
    parser.add_argument("--bootstrap-samples", type=int, default=5000)
    parser.add_argument("--jobs", type=int, default=1,
                        help="parallel seed workers (default: 1)")
    parser.add_argument("--json", type=Path,
                        default=Path("artifacts/benchmarks/sih-acceptance.json"))
    parser.add_argument("--csv", type=Path,
                        default=Path("artifacts/benchmarks/sih-acceptance.csv"))
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    try:
        payload = run_acceptance_benchmark(
            scenario_name=args.scenario,
            robot_counts=args.robots,
            seeds=args.seeds,
            first_seed=args.first_seed,
            duration_s=args.duration,
            baseline_policy=args.baseline,
            candidate_policy=args.candidate,
            allocation_policy=args.allocation_policy,
            threshold_pct=args.threshold,
            bootstrap_samples=args.bootstrap_samples,
            jobs=args.jobs,
            verbose=not args.quiet,
        )
    except ValueError as exc:
        parser.error(str(exc))
    write_json(payload, args.json)
    write_csv(payload, args.csv)
    _print_summary(payload)
    print(f"\nJSON: {args.json}")
    print(f"CSV:  {args.csv}")
    return 0 if payload["verdict"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
