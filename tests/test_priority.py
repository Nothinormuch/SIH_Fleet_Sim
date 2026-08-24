"""Properties of the decentralised priority engine."""

import random

from src.amr import (AMRBrain, CELL_ZONE_BASE, POLICIES, POLICY_BIOS_PIBT,
                     POLICY_BIOS_PIBT_V2)
from src.environment import (FREE, Warehouse, chokepoint_warehouse,
                             classic_warehouse, open_floor)
from src.messages import block_claim, decode, encode
from src.priority import PriorityKey, pibt_step
from src.settings import DEFAULT
from src.scenarios import dead_zone
from src.topology import analyse_topology, directed_circulation


def _key(rid: str, age: int) -> PriorityKey:
    return PriorityKey(service_age=age, robot_id=rid)


def test_priority_key_round_trip_and_total_order():
    low = PriorityKey(service_age=2, robot_id="AMR01")
    high = PriorityKey(waiting_age=1, robot_id="AMR02")
    assert high > low
    assert PriorityKey.from_wire(high.to_wire(), "fallback") == high
    assert PriorityKey.from_wire(["bad"], "AMR09") == PriorityKey(robot_id="AMR09")


def test_priority_inheritance_pushes_a_three_robot_chain():
    env = open_floor(5, 3)
    positions = {"A": (1, 1), "B": (2, 1), "C": (3, 1)}
    goals = {rid: (4, 1) for rid in positions}
    priorities = {"A": _key("A", 9), "B": _key("B", 2), "C": _key("C", 1)}
    preferred = {"A": (2, 1), "B": (3, 1), "C": (4, 1)}

    decision = pibt_step(env, positions, goals, priorities, preferred)

    assert decision.next_cells == {"A": (2, 1), "B": (3, 1), "C": (4, 1)}
    assert decision.inherited_from == {"B": "A", "C": "B"}
    assert decision.effective_priorities["C"] == priorities["A"]


def test_backtracking_rejects_a_two_robot_edge_swap():
    env = Warehouse(2, 1, ((FREE, FREE),), (), (), "two-cell")
    positions = {"A": (0, 0), "B": (1, 0)}
    goals = {"A": (1, 0), "B": (0, 0)}
    priorities = {"A": _key("A", 4), "B": _key("B", 1)}

    decision = pibt_step(env, positions, goals, priorities, goals)

    assert decision.next_cells == positions
    assert decision.backtracks > 0


def test_four_agent_rotation_is_legal_and_deterministic():
    env = open_floor(2, 2)
    positions = {"A": (0, 0), "B": (1, 0), "C": (1, 1), "D": (0, 1)}
    preferred = {"A": (1, 0), "B": (1, 1), "C": (0, 1), "D": (0, 0)}
    priorities = {rid: _key(rid, 5 - i) for i, rid in enumerate(sorted(positions))}

    first = pibt_step(env, positions, preferred, priorities, preferred)
    second = pibt_step(env, positions, preferred, priorities, preferred)

    assert first.next_cells == preferred
    assert second.next_cells == first.next_cells
    assert len(set(first.next_cells.values())) == len(positions)


def test_tree_appendage_gives_exit_priority_signal():
    # A 2x2 cycle with a two-cell spur attached on the right.
    grid = (
        (FREE, FREE, 1, 1),
        (FREE, FREE, FREE, FREE),
    )
    env = Warehouse(4, 2, grid, (), (), "lollipop")
    topo = analyse_topology(env)

    assert (3, 1) in topo.branch_of
    assert topo.leaving_branch((3, 1), (0, 0))
    assert not topo.leaving_branch((3, 1), (2, 1))


def test_random_step_outputs_have_no_vertex_conflict_or_edge_swap():
    env = open_floor(6, 6)
    cells = list(env.free_cells())
    rng = random.Random(26123)

    for case in range(100):
        starts = rng.sample(cells, 6)
        goals_list = rng.sample(cells, 6)
        positions = {f"R{i}": c for i, c in enumerate(starts)}
        goals = {f"R{i}": c for i, c in enumerate(goals_list)}
        keys = {rid: _key(rid, case + i) for i, rid in enumerate(positions)}
        decision = pibt_step(env, positions, goals, keys)

        assert len(set(decision.next_cells.values())) == len(positions)
        for rid, target in decision.next_cells.items():
            assert target == positions[rid] or target in env.neighbors(positions[rid])
        for a, a_to in decision.next_cells.items():
            for b, b_to in decision.next_cells.items():
                if a < b:
                    assert not (a_to == positions[b] and b_to == positions[a])


def test_block_lease_round_trip_carries_local_ttl_and_frozen_key():
    key = PriorityKey(waiting_age=7, service_age=11, robot_id="AMR03")
    message = block_claim("AMR03", 9, 12.5, 4, 99.0, 1000.0, 8,
                          ttl=4.0, priority_key=key.to_wire())
    restored = decode(encode(message))

    assert restored is not None
    assert restored.body["ttl"] == 4.0
    assert PriorityKey.from_wire(restored.body["pk"], restored.src) == key


def test_policy_is_exposed_under_bios_pibt_name():
    assert POLICY_BIOS_PIBT == "BIOS_PIBT.1"
    assert POLICY_BIOS_PIBT in POLICIES
    assert POLICY_BIOS_PIBT_V2 == "BIOS_PIBT.2"
    assert POLICY_BIOS_PIBT_V2 in POLICIES


def test_v2_circulation_is_strongly_connected_and_has_no_reverse_edge():
    env = classic_warehouse()
    circulation = directed_circulation(env)
    cells = list(env.free_cells())
    assert circulation.enabled

    for start in cells:
        seen, stack = {start}, [start]
        while stack:
            current = stack.pop()
            for nxt in env.neighbors(current):
                if nxt not in seen and circulation.allows(env, current, nxt):
                    seen.add(nxt)
                    stack.append(nxt)
        assert len(seen) == len(cells)

    for cell in cells:
        for nxt in env.neighbors(cell):
            assert not (circulation.allows(env, cell, nxt)
                        and circulation.allows(env, nxt, cell))


def test_v2_cell_lease_ids_are_unique_and_recognize_only_their_cell():
    env = classic_warehouse()
    brain = AMRBrain("AMR01", env, DEFAULT, policy=POLICY_BIOS_PIBT_V2)
    a, b = (4, 6), (5, 6)
    za, zb = brain._cell_zone_id(a), brain._cell_zone_id(b)

    assert za >= CELL_ZONE_BASE and zb >= CELL_ZONE_BASE and za != zb
    assert brain._zone_contains(za, a)
    assert not brain._zone_contains(za, b)


def test_cell_pitch_has_positive_standstill_clearance_budget():
    spec = DEFAULT.robot
    assert DEFAULT.cell_m > 2 * spec.radius_m + spec.omni_stop_m


def test_hundred_robot_scenario_scales_floor_capacity():
    small = dead_zone(n_robots=24, seed=20, mesh_radio=True)
    large = dead_zone(n_robots=100, seed=20, mesh_radio=True)

    assert large.n_robots == 100
    assert large.env.width > small.env.width and large.env.height > small.env.height
    assert len(set(large.starts)) == 100


def test_coordination_looks_past_current_centering_waypoint():
    brain = AMRBrain("AMR01", open_floor(4, 3), DEFAULT,
                     policy=POLICY_BIOS_PIBT)
    brain.path = [(1, 1), (2, 1), (3, 1)]
    brain.pidx = 0
    brain._last_cell = (1, 1)

    assert brain._next_cell() == (2, 1)


def test_corridor_waypoint_is_not_treated_as_a_passing_bay():
    env = chokepoint_warehouse(length=5)
    brain = AMRBrain("AMR01", env, DEFAULT, policy=POLICY_BIOS_PIBT)

    assert not brain._is_safe_retreat_bay((6, env.height // 2))
    assert brain._is_safe_retreat_bay((5, env.height // 2 + 1))


def test_robot_inside_controlled_block_is_never_stopped_at_its_exit():
    env = chokepoint_warehouse(length=5)
    brain = AMRBrain("AMR01", env, DEFAULT, policy=POLICY_BIOS_PIBT)
    y = env.height // 2

    assert brain._block_conflict(1.0, (10, y), (11, y), (0.0, "AMR01")) is None
