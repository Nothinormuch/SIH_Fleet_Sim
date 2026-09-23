"""Facility and AMR profile validation for vendor-neutral deployments."""

import json
from pathlib import Path

import pytest

from src.site_config import SiteConfigError, load_site_config


ROOT = Path(__file__).resolve().parents[1]


def test_example_site_config_is_connected_and_usable():
    site = load_site_config(ROOT / "config" / "site.example.json")

    assert site.fleet_id == "warehouse-demo-a"
    assert site.environment.name == "vendor-neutral-demo-warehouse"
    assert len(site.starts) == 3
    assert len(site.fingerprint) == 64
    assert site.config.robot.v_max == 1.2
    assert all(site.environment.passable(cell) for cell in site.starts)


def test_site_config_rejects_unknown_fields_and_unsafe_lane_pitch(tmp_path):
    original = json.loads(
        (ROOT / "config" / "site.example.json").read_text(encoding="utf-8")
    )
    original["magic_override"] = True
    path = tmp_path / "unknown.json"
    path.write_text(json.dumps(original), encoding="utf-8")
    with pytest.raises(SiteConfigError, match="unknown site fields"):
        load_site_config(path)

    del original["magic_override"]
    original["cell_m"] = 0.5
    path.write_text(json.dumps(original), encoding="utf-8")
    with pytest.raises(SiteConfigError, match="two robot radii"):
        load_site_config(path)


def test_site_config_rejects_disconnected_operational_cells(tmp_path):
    data = json.loads(
        (ROOT / "config" / "site.example.json").read_text(encoding="utf-8")
    )
    # Turn the entire x=10 aisle into a wall, isolating both docks at x=11.
    for row in data["warehouse"]["grid"]:
        row[10] = 1
    path = tmp_path / "disconnected.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SiteConfigError, match="passable component"):
        load_site_config(path)
