"""BIOS 6 Auction V2 deterministic release campaign.

The phrase "100% completion" is meaningful only after the workload, seeds, faults,
cutoffs, safety gates, and determinism check are fixed in code.  This module defines
that contract without changing a timeout, removing a fault, or counting partial work as
completion:

* 30 consecutive seeds by default;
* a 15-task open burst where one-step future bidding is active;
* the pinned 12-task SIH chokepoint workload at 0%, 5%, 10%, and 20% packet loss;
* a partition that heals and a winning robot that fails;
* every announced task complete inside each scenario's existing cutoff;
* zero robot/robot, robot/human, robot/rack contacts and zero detected deadlocks;
* no rejected valid completion certificates, bounded communication and allocation cost;
* identical semantic results under PYTHONHASHSEED 0, 1, and 42.

This is simulation evidence over a clearly defined feasible campaign.  It is not a
universal termination proof, a physical safety certification, or a Byzantine-security
claim.
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
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .amr import POLICY_BIOS_PIBT_V6
from .main import run_scenario
from .metrics import PolicyResult, percentile
from .scenarios import (partition_recovery, robot_failure_reassignment,
                        showcase_open_floor, sih_acceptance_overlap)
from .task_allocation import ALLOCATION_AUCTION_BUNDLE

DEFAULT_LOSSES = (0.0, 0.05, 0.10, 0.20)
HASH_SEEDS = (0, 1, 42)
MAX_MESSAGES_PER_ROBOT_SECOND = 15.0
MAX_ALLOCATION_P95_MS = 25.0


def parse_losses(value: str) -> list[float]:
    losses: list[float] = []
    for item in value.split(","):
        loss = float(item.strip())
        if not 0.0 <= loss <= 1.0:
            raise argparse.ArgumentTypeError(
                "packet loss must be between zero and one")
        if loss not in losses:
            losses.append(loss)
    if not losses:
        raise argparse.ArgumentTypeError("at least one loss value is required")
    return losses


def _build_case(kind: str, seed: int, loss: float | None):
    if kind == "burst_open":
        return showcase_open_floor(n_robots=5, tasks_per_robot=3, seed=seed)
    if kind == "packet_loss":
        scenario = sih_acceptance_overlap(
            n_robots=4, tasks_per_robot=3, seed=seed)
        scenario.net = replace(scenario.net, loss=float(loss))
        return scenario
    if kind == "partition_heal":
        return partition_recovery(n_robots=4, tasks_per_robot=1, seed=seed)
    if kind == "robot_failure":
        return robot_failure_reassignment(
            n_robots=3, tasks_per_robot=1, seed=seed)
    raise ValueError(f"unknown campaign case {kind!r}")


def _worker(spec: tuple[str, int, float | None]):
    kind, seed, loss = spec
    result = run_scenario(
        _build_case(kind, seed, loss), POLICY_BIOS_PIBT_V6, seed=seed,
        allocation_policy=ALLOCATION_AUCTION_BUNDLE,
    )
    return kind, loss, result


_DETERMINISTIC_FIELDS = (
    "policy", "scenario", "seed", "allocation_policy", "workload_id",
    "sim_seconds", "robots", "tasks_completed", "tasks_announced",
    "makespan_s", "task_times", "completed_all", "contacts_robot_robot",
    "contacts_robot_human", "contacts_robot_rack", "min_separation_m",
    "p05_separation_m", "deadlocks_detected", "retreats", "yields", "replans",
    "task_reassignments", "auction_bids_sent", "energy_bids_suppressed",
    "nonproductive_wait_ticks", "safety_stop_ticks", "msgs_sent", "bytes_sent",
    "heartbeat_messages_sent", "intent_messages_sent", "auction_messages_sent",
    "coordination_messages_sent", "future_candidates_evaluated", "future_bids_sent",
    "future_bids_won", "future_bids_lost", "future_promotions",
    "future_promotion_failures", "future_invalidations",
    "completion_certificates_accepted", "completion_certificates_relayed",
    "task_resurrections_suppressed", "rejected_task_completions",
    "rejected_task_conflicts", "rejected_epoch_jumps", "deadline_misses",
)


def _deterministic_payload(result: PolicyResult) -> dict:
    row = result.to_dict()
    return {field: row[field] for field in _DETERMINISTIC_FIELDS}


def _deterministic_digest(result: PolicyResult) -> str:
    wire = json.dumps(
        _deterministic_payload(result), sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(wire).hexdigest()


def _summary(results: list[PolicyResult]) -> dict:
    completed = [result for result in results if result.completed_all]
    makespans = [result.makespan_s for result in completed]
    return {
        "runs": len(results),
        "completed_runs": len(completed),
        "completion_rate": (
            round(len(completed) / len(results), 6) if results else 0.0),
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
        "messages_sent": sum(r.msgs_sent for r in results),
        "max_messages_per_robot_second": round(max(
            (r.msgs_per_robot_s for r in results), default=0.0), 3),
        "max_allocation_p95_ms": round(max(
            (r.allocation_compute_p95_ms for r in results), default=0.0), 4),
        "future_bids_sent": sum(r.future_bids_sent for r in results),
        "future_promotions": sum(r.future_promotions for r in results),
        "task_reassignments": sum(r.task_reassignments for r in results),
        "completion_certificates_accepted": sum(
            r.completion_certificates_accepted for r in results),
        "completion_certificates_relayed": sum(
            r.completion_certificates_relayed for r in results),
        "task_resurrections_suppressed": sum(
            r.task_resurrections_suppressed for r in results),
        "rejected_task_completions": sum(
            r.rejected_task_completions for r in results),
        "deadline_misses": sum(r.deadline_misses for r in results),
        "raw_runs": [r.to_dict() for r in sorted(results, key=lambda row: row.seed)],
    }


def _case_failures(name: str, summary: dict) -> list[str]:
    failures = []
    if summary["completed_runs"] != summary["runs"]:
        failures.append(
            f"{name}: only {summary['completed_runs']}/{summary['runs']} runs completed")
    if summary["tasks_completed"] != summary["tasks_announced"]:
        failures.append(
            f"{name}: tasks {summary['tasks_completed']}/{summary['tasks_announced']}")
    for field in ("robot_robot_contacts", "robot_human_contacts",
                  "robot_rack_contacts", "deadlocks_detected",
                  "rejected_task_completions", "deadline_misses"):
        if summary[field] != 0:
            failures.append(f"{name}: {field}={summary[field]}")
    if summary["max_messages_per_robot_second"] > MAX_MESSAGES_PER_ROBOT_SECOND:
        failures.append(
            f"{name}: communication rate "
            f"{summary['max_messages_per_robot_second']} exceeds "
            f"{MAX_MESSAGES_PER_ROBOT_SECOND}")
    if summary["max_allocation_p95_ms"] > MAX_ALLOCATION_P95_MS:
        failures.append(
            f"{name}: allocation P95 {summary['max_allocation_p95_ms']} ms exceeds "
            f"{MAX_ALLOCATION_P95_MS} ms")
    if summary["completion_certificates_accepted"] <= 0:
        failures.append(f"{name}: no completion certificate was exercised")
    return failures


def _probe_subprocess(kind: str, seed: int, loss: float | None,
                      hash_seed: int) -> dict:
    environment = dict(os.environ)
    environment["PYTHONHASHSEED"] = str(hash_seed)
    completed = subprocess.run(
        [sys.executable, "-m", "src.auction_v2_campaign", "--probe",
         kind, str(seed), "-" if loss is None else str(loss)],
        check=True, capture_output=True, text=True, timeout=180,
        env=environment,
    )
    return json.loads(completed.stdout)


def _determinism_gate() -> dict:
    probes = (
        ("burst_open", 1, None),
        ("packet_loss", 6, 0.20),
        ("partition_heal", 0, None),
        ("robot_failure", 0, None),
    )
    rows = []
    passed = True
    for kind, seed, loss in probes:
        variants = [
            _probe_subprocess(kind, seed, loss, hash_seed)
            for hash_seed in HASH_SEEDS
        ]
        digests = {variant["digest"] for variant in variants}
        case_passed = len(digests) == 1
        passed = passed and case_passed
        rows.append({
            "case": kind, "seed": seed, "loss": loss,
            "hash_seeds": list(HASH_SEEDS),
            "digests": [variant["digest"] for variant in variants],
            "passed": case_passed,
            "semantic_result": variants[0]["result"],
        })
    return {"passed": passed, "probes": rows}


def run_auction_v2_campaign(
    *, seeds: int = 30, first_seed: int = 0,
    losses: list[float] | None = None, jobs: int = 1,
    check_determinism: bool = True,
) -> dict:
    if seeds <= 0:
        raise ValueError("seeds must be positive")
    if jobs <= 0:
        raise ValueError("jobs must be positive")
    losses = list(DEFAULT_LOSSES if losses is None else losses)
    specs: list[tuple[str, int, float | None]] = []
    seed_range = range(first_seed, first_seed + seeds)
    specs.extend(("burst_open", seed, None) for seed in seed_range)
    for loss in losses:
        specs.extend(("packet_loss", seed, loss)
                     for seed in range(first_seed, first_seed + seeds))
    specs.extend(("partition_heal", seed, None)
                 for seed in range(first_seed, first_seed + seeds))
    specs.extend(("robot_failure", seed, None)
                 for seed in range(first_seed, first_seed + seeds))

    started = time.perf_counter()
    if jobs == 1:
        rows = [_worker(spec) for spec in specs]
    else:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            rows = list(pool.map(_worker, specs))

    burst = _summary([
        result for kind, _loss, result in rows if kind == "burst_open"])
    packet = {
        f"{loss:.2f}": _summary([
            result for kind, row_loss, result in rows
            if kind == "packet_loss" and row_loss == loss
        ])
        for loss in losses
    }
    partition = _summary([
        result for kind, _loss, result in rows if kind == "partition_heal"])
    failure = _summary([
        result for kind, _loss, result in rows if kind == "robot_failure"])

    failures = _case_failures("burst_open", burst)
    for loss, summary in packet.items():
        failures.extend(_case_failures(f"packet_loss_{loss}", summary))
    failures.extend(_case_failures("partition_heal", partition))
    failures.extend(_case_failures("robot_failure", failure))
    if burst["future_bids_sent"] <= 0 or burst["future_promotions"] <= 0:
        failures.append("burst_open: future-auction path was not exercised")
    if failure["task_reassignments"] < seeds:
        failures.append(
            f"robot_failure: only {failure['task_reassignments']} reassignments "
            f"across {seeds} runs")

    determinism = (
        _determinism_gate() if check_determinism
        else {"passed": None, "probes": [], "skipped": True})
    if check_determinism and not determinism["passed"]:
        failures.append("PYTHONHASHSEED semantic determinism gate failed")

    total_runs = len(rows)
    completed_runs = sum(result.completed_all for _kind, _loss, result in rows)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "claim_scope": (
            "100% completion across the checked-in feasible deterministic "
            "BIOS 6 Auction V2 acceptance campaign"),
        "claim_limit": (
            "simulation evidence only; not universal completion, physical safety "
            "certification, or Byzantine security"),
        "policy": POLICY_BIOS_PIBT_V6,
        "allocation_policy": ALLOCATION_AUCTION_BUNDLE,
        "seeds": seeds,
        "first_seed": first_seed,
        "losses": losses,
        "jobs": jobs,
        "fixed_cutoffs_s": {
            "burst_open": 240.0,
            "packet_loss": 1200.0,
            "partition_heal": 240.0,
            "robot_failure": 180.0,
        },
        "gates": {
            "all_tasks_complete": True,
            "all_contact_types": 0,
            "deadlocks_detected": 0,
            "rejected_task_completions": 0,
            "deadline_misses": 0,
            "max_messages_per_robot_second": MAX_MESSAGES_PER_ROBOT_SECOND,
            "max_allocation_p95_ms_current_machine": MAX_ALLOCATION_P95_MS,
            "hash_seeds": list(HASH_SEEDS),
        },
        "verdict": "pass" if not failures else "fail",
        "failures": failures,
        "total_runs": total_runs,
        "completed_runs": completed_runs,
        "completion_rate": round(completed_runs / total_runs, 6),
        "wall_time_s": round(time.perf_counter() - started, 3),
        "burst_open": burst,
        "packet_loss": packet,
        "partition_heal": partition,
        "robot_failure": failure,
        "determinism": determinism,
    }


def _probe(kind: str, seed: int, loss_text: str) -> int:
    loss = None if loss_text == "-" else float(loss_text)
    _row_kind, _row_loss, result = _worker((kind, seed, loss))
    print(json.dumps({
        "digest": _deterministic_digest(result),
        "result": _deterministic_payload(result),
    }, sort_keys=True, separators=(",", ":")))
    return 0


def _without_raw(summary: dict) -> dict:
    return {key: value for key, value in summary.items() if key != "raw_runs"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the fixed BIOS 6 Auction V2 release campaign")
    parser.add_argument("--seeds", type=int, default=30)
    parser.add_argument("--first-seed", type=int, default=0)
    parser.add_argument("--losses", type=parse_losses,
                        default=list(DEFAULT_LOSSES))
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--skip-determinism", action="store_true")
    parser.add_argument(
        "--json", type=Path,
        default=Path("artifacts/benchmarks/bios6-auction-v2-acceptance.json"))
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--probe", nargs=3, metavar=("CASE", "SEED", "LOSS"),
                        help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.probe:
        return _probe(args.probe[0], int(args.probe[1]), args.probe[2])

    result = run_auction_v2_campaign(
        seeds=args.seeds, first_seed=args.first_seed,
        losses=args.losses, jobs=args.jobs,
        check_determinism=not args.skip_determinism,
    )
    if not args.no_write:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "verdict": result["verdict"],
        "completed_runs": result["completed_runs"],
        "total_runs": result["total_runs"],
        "completion_rate": result["completion_rate"],
        "failures": result["failures"],
        "wall_time_s": result["wall_time_s"],
        "burst_open": _without_raw(result["burst_open"]),
        "packet_loss": {
            loss: _without_raw(summary)
            for loss, summary in result["packet_loss"].items()
        },
        "partition_heal": _without_raw(result["partition_heal"]),
        "robot_failure": _without_raw(result["robot_failure"]),
        "determinism": result["determinism"],
    }, indent=2))
    return 0 if result["verdict"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
