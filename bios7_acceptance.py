"""Preregistered BIOS7 study with immutable BIOS6 control and honest censoring.

Default invocation only prints a plan. --execute runs serial paired subprocesses,
persists every result, and stops progression on unsafe/incomplete candidate cases.
This is headless evidence, never a live-timing or physical-hardware release gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
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
NONREGRESSION_FIELDS = ("makespan_s", "msgs_sent", "bytes_sent")
CONFIGURATIONS = {**POLICY_CONFIGS, "bios6_safety_fixed": POLICY_CONFIGS["bios6"]}
CANDIDATE_CONFIGURATIONS = {"bios7", "bios7_no_release", "bios6_safety_fixed"}
RELEASE_STAGES = ("regression50", "capacity", "holdout", "stress", "repeat")
RELEASE_SEED = 2000


def payload_hash(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def semantic_fingerprint(result: dict) -> str:
    """Deterministic simulator outputs, excluding measured host-compute timing only."""
    return payload_hash({key: value for key, value in result.items()
        if not key.startswith(("allocation_compute_", "plan_cpu_"))})


def release_cases(stage: str) -> list[tuple[str, str, int, int, int]]:
    """Preregistered finite cases: (stage, case, fleet, seed, replicate).

    Stage artifacts are not a full release approval. In the full plan the known
    failing 50-AMR case runs before expansion. No scenario/physics edits occur here.
    """
    stages = RELEASE_STAGES if stage == "all" else (stage,)
    cases = []
    for name in stages:
        if name == "regression50":
            cases.append((name, "fixed_floor", 50, 0, 0))
        elif name == "capacity":
            cases.extend((name, case, robots, 0, 0) for robots, case in (
                (50, "scaled_floor"), (100, "fixed_floor"), (100, "scaled_floor"),
                *((n, c) for n in (3, 10, 25) for c in ("fixed_floor", "scaled_floor"))))
        elif name == "holdout":
            cases.extend((name, "sih", 10, seed, 0)
                         for seed in range(RELEASE_SEED, RELEASE_SEED + 30))
        elif name == "stress":
            cases.extend((name, case, robots, seed, 0)
                         for robots in (3, 10)
                         for case in ("humans", "blocked", "failure", "doorway", "crossflow", "open")
                         for seed in range(RELEASE_SEED, RELEASE_SEED + 3))
        elif name == "repeat":
            cases.extend((name, "sih", 10, RELEASE_SEED, replicate) for replicate in (1, 2))
    return cases


def coverage_check(case: str, result: dict) -> dict:
    required = {"humans": ("human_distance_m", "human_yield_ticks"),
                "blocked": ("dynamic_obstacles_detected", "dynamic_reroutes"),
                "failure": ("robot_failures", "task_reassignments")}.get(case, ())
    observations = {field: result.get(field) for field in required}
    return {"required_positive_metrics": observations,
            "exercised": all(isinstance(value, (int, float)) and value > 0
                             for value in observations.values()) if required else None}


def validate_worker_output(output: dict, request: dict, item: dict) -> None:
    if output.get("scenario_input_sha256") != payload_hash(request["scenario"]):
        raise ValueError("worker did not execute the serialized preregistered input")
    if output.get("physical_config") != request["physical_config"]:
        raise ValueError("worker physical configuration differs from the paired request")
    if output.get("controller_source_root") != str(Path(request["source_root"]).resolve()):
        raise ValueError("worker controller source tree mismatch")
    if output.get("config_sha256") != payload_hash(output.get("config")):
        raise ValueError("worker effective configuration hash mismatch")
    result = output.get("result", {})
    for field in (*CONTACT_FIELDS, *NONREGRESSION_FIELDS, "tasks_announced", "tasks_completed",
                  "sim_seconds", "robots", "seed"):
        value = result.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f"invalid/missing worker metric: {field}")
    if (result["robots"] != item["robots"] or result["seed"] != item["seed"] or
            result.get("policy") != request["policy"] or
            result["tasks_announced"] != item["diagnostics"]["tasks"]):
        raise ValueError("worker result identifies a different fleet, seed, policy or task catalog")
    if not isinstance(result.get("completed_all"), bool):
        raise ValueError("missing completion verdict")
    if result["completed_all"] != (result["tasks_completed"] == result["tasks_announced"]):
        raise ValueError("worker completion flag contradicts task counts")
    if result["tasks_completed"] > result["tasks_announced"]:
        raise ValueError("worker completed more tasks than the fixed catalog")
    if result["completed_all"] and not 0 < result["makespan_s"] <= result["sim_seconds"] + 0.1:
        raise ValueError("completed makespan lies outside the observed simulation")
    if result["sim_seconds"] > item["diagnostics"]["cutoff_s"] + 0.1:
        raise ValueError("worker silently extended the preregistered cutoff")


def compare_pair(baseline: dict, candidate: dict) -> dict:
    if baseline["world_fingerprint"] != candidate["world_fingerprint"]:
        return {"verdict": "invalid", "reason": "exogenous world mismatch"}
    if baseline.get("error") or candidate.get("error"):
        return {"verdict": "invalid", "reason": "a worker failed; no time claim"}
    b, c = baseline["output"]["result"], candidate["output"]["result"]
    if b["tasks_announced"] != c["tasks_announced"]:
        return {"verdict": "invalid", "reason": "announced task count mismatch"}
    safe = not any(c[field] for field in CONTACT_FIELDS)
    baseline_safe = not any(b[field] for field in CONTACT_FIELDS)
    nonregression, nonregression_evidence = {}, {}
    for field in NONREGRESSION_FIELDS:
        limit = b.get("sim_seconds") if field == "makespan_s" and not b["completed_all"] else b.get(field)
        eligible = safe and baseline_safe and c["completed_all"] and field in c and limit is not None
        if not eligible:
            nonregression[field], nonregression_evidence[field] = None, "unavailable"
        elif b["completed_all"]:
            nonregression[field] = c[field] <= limit
            nonregression_evidence[field] = "exact_completed_pair"
        else:
            # A logical simulation cutoff is not an external worker failure. Time
            # and cumulative transmitted counters can only grow beyond this prefix.
            proven = c[field] <= limit
            nonregression[field] = True if proven else None
            nonregression_evidence[field] = "censored_lower_bound" if proven else "unproven_censored"
    context = {"candidate_safe": safe, "baseline_safe": baseline_safe,
        "baseline_safety_scope": "completed_run" if b["completed_all"] else "observed_prefix_only",
        "baseline_future_safety_known": bool(b["completed_all"]),
        "valid_for_full_execution_safety_comparison": safe and baseline_safe and b["completed_all"],
        "baseline_contacts": {field: b[field] for field in CONTACT_FIELDS},
        "candidate_contacts": {field: c[field] for field in CONTACT_FIELDS},
        "valid_for_performance_claim": safe and baseline_safe,
        "nonregression": nonregression, "nonregression_evidence": nonregression_evidence,
        "metric_deltas": {field: c[field] - b[field] if field in b and field in c else None
                           for field in NONREGRESSION_FIELDS}}
    if not c["completed_all"]:
        return {"verdict": "incomplete", **context,
                "candidate_tasks_completed": c["tasks_completed"],
                "baseline_tasks_completed": b["tasks_completed"],
                "reduction_pct": None, "reduction_lower_bound_pct": None}
    denominator = b["makespan_s"] if b["completed_all"] else b["sim_seconds"]
    if denominator <= 0:
        return {"verdict": "invalid", "reason": "non-positive baseline time"}
    reduction = 100*(1-c["makespan_s"]/denominator)
    return {"verdict": "measured" if safe and baseline_safe else "unsafe", **context,
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
    def key(row, configuration):
        return (row.get("stage"), row["case"], row["robots"], row["seed"],
                row.get("replicate", 0), configuration)
    by_key = {key(r, r["configuration"]): r for r in rows}
    comparisons = []
    for row in candidate:
        for configuration in report["plan"]["configurations"]:
            if configuration == "bios7":
                continue
            base = by_key.get(key(row, configuration))
            if base is not None:
                comparisons.append({"case": row["case"], "robots": row["robots"],
                                    "seed": row["seed"], "baseline": configuration,
                                    "stage": row.get("stage"), "replicate": row.get("replicate", 0),
                                    **compare_pair(base, row)})
    sih = [c for c in comparisons if c["case"] == "sih" and c["baseline"] == "stop_wait"]
    expected_sih = sum(i["case"] == "sih" for i in report["plan"].get("inputs", []))
    sih_pass = (all(c.get("valid_for_performance_claim") and
                    c.get("reduction_lower_bound_pct", -1) is not None and
                    c.get("reduction_lower_bound_pct", -1) >= 20 for c in sih)
                if sih and len(sih) == expected_sih else None)
    grouped = {}
    for pair in comparisons:
        key = f'{pair["case"]}:{pair["robots"]}:vs_{pair["baseline"]}'
        grouped.setdefault(key, []).append(pair)
    aggregates = {}
    for key, pairs in grouped.items():
        exact = [p["reduction_pct"] for p in pairs if p.get("reduction_pct") is not None
                 and p.get("valid_for_performance_claim")]
        bounds = [p["reduction_lower_bound_pct"] for p in pairs
                  if p.get("reduction_lower_bound_pct") is not None
                  and p.get("valid_for_performance_claim")]
        aggregates[key] = {"paired_cases": len(pairs), "exact_pairs": len(exact),
            "median_exact_reduction_pct": statistics.median(exact) if exact else None,
            "minimum_reduction_bound_pct": min(bounds) if bounds else None,
            "unsafe_pairs_excluded_from_performance_claims": sum(
                p["verdict"] == "unsafe" for p in pairs),
            "incomplete_or_invalid_pairs": sum(p["verdict"] in ("invalid", "incomplete")
                                                for p in pairs)}
    configurations = {}
    for configuration in report["plan"]["configurations"]:
        observed = [row for row in rows if row["configuration"] == configuration]
        valid = [row["output"]["result"] for row in observed if not row.get("error")]
        configurations[configuration] = {"observed_runs": len(observed),
            "worker_errors": len(observed) - len(valid),
            "completed_runs": sum(r["completed_all"] for r in valid),
            "incomplete_runs": sum(not r["completed_all"] for r in valid),
            "contact_totals": {field: sum(r[field] for r in valid) for field in CONTACT_FIELDS}}
    strict_pairs = [p for p in comparisons if p["baseline"] == "bios6_safety_fixed"]
    expected = len(report["plan"].get("inputs", []))
    nonregression = {}
    for field in NONREGRESSION_FIELDS:
        verdicts = [p.get("nonregression", {}).get(field) for p in strict_pairs]
        nonregression[field] = (False if False in verdicts else
            True if verdicts and len(verdicts) == expected and all(v is True for v in verdicts)
            else None)
    coverage = [{"case": row["case"], "robots": row["robots"], "seed": row["seed"],
                 **coverage_check(row["case"], row.get("output", {}).get("result", {}))}
                for row in candidate if row["case"] in ("humans", "blocked", "failure")]
    repeats = {}
    for row in rows:
        if row.get("stage") == "repeat":
            repeat_key = f'{row["case"]}:{row["robots"]}:{row["seed"]}:{row["configuration"]}'
            repeats.setdefault(repeat_key, []).append(row)
    deterministic = {name: (len(group) == 2 and all(not row.get("error") for row in group)
        and len({row["output"].get("config_sha256") for row in group}) == 1
        and len({semantic_fingerprint(row["output"]["result"]) for row in group}) == 1)
        for name, group in repeats.items()}
    integrity = (len(by_key) == len(rows) and not any(r.get("error") for r in rows)
                 and report.get("source_integrity", True))
    is_release = report["plan"].get("phase") == "release"
    stage_pass = (report.get("finished", False) and integrity and bool(candidate) and safe
        and len(completed) == expected and all(value is True for value in nonregression.values())
        and (sih_pass is True if expected_sih else True)
        and all(c["exercised"] for c in coverage) and all(deterministic.values())) if is_release else None
    return {"candidate_runs_observed": len(candidate),
            "candidate_runs_completed": len(completed),
            "candidate_contact_free": safe if candidate else None,
            "all_declared_runs_finished": report.get("finished", False),
            "pinned_sih_20pct_gate": sih_pass, "comparisons": comparisons,
            "aggregates": aggregates, "configurations": configurations,
            "source_and_run_integrity": integrity,
            "strict_safety_fixed_v6_nonregression": nonregression,
            "fault_and_human_coverage": coverage,
            "deterministic_repeats": deterministic,
            "declared_headless_stage_pass": stage_pass,
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
    parser.add_argument("--phase", choices=("screen", "holdout", "scaling", "release"), default="screen")
    parser.add_argument("--release-stage", choices=("all", *RELEASE_STAGES), default="all")
    parser.add_argument("--cases", default=None)
    parser.add_argument("--robots", default=None)
    parser.add_argument("--seeds", type=int, default=None)
    parser.add_argument("--first-seed", type=int, default=None)
    parser.add_argument("--configurations", default=None)
    parser.add_argument("--prerequisite", type=Path, help="passing regression50 release artifact; "
                        "required before executing a separate release capacity stage")
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
    configs = (args.configurations or ("bios6,bios6_safety_fixed,bios7,stop_wait"
               if args.phase == "release" else "bios6,bios7,stop_wait,centralized")).split(",")
    if (not configs or len(configs) != len(set(configs)) or
            any(c not in CONFIGURATIONS for c in configs) or "bios7" not in configs or
            "bios6" not in configs or seeds < 1 or first < 0 or args.worker_timeout <= 0 or
            len(cases) != len(set(cases)) or len(robots) != len(set(robots)) or
            any(n < 3 or n > 100 for n in robots) or
            any(c not in ("sih", "fixed_floor", "scaled_floor", *STRESS_CASES) for c in cases)):
        parser.error("invalid cases/fleets/seeds/configurations; both BIOS6 and BIOS7 are required")
    if args.total_tasks is not None and any(c not in ("fixed_floor", "scaled_floor") for c in cases):
        parser.error("--total-tasks is only valid for explicit capacity studies")
    if 99 in range(first, first + seeds):
        parser.error("seed 99 is reserved for a hand-built demonstration, not this acceptance study")
    if args.phase == "holdout" and first < 1000:
        parser.error("holdout seeds must use the preregistered range beginning at 1000 or later")
    if args.phase == "release":
        if any(value is not None for value in (args.cases, args.robots, args.seeds,
                                               args.first_seed, args.total_tasks)):
            parser.error("release inputs are fixed; use a non-release phase for custom experiments")
        if not {"bios6", "bios6_safety_fixed", "bios7", "stop_wait"}.issubset(configs):
            parser.error("release requires untouched V6, safety-fixed V6, V7 and stop_wait")
    root = Path(__file__).resolve().parent
    inputs = []
    specs = (release_cases(args.release_stage) if args.phase == "release" else [
        (args.phase, case, n, seed, 0) for n in sorted(robots) for case in cases
        for seed in range(first, first + seeds)])
    for stage, case, n, seed, replicate in specs:
        try:
            sc = build_case(case, n, seed, args.total_tasks)
        except ValueError as exc:
            parser.error(str(exc))
        diagnostics = scenario_diagnostics(sc)
        if not diagnostics["valid_nonoverlapping_starts"] or diagnostics["unreachable_task_ids"]:
            parser.error(f"{case}/{n}/{seed}: invalid initial state/reachability")
        if case in ("sih", "crossflow", "doorway") and not diagnostics["overlap_fixture_valid"]:
            parser.error(f"{case}/{n}/{seed}: fixture does not contain shared nominal routes")
        item_configs = [c for c in configs if not (
            args.phase == "release" and case != "sih" and c == "stop_wait")]
        inputs.append({"stage": stage, "case": case, "robots": n, "seed": seed,
                       "replicate": replicate, "configurations": item_configs,
                       "diagnostics": diagnostics, "scenario": scenario_wire(sc)})
    plan = {"phase": args.phase, "cases": cases, "robot_counts": sorted(robots),
            "seeds_per_case": seeds, "first_seed": first, "configurations": configs,
            "total_tasks_override": args.total_tasks, "serial_execution": True,
            "traffic_overrides": {c: TRAFFIC_OVERRIDES.get(c, {}) for c in configs},
            "release_stage": args.release_stage if args.phase == "release" else None,
            "config_sources": {c: "candidate" if c in CANDIDATE_CONFIGURATIONS else "untouched_control"
                               for c in configs},
            "current_source_v6_scope": "BIOS6 policy from candidate tree, including shared safety/"
                "runtime/instrumentation changes; not a claim of a safety-only source cherry-pick.",
            "expected_runs": sum(len(item["configurations"]) for item in inputs),
            "nonregression_definition": "Each safe completed BIOS7 case must have makespan, "
                "total messages and total bytes <= same-source safety-fixed BIOS6. No tolerance. "
                "A safe logical-timeout baseline proves only a censored lower bound when candidate "
                "time <= its observed simulation window and candidate cumulative messages/bytes <= "
                "its observed counts; otherwise unknown. Baseline future safety remains unknown. "
                "Worker resource timeouts never provide a score. Unsafe controls are retained but "
                "excluded from valid performance claims.",
            "release_coverage_scope": "Finite registered stage only. Passing requires every "
                "declared candidate to finish contact-free, strict per-case time/messages/bytes "
                "nonregression, SIH >=20% when included, measured fault/human interactions, and "
                "matching semantic repeats when included. Full promotion also requires separate "
                "cause ablation, ownership/security, validated references and multihost/live timing.",
            "stop_rule": "Stop larger-fleet progression on candidate contact, timeout or worker error; "
                         "retain every observed result and mark all unrun cases.",
            "inputs": inputs}
    if args.phase == "release":
        plan.update(cases=list(dict.fromkeys(item["case"] for item in inputs)),
                    robot_counts=sorted({item["robots"] for item in inputs}),
                    seeds_per_case=None, first_seed=None)
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
    if args.phase == "release" and candidate_manifest["tracked_sources_dirty"]:
        parser.error("release candidate source must be a clean frozen worktree")
    prerequisite = None
    if args.phase == "release" and args.release_stage == "capacity":
        if args.prerequisite is None:
            parser.error("capacity expansion requires --prerequisite passing regression50 artifact")
        prerequisite = json.loads(args.prerequisite.read_text())
        if (prerequisite.get("plan", {}).get("release_stage") != "regression50" or
                not summarize(prerequisite)["declared_headless_stage_pass"] or
                prerequisite["candidate_manifest"]["source_manifest_sha256"] !=
                candidate_manifest["source_manifest_sha256"] or
                prerequisite["control_manifest"]["source_manifest_sha256"] !=
                control_manifest["source_manifest_sha256"]):
            parser.error("prerequisite must pass regression50 against this exact frozen source")
    report = {"schema_version": 2, "created_at": datetime.now(timezone.utc).isoformat(),
              "plan": plan, "control_manifest": control_manifest,
              "candidate_manifest": candidate_manifest, "runs": [], "finished": False,
              "source_integrity": True,
              "prerequisite_sha256": payload_hash(prerequisite) if prerequisite else None,
              "timing_scope": "Headless simulation on a potentially shared host; serial within this "
                  "campaign only. Wall/CPU metrics are not unloaded live-control or hard-RT proof.",
              "scope": "Finite headless study; no physical Pi or universal safety claim."}
    _save(report, args.output)
    physical = physical_config_manifest()
    stop_reason = None
    for index, item in enumerate(inputs):
        # Counterbalance deterministic pair order to reduce warmup/thermal order bias.
        item_configs = item["configurations"]
        order = item_configs if index % 2 == 0 else list(reversed(item_configs))
        stop_after_pair = False
        for configuration in order:
            policy, allocation = CONFIGURATIONS[configuration]
            candidate_source = configuration in CANDIDATE_CONFIGURATIONS
            source = root if candidate_source else control
            if any(source_manifest(path)["source_manifest_sha256"] != report[key]["source_manifest_sha256"]
                   for path, key in ((root, "candidate_manifest"), (control, "control_manifest"))):
                stop_reason = "source changed during campaign; recorded run set is incomplete"
                report["source_integrity"] = False
                break
            request = {"source_root": str(source), "scenario": item["scenario"],
                       "physical_config": physical, "policy": policy, "allocation": allocation,
                       "traffic_overrides": TRAFFIC_OVERRIDES.get(configuration, {})}
            row = {"case": item["case"], "robots": item["robots"], "seed": item["seed"],
                   "stage": item["stage"], "replicate": item["replicate"],
                   "configuration": configuration, "policy": policy, "allocation": allocation,
                   "world_fingerprint": item["diagnostics"]["world_fingerprint"]}
            try:
                process = subprocess.run(
                    [sys.executable, str(root / "bios7_acceptance_worker.py")], cwd=source,
                    input=json.dumps(request), text=True, capture_output=True,
                    env={**os.environ, "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1"},
                    timeout=args.worker_timeout, check=True)
                row["output"] = json.loads(process.stdout)
                validate_worker_output(row["output"], request, item)
                row["semantic_result_sha256"] = semantic_fingerprint(row["output"]["result"])
                if any(source_manifest(path)["source_manifest_sha256"] != report[key]["source_manifest_sha256"]
                       for path, key in ((root, "candidate_manifest"), (control, "control_manifest"))):
                    report["source_integrity"] = False
                    raise ValueError("source changed while worker was running; result invalid")
            except (subprocess.SubprocessError, ValueError, OSError) as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"
                if isinstance(exc, subprocess.CalledProcessError):
                    row["stderr"] = exc.stderr[-5000:]
                if isinstance(exc, subprocess.TimeoutExpired):
                    row["resource_limit"] = {"worker_wall_timeout_s": args.worker_timeout,
                        "scope": "external CPU/resource limit; not a measured simulation makespan"}
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
    if args.phase == "release":
        return 0 if summary["declared_headless_stage_pass"] else 2
    return 0 if (report["finished"] and summary["candidate_contact_free"] and
                 summary["candidate_runs_completed"] == summary["candidate_runs_observed"] and
                 summary["pinned_sih_20pct_gate"] is not False) else 2


if __name__ == "__main__":
    raise SystemExit(main())
