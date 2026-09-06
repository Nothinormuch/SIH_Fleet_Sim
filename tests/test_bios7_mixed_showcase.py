"""BIOS7 must preserve the real ten-AMR mixed-traffic demo regressions.

These are known development seeds, not an untouched acceptance holdout. Keep the
existing humans, racks, radio dead zone, failure and 800 s showcase window.
"""
from dataclasses import replace

import pytest

from src.amr import POLICY_BIOS_PIBT_V7
from src.main import run_scenario
from src.scenarios import SHOWCASE_SCENARIOS
from src.task_allocation import ALLOCATION_AUCTION_BUNDLE


@pytest.mark.parametrize("seed", (0, 1, 4))
def test_bios7_finishes_ten_amr_mixed_showcase_without_contacts(seed):
    profile = SHOWCASE_SCENARIOS["showcase_grand_challenge"]
    scenario = replace(profile["builder"](n_robots=10, seed=seed),
                       duration_s=float(profile["duration"]))
    assert scenario.humans and scenario.net.dead_zones
    result = run_scenario(scenario, POLICY_BIOS_PIBT_V7, seed=seed,
                          allocation_policy=ALLOCATION_AUCTION_BUNDLE)
    assert result.completed_all
    assert result.tasks_completed == result.tasks_announced == 20
    assert result.makespan_s < profile["duration"]
    assert result.human_distance_m > 0
    assert result.contacts_robot_robot == 0
    assert result.contacts_robot_human == 0
    assert result.contacts_robot_rack == 0
