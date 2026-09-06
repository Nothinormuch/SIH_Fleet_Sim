"""Source-isolated worker: import controllers only from the requested source tree.

The new harness supplies a serialized world, so an unchanged BIOS6 checkout can run
new fixtures without copying or changing a single controller/world source file.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import sys
import time
from pathlib import Path

try:
    import resource
except ImportError:  # Windows has no resource module; do not fabricate peak memory.
    resource = None


def decode_scenario(payload):
    # These imports happen only after main installs the explicitly selected tree.
    from src.amr import Task
    from src.environment import Warehouse
    from src.scenarios import ObstacleEvent, Scenario
    from src.settings import NetSpec

    data = dict(payload)
    env = dict(data["env"])
    env["grid"] = tuple(tuple(row) for row in env["grid"])
    for key in ("stations", "docks"):
        env[key] = tuple(tuple(cell) for cell in env[key])
    data["env"] = Warehouse(**env)
    network = dict(data["net"])
    network["dead_zones"] = tuple(tuple(zone) for zone in network["dead_zones"])
    data["net"] = NetSpec(**network)
    data["starts"] = [tuple(cell) for cell in data["starts"]]
    data["humans"] = [[tuple(cell) for cell in route] for route in data["humans"]]
    data["obstacles"] = [ObstacleEvent(**{**event, "cell": tuple(event["cell"])})
                         for event in data["obstacles"]]

    def task(row):
        return Task(**{**row, "pick": tuple(row["pick"]), "drop": tuple(row["drop"])})
    data["assignments"] = [[task(row) for row in queue] for queue in data["assignments"]]
    data["unassigned"] = [task(row) for row in data["unassigned"]]
    return Scenario(**data)


def main():
    request = json.load(sys.stdin)
    root = Path(request["source_root"]).resolve()
    sys.path.insert(0, str(root))
    from src.main import run_scenario
    from src.settings import DEFAULT
    import src.main as controller_module

    if not Path(controller_module.__file__).resolve().is_relative_to(root):
        raise RuntimeError("refusing mixed source-tree imports")
    physical = {key: dataclasses.asdict(DEFAULT)[key]
                for key in ("robot", "rates", "cell_m")}
    if physical != request["physical_config"]:
        raise ValueError("physical/safety/rate constants differ between control and candidate")
    traffic_overrides = request.get("traffic_overrides", {})
    if any(key != "v7_passage_release" for key in traffic_overrides):
        raise ValueError("only the registered passage-release ablation may override controller config")
    cfg = (dataclasses.replace(DEFAULT, traffic=dataclasses.replace(
                DEFAULT.traffic, **traffic_overrides)) if traffic_overrides else DEFAULT)
    scenario = decode_scenario(request["scenario"])
    cfg = dataclasses.replace(cfg, net=scenario.net, seed=scenario.seed)
    allocation = request["allocation"]
    if allocation == "preassigned":
        # Mirrors the declared static baseline, with exactly the supplied task catalog.
        if scenario.unassigned:
            scenario.assignments = [[] for _ in scenario.starts]
            for index, task in enumerate(scenario.unassigned):
                scenario.assignments[index % len(scenario.starts)].append(task)
            scenario.unassigned = []
        allocation = None
        scenario.use_auction = False
    start_wall, start_cpu = time.perf_counter(), time.process_time()
    result = run_scenario(scenario, request["policy"], seed=scenario.seed, cfg=cfg,
                          allocation_policy=allocation).to_dict()
    config = dataclasses.asdict(cfg)
    json.dump({
        "result": result, "config": config,
        "config_sha256": hashlib.sha256(json.dumps(
            config, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "controller_source_root": str(root),
        "physical_config": physical,
        "scenario_input_sha256": hashlib.sha256(json.dumps(
            request["scenario"], sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "wall_time_s": time.perf_counter()-start_wall,
        "process_cpu_s": time.process_time()-start_cpu,
        "max_rss_platform_units": (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                                   if resource is not None else None),
        "memory_scope": "ru_maxrss bytes on macOS, KiB on Linux; process peak",
        "timing_scope": "headless process wall/CPU on current host; other workloads may share "
                        "the host; not live controller latency or hard-real-time evidence",
    }, sys.stdout, allow_nan=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
