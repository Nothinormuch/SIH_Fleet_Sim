"""Regression tests for the honest competition and centralized reference baselines."""

from backend.server import parse_run_request
from src.amr import (CENTRAL_POLICIES, POLICIES,
                     POLICY_PRIORITIZED_SPACE_TIME,
                     POLICY_STOP_WAIT_COMPETITION)
from src.main import MANAGED_POLICIES, run_scenario
from src.scenarios import open_floor_control
from src.task_allocation import ALLOCATION_PREASSIGNED


def test_comparison_policies_are_real_registered_execution_paths():
    assert POLICY_STOP_WAIT_COMPETITION in POLICIES
    assert POLICY_PRIORITIZED_SPACE_TIME in POLICIES
    assert POLICY_PRIORITIZED_SPACE_TIME in CENTRAL_POLICIES
    assert POLICY_PRIORITIZED_SPACE_TIME in MANAGED_POLICIES


def test_collaboration_labels_map_to_the_corrected_policies():
    competition = parse_run_request({
        "policy": "stop-and-wait(Competition)", "robots": 3,
    })
    established = parse_run_request({
        "policy": "Already-Established_algorithm", "robots": 3,
    })
    assert competition["policy"] == POLICY_STOP_WAIT_COMPETITION
    assert established["policy"] == POLICY_PRIORITIZED_SPACE_TIME


def test_competition_stop_wait_remains_noncooperative():
    result = run_scenario(
        open_floor_control(n_robots=3, tasks_per_robot=1, seed=0),
        POLICY_STOP_WAIT_COMPETITION,
        allocation_policy=ALLOCATION_PREASSIGNED,
    )
    assert result.completed_all
    assert result.auction_bids_sent == 0
    assert result.intent_messages_sent == 0
    assert result.coordination_messages_sent == 0
    assert result.contacts_robot_robot == 0
    assert result.contacts_robot_rack == 0


def test_prioritized_space_time_policy_executes_manager_plans():
    result = run_scenario(
        open_floor_control(n_robots=3, tasks_per_robot=1, seed=0),
        POLICY_PRIORITIZED_SPACE_TIME,
        allocation_policy=ALLOCATION_PREASSIGNED,
    )
    assert result.completed_all
    assert result.plan_calls > 0
    assert result.contacts_robot_robot == 0
    assert result.contacts_robot_rack == 0
