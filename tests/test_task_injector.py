"""WMS announcement bridge validation."""

import json
from pathlib import Path

import pytest

from src.task_injector import TaskInputError, load_task_file


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "config" / "site.example.json"


def test_example_tasks_match_site_and_preserve_auction_fields():
    tasks = load_task_file(ROOT / "config" / "tasks.example.json", SITE)

    assert [task.task_id for task in tasks] == ["ORDER-001", "ORDER-002"]
    assert tasks[1].cargo_type == "fragile"
    assert tasks[1].priority == 5
    assert tasks[1].deadline_s == 120.0


def test_task_file_rejects_rack_target_and_payload_over_capacity(tmp_path):
    data = json.loads(
        (ROOT / "config" / "tasks.example.json").read_text(encoding="utf-8")
    )
    data["tasks"][0]["pickup"] = [2, 2]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(TaskInputError, match="pickup is not passable"):
        load_task_file(path, SITE)

    data["tasks"][0]["pickup"] = [0, 1]
    data["tasks"][0]["cargo_weight_kg"] = 101
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(TaskInputError, match="payload capacity"):
        load_task_file(path, SITE)
