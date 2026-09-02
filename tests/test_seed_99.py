"""Acceptance gates for the fixed Seed 99 launch-congestion demonstration."""

from __future__ import annotations

import pytest

from src.amr import POLICY_BIOS_PIBT_V6, POLICY_STOP_WAIT
from src.main import run_for_dashboard, run_scenario
from src.scenarios import (SEED_99_DEMO_ROBOTS, SEED_99_DEMO_SEED,
                           seed_99_congestion)
from src.task_allocation import ALLOCATION_AUCTION_BUNDLE


@pytest.fixture(scope="module")
def seed_99_payload() -> dict:
    return run_for_dashboard(
        "showcase_grand_challenge",
        POLICY_BIOS_PIBT_V6,
        robots=10,
        seed=SEED_99_DEMO_SEED,
        duration=180,
        allocation_policy=ALLOCATION_AUCTION_BUNDLE,
    )


def test_seed_99_is_a_fixed_six_amr_crossing_workload() -> None:
    scenario = seed_99_congestion()

    assert scenario.seed == SEED_99_DEMO_SEED
    assert scenario.n_robots == SEED_99_DEMO_ROBOTS
    assert len(set(scenario.starts)) == SEED_99_DEMO_ROBOTS
    assert all(scenario.env.passable(cell) for cell in scenario.starts)
    assert all(scenario.env.passable(task.drop) for task in scenario.unassigned)
    assert [task.pick for task in scenario.unassigned] == scenario.starts
    assert all(not queue for queue in scenario.assignments)
    assert scenario.use_auction

    with pytest.raises(ValueError, match="exactly 6 robots"):
        seed_99_congestion(n_robots=7)
    with pytest.raises(ValueError, match="requires seed 99"):
        seed_99_congestion(seed=98)


def test_seed_99_dashboard_measures_and_breaks_the_opening_gridlock(
    seed_99_payload: dict,
) -> None:
    meta = seed_99_payload["meta"]
    evidence = seed_99_payload["demo_evidence"]
    summary = seed_99_payload["summary"]

    assert meta["requested_scenario"] == "showcase_grand_challenge"
    assert meta["requested_robots"] == 10
    assert meta["scenario"] == "seed_99_congestion"
    assert meta["seed_99_demo"]
    assert meta["robots"] == SEED_99_DEMO_ROBOTS

    assert evidence["full_gridlock_observed"]
    assert evidence["peak_simultaneously_blocked"] == SEED_99_DEMO_ROBOTS
    assert evidence["full_gridlock_detected_s"] is not None
    assert evidence["first_release_s"] > evidence["full_gridlock_detected_s"]
    assert 0 < evidence["first_release_latency_s"] <= 1.0

    assert summary["completed_all"]
    assert summary["tasks_completed"] == summary["tasks_announced"] == 6
    assert summary["makespan_s"] < 120.0
    assert summary["yields"] > 0
    assert summary["contacts_robot_robot"] == 0
    assert summary["contacts_robot_human"] == 0
    assert summary["contacts_robot_rack"] == 0


def test_seed_99_distinguishes_bios6_from_untouched_stop_and_wait(
    seed_99_payload: dict,
) -> None:
    baseline = run_scenario(
        seed_99_congestion(),
        POLICY_STOP_WAIT,
        seed=SEED_99_DEMO_SEED,
        allocation_policy=ALLOCATION_AUCTION_BUNDLE,
    )
    bios = seed_99_payload["summary"]

    assert bios["completed_all"]
    assert bios["tasks_completed"] == 6
    assert not baseline.completed_all
    assert baseline.tasks_completed == 0
    assert baseline.nonproductive_wait_ticks > bios["nonproductive_wait_ticks"]
    assert baseline.contacts_robot_robot == 0
