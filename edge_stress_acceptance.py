"""Repeatable broader acceptance. No result is discarded or promoted on failure."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from src.amr import POLICY_BIOS_PIBT_V6
from src.edge_lab import _available_ports
from src.hil_demo import run_hil_demo
from src.main import run_scenario
from src.scenarios import SCENARIOS
from src.task_allocation import ALLOCATION_AUCTION_BUNDLE

SCENARIOS_TO_TEST = ("edge_overlap", "edge_chokepoint", "blocked_aisle",
                     "robot_failure_reassignment", "edge_human_crossing")


def run_case(case):
    name, count, seed, mode = case
    scenario = SCENARIOS[name](n_robots=count, seed=seed)
    if mode == "headless":
        raw = run_scenario(scenario, POLICY_BIOS_PIBT_V6, seed=seed,
                           allocation_policy=ALLOCATION_AUCTION_BUNDLE).to_dict()
        passed = raw["completed_all"] and all(raw[key] == 0 for key in
            ("contacts_robot_robot", "contacts_robot_human", "contacts_robot_rack"))
    else:
        base = _available_ports(count)
        raw = run_hil_demo(name, robots=count, seed=seed,
                           duration_s=scenario.duration_s,
                           peer_port=base, sensor_base_port=base+1,
                           actuator_base_port=base+1+count,
                           require_task_completion=True,
                           finish_when_complete=True,
                           # Exercise the visual telemetry path even in CLI runs.
                           on_snapshot=lambda snapshot: None)
        passed = raw["success"]
    return {"scenario": name, "robots": count, "seed": seed,
            "pass": bool(passed), "raw": raw}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("headless", "live"), default="headless")
    parser.add_argument("--robots", default="3,6,10")
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--scenarios", default=",".join(SCENARIOS_TO_TEST))
    parser.add_argument("--output", default="artifacts/deployment/edge-stress-acceptance.json")
    args = parser.parse_args()
    counts = [int(n) for n in args.robots.split(",")]
    names = args.scenarios.split(",")
    if (not counts or any(n < 3 or n > 10 for n in counts) or args.seeds < 1
            or args.jobs < 1 or any(n not in SCENARIOS for n in names)):
        parser.error("require 3–10 robots, positive seeds/jobs and registered scenarios")
    if args.mode == "live" and args.jobs != 1:
        parser.error("live timing tests must run serially: --jobs 1")
    root = Path(__file__).resolve().parent
    files = sorted((root / "src").glob("*.py")) + [Path(__file__).resolve()]
    provenance = {
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "source_sha256": {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in files},
    }
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "mode": args.mode,
              "provenance": provenance, "runs": [], "pass": False,
              "scope": "Finite campaign; live timing is host-dependent, not deterministic. "
                       "Humans vary speed/pause/direction but retain local collision avoidance."}
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    cases = [(name, n, s, args.mode) for name in names for n in counts for s in range(args.seeds)]
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(run_case, case): case for case in cases}
        for future in as_completed(futures):
            case = futures[future]
            try:
                row = future.result()
            except Exception as exc:
                row = {"scenario": case[0], "robots": case[1], "seed": case[2],
                       "pass": False, "error": f"{type(exc).__name__}: {exc}"}
            report["runs"].append(row)
            report["runs"].sort(key=lambda r: (r["scenario"], r["robots"], r["seed"]))
            report["finished"] = len(report["runs"]) == len(cases)
            report["pass"] = report["finished"] and all(r["pass"] for r in report["runs"])
            target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
            raw = row.get("raw", {})
            print(f'{len(report["runs"])}/{len(cases)} {case[:3]} '
                  f'{"PASS" if row["pass"] else "FAIL"} '
                  f'{raw.get("tasks_completed", "?")} tasks {row.get("error", "")}', flush=True)
    return 0 if report["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
