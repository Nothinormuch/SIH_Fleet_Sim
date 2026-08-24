"""Web-dashboard unit contract and rendering regressions."""

from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import subprocess

import pytest

from src.environment import RACK
from src.main import run_for_dashboard


ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")


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
