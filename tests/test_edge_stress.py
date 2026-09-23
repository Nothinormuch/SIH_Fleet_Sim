"""Regressions for shared-space deployment and measured, seeded stress cases."""
import pytest

from src.amr import AMRBrain, POLICY_BIOS_PIBT_V6
from src.edge_lab import EdgeLab
from src.environment import Warehouse, corridors
from src.hil_demo import run_hil_demo
from src.main import run_scenario
from src.scenarios import edge_chokepoint, edge_human_crossing, edge_overlap, workload_fingerprint
from src.settings import DEFAULT
from src.world import World


def test_articulation_doorway_has_two_distinct_controlled_mouths():
    sc = edge_chokepoint(10)
    blocks = corridors(sc.env)
    door = (6, sc.env.height // 2)
    cid = blocks.id_of(door)
    assert cid is not None
    assert len(blocks.members[cid]) == 3
    assert len(blocks.ends[cid]) == 2
    assert blocks.nearest_end(cid, (5, door[1])) != blocks.nearest_end(cid, (7, door[1]))


def test_neighboring_doors_never_publish_overlapping_zones():
    grid = [[0] * 11 for _ in range(9)]
    for y in range(9):
        if y != 4:
            grid[y][4] = grid[y][6] = 1
    env = Warehouse(11, 9, tuple(tuple(row) for row in grid), (), ())
    blocks = corridors(env)
    seen = set()
    for cid, cells in blocks.members.items():
        assert not seen & cells
        assert all(blocks.of[c] == cid for c in cells)
        seen.update(cells)


def test_ten_robot_doorway_completes_without_contacts():
    result = run_scenario(edge_chokepoint(10), POLICY_BIOS_PIBT_V6,
                          allocation_policy="auction_bundle")
    assert result.completed_all and result.tasks_completed == 10
    assert result.contacts_robot_robot == result.contacts_robot_human == result.contacts_robot_rack == 0


def test_human_variations_are_repeatable_and_not_advertised_to_robots():
    sc = edge_human_crossing(3, seed=7)
    def sample(seed):
        world = World(sc.env, DEFAULT, seed=seed)
        world.human_randomized = True
        for i, route in enumerate(sc.humans):
            world.add_human(f"H{i}", route)
        for _ in range(500):
            world.step(.02, {})
        return world.snapshot()["humans"], world.human_behavior_events
    assert sample(7) == sample(7)
    assert sample(7) != sample(8)
    assert sample(7)[1] > 3
    before = workload_fingerprint(sc, DEFAULT, "auction_bundle")
    sc.human_randomized = False
    assert before != workload_fingerprint(sc, DEFAULT, "auction_bundle")


@pytest.mark.parametrize("count", [3, 6, 10])
def test_shared_profiles_have_feasible_distinct_starts(count):
    for make in (edge_overlap, edge_chokepoint, edge_human_crossing):
        sc = make(count)
        assert len(set(sc.starts)) == count
        assert sc.n_tasks == count
        assert all(sc.env.passable(c) for c in sc.starts)


def test_lab_validates_profile_fleet_and_seed_before_spawning():
    lab = EdgeLab()
    for options in ({"profile": "unknown"}, {"robots": 11}, {"robots": True},
                    {"robots": 2}, {"seed": -1}, {"seed": "0"}):
        with pytest.raises(ValueError):
            lab.start(**options)


def test_socket_ports_must_not_overlap_at_ten_nodes():
    with pytest.raises(ValueError, match="overlap"):
        run_hil_demo(robots=10, sensor_base_port=35001,
                     actuator_base_port=35004, peer_port=35000)


def test_corridor_cache_does_not_cache_task_ownership_or_mutable_results(monkeypatch):
    from src import amr
    sc = edge_chokepoint(3)
    brain = AMRBrain("AMR01", sc.env, DEFAULT, policy=POLICY_BIOS_PIBT_V6)
    task = sc.assignments[0][0]
    original = amr.astar
    calls = []
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(amr, "astar", counted)
    first = brain._task_corridor_directions(task)
    assert first
    first.clear()
    assert brain._task_corridor_directions(task)
    assert len(calls) == 1
    task.pick, task.drop = task.drop, task.pick
    assert brain._task_corridor_directions(task)
    assert len(calls) == 2
