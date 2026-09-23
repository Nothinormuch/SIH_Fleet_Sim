"""Authenticated, announcement-only bridge from a WMS task file to BIOS peers."""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path

from . import messages as msg
from .site_config import SiteConfigError, load_site_config
from .settings import DEFAULT
from .transport import DEFAULT_GROUP, DEFAULT_PORT, UdpMulticastTransport


class TaskInputError(ValueError):
    pass


@dataclass(frozen=True)
class AnnouncedTask:
    task_id: str
    pickup: tuple[int, int]
    drop: tuple[int, int]
    cargo_type: str = "normal"
    cargo_weight_kg: float = 0.0
    priority: int = 1
    deadline_s: float | None = None
    generation: int = 0


def _cell(value, name: str) -> tuple[int, int]:
    if (not isinstance(value, list) or len(value) != 2
            or any(isinstance(item, bool) or not isinstance(item, int)
                   for item in value)):
        raise TaskInputError(f"{name} must be [x, y] integers")
    return value[0], value[1]


def load_task_file(path: str | Path, site_config: str | Path) -> list[AnnouncedTask]:
    try:
        site = load_site_config(site_config)
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except SiteConfigError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TaskInputError(f"cannot read task file: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise TaskInputError("task file must be an object with schema_version 1")
    if set(data) - {"schema_version", "tasks"}:
        raise TaskInputError("task file contains unknown top-level fields")
    source_tasks = data.get("tasks")
    if not isinstance(source_tasks, list) or not source_tasks:
        raise TaskInputError("tasks must be a non-empty list")
    tasks: list[AnnouncedTask] = []
    ids = set()
    for index, raw in enumerate(source_tasks):
        if not isinstance(raw, dict):
            raise TaskInputError(f"tasks[{index}] must be an object")
        allowed = {
            "task_id", "pickup", "drop", "cargo_type", "cargo_weight_kg",
            "priority", "deadline_s", "generation",
        }
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise TaskInputError(
                f"tasks[{index}] has unknown fields: {', '.join(unknown)}"
            )
        task_id = raw.get("task_id")
        if not isinstance(task_id, str) or not task_id or len(task_id) > 64:
            raise TaskInputError(f"tasks[{index}].task_id is invalid")
        if task_id in ids:
            raise TaskInputError(f"duplicate task_id {task_id!r}")
        ids.add(task_id)
        pickup = _cell(raw.get("pickup"), f"tasks[{index}].pickup")
        drop = _cell(raw.get("drop"), f"tasks[{index}].drop")
        if not site.environment.passable(pickup):
            raise TaskInputError(f"task {task_id} pickup is not passable")
        if not site.environment.passable(drop):
            raise TaskInputError(f"task {task_id} drop is not passable")
        cargo_type = raw.get("cargo_type", "normal")
        if cargo_type not in ("normal", "fragile", "heavy", "hazardous"):
            raise TaskInputError(f"task {task_id} has invalid cargo_type")
        weight = raw.get("cargo_weight_kg", 0.0)
        if (isinstance(weight, bool) or not isinstance(weight, (int, float))
                or not math.isfinite(float(weight)) or float(weight) < 0.0):
            raise TaskInputError(f"task {task_id} has invalid cargo weight")
        if float(weight) > site.config.robot.max_payload_kg:
            raise TaskInputError(
                f"task {task_id} exceeds configured AMR payload capacity"
            )
        priority = raw.get("priority", 1)
        if (isinstance(priority, bool) or not isinstance(priority, int)
                or not 1 <= priority <= 100):
            raise TaskInputError(f"task {task_id} priority must be 1..100")
        deadline = raw.get("deadline_s")
        if (deadline is not None
                and (isinstance(deadline, bool)
                     or not isinstance(deadline, (int, float))
                     or not math.isfinite(float(deadline))
                     or float(deadline) < 0.0)):
            raise TaskInputError(f"task {task_id} has invalid deadline_s")
        generation = raw.get("generation", 0)
        if (isinstance(generation, bool) or not isinstance(generation, int)
                or not 0 <= generation <= 2 ** 31 - 1):
            raise TaskInputError(f"task {task_id} has invalid generation")
        tasks.append(AnnouncedTask(
            task_id=task_id,
            pickup=pickup,
            drop=drop,
            cargo_type=cargo_type,
            cargo_weight_kg=float(weight),
            priority=priority,
            deadline_s=None if deadline is None else float(deadline),
            generation=generation,
        ))
    return tasks


def announce_task_file(path: str | Path, site_config: str | Path,
                       shared_key: str, group: str = DEFAULT_GROUP,
                       port: int = DEFAULT_PORT, interface: str = "0.0.0.0",
                       repeats: int = 2, repeat_interval_s: float = 1.0) -> dict:
    if len(shared_key.encode("utf-8")) < 16:
        raise TaskInputError("fleet pre-shared key must contain at least 16 bytes")
    if not 1 <= repeats <= 100:
        raise TaskInputError("repeats must be between 1 and 100")
    if not 0.0 <= repeat_interval_s <= 60.0:
        raise TaskInputError("repeat interval must be between 0 and 60 seconds")
    site = load_site_config(site_config)
    tasks = load_task_file(path, site_config)
    transport = UdpMulticastTransport(
        "WMS", group=group, port=port, interface=interface,
        shared_key=shared_key, require_auth=True,
    )
    sequence = 0
    try:
        for repeat in range(repeats):
            for task in tasks:
                sequence += 1
                transport.send(msg.task_new(
                    "WMS", sequence, 0.0, task.task_id,
                    task.pickup, task.drop,
                    epoch=0,
                    bid_until=DEFAULT.traffic.auction_bid_window_s,
                    cargo_type=task.cargo_type,
                    cargo_weight=task.cargo_weight_kg,
                    priority=task.priority,
                    deadline=task.deadline_s,
                    generation=task.generation,
                    descriptor_deadline_s=task.deadline_s,
                ))
            if repeat + 1 < repeats:
                time.sleep(repeat_interval_s)
    finally:
        transport.close()
    return {
        "success": transport.stats["send_failed"] == 0,
        "role": "announcement_only_task_source",
        "selects_winners": False,
        "assigns_robots": False,
        "fleet_id": site.fleet_id,
        "map_version": site.map_version,
        "site_fingerprint_sha256": site.fingerprint,
        "tasks": len(tasks),
        "repeats": repeats,
        "messages_sent": transport.stats["sent"],
        "bytes_sent": transport.stats["bytes_sent"],
        "send_failed": transport.stats["send_failed"],
        "destination": f"{group}:{port}",
        "authenticated": True,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and announce WMS tasks without selecting AMRs",
    )
    parser.add_argument("--tasks", required=True)
    parser.add_argument("--site-config", required=True)
    parser.add_argument("--group", default=DEFAULT_GROUP)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--interface", default="0.0.0.0")
    parser.add_argument("--psk-env", default="SIH_FLEET_PSK")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--repeat-interval", type=float, default=1.0)
    parser.add_argument("--output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    secret = os.environ.get(args.psk_env)
    if not secret:
        raise SystemExit(f"{args.psk_env} is unset")
    try:
        result = announce_task_file(
            args.tasks, args.site_config, secret,
            group=args.group, port=args.port, interface=args.interface,
            repeats=args.repeats, repeat_interval_s=args.repeat_interval,
        )
    except (TaskInputError, SiteConfigError) as exc:
        raise SystemExit(f"refusing task announcement: {exc}") from exc
    encoded = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if result["success"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
