"""Preregistered BIOS7 study with immutable BIOS6 control and honest censoring.

Default invocation only prints a plan. --execute runs serial paired subprocesses,
persists every result, and stops progression on unsafe/incomplete candidate cases.
This is headless evidence, never a live-timing or physical-hardware release gate.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.bios7_study import (CONTROL_COMMIT, POLICY_CONFIGS, STRESS_CASES, TRAFFIC_OVERRIDES,
                            build_case, physical_config_manifest, scenario_diagnostics,
                            scenario_wire, source_manifest)

CONTACT_FIELDS = ("contacts_robot_robot", "contacts_robot_human", "contacts_robot_rack")


def compare_pair(baseline: dict, candidate: dict) -> dict:
    if baseline["world_fingerprint"] != candidate["world_fingerprint"]:
        return {"verdict": "invalid", "reason": "exogenous world mismatch"}
    if baseline.get("error") or candidate.get("error"):
        return {"verdict": "invalid", "reason": "a worker failed; no time claim"}
    b, c = baseline["output"]["result"], candidate["output"]["result"]
    if b["tasks_announced"] != c["tasks_announced"]:
        return {"verdict": "invalid", "reason": "announced task count mismatch"}
    safe = not any(c[field] for field in CONTACT_FIELDS)
    if not c["completed_all"]:
        return {"verdict": "incomplete", "candidate_safe": safe,
                "candidate_tasks_completed": c["tasks_completed"],
                "baseline_tasks_completed": b["tasks_completed"],
                "reduction_pct": None, "reduction_lower_bound_pct": None}
    denominator = b["makespan_s"] if b["completed_all"] else b["sim_seconds"]
    if denominator <= 0:
        return {"verdict": "invalid", "reason": "non-positive baseline time"}
    reduction = 100*(1-c["makespan_s"]/denominator)
    return {"verdict": "measured" if safe else "unsafe", "candidate_safe": safe,
            "kind": "exact" if b["completed_all"] else "right_censored_lower_bound",
            "reduction_pct": reduction if b["completed_all"] else None,
            "reduction_lower_bound_pct": reduction,
            "candidate_makespan_s": c["makespan_s"],
            "baseline_makespan_s": b["makespan_s"] if b["completed_all"] else None,
            "baseline_observation_window_s": b["sim_seconds"]}


def summarize(report: dict) -> dict:
    rows = report["runs"]
    candidate = [row for row in rows if row["configuration"] == "bios7"]
    completed = [row for row in candidate if not row.get("error")
                 and row["output"]["result"]["completed_all"]]
    safe = all(not row.get("error") and
               not any(row["output"]["result"][field] for field in CONTACT_FIELDS)
               for row in candidate)
    by_key = {(r["case"], r["robots"], r["seed"], r["configuration"]): r for r in rows}
    comparisons = []
    for row in candidate:
        for configuration in report["plan"]["configurations"]:
            if configuration == "bios7":
                continue
            base = by_key.get((row["case"], row["robots"], row["seed"], configuration))
            if base is not None:
                comparisons.append({"case": row["case"], "robots": row["robots"],
                                    "seed": row["seed"], "baseline": configuration,
                                    **compare_pair(base, row)})
    sih = [c for c in comparisons if c["case"] == "sih" and c["baseline"] == "stop_wait"]
    expected_sih = sum(i["case"] == "sih" for i in report["plan"].get("inputs", []))
    sih_pass = (all(c.get("candidate_safe") and
                    c.get("reduction_lower_bound_pct", -1) is not None and
                    c.get("reduction_lower_bound_pct", -1) >= 20 for c in sih)
                if sih and len(sih) == expected_sih else None)
    grouped = {}
    for pair in comparisons:
        key = f'{pair["case"]}:{pair["robots"]}:vs_{pair["baseline"]}'
        grouped.setdefault(key, []).append(pair)
    aggregates = {}
    for key, pairs in grouped.items():
        exact = [p["reduction_pct"] for p in pairs if p.get("reduction_pct") is not None]
        bounds = [p["reduction_lower_bound_pct"] for p in pairs
                  if p.get("reduction_lower_bound_pct") is not None]
        aggregates[key] = {"paired_cases": len(pairs), "exact_pairs": len(exact),
            "median_exact_reduction_pct": statistics.median(exact) if exact else None,
            "minimum_reduction_bound_pct": min(bounds) if bounds else None,
            "incomplete_or_invalid_pairs": sum(p["verdict"] in ("invalid", "incomplete")
                                                for p in pairs)}
    return {"candidate_runs_observed": len(candidate),
            "candidate_runs_completed": len(completed),
            "candidate_contact_free": safe if candidate else None,
            "all_declared_runs_finished": report.get("finished", False),
            "pinned_sih_20pct_gate": sih_pass, "comparisons": comparisons,
            "aggregates": aggregates,
            "promotion_allowed": False,
            "promotion_note": "This headless screen alone cannot approve release: "
                              "heldout non-regression, ownership and live timing gates are separate."}


def _save(report: dict, target: Path):
    report["summary"] = summarize(report)
    temporary = target.with_suffix(target.suffix + ".pending")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temporary.replace(target)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--control-root", type=Path)
    parser.add_argument("--phase", choices=("screen", "holdout", "scaling"), default="screen")
    parser.add_argument("--cases", default=None)
    parser.add_argument("--robots", default=None)
    parser.add_argument("--seeds", type=int, default=None)
    parser.add_argument("--first-seed", type=int, default=None)
    parser.add_argument("--configurations", default="bios6,bios7,stop_wait,centralized")
    parser.add_argument("--total-tasks", type=int, default=None,
                        help="fixed catalog size for capacity suites only; otherwise tasks/robot fixed")
    parser.add_argument("--worker-timeout", type=float, default=1800)
    parser.add_argument("--output", type=Path,
                        default=Path("artifacts/benchmarks/bios7-screen.json"))
    args = parser.parse_args(argv)
    cases = (args.cases or ("fixed_floor,scaled_floor" if args.phase == "scaling"
             else "sih,crossflow,doorway,open")).split(",")
    robots = [int(n) for n in (args.robots or ("3,10,25,50,100" if args.phase == "scaling"
                                                   else "3,10")).split(",")]
    seeds = args.seeds if args.seeds is not None else (30 if args.phase == "holdout" else 3)
    first = args.first_seed if args.first_seed is not None else (1000 if args.phase == "holdout" else 0)
    configs = args.configurations.split(",")
    if (not configs or len(configs) != len(set(configs)) or
            any(c not in POLICY_CONFIGS for c in configs) or "bios7" not in configs or
            "bios6" not in configs or seeds < 1 or first < 0 or args.worker_timeout <= 0 or
            len(robots) != len(set(robots)) or any(n < 3 or n > 100 for n in robots) or
            any(c not in ("sih", "fixed_floor", "scaled_floor", *STRESS_CASES) for c in cases)):
        parser.error("invalid cases/fleets/seeds/configurations; both BIOS6 and BIOS7 are required")
    if args.total_tasks is not None and any(c not in ("fixed_floor", "scaled_floor") for c in cases):
        parser.error("--total-tasks is only valid for explicit capacity studies")
    if 99 in range(first, first + seeds):
        parser.error("seed 99 is reserved for a hand-built demonstration, not this acceptance study")
    if args.phase == "holdout" and first < 1000:
        parser.error("holdout seeds must use the preregistered range beginning at 1000 or later")
    root = Path(__file__).resolve().parent
    inputs = []
    for n in sorted(robots):
        for case in cases:
            for seed in range(first, first + seeds):
                try:
                    sc = build_case(case, n, seed, args.total_tasks)
                except ValueError as exc:
                    parser.error(str(exc))
                diagnostics = scenario_diagnostics(sc)
                if not diagnostics["valid_nonoverlapping_starts"] or diagnostics["unreachable_task_ids"]:
                    parser.error(f"{case}/{n}/{seed}: invalid initial state/reachability")
                if case in ("sih", "crossflow", "doorway") and not diagnostics["overlap_fixture_valid"]:
                    parser.error(f"{case}/{n}/{seed}: fixture does not contain shared nominal routes")
                inputs.append({"case": case, "robots": n, "seed": seed,
                               "diagnostics": diagnostics, "scenario": scenario_wire(sc)})
    plan = {"phase": args.phase, "cases": cases, "robot_counts": sorted(robots),
            "seeds_per_case": seeds, "first_seed": first, "configurations": configs,
            "total_tasks_override": args.total_tasks, "serial_execution": True,
            "traffic_overrides": {c: TRAFFIC_OVERRIDES.get(c, {}) for c in configs},
            "expected_runs": len(inputs)*len(configs),
            "stop_rule": "Stop larger-fleet progression on candidate contact, timeout or worker error; "
                         "retain every observed result and mark all unrun cases.",
            "inputs": inputs}
    if not args.execute:
        print(json.dumps(plan, indent=2, allow_nan=False))
        return 0
    if args.control_root is None:
        parser.error("--execute requires --control-root at immutable BIOS6 commit " + CONTROL_COMMIT)
    control = args.control_root.resolve()
    control_manifest = source_manifest(control)
    if control_manifest["commit"] != CONTROL_COMMIT or control_manifest["tracked_sources_dirty"]:
        parser.error("control source must be clean, unchanged BIOS6 at " + CONTROL_COMMIT)
    if args.output.exists():
        parser.error("output exists; choose a new evidence filename to preserve earlier results")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    candidate_manifest = source_manifest(root)
    report = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
              "plan": plan, "control_manifest": control_manifest,
              "candidate_manifest": candidate_manifest, "runs": [], "finished": False,
              "scope": "Finite headless study; no physical Pi or universal safety claim."}
    _save(report, args.output)
    physical = physical_config_manifest()
    stop_reason = None
    for index, item in enumerate(inputs):
        # Counterbalance deterministic pair order to reduce warmup/thermal order bias.
        order = configs if index % 2 == 0 else list(reversed(configs))
        stop_after_pair = False
        for configuration in order:
            policy, allocation = POLICY_CONFIGS[configuration]
            candidate_source = configuration.startswith("bios7")
            source = root if candidate_source else control
            source_key = "candidate_manifest" if candidate_source else "control_manifest"
            if source_manifest(source)["source_manifest_sha256"] != report[source_key]["source_manifest_sha256"]:
                stop_reason = "source changed during campaign; recorded run set is incomplete"
                break
            request = {"source_root": str(source), "scenario": item["scenario"],
                       "physical_config": physical, "policy": policy, "allocation": allocation,
                       "traffic_overrides": TRAFFIC_OVERRIDES.get(configuration, {})}
            row = {"case": item["case"], "robots": item["robots"], "seed": item["seed"],
                   "configuration": configuration, "policy": policy, "allocation": allocation,
                   "world_fingerprint": item["diagnostics"]["world_fingerprint"]}
            try:
                process = subprocess.run(
                    [sys.executable, str(root / "bios7_acceptance_worker.py")], cwd=source,
                    input=json.dumps(request), text=True, capture_output=True,
                    env={**os.environ, "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1"},
                    timeout=args.worker_timeout, check=True)
                row["output"] = json.loads(process.stdout)
                if source_manifest(source)["source_manifest_sha256"] != report[source_key]["source_manifest_sha256"]:
                    raise ValueError("source changed while worker was running; result invalid")
            except (subprocess.SubprocessError, ValueError, OSError) as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"
                if isinstance(exc, subprocess.CalledProcessError):
                    row["stderr"] = exc.stderr[-5000:]
            report["runs"].append(row)
            _save(report, args.output)
            raw = row.get("output", {}).get("result", {})
            print(f'{item["case"]} robots={item["robots"]} seed={item["seed"]} '
                  f'{configuration}: {raw.get("tasks_completed", "?")}/'
                  f'{raw.get("tasks_announced", "?")} '
                  f'{row.get("error", "completed" if raw.get("completed_all") else "timeout")}', flush=True)
            if row.get("error") or (configuration == "bios7" and
                (not raw.get("completed_all") or any(raw.get(field, 0) for field in CONTACT_FIELDS))):
                stop_after_pair = True
        if stop_reason or stop_after_pair:
            stop_reason = stop_reason or "candidate unsafe/incomplete or worker failed; progression stopped"
            break
    report["finished"] = len(report["runs"]) == plan["expected_runs"] and stop_reason is None
    report["stop_reason"] = stop_reason
    report["unrun_count"] = plan["expected_runs"]-len(report["runs"])
    _save(report, args.output)
    summary = report["summary"]
    return 0 if (report["finished"] and summary["candidate_contact_free"] and
                 summary["candidate_runs_completed"] == summary["candidate_runs_observed"] and
                 summary["pinned_sih_20pct_gate"] is not False) else 2


if __name__ == "__main__":
    raise SystemExit(main())
