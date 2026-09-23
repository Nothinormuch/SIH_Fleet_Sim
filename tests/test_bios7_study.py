"""Harness integrity tests; these do not tune candidate performance."""
from dataclasses import asdict

import pytest

from bios7_acceptance import compare_pair, main, summarize
from bios7_acceptance_worker import decode_scenario
from src.baseline_comparison import _catalog_digest
from src.bios7_study import (POLICY_CONFIGS, build_case, capacity_scenario,
                            scenario_diagnostics, scenario_wire)
from src.amr import POLICIES
from src.scenarios import (ObstacleEvent, edge_overlap,
                           scenario_catalog_fingerprint, sih_acceptance_overlap)


def test_new_overlap_fixture_contains_shared_routes():
    for robots in (3, 6, 10):
        sc = edge_overlap(robots)
        diagnostics = scenario_diagnostics(sc)
        assert diagnostics["overlap_fixture_valid"]
        assert diagnostics["shared_nominal_route_cells"] > 0
        assert any(task.pick[1] != task.drop[1]
                   for queue in sc.assignments for task in queue)
        assert not diagnostics["unreachable_task_ids"]
        assert diagnostics["valid_nonoverlapping_starts"]


@pytest.mark.parametrize("change", [
    lambda sc: sc.humans.append([(4, 1), (4, 8)]),
    lambda sc: setattr(sc, "human_randomized", True),
    lambda sc: sc.obstacles.append(ObstacleEvent("PALLET", (5, 4), 1, 10)),
    lambda sc: setattr(sc, "initial_battery_fracs", [0.8, 0.9, 1.0]),
    lambda sc: setattr(sc, "pose_noise_m", 0.01),
    lambda sc: setattr(sc.assignments[0][0], "announced_t", 3.0),
    lambda sc: setattr(sc, "kill_manager_at", 8.0),
])
def test_architecture_digest_covers_every_exogenous_field(change):
    sc = edge_overlap(3)
    before = _catalog_digest(sc)
    change(sc)
    assert _catalog_digest(sc) != before


def test_catalog_digest_ignores_static_assignment_but_not_task_description():
    sc = edge_overlap(3)
    before = scenario_catalog_fingerprint(sc)
    sc.assignments.reverse()
    sc.use_auction = True
    assert scenario_catalog_fingerprint(sc) == before
    sc.assignments[0][0].cargo_weight = 4.0
    assert scenario_catalog_fingerprint(sc) != before


def test_wire_roundtrip_preserves_world_and_all_task_fields():
    sc = sih_acceptance_overlap(10, seed=0)
    sc.obstacles = [ObstacleEvent("X", (2, 2), 4, 8)]
    rebuilt = decode_scenario(scenario_wire(sc))
    assert asdict(rebuilt) == asdict(sc)
    assert scenario_catalog_fingerprint(rebuilt) == scenario_catalog_fingerprint(sc)


def test_fixed_and_scaled_capacity_are_separate_and_place_all_robots():
    fixed_small = capacity_scenario(3, 7, scaled=False)
    fixed_large = capacity_scenario(100, 7, scaled=False)
    assert fixed_small.env == fixed_large.env
    scaled_small = capacity_scenario(3, 7, scaled=True)
    scaled_large = capacity_scenario(100, 7, scaled=True)
    assert scaled_small.env.width * scaled_small.env.height < (
        scaled_large.env.width * scaled_large.env.height)
    assert len(scaled_small.env.docks) < len(scaled_large.env.docks)
    assert len(scaled_small.env.stations) < len(scaled_large.env.stations)
    for sc in (fixed_small, fixed_large, scaled_small, scaled_large):
        assert len(sc.starts) == sc.n_robots
        assert len(set(sc.starts)) == sc.n_robots
        assert all(sc.env.passable(cell) for cell in sc.starts)
        assert sc.n_tasks == 2 * sc.n_robots


def test_fixed_catalog_axis_keeps_external_tasks_equal_across_fleet_sizes():
    a = capacity_scenario(3, 2, scaled=False, total_tasks=20)
    b = capacity_scenario(10, 2, scaled=False, total_tasks=20)
    def catalog(sc):
        return sorted((t.tid, t.pick, t.drop) for q in sc.assignments for t in q)
    assert catalog(a) == catalog(b)


def test_pinned_sih_cannot_be_resized_or_given_easier_work():
    sc = build_case("sih", 10, 0)
    assert sc.duration_s == 1200
    assert sc.n_tasks == 30
    assert sc.env.width == 25 and sc.env.height == 9
    with pytest.raises(ValueError):
        build_case("sih", 100, 0)
    with pytest.raises(ValueError):
        build_case("sih", 10, 0, total_tasks=3)


def test_policy_labels_execute_real_registered_controllers():
    assert all(policy in POLICIES for policy, _ in POLICY_CONFIGS.values())


def _row(completed=True, *, cutoff=1200, makespan=800, contact=0):
    return {"world_fingerprint": "same", "output": {"result": {
        "completed_all": completed, "tasks_completed": 30 if completed else 10,
        "tasks_announced": 30, "makespan_s": makespan, "sim_seconds": cutoff,
        "contacts_robot_robot": contact, "contacts_robot_human": 0,
        "contacts_robot_rack": 0}}}


def test_pairing_never_converts_timeout_into_exact_speedup():
    result = compare_pair(_row(False), _row(makespan=968.12))
    assert result["kind"] == "right_censored_lower_bound"
    assert result["reduction_pct"] is None
    assert result["baseline_makespan_s"] is None
    assert result["reduction_lower_bound_pct"] == pytest.approx(19.3233333)
    assert compare_pair(_row(False), _row(False))["reduction_pct"] is None
    assert compare_pair(_row(), _row(contact=1))["verdict"] == "unsafe"
    wrong = _row()
    wrong["world_fingerprint"] = "different"
    assert compare_pair(_row(), wrong)["verdict"] == "invalid"


def test_partial_report_cannot_be_promoted_and_missing_gate_is_unknown():
    report = {"plan": {"configurations": ["bios6", "bios7"]}, "runs": [],
              "finished": False}
    summary = summarize(report)
    assert summary["promotion_allowed"] is False
    assert summary["pinned_sih_20pct_gate"] is None
    assert summary["all_declared_runs_finished"] is False


def test_default_cli_only_prints_plan(tmp_path, capsys):
    output = tmp_path / "must-not-exist.json"
    assert main(["--cases", "crossflow", "--robots", "3", "--seeds", "1",
                 "--output", str(output)]) == 0
    assert not output.exists()
    assert '"expected_runs": 4' in capsys.readouterr().out


def test_holdout_and_demo_seeds_cannot_be_relabeled_as_acceptance():
    with pytest.raises(SystemExit):
        main(["--phase", "holdout", "--first-seed", "0"])
    with pytest.raises(SystemExit):
        main(["--first-seed", "99", "--seeds", "1"])
