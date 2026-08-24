"""End-to-end release tests for the failures named by the SIH statement."""

from dataclasses import replace

from src.amr import POLICY_BIOS_PIBT_V3, POLICY_STOP_WAIT
from src.main import run_scenario
from src.scenarios import (blocked_aisle, human_in_aisle, open_floor_control,
                           partition_recovery, robot_failure_reassignment,
                           sih_acceptance_overlap)
from src.task_allocation import ALLOCATION_AUCTION, ALLOCATION_PREASSIGNED


def test_blocked_aisle_is_detected_and_rerouted_without_contact():
    result = run_scenario(
        blocked_aisle(n_robots=3, tasks_per_robot=1, seed=0),
        POLICY_BIOS_PIBT_V3,
        allocation_policy=ALLOCATION_PREASSIGNED,
    )

    assert result.completed_all
    assert result.dynamic_obstacles_detected >= 1
    assert result.dynamic_reroutes >= 1
    assert result.contacts_robot_robot == 0
    assert result.contacts_robot_rack == 0


def test_failed_auction_winner_is_reassigned_and_completed():
    result = run_scenario(
        robot_failure_reassignment(n_robots=3, seed=0),
        POLICY_BIOS_PIBT_V3,
        allocation_policy=ALLOCATION_AUCTION,
    )

    assert result.robot_failures == 1
    assert result.task_reassignments >= 1
    assert result.completed_all
    assert result.tasks_completed == result.tasks_announced == 1
    assert result.contacts_robot_robot == 0


def test_partition_heals_and_catalogs_converge():
    result = run_scenario(
        partition_recovery(n_robots=4, seed=0),
        POLICY_BIOS_PIBT_V3,
        allocation_policy=ALLOCATION_AUCTION,
    )

    assert result.completed_all
    assert result.tasks_completed == result.tasks_announced == 4
    assert result.contacts_robot_robot == 0


def test_lossy_completion_catalog_converges_corridor_wave_membership():
    scenario = sih_acceptance_overlap(n_robots=4, seed=6)
    scenario.net = replace(scenario.net, loss=0.20)
    scenario.duration_s = 500.0

    result = run_scenario(
        scenario, POLICY_BIOS_PIBT_V3,
        allocation_policy=ALLOCATION_AUCTION,
    )

    assert result.completed_all
    assert result.tasks_completed == result.tasks_announced == 12
    assert result.contacts_robot_robot == 0
    assert result.contacts_robot_rack == 0


def test_human_scenario_finishes_without_parking_in_worker_path():
    result = run_scenario(
        human_in_aisle(n_robots=4, tasks_per_robot=3, seed=0),
        POLICY_BIOS_PIBT_V3,
        allocation_policy=ALLOCATION_AUCTION,
    )

    assert result.completed_all
    assert result.contacts_robot_robot == 0
    assert result.contacts_robot_human == 0
    assert result.min_separation_m >= 0.90


def test_negative_control_has_no_false_coordination_win():
    baseline = run_scenario(
        open_floor_control(n_robots=4, tasks_per_robot=1, seed=0),
        POLICY_STOP_WAIT,
        allocation_policy=ALLOCATION_PREASSIGNED,
    )
    candidate = run_scenario(
        open_floor_control(n_robots=4, tasks_per_robot=1, seed=0),
        POLICY_BIOS_PIBT_V3,
        allocation_policy=ALLOCATION_PREASSIGNED,
    )

    assert baseline.completed_all and candidate.completed_all
    relative_difference = abs(candidate.makespan_s - baseline.makespan_s) \
        / baseline.makespan_s
    assert relative_difference <= 0.10
    assert baseline.contacts_robot_robot == candidate.contacts_robot_robot == 0
