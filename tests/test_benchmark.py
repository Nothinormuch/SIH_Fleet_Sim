"""Integrity and release-gate tests for the SIH acceptance benchmark."""

from dataclasses import replace

import pytest

from src import messages as msg
from src.amr import (AMRBrain, MODE_P2P, POLICY_BIOS_PIBT_V3,
                     POLICY_STOP_WAIT, Peer)
from src.benchmark import parse_robot_counts
from src.environment import open_floor
from src.metrics import PolicyResult, compare_paired, percentile
from src.priority import PriorityKey
from src.scenarios import sih_acceptance_overlap, workload_fingerprint
from src.settings import DEFAULT


def _result(policy: str, seed: int, workload: str, *, completed: bool,
            makespan: float, cutoff: float = 100.0,
            contacts: int = 0) -> PolicyResult:
    return PolicyResult(
        policy=policy,
        scenario="sih_acceptance_overlap",
        seed=seed,
        allocation_policy="auction",
        workload_id=workload,
        sim_seconds=makespan if completed else cutoff,
        robots=4,
        tasks_completed=12 if completed else 4,
        tasks_announced=12,
        makespan_s=makespan if completed else cutoff,
        completed_all=completed,
        contacts_robot_robot=contacts,
        robot_hours=0.5,
        msgs_per_robot_s=8.0,
        plan_cpu_mean_ms=0.2,
    )


def test_workload_fingerprint_is_deterministic_and_task_sensitive():
    first = sih_acceptance_overlap(n_robots=4, seed=7)
    second = sih_acceptance_overlap(n_robots=4, seed=7)
    cfg = replace(DEFAULT, net=first.net, seed=7)

    before = workload_fingerprint(first, cfg, "auction")
    assert before == workload_fingerprint(second, cfg, "auction")

    second.unassigned[0].drop = (second.unassigned[0].drop[0],
                                 second.unassigned[0].drop[1] - 1)
    assert before != workload_fingerprint(second, cfg, "auction")


def test_acceptance_scenario_pins_the_overlap_workload():
    scenario = sih_acceptance_overlap(n_robots=4, seed=0)

    assert scenario.name == "sih_acceptance_overlap"
    assert scenario.use_auction
    assert scenario.duration_s == 1200.0
    assert scenario.n_robots == 4
    assert len(scenario.unassigned) == 12
    assert all(task.pick[0] < 6 or task.pick[0] > scenario.env.width - 7
               for task in scenario.unassigned)


def test_paired_exact_comparison_passes_only_matching_seed_workloads():
    baseline = [_result(POLICY_STOP_WAIT, seed, f"work-{seed}", completed=True,
                        makespan=100.0 + seed)
                for seed in range(3)]
    candidate = [_result(POLICY_BIOS_PIBT_V3, seed, f"work-{seed}", completed=True,
                         makespan=70.0 + seed)
                 for seed in range(3)]

    report = compare_paired(baseline, candidate, bootstrap_samples=100)

    assert report["verdict"] == "pass"
    assert report["evidence_kind"] == "exact_paired_makespan"
    assert report["minimum_reduction_lower_bound_pct"] > 20.0
    assert report["baseline_censored_seeds"] == []


def test_paired_censored_comparison_reports_a_conservative_lower_bound():
    baseline = [_result(POLICY_STOP_WAIT, 0, "same", completed=False,
                        makespan=100.0, cutoff=100.0)]
    candidate = [_result(POLICY_BIOS_PIBT_V3, 0, "same", completed=True,
                         makespan=70.0)]

    report = compare_paired(baseline, candidate, bootstrap_samples=10)

    assert report["verdict"] == "pass"
    assert report["evidence_kind"] == "right_censored_conservative_lower_bound"
    assert report["minimum_reduction_lower_bound_pct"] == 30.0
    assert report["baseline_censored_seeds"] == [0]


def test_paired_comparison_refuses_candidate_timeout_or_contact():
    baseline = [_result(POLICY_STOP_WAIT, 0, "same", completed=False,
                        makespan=100.0)]
    timed_out = [_result(POLICY_BIOS_PIBT_V3, 0, "same", completed=False,
                         makespan=100.0)]
    assert compare_paired(baseline, timed_out)["verdict"] == "incomplete"

    unsafe = [_result(POLICY_BIOS_PIBT_V3, 0, "same", completed=True,
                      makespan=60.0, contacts=1)]
    report = compare_paired(baseline, unsafe, bootstrap_samples=10)
    assert report["verdict"] == "fail"
    assert report["candidate_contacts_total"] == 1


def test_paired_comparison_refuses_seed_and_workload_mismatch():
    base = [_result(POLICY_STOP_WAIT, 0, "A", completed=True, makespan=100.0)]
    wrong_work = [_result(POLICY_BIOS_PIBT_V3, 0, "B", completed=True,
                          makespan=60.0)]
    missing_seed = [_result(POLICY_BIOS_PIBT_V3, 1, "A", completed=True,
                            makespan=60.0)]

    assert compare_paired(base, wrong_work)["verdict"] == "invalid"
    assert "fingerprint" in compare_paired(base, wrong_work)["reason"]
    assert compare_paired(base, missing_seed)["verdict"] == "invalid"
    assert "seed mismatch" in compare_paired(base, missing_seed)["reason"]


def test_percentile_and_robot_count_parsing_are_deterministic():
    assert percentile([1.0, 2.0], 0.95) == pytest.approx(1.95)
    assert parse_robot_counts("4,6,8,6") == [4, 6, 8]
    with pytest.raises(Exception):
        parse_robot_counts("2")


def test_v3_does_not_commit_an_uncontested_open_floor_cell():
    brain = AMRBrain(
        "AMR01", open_floor(8, 8), DEFAULT,
        policy=POLICY_BIOS_PIBT_V3,
    )

    assert brain._bios_v3_cell_coordinate(0.0, type("S", (), {"cell": (1, 1)})(),
                                          (2, 1)) is None
    assert brain._cell_gate_since == {}


def test_v3_still_commits_a_real_merge_contender():
    brain = AMRBrain(
        "AMR01", open_floor(8, 8), DEFAULT,
        policy=POLICY_BIOS_PIBT_V3,
    )
    brain.peers["AMR02"] = Peer(
        "AMR02", cell=(2, 2), goal=(4, 1), intent=[(2, 1)], last_seen=0.0)

    result = brain._bios_v3_cell_coordinate(
        0.0, type("S", (), {"cell": (1, 1)})(), (2, 1))

    assert result == "cell-gate"


def test_v3_occupied_cell_is_delegated_to_pibt_after_commit_round():
    brain = AMRBrain(
        "AMR01", open_floor(8, 8), DEFAULT,
        policy=POLICY_BIOS_PIBT_V3,
    )
    brain.peers["AMR02"] = Peer(
        "AMR02", cell=(2, 1), goal=(1, 1), intent=[(1, 1)], last_seen=0.0)
    sensors = type("S", (), {"cell": (1, 1)})()

    assert brain._bios_v3_cell_coordinate(0.0, sensors, (2, 1)) == "cell-gate"
    assert brain._bios_v3_cell_coordinate(0.5, sensors, (2, 1)) is None


def test_idle_heartbeat_clears_a_peers_stale_route_intent():
    brain = AMRBrain(
        "AMR01", open_floor(8, 8), DEFAULT,
        policy=POLICY_BIOS_PIBT_V3,
    )
    brain.peers["AMR02"] = Peer(
        "AMR02", cell=(2, 1), goal=(5, 1), intent=[(3, 1), (4, 1)],
        windows=[(0.0, 1.0), (1.0, 2.0)], last_seen=0.0,
    )
    heartbeat = msg.heartbeat(
        "AMR02", 2, 3.0, (3.5, 2.1, 0.0), (2, 1), 0.8,
        MODE_P2P, "idle", None, goal=None,
    )

    brain._ingest(3.0, [heartbeat])

    assert brain.peers["AMR02"].intent == []
    assert brain.peers["AMR02"].windows == []


def test_robot_without_a_goal_never_broadcasts_old_path_as_intent():
    brain = AMRBrain(
        "AMR01", open_floor(8, 8), DEFAULT,
        policy=POLICY_BIOS_PIBT_V3,
    )
    brain.path = [(1, 1), (2, 1), (3, 1)]
    brain.pidx = 1
    brain.goal = None

    assert brain._intent_horizon(5.0) == ([], [])


def test_v3_executes_pibt_escape_at_an_uncontrolled_degree_two_corner():
    scenario = sih_acceptance_overlap(n_robots=4, seed=0)
    brain = AMRBrain(
        "AMR03", scenario.env, DEFAULT,
        policy=POLICY_BIOS_PIBT_V3,
    )
    brain.goal = (4, 0)
    brain.path = [(5, 0), (4, 0)]
    brain.pidx = 1
    brain._last_cell = (5, 0)
    brain._pub_priority_key = PriorityKey(
        distance_bias=-1, robot_id="AMR03")
    brain.peers["AMR08"] = Peer(
        "AMR08", cell=(4, 0), goal=(5, 0), intent=[(5, 0)],
        priority_key=PriorityKey(service_age=10, distance_bias=-1,
                                 robot_id="AMR08"),
        last_seen=0.0,
    )
    sensors = type(
        "S", (),
        {"cell": (5, 0), "pose": (7.7, 0.7, 0.0), "v": 0.0},
    )()

    assert brain.env.degree((5, 0)) == 2
    assert brain._controlled_block((5, 0)) is None
    assert brain._bios_pibt_coordinate(1.0, sensors, (4, 0)) is None
    assert brain.path == [(5, 0), (5, 1)]


def test_v3_does_not_stage_for_a_mouth_occupant_moving_away():
    scenario = sih_acceptance_overlap(n_robots=8, seed=8)
    brain = AMRBrain(
        "AMR02", scenario.env, DEFAULT,
        policy=POLICY_BIOS_PIBT_V3,
    )
    brain._last_cell = (19, 2)
    brain.path = [(19, 2), (19, 3), (19, 4), (18, 4)]
    brain.pidx = 1
    brain.peers["AMR07"] = Peer(
        "AMR07", cell=(19, 4), goal=(19, 3), intent=[(19, 3)],
        last_seen=0.0,
    )

    assert brain._v3_staging_conflict(0.0, (19, 2)) is None


def test_v3_arms_recovery_for_the_leader_of_a_pibt_convoy():
    scenario = sih_acceptance_overlap(n_robots=8, seed=8)
    brain = AMRBrain(
        "AMR07", scenario.env, DEFAULT,
        policy=POLICY_BIOS_PIBT_V3,
    )
    brain.goal = (19, 3)
    brain._pub_priority_key = PriorityKey(
        waiting_age=2, distance_bias=-1, robot_id="AMR07")
    brain.peers["AMR02"] = Peer(
        "AMR02", cell=(19, 2), goal=(5, 8), intent=[(19, 3)],
        priority_key=PriorityKey(waiting_age=1, service_age=20, loaded=1,
                                 robot_id="AMR02"),
        last_seen=0.0,
    )
    brain.peers["AMR03"] = Peer(
        "AMR03", cell=(20, 4), goal=(0, 4), intent=[(19, 4)],
        priority_key=PriorityKey(service_age=20, loaded=1,
                                 robot_id="AMR03"),
        last_seen=0.0,
    )
    sensors = type(
        "S", (),
        {"cell": (19, 4), "pose": (27.3, 6.2, -1.57), "v": 0.0},
    )()
    brain._stall_since = 4.0

    assert brain._bios_pibt_coordinate(10.0, sensors, (19, 3)) is None
    assert brain._creep_until == 16.0
    assert brain._stall_since is None


def test_pibt_waiting_priority_has_bounded_authorization_hysteresis():
    brain = AMRBrain(
        "AMR01", open_floor(8, 8), DEFAULT,
        policy=POLICY_BIOS_PIBT_V3,
    )
    sensors = type("S", (), {"battery_frac": 1.0, "cell": (1, 1)})()

    brain._track_block(2.0, True, "AMR02")
    before = brain._priority_key(6.0, sensors)
    brain._track_block(6.0, False, None)
    after = brain._priority_key(7.0, sensors)
    expired = brain._priority_key(13.0, sensors)

    assert before.waiting_age == 4
    assert brain.blocked_since is None
    assert after.waiting_age == 5
    assert expired.waiting_age == 0


def test_v3_stages_follower_when_convoy_leader_turns():
    scenario = sih_acceptance_overlap(n_robots=4, seed=11)
    brain = AMRBrain(
        "AMR01", scenario.env, DEFAULT,
        policy=POLICY_BIOS_PIBT_V3,
    )
    brain.goal = (21, 8)
    brain._pub_priority_key = PriorityKey(
        service_age=10, distance_bias=-1, robot_id="AMR01")
    brain.peers["AMR03"] = Peer(
        "AMR03", cell=(21, 8), goal=(21, 5), intent=[(20, 8)],
        priority_key=PriorityKey(service_age=10, distance_bias=-3,
                                 robot_id="AMR03"),
        last_seen=0.0,
    )
    sensors = type(
        "S", (),
        {"cell": (21, 7), "pose": (30.1, 10.93, 1.57), "v": 0.0},
    )()

    assert brain._bios_pibt_coordinate(10.0, sensors, (21, 8)) == "AMR03"
    assert brain._creep_until == 16.0


def test_idle_robot_vacates_to_a_cell_outside_the_peer_intent_horizon():
    scenario = sih_acceptance_overlap(n_robots=8, seed=25)
    brain = AMRBrain(
        "AMR03", scenario.env, DEFAULT,
        policy=POLICY_BIOS_PIBT_V3,
        home=(19, 4),
    )
    brain.peers["AMR08"] = Peer(
        "AMR08", cell=(19, 6), goal=(5, 8),
        intent=[(19, 5), (19, 4), (18, 4), (17, 4)],
        last_seen=0.0,
    )
    sensors = type("S", (), {"cell": (19, 5)})()

    brain._vacate_if_in_the_way(10.0, sensors)

    assert brain.goal == (20, 5)
    assert brain.goal not in brain.peers["AMR08"].intent


def test_v3_stages_an_overclose_straight_convoy_follower():
    scenario = sih_acceptance_overlap(n_robots=8, seed=25)
    brain = AMRBrain(
        "AMR08", scenario.env, DEFAULT,
        policy=POLICY_BIOS_PIBT_V3,
    )
    brain.goal = (24, 1)
    brain._pub_priority_key = PriorityKey(
        service_age=10, loaded=1, robot_id="AMR08")
    brain.peers["AMR01"] = Peer(
        "AMR01", cell=(5, 5), pose=(7.41, 7.72, -0.06),
        goal=(21, 8), intent=[(5, 4)],
        priority_key=PriorityKey(service_age=10, loaded=1,
                                 robot_id="AMR01"),
        last_seen=0.0,
    )
    sensors = type(
        "S", (),
        {"cell": (5, 6), "pose": (7.72, 8.58, -1.59), "v": 0.0},
    )()

    assert brain._bios_pibt_coordinate(10.0, sensors, (5, 5)) == "AMR01"
    assert brain._creep_until == 16.0
