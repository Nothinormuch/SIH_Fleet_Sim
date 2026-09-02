"""Web-dashboard unit contract and rendering regressions."""

from __future__ import annotations

import json
import http.client
import math
from pathlib import Path
import shutil
import subprocess
import threading
from dataclasses import replace
from http.server import ThreadingHTTPServer

import pytest

from src.environment import RACK
from src.amr import POLICY_BIOS_PIBT_V6
from src.main import run_for_dashboard, run_scenario
from backend.server import Handler, RequestValidationError, parse_run_request
from src.scenarios import SHOWCASE_SCENARIOS
from src.settings import DEFAULT
from src.task_allocation import ALLOCATION_AUCTION_BUNDLE
from src.world import World


ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")


def test_run_request_validation_rejects_clamping_and_combined_overload():
    with pytest.raises(RequestValidationError, match="robots must be between"):
        parse_run_request({"robots": 101})
    with pytest.raises(RequestValidationError, match="duration must be between"):
        parse_run_request({"duration": 901})
    with pytest.raises(RequestValidationError, match="robot-seconds"):
        parse_run_request({"robots": 100, "duration": 900})
    with pytest.raises(RequestValidationError, match="whole numbers"):
        parse_run_request({"robots": 3.5})
    with pytest.raises(RequestValidationError, match="numbers"):
        parse_run_request({"seed": 1e999})
    with pytest.raises(RequestValidationError, match="JSON object"):
        parse_run_request([])

    parsed = parse_run_request({"robots": "100", "duration": "120", "seed": "9"})
    assert parsed["robots"] == 100
    assert parsed["duration"] == 120.0
    assert parsed["seed"] == 9
    assert parsed["policy"] == "BIOS_PIBT.6"


def test_jury_showcases_share_the_energy_aware_auction_profile():
    assert list(SHOWCASE_SCENARIOS) == [
        "showcase_open_floor",
        "showcase_chokepoint",
        "showcase_human",
        "showcase_dead_zone",
        "showcase_grand_challenge",
    ]
    for name, profile in SHOWCASE_SCENARIOS.items():
        scenario = profile["builder"](
            n_robots=profile["robots"], seed=profile["seed"])
        assert scenario.name == name
        assert scenario.use_auction
        assert len(scenario.initial_battery_fracs) == scenario.n_robots
        assert len(set(scenario.initial_battery_fracs)) > 1
        assert scenario.unassigned
        assert not any(scenario.assignments)
        assert {task.cargo_type for task in scenario.unassigned}.issubset(
            {"normal", "fragile", "heavy", "hazardous"})


def test_showcase_pedestrians_work_across_the_warehouse_without_entering_racks():
    expected_counts = {"showcase_human": 3, "showcase_grand_challenge": 5}
    for name, expected in expected_counts.items():
        profile = SHOWCASE_SCENARIOS[name]
        scenario = profile["builder"](
            n_robots=profile["robots"], seed=profile["seed"])
        assert len(scenario.humans) == expected
        crew_cells = [cell for route in scenario.humans for cell in route]
        for route in scenario.humans:
            xs = [cell[0] for cell in route]
            ys = [cell[1] for cell in route]
            assert max(xs) - min(xs) >= 2
            assert max(ys) - min(ys) >= scenario.env.height // 5
            assert all(0 < y < scenario.env.height - 1 for y in ys)
        assert max(cell[0] for cell in crew_cells) - min(
            cell[0] for cell in crew_cells) >= 2 * scenario.env.width // 3
        world = World(scenario.env, DEFAULT, seed=scenario.seed)
        for index, route in enumerate(scenario.humans):
            human = world.add_human(f"H{index + 1}", route)
            assert not human.uses_apron
            assert not world._human_hits_static(
                (human.x, human.y), human.radius, human.uses_apron)

        # Walk every route for three minutes without AMRs. This covers multiple shelf
        # banks and cross-aisles rather than a short decorative patrol.
        for _ in range(int(180 * DEFAULT.rates.world_hz)):
            world.step(1.0 / DEFAULT.rates.world_hz, {})
            for human in world.humans.values():
                assert not world._human_hits_static(
                    (human.x, human.y), human.radius, human.uses_apron)

        assert all(human.distance_travelled > 40.0 for human in world.humans.values())
        assert all(human.work_visits >= 2 for human in world.humans.values())


def test_grand_challenge_workers_spawn_safely_inside_shared_warehouse_space():
    scenario = SHOWCASE_SCENARIOS["showcase_grand_challenge"]["builder"](
        n_robots=10, seed=1)
    world = World(scenario.env, DEFAULT, seed=scenario.seed)
    for index, cell in enumerate(scenario.starts):
        world.add_robot(f"AMR{index + 1:02d}", cell, 0.0)
    humans = [
        world.add_human(f"H{index + 1}", route)
        for index, route in enumerate(scenario.humans)
    ]

    protected = (
        DEFAULT.robot.radius_m + humans[0].radius
        + DEFAULT.robot.omni_stop_m + 0.16
    )
    for human in humans:
        assert min(
            math.hypot(human.x - robot.x, human.y - robot.y)
            for robot in world.robots.values()
        ) > protected
        assert not human.uses_apron
        assert 0.0 < human.x < scenario.env.width * DEFAULT.cell_m
        assert 0.0 < human.y < scenario.env.height * DEFAULT.cell_m


def test_grand_challenge_finishes_inside_its_dashboard_evidence_window():
    profile = SHOWCASE_SCENARIOS["showcase_grand_challenge"]
    scenario = replace(
        profile["builder"](
            n_robots=profile["robots"], seed=profile["seed"]),
        duration_s=float(profile["duration"]),
    )

    result = run_scenario(
        scenario, POLICY_BIOS_PIBT_V6, seed=profile["seed"],
        allocation_policy=ALLOCATION_AUCTION_BUNDLE,
    )

    assert result.completed_all
    assert result.tasks_completed == result.tasks_announced == 16
    assert result.makespan_s < profile["duration"]
    assert result.contacts_robot_robot == 0
    assert result.contacts_robot_human == 0
    assert result.contacts_robot_rack == 0


def test_grand_challenge_liveness_does_not_depend_on_pedestrian_interference():
    """Mixed worker routing must not be the perturbation that unsticks the AMRs."""
    profile = SHOWCASE_SCENARIOS["showcase_grand_challenge"]
    scenario = replace(
        profile["builder"](
            n_robots=profile["robots"], seed=profile["seed"]),
        humans=[], duration_s=float(profile["duration"]),
    )

    result = run_scenario(
        scenario, POLICY_BIOS_PIBT_V6, seed=profile["seed"],
        allocation_policy=ALLOCATION_AUCTION_BUNDLE,
    )

    assert result.completed_all
    assert result.tasks_completed == result.tasks_announced == 16
    assert result.contacts_robot_robot == 0
    assert result.contacts_robot_rack == 0


def test_loaded_robot_preempts_idle_parking_cycle_in_grand_challenge():
    """Seed 4 previously left one loaded AMR behind two optional parking trips."""
    profile = SHOWCASE_SCENARIOS["showcase_grand_challenge"]
    scenario = replace(
        profile["builder"](n_robots=8, seed=4),
        duration_s=float(profile["duration"]),
    )

    result = run_scenario(
        scenario, POLICY_BIOS_PIBT_V6, seed=4,
        allocation_policy=ALLOCATION_AUCTION_BUNDLE,
    )

    assert result.completed_all
    assert result.tasks_completed == result.tasks_announced == 16
    assert result.contacts_robot_robot == 0
    assert result.contacts_robot_human == 0
    assert result.contacts_robot_rack == 0


def test_human_route_rejects_a_rack_endpoint_instead_of_clipping_through_it():
    profile = SHOWCASE_SCENARIOS["showcase_human"]
    scenario = profile["builder"](
        n_robots=profile["robots"], seed=profile["seed"])
    rack = next(
        (x, y)
        for y, row in enumerate(scenario.env.grid)
        for x, value in enumerate(row)
        if value == RACK
    )
    free = next(iter(scenario.env.free_cells()))
    with pytest.raises(ValueError, match="is not passable"):
        World(scenario.env, DEFAULT).add_human("H-bad", [rack, free])


def test_dashboard_run_is_post_only_and_security_headers_are_present():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        conn.request("GET", "/api/run")
        response = conn.getresponse()
        assert response.status == 405
        assert response.getheader("Allow") == "POST"
        assert response.getheader("X-Content-Type-Options") == "nosniff"
        assert response.getheader("Content-Security-Policy")
        response.read()

        conn.request(
            "POST", "/api/run", body="[]",
            headers={"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        assert response.status == 400
        assert json.loads(response.read())["error"] == "request body must be a JSON object"
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.skipif(NODE is None, reason="Node is required for the canvas unit test")
def test_canvas_converts_metric_poses_to_grid_cells():
    """The V2 1.4 m pitch must not be treated as one canvas cell per metre."""
    script = r"""
global.window = {devicePixelRatio: 1};
const {View} = require('./frontend/js/environment.js');
const ctx = {setTransform() {}, clearRect() {}};
const canvas = {
  getContext() { return ctx; },
  getBoundingClientRect() { return {width: 620, height: 420}; },
};
const view = new View(canvas);
view.resize({width: 31, height: 21}, 1.4);
const screen = view.worldToScreen(2.5 * 1.4, 1.5 * 1.4);
const rect = view.cellRect(2, 1);
const centre = [rect[0] + rect[2] / 2, rect[1] + rect[3] / 2];
if (Math.abs(screen[0] - centre[0]) > 1e-9 ||
    Math.abs(screen[1] - centre[1]) > 1e-9) process.exit(2);
console.log(JSON.stringify({screen, centre}));
"""
    completed = subprocess.run(
        [NODE, "-e", script], cwd=ROOT, check=True,
        capture_output=True, text=True,
    )
    result = json.loads(completed.stdout)
    assert result["screen"] == result["centre"]


@pytest.mark.skipif(NODE is None, reason="Node is required for the canvas unit test")
def test_canvas_fits_the_real_pedestrian_apron_instead_of_clipping_workers():
    """The fallback camera must frame the worker route, not only the AMR grid."""
    script = r"""
global.window = {devicePixelRatio: 1};
const {View} = require('./frontend/js/environment.js');
const ctx = {setTransform() {}, clearRect() {}};
const canvas = {
  getContext() { return ctx; },
  getBoundingClientRect() { return {width: 900, height: 600}; },
};
const map = {
  width: 31,
  height: 21,
  pedestrian_apron: true,
  pedestrian_apron_offset_m: 3.5,
  pedestrian_apron_width_m: 1.204,
};
const view = new View(canvas);
view.resize(map, 1.4);
const points = [
  view.worldToScreen(-3.5, -3.5),
  view.worldToScreen(31 * 1.4 + 3.5, 21 * 1.4 + 3.5),
];
for (const [x, y] of points) {
  if (!(x >= 0 && x <= 900 && y >= 0 && y <= 600)) process.exit(2);
}
if (!(view.apronMarginCells > 2.5)) process.exit(3);
console.log(JSON.stringify({points, margin: view.apronMarginCells}));
"""
    completed = subprocess.run(
        [NODE, "-e", script], cwd=ROOT, check=True,
        capture_output=True, text=True,
    )
    result = json.loads(completed.stdout)
    assert result["margin"] > 2.5
    assert all(0 <= value <= limit for point in result["points"]
               for value, limit in zip(point, (900, 600)))


def test_dashboard_reports_showcase_workers_on_the_shared_warehouse_floor():
    payload = run_for_dashboard(
        "showcase_human", POLICY_BIOS_PIBT_V6,
        robots=5, seed=7, duration=10,
        allocation_policy=ALLOCATION_AUCTION_BUNDLE,
    )
    warehouse = payload["map"]
    assert not warehouse["pedestrian_apron"]
    assert "pedestrian_apron_offset_m" not in warehouse
    assert "pedestrian_apron_width_m" not in warehouse


def test_dashboard_metric_frames_remain_inside_free_map_cells():
    payload = run_for_dashboard(
        "dead_zone_mesh", "BIOS_PIBT.2", robots=4, seed=20, duration=20,
    )
    assert payload["meta"]["pose_units"] == "metres"
    cell_m = payload["meta"]["cell_m"]
    warehouse = payload["map"]

    for frame in payload["frames"]:
        for robot in frame["robots"]:
            cell = (math.floor(robot["x"] / cell_m),
                    math.floor(robot["y"] / cell_m))
            x, y = cell
            assert 0 <= x < warehouse["width"]
            assert 0 <= y < warehouse["height"]
            assert warehouse["grid"][y][x] != RACK

    assert payload["summary"]["contacts_robot_rack"] == 0


def test_dashboard_completion_uses_unique_tasks_and_includes_the_final_frame():
    payload = run_for_dashboard(
        "showcase_open_floor", "BIOS_PIBT.5",
        robots=4, seed=4, duration=180, allocation_policy="auction",
    )
    summary = payload["summary"]
    assert summary["completed_all"]
    assert summary["tasks_completed"] == summary["tasks_announced"] == 8
    assert payload["frames"][-1]["tasks_completed"] == 8
    assert all(0 <= frame["tasks_completed"] <= 8 for frame in payload["frames"])
