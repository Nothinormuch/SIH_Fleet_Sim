"""Validated, vendor-neutral warehouse and AMR deployment configuration.

The benchmark scenarios are evidence fixtures, not a real facility database.  A
deployed node instead loads this file on every robot.  Vendor drivers normalize their
controller data to SI units; BIOS consumes the same map, footprint, motion envelope,
energy envelope, and start cells regardless of the controller brand.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, fields, replace
from pathlib import Path

from .environment import DOCK, FREE, RACK, STATION, Warehouse
from .settings import DEFAULT, Config, RobotSpec


class SiteConfigError(ValueError):
    """A deployment file cannot be interpreted safely and consistently."""


@dataclass(frozen=True)
class SiteDefinition:
    environment: Warehouse
    starts: tuple[tuple[int, int], ...]
    config: Config
    fleet_id: str
    map_version: str
    map_frame: str
    robot_model: str
    fingerprint: str


_PROFILE_FIELDS = {field.name for field in fields(RobotSpec)}
_STRICTLY_POSITIVE = {
    "radius_m", "v_max", "a_max", "omega_max", "alpha_max",
    "sense_radius_m", "battery_full_wh", "draw_move_w", "draw_idle_w",
    "charge_w", "max_payload_kg",
}
_NON_NEGATIVE = {
    "reaction_s", "safety_margin_m", "omni_stop_m", "safety_cone_rad",
    "static_cone_rad", "v_turn",
}


def _object(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise SiteConfigError(f"{label} must be an object")
    return value


def _text(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SiteConfigError(f"{label} must be a non-empty string")
    if len(value) > 128:
        raise SiteConfigError(f"{label} is too long")
    return value.strip()


def _integer(value, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SiteConfigError(f"{label} must be an integer")
    if not minimum <= value <= maximum:
        raise SiteConfigError(f"{label} must be between {minimum} and {maximum}")
    return value


def _finite_number(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SiteConfigError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise SiteConfigError(f"{label} must be finite")
    return number


def _cell(value, label: str, width: int, height: int) -> tuple[int, int]:
    if (not isinstance(value, list) or len(value) != 2
            or any(isinstance(item, bool) or not isinstance(item, int)
                   for item in value)):
        raise SiteConfigError(f"{label} must be [x, y] integers")
    cell = (value[0], value[1])
    if not (0 <= cell[0] < width and 0 <= cell[1] < height):
        raise SiteConfigError(f"{label} is outside the warehouse")
    return cell


def _cells(value, label: str, width: int, height: int) -> tuple[tuple[int, int], ...]:
    if not isinstance(value, list):
        raise SiteConfigError(f"{label} must be a list")
    cells = tuple(_cell(item, f"{label}[{index}]", width, height)
                  for index, item in enumerate(value))
    if len(set(cells)) != len(cells):
        raise SiteConfigError(f"{label} contains duplicate cells")
    return cells


def _validate_connectivity(environment: Warehouse,
                           operational: tuple[tuple[int, int], ...]) -> None:
    if not operational:
        return
    reachable = {operational[0]}
    pending = [operational[0]]
    while pending:
        current = pending.pop()
        for neighbor in environment.neighbors(current):
            if neighbor not in reachable:
                reachable.add(neighbor)
                pending.append(neighbor)
    missing = [cell for cell in operational if cell not in reachable]
    if missing:
        raise SiteConfigError(
            f"stations, docks and starts must share a passable component; "
            f"unreachable: {missing[:8]}"
        )


def load_site_config(path: str | Path) -> SiteDefinition:
    """Load and fail closed on an invalid facility or robot profile."""
    source = Path(path)
    try:
        raw = source.read_bytes()
        data = json.loads(raw)
    except OSError as exc:
        raise SiteConfigError(f"cannot read site config: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SiteConfigError(f"site config is not valid UTF-8 JSON: {exc}") from exc
    root = _object(data, "site config")
    allowed_root = {
        "schema_version", "fleet_id", "map_version", "map_frame",
        "robot_model", "cell_m", "warehouse", "starts", "robot_profile",
    }
    unknown_root = sorted(set(root) - allowed_root)
    if unknown_root:
        raise SiteConfigError(f"unknown site fields: {', '.join(unknown_root)}")
    if root.get("schema_version") != 1:
        raise SiteConfigError("schema_version must be 1")
    fleet_id = _text(root.get("fleet_id"), "fleet_id")
    map_version = _text(root.get("map_version"), "map_version")
    map_frame = _text(root.get("map_frame", "map"), "map_frame")
    robot_model = _text(root.get("robot_model"), "robot_model")
    cell_m = _finite_number(root.get("cell_m"), "cell_m")
    if not 0.25 <= cell_m <= 20.0:
        raise SiteConfigError("cell_m must be between 0.25 and 20 metres")

    warehouse_data = _object(root.get("warehouse"), "warehouse")
    allowed_warehouse = {"name", "width", "height", "grid", "stations", "docks"}
    unknown_warehouse = sorted(set(warehouse_data) - allowed_warehouse)
    if unknown_warehouse:
        raise SiteConfigError(
            f"unknown warehouse fields: {', '.join(unknown_warehouse)}"
        )
    width = _integer(warehouse_data.get("width"), "warehouse.width", 3, 500)
    height = _integer(warehouse_data.get("height"), "warehouse.height", 3, 500)
    grid_data = warehouse_data.get("grid")
    if not isinstance(grid_data, list) or len(grid_data) != height:
        raise SiteConfigError("warehouse.grid row count must equal height")
    rows: list[tuple[int, ...]] = []
    for y, row in enumerate(grid_data):
        if not isinstance(row, list) or len(row) != width:
            raise SiteConfigError(f"warehouse.grid[{y}] width must equal width")
        parsed = []
        for x, tile in enumerate(row):
            if isinstance(tile, bool) or tile not in (FREE, RACK, STATION, DOCK):
                raise SiteConfigError(
                    f"warehouse.grid[{y}][{x}] must be 0, 1, 2 or 3"
                )
            parsed.append(tile)
        rows.append(tuple(parsed))
    stations = _cells(
        warehouse_data.get("stations"), "warehouse.stations", width, height,
    )
    docks = _cells(warehouse_data.get("docks"), "warehouse.docks", width, height)
    starts = _cells(root.get("starts"), "starts", width, height)
    if len(starts) < 3:
        raise SiteConfigError("starts must contain at least three AMR start cells")
    if not stations:
        raise SiteConfigError("warehouse requires at least one station")
    if not docks:
        raise SiteConfigError("warehouse requires at least one charging dock")
    for station in stations:
        if rows[station[1]][station[0]] != STATION:
            raise SiteConfigError(f"station {station} is not marked as grid tile 2")
    for dock in docks:
        if rows[dock[1]][dock[0]] != DOCK:
            raise SiteConfigError(f"dock {dock} is not marked as grid tile 3")
    for start in starts:
        if rows[start[1]][start[0]] == RACK:
            raise SiteConfigError(f"start {start} is inside a rack")

    profile_data = _object(root.get("robot_profile"), "robot_profile")
    unknown_profile = sorted(set(profile_data) - _PROFILE_FIELDS)
    if unknown_profile:
        raise SiteConfigError(
            f"unknown robot_profile fields: {', '.join(unknown_profile)}"
        )
    profile_values = {}
    for name, value in profile_data.items():
        number = _finite_number(value, f"robot_profile.{name}")
        if name in _STRICTLY_POSITIVE and number <= 0.0:
            raise SiteConfigError(f"robot_profile.{name} must be positive")
        if name in _NON_NEGATIVE and number < 0.0:
            raise SiteConfigError(f"robot_profile.{name} must be non-negative")
        profile_values[name] = number
    robot = replace(DEFAULT.robot, **profile_values)
    if robot.v_turn > robot.v_max:
        raise SiteConfigError("robot_profile.v_turn cannot exceed v_max")
    required_pitch = 2.0 * (robot.radius_m + robot.safety_margin_m)
    if cell_m + 1e-9 < required_pitch:
        raise SiteConfigError(
            "cell_m is smaller than two robot radii plus two safety margins"
        )

    environment = Warehouse(
        width=width,
        height=height,
        grid=tuple(rows),
        stations=stations,
        docks=docks,
        name=_text(warehouse_data.get("name"), "warehouse.name"),
    )
    _validate_connectivity(environment, starts + stations + docks)
    canonical = json.dumps(root, sort_keys=True, separators=(",", ":"),
                           allow_nan=False).encode("utf-8")
    fingerprint = hashlib.sha256(canonical).hexdigest()
    return SiteDefinition(
        environment=environment,
        starts=starts,
        config=replace(DEFAULT, cell_m=cell_m, robot=robot),
        fleet_id=fleet_id,
        map_version=map_version,
        map_frame=map_frame,
        robot_model=robot_model,
        fingerprint=fingerprint,
    )
