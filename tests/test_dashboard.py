"""Web-dashboard unit contract and rendering regressions."""

from __future__ import annotations

import json
import http.client
import math
from pathlib import Path
import shutil
import subprocess
import threading
from http.server import ThreadingHTTPServer

import pytest

from src.environment import RACK
from src.main import run_for_dashboard
from backend.server import Handler, RequestValidationError, parse_run_request
from src.scenarios import SHOWCASE_SCENARIOS


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
    assert parsed["policy"] == "BIOS_PIBT.5"


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
