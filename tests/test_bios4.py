import pytest
from src.amr import (AMRBrain, Peer, POLICY_BIOS_3, POLICY_BIOS_4,
                     POLICY_BIOS_FAMILY, POLICIES, Task)
from src.environment import classic_warehouse, open_floor
from src.planner import astar
from src.settings import DEFAULT
from src.world import World


def test_bios4_policy_registered_and_in_family():
    assert POLICY_BIOS_4 in POLICIES
    assert POLICY_BIOS_4 in POLICY_BIOS_FAMILY


def test_bios4_brain_initialization():
    env = classic_warehouse()
    brain = AMRBrain('A', env, DEFAULT, policy=POLICY_BIOS_4, home=(1, 4))
    assert brain.policy == POLICY_BIOS_4
    # Inherits the whole BIOS lineage: yield bookkeeping and congestion escape.
    assert brain.yield_count == 0.0
    assert brain.mode_congestion is None
    assert hasattr(brain, '_calculate_task_score')
    assert hasattr(brain, '_priority_standoff')


def test_bios4_high_yield_peer_is_tougher_obstacle():
    env = open_floor()
    brain = AMRBrain('A', env, DEFAULT, policy=POLICY_BIOS_4, home=(1, 4))
    brain.yield_count = 2.0          # we have yielded a little
    # Same pose, same priority; only the published stop-count differs. The peer
    # that has stopped for others more often must cost strictly more to cross.
    brain.peers['B'] = Peer('B', cell=(5, 5), last_seen=10.0,
                            priority=0.0, yield_count=9.0)
    brain.peers['C'] = Peer('C', cell=(12, 12), last_seen=10.0,
                            priority=0.0, yield_count=0.0)
    pens = brain._peer_obstacle_penalties(10.0)
    assert pens[(5, 5)] > pens[(12, 12)]


def test_bios4_rank_flips_obstacle_hardness():
    env = open_floor()
    brain = AMRBrain('A', env, DEFAULT, policy=POLICY_BIOS_4, home=(1, 4))
    brain.yield_count = 8.0
    # We outrank D: our planner sees them as soft. E outranks us: hard.
    brain.peers['D'] = Peer('D', cell=(5, 5), last_seen=10.0,
                            priority=0.0, yield_count=0.0)
    pens_soft = dict(brain._peer_obstacle_penalties(10.0))
    del brain.peers['D']
    brain.peers['E'] = Peer('E', cell=(5, 5), last_seen=10.0,
                            priority=0.0, yield_count=20.0)
    pens_hard = brain._peer_obstacle_penalties(10.0)
    spec = DEFAULT.traffic
    assert pens_soft[(5, 5)] == pytest.approx(
        spec.bios4_peer_cell_cost / spec.bios4_precedence_boost)
    assert pens_hard[(5, 5)] > spec.bios4_peer_cell_cost


def test_bios4_stale_peer_gets_no_precedence():
    env = classic_warehouse()
    brain = AMRBrain('A', env, DEFAULT, policy=POLICY_BIOS_4, home=(1, 4))
    brain.peers['F'] = Peer('F', cell=(5, 5), last_seen=-100.0,
                            priority=0.0, yield_count=50.0)
    assert brain._peer_obstacle_penalties(0.0) == {}


def test_bios4_other_policies_get_no_overlay():
    env = classic_warehouse()
    for pol in (POLICY_BIOS_3, 'hierarchical'):
        brain = AMRBrain('A', env, DEFAULT, policy=pol, home=(1, 4))
        brain.peers['B'] = Peer('B', cell=(5, 5), last_seen=10.0, yield_count=9.0)
        assert brain._peer_obstacle_penalties(10.0) == {}


def test_bios4_replan_routes_around_tough_peer_when_free():
    """The overlay must actually bend A*: an equal-cost alternative is taken."""
    env = open_floor()                    # wide-open grid: alternatives exist
    brain = AMRBrain('A', env, DEFAULT, policy=POLICY_BIOS_4, home=(2, 2))
    start, goal = (2, 10), (17, 10)       # straight shot down one row
    peer_at = (9, 10)                     # dead centre of that row
    base = astar(env, start, goal)

    brain.peers['G'] = Peer('G', cell=peer_at, last_seen=1.0,
                            priority=0.0, yield_count=25.0)
    overlay = brain._peer_obstacle_penalties(1.0)
    detoured = astar(env, start, goal, extra_cost=dict(overlay))
    assert base and detoured
    assert peer_at in base                # without overlay the route crosses them
    ring = set(env.neighbors(peer_at)) | {peer_at}
    assert not any(c in ring for c in detoured)   # with it, keep the distance


def test_bios4_note_yield_counts_stops():
    env = classic_warehouse()
    brain = AMRBrain('A', env, DEFAULT, policy=POLICY_BIOS_4, home=(1, 4))
    brain._note_yield()
    brain._note_yield()
    assert brain.yield_count == 2.0       # raw, never decays - the precedence ledger
    assert brain.yield_credit == 2.0      # runtime EMA input, decays per route tick


def test_bios4_standoff_gating_matches_bios3():
    """BIOS_4 inherits the priority standoff from the previous mechanism set."""
    env = classic_warehouse()
    w = World(env, DEFAULT, seed=0)
    w.add_robot('A', (1, 4), 0.0)
    sensors = w.sense('A')
    brain = AMRBrain('A', env, DEFAULT, policy=POLICY_BIOS_4, home=(1, 4))
    brain.path = [(1, 4), (2, 4)]
    brain.pidx = 1
    brain.peers['B'] = Peer('B', cell=(2, 4), pose=(2.5, 4.5, 0.0),
                            priority=0.0, yield_count=3.0, last_seen=1.0)
    assert brain._peer_precedes(brain.peers['B'], brain._arbitration_key())
