"""Properties of the decentralised priority engine."""

import random

from src.amr import (AMRBrain, CELL_ZONE_BASE, POLICIES, POLICY_BIOS_PIBT,
                     POLICY_BIOS_PIBT_V2, POLICY_BIOS_PIBT_V3,
                     POLICY_BIOS_PIBT_V5, Peer,
                     ST_IDLE, Task)
from src.environment import (FREE, Warehouse, chokepoint_warehouse,
                             classic_warehouse, open_floor)
from src.messages import BID, TASK_DONE, TASK_NEW, block_claim, decode, encode
from src.priority import PriorityKey, pibt_step
from src.settings import DEFAULT
from src.scenarios import dead_zone
from src.task_allocation import ALLOCATION_AUCTION
from src.topology import analyse_topology, directed_circulation
from src.world import World


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
    assert POLICY_BIOS_PIBT_V3 == "BIOS_PIBT.3"
    assert POLICY_BIOS_PIBT_V3 in POLICIES
    assert POLICY_BIOS_PIBT_V5 == "BIOS_PIBT.5"
    assert POLICY_BIOS_PIBT_V5 in POLICIES


def test_v5_rejects_energy_infeasible_task_and_accepts_charged_robot():
    env = open_floor(10, 10)
    world = World(env, DEFAULT, seed=0)
    state = world.add_robot("AMR01", (1, 1))
    brain = AMRBrain("AMR01", env, DEFAULT, policy=POLICY_BIOS_PIBT_V5,
                     allocation_policy=ALLOCATION_AUCTION)
    task = Task("LONG", (8, 1), (8, 8))

    state.battery_wh = 0.15 * DEFAULT.robot.battery_full_wh
    low = world.sense("AMR01")
    state.battery_wh = 0.80 * DEFAULT.robot.battery_full_wh
    high = world.sense("AMR01")

    assert not brain._energy_feasible(task, low)[0]
    assert brain._energy_feasible(task, high)[0]


def test_v5_candidate_filter_prefers_nearest_healthy_robots_without_age_expansion():
    env = open_floor(12, 4)
    brain = AMRBrain("AMR04", env, DEFAULT, policy=POLICY_BIOS_PIBT_V5,
                     allocation_policy=ALLOCATION_AUCTION)
    task = Task("T", (1, 1), (10, 1), announced_t=0.0)
    brain.peers = {
        "AMR01": Peer("AMR01", cell=(1, 1), last_seen=21.0),
        "AMR02": Peer("AMR02", cell=(2, 1), last_seen=21.0),
        "AMR03": Peer("AMR03", cell=(3, 1), last_seen=21.0),
    }
    sensors = type("S", (), {"cell": (9, 1), "battery_frac": 0.8})()

    assert not brain._energy_candidate(task, 1.0, sensors)
    assert not brain._energy_candidate(task, 21.0, sensors)


def test_v5_candidate_filter_replaces_stale_and_energy_infeasible_peers():
    env = open_floor(12, 4)
    brain = AMRBrain("AMR04", env, DEFAULT, policy=POLICY_BIOS_PIBT_V5,
                     allocation_policy=ALLOCATION_AUCTION)
    task = Task("T", (1, 1), (10, 1), announced_t=0.0)
    brain.peers = {
        "AMR01": Peer("AMR01", cell=(1, 1), last_seen=-10.0),
        # Above the generic 15% charge trigger but below this task's predicted
        # reserve, so it must not consume a candidate slot.
        "AMR02": Peer("AMR02", cell=(2, 1), last_seen=1.0,
                      battery_frac=0.151),
        "AMR03": Peer("AMR03", cell=(3, 1), last_seen=1.0),
    }
    sensors = type("S", (), {"cell": (9, 1), "battery_frac": 0.8})()

    assert brain._energy_candidate(task, 1.0, sensors)


def test_v5_caps_each_round_to_the_declared_bid_bundle():
    env = open_floor(12, 4)
    world = World(env, DEFAULT, seed=0)
    world.add_robot("AMR01", (1, 1))
    brain = AMRBrain("AMR01", env, DEFAULT, policy=POLICY_BIOS_PIBT_V5,
                     allocation_policy=ALLOCATION_AUCTION)
    brain.open_tasks = {
        f"T{i:02d}": Task(f"T{i:02d}", (2 + i % 8, 1), (10, 2))
        for i in range(20)
    }
    outbox = []

    brain._run_v3_batch_auction(0.0, world.sense("AMR01"), outbox)

    assert len(outbox) == DEFAULT.traffic.energy_bid_bundle
    assert brain.stats["auction_bids_sent"] == len(outbox)


def test_v3_peer_catalog_gossips_a_missed_task_without_a_manager():
    brain = AMRBrain(
        "AMR01", open_floor(6, 6), DEFAULT,
        policy=POLICY_BIOS_PIBT_V3,
        allocation_policy=ALLOCATION_AUCTION,
    )
    brain.open_tasks["T1"] = Task("T1", (1, 1), (4, 4), 0.0, 2, 0.6)
    outbox = []

    brain._broadcast_task_catalog(1.0, outbox)

    assert len(outbox) == 1
    assert outbox[0].type == TASK_NEW
    assert outbox[0].src == "AMR01"
    assert outbox[0].body["task"] == "T1"
    assert outbox[0].body["e"] == 2


def test_v3_peer_catalog_repeats_completion_records():
    brain = AMRBrain(
        "AMR01", open_floor(6, 6), DEFAULT,
        policy=POLICY_BIOS_PIBT_V3,
        allocation_policy=ALLOCATION_AUCTION,
    )
    brain.completed_tasks.add("T1")
    outbox = []

    brain._broadcast_completion_catalog(1.0, outbox)

    assert len(outbox) == 1
    assert outbox[0].type == TASK_DONE
    assert outbox[0].body["task"] == "T1"


def test_v3_chokepoint_wave_members_do_not_refill_mid_phase():
    env = chokepoint_warehouse(length=13)
    world = World(env, DEFAULT, seed=0)
    world.add_robot("A", (1, 1), 0.0)
    brain = AMRBrain(
        "A", env, DEFAULT, policy=POLICY_BIOS_PIBT_V3,
        allocation_policy=ALLOCATION_AUCTION,
    )
    brain.open_tasks = {
        "T000": Task("T000", (1, 1), (23, 1)),
        "T001": Task("T001", (23, 2), (1, 2)),
        "T002": Task("T002", (1, 3), (23, 3)),
        "T004": Task("T004", (1, 5), (23, 5)),
    }

    brain._run_v3_batch_auction(0.0, world.sense("A"), [])
    brain._run_v3_batch_auction(0.7, world.sense("A"), [])

    cid = next(iter(brain._task_corridor_directions(brain.open_tasks["T000"])))
    entry, members = brain._v3_corridor_waves[cid]
    assert entry == (6, 4)
    assert members == ("T000", "T002")

    # Finishing one member must not admit T004 into the still-active wave.
    brain.completed_tasks.add("T002")
    brain.open_tasks.pop("T002")
    assert brain._v3_corridor_waves[cid][1] == ("T000", "T002")


def test_v3_stages_one_cell_before_an_occupied_chokepoint_mouth():
    env = chokepoint_warehouse(length=13)
    brain = AMRBrain(
        "A", env, DEFAULT, policy=POLICY_BIOS_PIBT_V3,
        allocation_policy=ALLOCATION_AUCTION,
    )
    brain.path = [(5, 2), (5, 3), (5, 4), (6, 4), (7, 4)]
    brain.pidx = 1
    brain._last_cell = (5, 2)
    brain.peers["B"] = Peer(
        "B", cell=(5, 4), goal=(7, 4), intent=[(6, 4), (7, 4)],
        last_seen=0.0,
    )

    assert brain._v3_staging_conflict(0.0, (5, 2)) == "B"


def test_v3_idle_vacate_never_enters_a_bidirectional_block():
    env = chokepoint_warehouse(length=13)
    world = World(env, DEFAULT, seed=0)
    world.add_robot("A", (5, 4), 0.0)
    brain = AMRBrain(
        "A", env, DEFAULT, policy=POLICY_BIOS_PIBT_V3, home=(23, 4),
        allocation_policy=ALLOCATION_AUCTION,
    )
    brain.peers["B"] = Peer("B", cell=(4, 4), goal=(5, 4), last_seen=0.0)

    brain._vacate_if_in_the_way(0.0, world.sense("A"))

    assert brain.goal is not None
    assert brain._controlled_block(brain.goal) is None


def test_v3_duplicate_cell_loser_exits_to_a_non_corridor_cell():
    env = chokepoint_warehouse(length=13)
    world = World(env, DEFAULT, seed=0)
    world.add_robot("B", (5, 4), 0.0)
    brain = AMRBrain(
        "B", env, DEFAULT, policy=POLICY_BIOS_PIBT_V3,
        allocation_policy=ALLOCATION_AUCTION,
    )
    brain.peers["A"] = Peer(
        "A", cell=(5, 4), pose=(7.8, 6.8, 0.0), last_seen=0.0,
    )

    repaired = brain._repair_duplicate_cell(0.0, world.sense("B"))

    assert repaired
    assert brain._cell_repair_target == brain.path[-1]
    assert brain.path[-1] in env.neighbors((5, 4))
    assert brain._controlled_block(brain.path[-1]) is None


def test_v3_batch_auction_bounds_work_per_drop_cell():
    env = open_floor(8, 8)
    world = World(env, DEFAULT, seed=0)
    starts = {"A": (0, 0), "B": (3, 0), "C": (6, 0)}
    brains = {}
    for rid, start in starts.items():
        world.add_robot(rid, start, 0.0)
        brains[rid] = AMRBrain(
            rid, env, DEFAULT, policy=POLICY_BIOS_PIBT_V3,
            allocation_policy=ALLOCATION_AUCTION,
        )

    tasks = [
        Task("T1", (1, 1), (7, 7), 0.0, 0, 0.6),
        Task("T2", (2, 1), (7, 7), 0.0, 0, 0.6),
        Task("T3", (3, 1), (7, 7), 0.0, 0, 0.6),
        Task("T4", (4, 1), (0, 7), 0.0, 0, 0.6),
    ]
    first_round = {}
    for rid, brain in brains.items():
        brain.open_tasks = {task.tid: Task(**task.__dict__) for task in tasks}
        brain.peers = {
            other: Peer(other, state=ST_IDLE, last_seen=0.0)
            for other in brains if other != rid
        }
        outbox = []
        brain._run_v3_batch_auction(0.0, world.sense(rid), outbox)
        first_round[rid] = [message for message in outbox if message.type == BID]

    all_bids = [message for messages in first_round.values() for message in messages]
    winners = []
    for rid, brain in brains.items():
        brain._ingest(0.7, [message for message in all_bids if message.src != rid])
        for peer in brain.peers.values():
            peer.last_seen = 0.7
        outbox = []
        brain._run_v3_batch_auction(0.7, world.sense(rid), outbox)
        assert all(claim[2] == rid for claim in brain._task_claims.values())
        if brain.task is not None:
            winners.append(brain.task)

    assert len({task.tid for task in winners}) == len(winners)
    assert sum(task.drop == (7, 7) for task in winners) \
        <= DEFAULT.traffic.auction_drop_capacity


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
