"""Release evidence cannot pass by dropping faults, censoring or a comparator."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bios7_acceptance import (CANDIDATE_CONFIGURATIONS, compare_pair, coverage_check,
    main, payload_hash, release_cases, semantic_fingerprint, summarize, validate_worker_output)
from src.bios7_study import CONTROL_COMMIT


def result(**overrides):
    return {"completed_all": True, "tasks_announced": 30, "tasks_completed": 30,
        "makespan_s": 800.0, "sim_seconds": 800.0, "robots": 10, "seed": 2000,
        "policy": "BIOS_PIBT.7", "msgs_sent": 1000, "bytes_sent": 10000,
        "contacts_robot_robot": 0, "contacts_robot_human": 0, "contacts_robot_rack": 0,
        "allocation_compute_mean_ms": 1.0, "plan_cpu_total_s": 1.0, **overrides}


def row(configuration="bios7", *, case="sih", stage="holdout", replicate=0, **metrics):
    return {"case": case, "stage": stage, "robots": 10, "seed": 2000,
        "replicate": replicate, "configuration": configuration, "world_fingerprint": "same",
        "output": {"result": result(**metrics), "config_sha256": "same"}}


def report(**candidate):
    return {"plan": {"phase": "release", "configurations": [
        "bios6", "bios6_safety_fixed", "bios7", "stop_wait"],
        "inputs": [{"case": "sih"}]}, "finished": True, "source_integrity": True,
        "runs": [row("bios6", contacts_robot_rack=19), row("bios6_safety_fixed"),
                 row(**candidate), row("stop_wait", makespan_s=1100)]}


def test_release_preserves_first_failed_capacity_case_before_expansion():
    cases = release_cases("all")
    assert cases[0] == ("regression50", "fixed_floor", 50, 0, 0)
    assert next(i for i, item in enumerate(cases) if item[2] == 100) > 0
    assert {item[2] for item in cases} == {3, 10, 25, 50, 100}
    heldout = [item for item in cases if item[0] == "holdout"]
    assert [item[3] for item in heldout] == list(range(2000, 2030))
    assert all(item[1:3] == ("sih", 10) for item in heldout)
    assert len(release_cases("stress")) == 36
    assert release_cases("repeat") == [
        ("repeat", "sih", 10, 2000, 1), ("repeat", "sih", 10, 2000, 2)]


def test_safety_fixed_v6_uses_candidate_source_but_original_does_not():
    assert "bios6_safety_fixed" in CANDIDATE_CONFIGURATIONS
    assert "bios6" not in CANDIDATE_CONFIGURATIONS


def test_unsafe_baseline_is_retained_but_not_a_valid_performance_claim():
    compared = compare_pair(row(contacts_robot_rack=19), row(makespan_s=700))
    assert compared["verdict"] == "unsafe"
    assert compared["baseline_safe"] is False
    assert compared["baseline_contacts"]["contacts_robot_rack"] == 19
    assert compared["reduction_pct"] == 12.5  # Descriptive raw value, explicitly unsafe.
    assert compared["valid_for_performance_claim"] is False
    assert compared["nonregression"]["makespan_s"] is None


def test_safe_release_stage_pass_is_not_automatic_promotion():
    summary = summarize(report())
    assert summary["declared_headless_stage_pass"] is True
    assert summary["promotion_allowed"] is False
    assert summary["configurations"]["bios6"]["contact_totals"]["contacts_robot_rack"] == 19
    assert summary["aggregates"]["sih:10:vs_bios6"]["exact_pairs"] == 0


@pytest.mark.parametrize("metric", ["makespan_s", "msgs_sent", "bytes_sent"])
def test_each_per_case_nonregression_is_strict(metric):
    summary = summarize(report(**{metric: result()[metric] + 1}))
    assert summary["strict_safety_fixed_v6_nonregression"][metric] is False
    assert summary["declared_headless_stage_pass"] is False


def test_aggregate_gain_cannot_hide_one_regressed_case():
    document = report()
    second = deepcopy(document["runs"])
    for entry in second:
        entry["seed"] = 2001
        if entry["configuration"] == "bios7":
            entry["output"]["result"]["msgs_sent"] = 1001
        elif entry["configuration"] == "bios6_safety_fixed":
            entry["output"]["result"]["msgs_sent"] = 1000
    document["runs"][2]["output"]["result"]["msgs_sent"] = 10
    document["runs"].extend(second)
    document["plan"]["inputs"].append({"case": "sih"})
    assert summarize(document)["declared_headless_stage_pass"] is False


@pytest.mark.parametrize("change", [
    lambda doc: doc.update(finished=False),
    lambda doc: doc.update(source_integrity=False),
    lambda doc: doc["runs"].pop(1),
    lambda doc: doc["runs"].append(deepcopy(doc["runs"][2])),
    lambda doc: doc["runs"][2].update(error="worker timeout"),
    lambda doc: doc["runs"][2]["output"]["result"].update(contacts_robot_human=1),
    lambda doc: doc["runs"][2]["output"]["result"].update(completed_all=False),
    lambda doc: doc["runs"][3]["output"]["result"].update(contacts_robot_rack=1),
])
def test_partial_unsafe_or_invalid_runs_cannot_pass(change):
    document = report()
    change(document)
    assert summarize(document)["declared_headless_stage_pass"] is False


@pytest.mark.parametrize("baseline_complete", [True, False])
def test_incomplete_candidate_cannot_claim_full_safety_or_performance(baseline_complete):
    compared = compare_pair(row(completed_all=baseline_complete,
        tasks_completed=30 if baseline_complete else 20),
        row(completed_all=False, tasks_completed=20))
    assert compared["verdict"] == "incomplete"
    assert compared["candidate_safe"] is True  # Observed prefix remains contact-free.
    assert compared["valid_for_full_execution_safety_comparison"] is False
    assert compared["valid_for_performance_claim"] is False
    assert compared["reduction_pct"] is None
    assert compared["reduction_lower_bound_pct"] is None
    assert all(value is None for value in compared["nonregression"].values())
    assert summarize(report(completed_all=False, tasks_completed=20))[
        "declared_headless_stage_pass"] is False


def test_timeout_stays_censored_and_external_resource_timeout_has_no_score():
    compared = compare_pair(row(completed_all=False, sim_seconds=1200), row())
    assert compared["kind"] == "right_censored_lower_bound"
    assert compared["reduction_pct"] is None
    assert compared["reduction_lower_bound_pct"] == pytest.approx(100 / 3)
    failed = row()
    failed["error"] = "TimeoutExpired: external wall budget"
    assert compare_pair(failed, row())["verdict"] == "invalid"


def test_censored_lower_bound_can_prove_nonregression_without_claiming_baseline_completion():
    compared = compare_pair(row(completed_all=False, tasks_completed=20,
        sim_seconds=1000, msgs_sent=1200, bytes_sent=12000), row())
    assert all(value is True for value in compared["nonregression"].values())
    assert set(compared["nonregression_evidence"].values()) == {"censored_lower_bound"}
    assert compared["baseline_makespan_s"] is None
    assert compared["baseline_future_safety_known"] is False
    assert compared["baseline_safety_scope"] == "observed_prefix_only"
    assert compared["valid_for_full_execution_safety_comparison"] is False


@pytest.mark.parametrize("limits,field", [
    ({"sim_seconds": 700}, "makespan_s"),
    ({"msgs_sent": 999}, "msgs_sent"),
    ({"bytes_sent": 9999}, "bytes_sent"),
])
def test_censored_inequality_not_yet_proven_is_unknown_not_exact_regression(limits, field):
    compared = compare_pair(row(completed_all=False, tasks_completed=20, **limits), row())
    assert compared["nonregression"][field] is None
    assert compared["nonregression_evidence"][field] == "unproven_censored"


def test_unsafe_censored_baseline_never_proves_nonregression():
    compared = compare_pair(row(completed_all=False, tasks_completed=20,
        sim_seconds=1200, msgs_sent=1500, bytes_sent=15000, contacts_robot_rack=1), row())
    assert all(value is None for value in compared["nonregression"].values())
    assert compared["valid_for_performance_claim"] is False


def test_unproven_censored_summary_is_unknown_and_cannot_pass():
    document = report()
    document["runs"][1]["output"]["result"].update(
        completed_all=False, tasks_completed=20, msgs_sent=999)
    summary = summarize(document)
    assert summary["strict_safety_fixed_v6_nonregression"]["msgs_sent"] is None
    assert summary["declared_headless_stage_pass"] is False


@pytest.mark.parametrize("case,required", [
    ("humans", ("human_distance_m", "human_yield_ticks")),
    ("blocked", ("dynamic_obstacles_detected", "dynamic_reroutes")),
    ("failure", ("robot_failures", "task_reassignments")),
])
def test_fault_coverage_requires_measured_interaction_not_just_scenario_label(case, required):
    assert coverage_check(case, {})["exercised"] is False
    measurements = dict.fromkeys(required, 1)
    assert coverage_check(case, measurements)["exercised"] is True
    measurements[required[-1]] = 0
    assert coverage_check(case, measurements)["exercised"] is False


def test_repeated_semantics_ignore_cpu_time_but_not_messages_or_contact():
    a = result()
    assert semantic_fingerprint(a) == semantic_fingerprint({**a, "plan_cpu_total_s": 99})
    assert semantic_fingerprint(a) != semantic_fingerprint({**a, "msgs_sent": 1001})
    assert semantic_fingerprint(a) != semantic_fingerprint({**a, "contacts_robot_rack": 1})


def test_repeated_results_and_configs_must_match():
    document = report()
    for entry in document["runs"]:
        entry.update(stage="repeat", replicate=1)
    second = deepcopy(document["runs"])
    for entry in second:
        entry["replicate"] = 2
        entry["output"]["result"]["plan_cpu_total_s"] = 99
    document["runs"].extend(second)
    document["plan"]["inputs"].append({"case": "sih"})
    assert all(summarize(document)["deterministic_repeats"].values())
    document["runs"][6]["output"]["config_sha256"] = "changed"
    assert summarize(document)["declared_headless_stage_pass"] is False


def worker_context(tmp_path):
    request = {"source_root": str(tmp_path), "scenario": {"test": 1},
               "physical_config": {"cell_m": 1}, "policy": "BIOS_PIBT.7"}
    output = {"result": result(), "scenario_input_sha256": payload_hash(request["scenario"]),
              "physical_config": request["physical_config"], "config": {"test": 1},
              "config_sha256": payload_hash({"test": 1}), "controller_source_root": str(tmp_path)}
    item = {"robots": 10, "seed": 2000, "diagnostics": {"tasks": 30, "cutoff_s": 1200}}
    return output, request, item


def test_worker_validates_full_input_hash_and_frozen_identity(tmp_path):
    output, request, item = worker_context(tmp_path)
    validate_worker_output(output, request, item)
    output["scenario_input_sha256"] = "wrong"
    with pytest.raises(ValueError, match="preregistered input"):
        validate_worker_output(output, request, item)


@pytest.mark.parametrize("change", [
    lambda output: output.update(controller_source_root="/another/source"),
    lambda output: output.update(physical_config={"cell_m": 2}),
    lambda output: output.update(config_sha256="wrong"),
    lambda output: output["result"].update(seed=2001),
    lambda output: output["result"].update(tasks_completed=29),
    lambda output: output["result"].update(msgs_sent=float("nan")),
    lambda output: output["result"].update(sim_seconds=1201),
])
def test_worker_rejects_inconsistent_evidence(tmp_path, change):
    output, request, item = worker_context(tmp_path)
    change(output)
    with pytest.raises(ValueError):
        validate_worker_output(output, request, item)


def test_release_cannot_be_given_an_easier_catalog_or_observed_seeds():
    with pytest.raises(SystemExit):
        main(["--phase", "release", "--first-seed", "1000"])
    with pytest.raises(SystemExit):
        main(["--phase", "release", "--total-tasks", "1"])


def mock_campaign(monkeypatch, tmp_path, *, mutate_candidate=False):
    """Exercise source refusal/incremental persistence with no simulator CPU run."""
    calls = []
    control = tmp_path / "control"
    changed = False

    def manifest(path):
        is_control = Path(path) == control
        return {"commit": CONTROL_COMMIT if is_control else "candidate",
                "tracked_sources_dirty": False,
                "source_manifest_sha256": "control" if is_control else (
                    "changed" if changed else "candidate")}

    def worker(command, **options):
        nonlocal changed
        request = json.loads(options["input"])
        calls.append(request)
        scenario = request["scenario"]
        config = {"mock_worker": True}
        metrics = result(policy=request["policy"], robots=len(scenario["starts"]),
            seed=scenario["seed"], tasks_announced=100, tasks_completed=100,
            contacts_robot_rack=19 if Path(request["source_root"]) == control else 0)
        output = {"result": metrics, "config": config, "config_sha256": payload_hash(config),
            "physical_config": request["physical_config"],
            "scenario_input_sha256": payload_hash(scenario),
            "controller_source_root": request["source_root"]}
        changed = mutate_candidate
        return SimpleNamespace(stdout=json.dumps(output))

    monkeypatch.setattr("bios7_acceptance.source_manifest", manifest)
    monkeypatch.setattr("bios7_acceptance.subprocess.run", worker)
    return control, calls


def test_execute_isolates_control_and_persists_every_comparator(monkeypatch, tmp_path):
    control, calls = mock_campaign(monkeypatch, tmp_path)
    output = tmp_path / "evidence.json"
    args = ["--execute", "--phase", "release", "--release-stage", "regression50",
            "--control-root", str(control), "--output", str(output)]
    assert main(args) == 0
    document = json.loads(output.read_text())
    assert document["summary"]["declared_headless_stage_pass"] is True
    assert document["summary"]["promotion_allowed"] is False
    assert len(calls) == 3
    assert Path(calls[0]["source_root"]) == control
    assert calls[1]["source_root"] == calls[2]["source_root"] != calls[0]["source_root"]
    assert calls[1]["policy"] != calls[2]["policy"]
    assert len({payload_hash(call["scenario"]) for call in calls}) == 1
    assert document["runs"][0]["output"]["result"]["contacts_robot_rack"] == 19
    with pytest.raises(SystemExit):
        main(args)  # Previously observed failure/control evidence cannot be overwritten.
    assert len(calls) == 3


def test_source_mutation_during_control_worker_invalidates_campaign(monkeypatch, tmp_path):
    control, calls = mock_campaign(monkeypatch, tmp_path, mutate_candidate=True)
    output = tmp_path / "source-mutated.json"
    assert main(["--execute", "--phase", "release", "--release-stage", "regression50",
                 "--control-root", str(control), "--output", str(output)]) == 2
    document = json.loads(output.read_text())
    assert document["source_integrity"] is False
    assert document["summary"]["declared_headless_stage_pass"] is False
    assert len(calls) == 1
    assert "source changed while worker" in document["runs"][0]["error"]
    assert document["unrun_count"] == 2


def test_capacity_expansion_requires_prior_safe_fixed50_gate(monkeypatch, tmp_path):
    control, calls = mock_campaign(monkeypatch, tmp_path)
    with pytest.raises(SystemExit):
        main(["--execute", "--phase", "release", "--release-stage", "capacity",
              "--control-root", str(control), "--output", str(tmp_path / "capacity.json")])
    assert calls == []
