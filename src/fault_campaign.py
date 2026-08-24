"""Seeded communication, partition, and robot-failure release campaign."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .amr import POLICY_BIOS_PIBT_V3
from .main import run_scenario
from .metrics import percentile
from .scenarios import (partition_recovery, robot_failure_reassignment,
                        sih_acceptance_overlap)
from .task_allocation import ALLOCATION_AUCTION


def parse_losses(value: str) -> list[float]:
    losses = []
    for item in value.split(","):
        loss = float(item.strip())
        if not 0.0 <= loss <= 1.0:
            raise argparse.ArgumentTypeError("packet loss must be between 0 and 1")
        if loss not in losses:
            losses.append(loss)
    if not losses:
        raise argparse.ArgumentTypeError("at least one packet-loss value is required")
    return losses


def _worker(args: tuple[str, int, float | None]):
    kind, seed, loss = args
    if kind == "packet_loss":
        scenario = sih_acceptance_overlap(n_robots=4, seed=seed)
        scenario.net = replace(scenario.net, loss=float(loss))
    elif kind == "partition_heal":
        scenario = partition_recovery(n_robots=4, seed=seed)
    elif kind == "robot_failure":
        scenario = robot_failure_reassignment(n_robots=3, seed=seed)
    else:
        raise ValueError(f"unknown campaign kind {kind!r}")
    result = run_scenario(
        scenario, POLICY_BIOS_PIBT_V3, seed=seed,
        allocation_policy=ALLOCATION_AUCTION)
    return kind, loss, result


def _summary(results) -> dict:
    completed = [result for result in results if result.completed_all]
    makespans = [result.makespan_s for result in completed]
    return {
        "runs": len(results),
        "completed": len(completed),
        "completion_rate": len(completed) / len(results) if results else 0.0,
        "median_makespan_s": (round(statistics.median(makespans), 3)
                              if makespans else None),
        "p95_makespan_s": (round(percentile(makespans, 0.95), 3)
                           if makespans else None),
        "robot_robot_contacts": sum(r.contacts_robot_robot for r in results),
        "robot_human_contacts": sum(r.contacts_robot_human for r in results),
        "robot_rack_contacts": sum(r.contacts_robot_rack for r in results),
        "task_reassignment_observations": sum(r.task_reassignments for r in results),
        "raw_runs": [result.to_dict() for result in sorted(results,
                                                             key=lambda r: r.seed)],
    }


def run_fault_campaign(*, seeds: int = 30, first_seed: int = 0,
                       losses: list[float] | None = None,
                       jobs: int = 1) -> dict:
    if seeds <= 0:
        raise ValueError("seeds must be positive")
    if jobs <= 0:
        raise ValueError("jobs must be positive")
    losses = losses or [0.0, 0.05, 0.10, 0.20]
    tasks = []
    for loss in losses:
        tasks.extend(("packet_loss", seed, loss)
                     for seed in range(first_seed, first_seed + seeds))
    tasks.extend(("partition_heal", seed, None)
                 for seed in range(first_seed, first_seed + seeds))
    tasks.extend(("robot_failure", seed, None)
                 for seed in range(first_seed, first_seed + seeds))

    started = time.perf_counter()
    if jobs == 1:
        rows = [_worker(task) for task in tasks]
    else:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            rows = list(pool.map(_worker, tasks))

    packet = {}
    for loss in losses:
        packet[f"{loss:.2f}"] = _summary([
            result for kind, row_loss, result in rows
            if kind == "packet_loss" and row_loss == loss
        ])
    partition = _summary([
        result for kind, _loss, result in rows if kind == "partition_heal"
    ])
    failure = _summary([
        result for kind, _loss, result in rows if kind == "robot_failure"
    ])
    summaries = [*packet.values(), partition, failure]
    passed = all(
        item["completed"] == item["runs"]
        and item["robot_robot_contacts"] == 0
        and item["robot_human_contacts"] == 0
        and item["robot_rack_contacts"] == 0
        for item in summaries
    ) and failure["task_reassignment_observations"] >= seeds
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy": POLICY_BIOS_PIBT_V3,
        "seeds": seeds,
        "first_seed": first_seed,
        "losses": losses,
        "jobs": jobs,
        "verdict": "pass" if passed else "fail",
        "wall_time_s": round(time.perf_counter() - started, 3),
        "packet_loss": packet,
        "partition_heal": partition,
        "robot_failure": failure,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run packet-loss, partition-heal, and robot-failure gates")
    parser.add_argument("--seeds", type=int, default=30)
    parser.add_argument("--first-seed", type=int, default=0)
    parser.add_argument("--losses", type=parse_losses,
                        default=[0.0, 0.05, 0.10, 0.20])
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--json", type=Path,
                        default=Path("artifacts/benchmarks/fault-campaign.json"))
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)
    result = run_fault_campaign(
        seeds=args.seeds, first_seed=args.first_seed,
        losses=args.losses, jobs=args.jobs)
    if not args.no_write:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "verdict": result["verdict"],
        "wall_time_s": result["wall_time_s"],
        "packet_loss": {
            loss: {key: value for key, value in summary.items()
                   if key != "raw_runs"}
            for loss, summary in result["packet_loss"].items()
        },
        "partition_heal": {
            key: value for key, value in result["partition_heal"].items()
            if key != "raw_runs"
        },
        "robot_failure": {
            key: value for key, value in result["robot_failure"].items()
            if key != "raw_runs"
        },
    }, indent=2))
    return 0 if result["verdict"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
