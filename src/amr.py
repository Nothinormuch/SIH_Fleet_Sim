"""The AMR agent: three control loops, one per timescale, plus the comms that bind them.

THE ARCHITECTURAL CLAIM
=======================
The problem statement asks for a fully decentralised fleet and treats centralisation as
the flaw. That framing does not survive contact with how AMR fleets are actually built,
so this agent implements something different and says why:

    Layer 0  SAFETY          50 Hz   onboard, certified, NEVER network-dependent
    Layer 1  LOCAL TRAFFIC   10 Hz   onboard, peer intents, degrades gracefully
    Layer 2  GLOBAL ROUTE     1 Hz   central optimiser when reachable, P2P when not

Three separate loops, because the statement conflates them. The "split-second decisions"
it wants moved to the edge - protective stopping and local avoidance - were never on a
server in any real product; they are Layers 0 and 1 and they are already onboard. The
only loop a fleet manager ever owned is Layer 2, running at 0.1-1 Hz, where a 4 ms LAN
round trip is worth 5 mm of robot travel. Latency is not what causes warehouse
collisions. Localisation error is.

The repository keeps both choices explicit: `POLICY_HIERARCHICAL` is the optional
central-plus-peer baseline, while `POLICY_DECENTRALIZED` removes the manager from the
task and route path entirely. The benchmark can therefore measure the cost of giving
up global coordination instead of quietly substituting a central server.

WHY ALL POLICIES SHARE THIS CLASS
=======================================
Policies are fields, not separate implementations. They share one trajectory follower,
one safety layer and one physics interface, so a throughput difference between them is
caused by coordination and nothing else. Separately tuned controllers would make any
speedup number meaningless.

THE AGENT DOES NO I/O
=====================
`step()` takes an inbox and returns an outbox. Sockets live in transport.py. That is
what lets the identical brain run as a real UDP process for the demo, run headless at
several hundred times realtime for the statistics, and drop onto a Raspberry Pi without
a single edit.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from . import messages as msg
from .environment import Warehouse, corridors
from .geometry import (Cell, angle_diff, bearing, cell_center, clamp, dist,
                       manhattan, segment_point_distance, to_cell)
from .planner import astar
from .priority import PriorityKey, pibt_step
from .settings import Config
from .task_allocation import (ALLOCATION_AUCTION, ALLOCATION_AUCTION_BUNDLE,
                              ALLOCATION_HUNGARIAN, validate_allocation_policy)
from .topology import analyse_topology, directed_circulation
from .world import Actuation, Sensors

# ---------------------------------------------------------------------- constants

POLICY_STOP_WAIT = "stop_and_wait"
POLICY_CENTRAL = "central"
POLICY_HIERARCHICAL = "hierarchical"
POLICY_BIOS = "BIOS_1.0.0"
POLICY_BIOS_PIBT = "BIOS_PIBT.1"
POLICY_BIOS_PIBT_V2 = "BIOS_PIBT.2"
POLICY_BIOS_PIBT_V3 = "BIOS_PIBT.3"
POLICY_BIOS_PIBT_V5 = "BIOS_PIBT.5"
POLICY_BIOS_PIBT_V6 = "BIOS_PIBT.6"
POLICY_DECENTRALIZED = "decentralized"
# The learned neuroevolution policy. 549-parameter PolicyNet; same Layer-1
# arbitration slot as the BIOS family. See bios4.py for architecture.
POLICY_BIOS4 = "BIOS_4"
_BIOS_FAMILY = (POLICY_BIOS, POLICY_BIOS4)
ENERGY_AUCTION_POLICIES = (POLICY_BIOS_PIBT_V5, POLICY_BIOS_PIBT_V6)
V3_AUCTION_POLICIES = (POLICY_BIOS_PIBT_V3, *ENERGY_AUCTION_POLICIES)
DIRECTED_POLICIES = (POLICY_BIOS_PIBT_V2, *V3_AUCTION_POLICIES)
PIBT_POLICIES = (POLICY_BIOS_PIBT, *DIRECTED_POLICIES)
DECENTRAL_POLICIES = (POLICY_BIOS, POLICY_DECENTRALIZED, *PIBT_POLICIES, POLICY_BIOS4)
CENTRAL_POLICIES = (POLICY_CENTRAL,)
POLICIES = (POLICY_STOP_WAIT, POLICY_CENTRAL, POLICY_HIERARCHICAL,
            POLICY_BIOS, POLICY_DECENTRALIZED, *PIBT_POLICIES, POLICY_BIOS4)

MODE_CENTRAL = "CENTRAL_OK"
MODE_P2P = "DEGRADED_P2P"

ST_IDLE = "idle"
ST_TO_PICK = "to_pick"
ST_TO_DROP = "to_drop"
ST_CHARGING = "charging"
ST_BLOCKED = "blocked"
ST_RETREAT = "retreat"

CELL_ZONE_BASE = 1_000_000


@dataclass
class Task:
    tid: str
    pick: Cell
    drop: Cell
    announced_t: float = 0.0
    auction_epoch: int = 0
    bid_deadline: float = 0.0
    cargo_type: str = "normal"
    cargo_weight: float = 0.0
    priority: int = 1
    # Receiver-local absolute completion deadline. It is sent on the wire only as a
    # remaining-time TTL because independent edge nodes have unrelated clock epochs.
    deadline: float | None = None
    # Local mirror for observability; `_task_claims` remains authoritative.
    lease_owner: str | None = None
    lease_until: float = 0.0


@dataclass
class Peer:
    """What one robot believes about another. Always stale, never authoritative."""

    rid: str
    cell: Cell = (0, 0)
    pose: tuple[float, float, float] = (0.0, 0.0, 0.0)
    priority: float = 0.0
    blocked_on: str | None = None
    state: str = ST_IDLE
    goal: Cell | None = None
    last_seen: float = -1e9
    intent: list[Cell] = field(default_factory=list)
    windows: list[tuple[float, float]] = field(default_factory=list)
    priority_key: PriorityKey | None = None
    battery_frac: float = 1.0
    task_id: str | None = None


class AMRBrain:
    """One robot's entire decision-making. Pure: no sockets, no clocks, no globals."""

    def __init__(self, rid: str, env: Warehouse, cfg: Config,
                 policy: str = POLICY_HIERARCHICAL, home: Cell = (0, 0),
                 allocation_policy: str | None = None,
                 policy_model=None) -> None:
        if policy not in POLICIES:
            raise ValueError(f"unknown policy {policy!r}")
        validate_allocation_policy(allocation_policy)
        self.rid = rid
        self.env = env
        self.cfg = cfg
        self.policy = policy
        self.allocation_policy = allocation_policy
        self.home = home
        # Single-file blocks, shared and cached across the fleet. Acquiring a whole
        # block before entering is what stops two robots meeting halfway down a
        # one-lane aisle, where no per-cell rule can help either of them.
        self.blocks = corridors(env)
        self.topology = analyse_topology(env)
        self.circulation = directed_circulation(env)

        self.path: list[Cell] = []
        # Earliest-entry time per cell, present only while following a central plan.
        # A space-time plan is a *schedule*: without these the robot would collapse the
        # planned waits and sail straight through the conflict they were avoiding.
        self.path_times: list[float] = []
        self.pidx = 0
        self.goal: Cell | None = None
        self.task: Task | None = None
        # ``auction_bundle`` is an experimental BIOS 6 allocator, not a new motion
        # policy. It may hold exactly one leased task after the executing task.
        self.future_task: Task | None = None
        self._future_context: tuple[str, int, int] | None = None
        self._future_bid: tuple[str, int, str, int, int, float] | None = None
        self._future_bid_contexts: dict[
            tuple[str, int, str], tuple[str, int, int]
        ] = {}
        self._pending_unknown_bids: dict[
            str, list[tuple[float, str, int, float,
                            tuple[str, int, int] | None]]
        ] = {}
        self._peer_future_nominations: dict[
            str, tuple[int, float, float, str, int, int]
        ] = {}
        self._future_generation = 0
        self._last_future_lease_broadcast = -1e9
        self._last_future_revalidate = -1e9
        self._future_retry_after = -1e9
        self._future_needs_reconcile = False
        self._future_network_candidate_since: float | None = None
        self._known_peer_ids: set[str] = set()
        self._task_descriptors: dict[str, tuple] = {}
        self._task_descriptor_from_wms: set[str] = set()
        self._completion_proofs: dict[str, tuple[int, str]] = {}
        self.allocation_compute_ms: list[float] = []
        self.state = ST_IDLE
        self.mode = MODE_P2P
        self.epoch = 0

        self.open_tasks: dict[str, Task] = {}
        self.completed_tasks: set[str] = set()
        # task_id -> (auction epoch, bid cost, owner, lease expiry)
        self._task_claims: dict[str, tuple[int, float, str, float]] = {}
        # Manager-directed awards can arrive before their TASK_NEW packet because
        # both travel over a lossy, delayed network. Keep the destination marker until
        # the task is visible and the task loop can accept it with the current pose.
        self._awarded: set[str] = set()
        # Peer nominations are advisory auction results, not manager commands. Keep
        # their exact epoch/cost/lease so eligibility can be revalidated locally at
        # the moment of acceptance using the robot's current sensors and battery.
        self._peer_nominations: dict[str, tuple[int, float, float]] = {}
        # A pre-assigned work queue. The headline benchmark uses this so that task
        # allocation is IDENTICAL across the route-coordination policies - otherwise a makespan
        # difference could be caused by who got which job rather than by how the
        # fleet handles traffic, and the 20% claim would be unattributable.
        self.queue: list[Task] = []
        # Compatibility flag for callers from the earlier scenario-based runner. New
        # code should pass allocation_policy explicitly instead.
        self.use_auction = allocation_policy == ALLOCATION_AUCTION
        self.completed: list[tuple[str, float, float]] = []   # (tid, start_t, done_t)
        self._task_started_t = 0.0

        self.peers: dict[str, Peer] = {}
        self._bids: dict[str, dict[tuple[int, str], float]] = {}
        self._bid_seen_t: dict[tuple[str, int, str], float] = {}
        self._bid_opened: dict[str, float] = {}
        self._last_lease_broadcast = -1e9
        self._v3_round_started: float | None = None
        self._remote_winner_since: float | None = None
        self._remote_winner_fingerprint: tuple[
            tuple[str, int, str, float], ...
        ] | None = None
        self._energy_retry_after = -1e9
        # A duplicate worker canceled by peer completion must first leave the lane it
        # occupied. Becoming goal-less in a single-file block turns an otherwise
        # correct convergence event into a permanent physical obstruction.
        self._needs_duplicate_vacate = False
        # BIOS 5 may need to move one idle bidder to the pickup side of an empty
        # two-way chokepoint before the next auction.  This is deliberately not a
        # task award: an AMR approaching against the task's loaded direction while
        # already holding that task can deadlock with the current cargo wave.
        self._auction_reposition_target: Cell | None = None
        # Static energy requirements used to rank peers. The warehouse and task
        # endpoints do not move, so recomputing the same A* legs every 0.6 s would
        # turn a network optimization into a planner CPU regression.
        self._energy_required_cache: dict[
            tuple[Cell, str, Cell, Cell, str, float], tuple[float, float] | None
        ] = {}
        # block -> (entry mouth, immutable task ids in this directional batch)
        self._v3_corridor_waves: dict[int, tuple[Cell, tuple[str, ...]]] = {}
        self._last_catalog_broadcast = -1e9
        self._catalog_cursor = 0
        self._last_completion_broadcast = -1e9
        self._completion_cursor = 0
        self._last_heartbeat_broadcast = -1e9
        self._last_heartbeat_signature: tuple | None = None
        self._last_intent_broadcast = -1e9
        self._last_intent_signature: tuple | None = None
        # (task, epoch) -> (last rounded cost, local send time).  Other robots retain
        # received bids for a bounded cache window while fresh heartbeat state proves
        # that the bidder is still idle.
        self._v6_bid_broadcasts: dict[tuple[str, int], tuple[float, float]] = {}

        # BIOS 6 observability is generated by the deterministic decision code, not
        # by an LLM inventing a story after the fact.  The dashboard receives only
        # the latest bounded records, while headless runs count every reason code.
        self.decision_log: list[dict] = []

        # Cells this robot has learned to avoid, with a decaying penalty. Contested
        # cells become expensive, never impassable - marking them impassable is how a
        # jam turns into an unsolvable map.
        self.penalty: dict[Cell, float] = {}
        # Directed-edge traversal experience: (delay seconds, samples, updated time).
        # Unlike the short-lived contested-cell penalty, this is a decaying memory of
        # repeated traffic.  It guides A* and bids but never marks an edge impassable.
        self._edge_experience: dict[
            tuple[Cell, Cell], tuple[float, int, float]
        ] = {}
        self._experience_dirty: set[tuple[Cell, Cell]] = set()
        self._experience_seen: dict[tuple[str, Cell, Cell], int] = {}
        self._last_experience_share = -1e9
        self._v6_wait_edge: tuple[Cell, Cell] | None = None
        self._v6_wait_s = 0.0
        # Stationary, anonymous lidar returns become short-lived local map obstacles.
        # They are never learned from peer messages, so a radio failure cannot erase
        # the physical blocked-aisle response. cell -> receiver-local expiry.
        self._dynamic_blocked_until: dict[Cell, float] = {}
        self._dynamic_candidates: dict[Cell, tuple[float, float, int]] = {}
        # V6 short-horizon forecasts: cell -> (cost, receiver-local expiry, source).
        # These are soft route hints only.  A prediction may be wrong, so it must not
        # enter the hard blocked-cell set used for stationary objects.
        self._predicted_cell_cost: dict[Cell, tuple[float, float, str]] = {}
        self._last_predictive_replan = -1e9

        # When Layer 0 first refused to move us. Layer 1 has to see this: a fleet
        # whose robots are all safety-stopped nose to nose is deadlocked, and if the
        # traffic layer only ever counts *its own* holds it will report everything
        # healthy while nothing moves.
        self._stall_since: float | None = None
        # Block id -> when we first announced our intent to enter it. Entering is a
        # two-phase commit: announce, observe for a round, then go.
        self._gate_since: dict[int, float] = {}
        self._gate_committed: set[int] = set()
        # V2 cell-centre merge gate.  Junction admission waits one complete multicast
        # round, then every contender compares the same frozen total-order key.
        self._cell_gate_since: dict[Cell, float] = {}
        self._cell_repair_target: Cell | None = None
        # The priority we last BROADCAST. Arbitration must use this, never the live
        # value - see _arbitration_key.
        self._pub_priority = 0.0
        self._pub_priority_key = PriorityKey(robot_id=rid)
        self.blocked_since: float | None = None
        # Bounded priority hysteresis: when a wait is authorized to move, preserve its
        # accumulated rank long enough to turn and cross one cell.  Resetting it in the
        # same tick makes contenders alternate permission without translating; keeping
        # it until progress forever can starve traffic behind a physically stuck AMR.
        self._priority_grace_since: float | None = None
        self._priority_grace_until = -1e9
        self.blocked_on: str | None = None
        self.retreat_target: Cell | None = None
        self._retreat_for: str | None = None
        self._retreat_block_cid: int | None = None
        self._retreat_origin: Cell | None = None
        self._retreat_contested: Cell | None = None
        self._retreat_since = 0.0
        self._last_progress_t = 0.0
        self._last_cell: Cell | None = None
        # BIOS_1.0.0: when an unstick step is armed we allow Layer 0 to creep out of
        # a peer-jam instead of freezing; window closes once the step has landed.
        self._creep_until = -1e9
        # BIOS_1.0.0 block token: block id -> (owner, expiry) learned from the wire,
        # plus the block id we ourselves currently hold.
        # block id -> (owner, local expiry, scalar priority, epoch, rich key)
        self._claims: dict[int, tuple[str, float, float, int,
                                     PriorityKey | None]] = {}
        self._claim_cid: int | None = None
        self._claim_priority_key: PriorityKey | None = None
        self._last_claim_t = -1e9

        self._t_route = -1e9
        self._t_reactive = -1e9
        self._t_hb = -1e9
        self._mgr_seen = -1e9
        self._seq = 0
        self._hold = False

        # Everything the report quotes about this agent, measured not asserted.
        self.stats = {
            "replans": 0, "yields": 0, "deadlocks_detected": 0, "retreats": 0,
            "safety_stops": 0, "msgs_sent": 0, "bytes_sent": 0, "msgs_recv": 0,
            "plan_cpu_s": 0.0, "plan_calls": 0, "plan_cpu_max_s": 0.0,
            "central_plans": 0, "local_plans": 0, "seconds_degraded": 0.0,
            "priority_decisions": 0, "priority_inheritances": 0,
            "priority_backtracks": 0, "priority_forced_moves": 0,
            "priority_waits": 0,
            # Which verb BIOS_4 chose, per tick. Kept because "it completed 7 tasks" is
            # not a description of a policy - the mix of verbs is, and it is the only
            # way to tell a trained network apart from one that learned to always hold.
            "bios4_proceed": 0, "bios4_hold": 0, "bios4_yield": 0,
            "bios4_claim": 0, "bios4_reroute": 0, "bios4_unstick": 0,
            # Cells of net approach to the current goal, summed over the run. Exists
            # because "tasks completed" is 0-3 over a short episode and therefore says
            # almost nothing about whether one policy is better than another - this is
            # the same measurement at a resolution you can actually steer by.
            "progress_cells": 0,
            # Edge-integration resilience tracking
            "dynamic_obstacles_detected": 0, "dynamic_reroutes": 0, "task_reassignments": 0,
            "auction_bids_sent": 0, "energy_bids_suppressed": 0,
            "energy_no_eligible_rounds": 0,
            # BIOS 6 release metrics.  Keeping these counters on every policy makes
            # paired comparisons explicit rather than reconstructing them from UI
            # telemetry after the run.
            "nonproductive_wait_ticks": 0,
            "heartbeat_messages_sent": 0, "intent_messages_sent": 0,
            "auction_messages_sent": 0, "coordination_messages_sent": 0,
            "heartbeat_messages_suppressed": 0,
            "intent_messages_suppressed": 0,
            "lease_renewals_suppressed": 0,
            "bid_rebroadcasts_suppressed": 0,
            "decision_events": 0,
            "congestion_samples": 0,
            "experience_messages_sent": 0,
            "experience_updates_received": 0,
            "experience_guided_replans": 0,
            "predictive_hazards_seen": 0,
            "predictive_reroutes": 0,
            "charger_contentions_avoided": 0,
            # Experimental bounded-future allocator and hardening telemetry.
            "future_candidates_evaluated": 0,
            "future_bids_sent": 0, "future_bids_won": 0,
            "future_bids_lost": 0, "future_capacity_rejections": 0,
            "stale_future_awards_rejected": 0,
            "future_version_mismatches": 0,
            "future_energy_rejections": 0,
            "future_deadline_rejections": 0,
            "future_charger_rejections": 0,
            "future_lease_renewals": 0, "future_lease_expiries": 0,
            "future_invalidations": 0, "future_promotions": 0,
            "future_promotion_failures": 0,
            "future_network_fallbacks": 0,
            "future_hysteresis_prevented": 0,
            "rejected_unknown_bids": 0,
            "deferred_unknown_bids": 0,
            "rejected_epoch_jumps": 0,
            "rejected_task_conflicts": 0,
            "rejected_task_completions": 0,
            "rejected_directed_awards": 0,
        }

    # ================================================================== main tick

    def step(self, t: float, sensors: Sensors,
             inbox: list[msg.Message]) -> tuple[Actuation, list[msg.Message]]:
        outbox: list[msg.Message] = []
        self._ingest(t, inbox)
        self._expire_peers(t)
        self._expire_task_claims(t)

        if self.mode == MODE_P2P and self.policy not in (POLICY_STOP_WAIT,
                                                          *DECENTRAL_POLICIES):
            self.stats["seconds_degraded"] += 1.0 / self.cfg.rates.world_hz

        cell = sensors.cell
        if cell != self._last_cell:
            if self.policy == POLICY_BIOS_PIBT_V6:
                self._v6_finish_wait_episode(t)
            self._cell_gate_since.pop(cell, None)
            if self._cell_repair_target == cell:
                self._cell_repair_target = None
            self._last_cell = cell
            self._last_progress_t = t
            self._priority_grace_since = None
            self._priority_grace_until = -1e9

        self._observe_dynamic_obstacles(t, sensors)

        self._task_loop(t, sensors, outbox)

        if t - self._t_route >= 1.0 / self.cfg.rates.route_hz:
            self._t_route = t
            self._route_loop(t, sensors, outbox)

        if t - self._t_reactive >= 1.0 / self.cfg.rates.reactive_hz:
            self._t_reactive = t
            self._traffic_loop(t, sensors, outbox)

        if self.policy in DECENTRAL_POLICIES:
            # Maintain the chokepoint token every control tick so any rival learns
            # as early as possible and no second robot slips into the single lane.
            self._bios_claim(t, sensors, self._next_cell(), outbox)

        act = self._follow(t, sensors)
        act = self._safety(sensors, act)           # Layer 0 has the last word, always

        is_traffic_wait = (
            self.goal is not None and abs(act.v) <= 0.02
            and (self._hold or act.safety_stop)
        )
        if is_traffic_wait:
            self.stats["nonproductive_wait_ticks"] += 1
        if self.policy == POLICY_BIOS_PIBT_V6:
            self._v6_track_wait(sensors, is_traffic_wait)

        if act.safety_stop and self.goal is not None:
            if self._stall_since is None:
                self._stall_since = t
        else:
            # This timestamp represents a Layer-0 refusal, not generic lack of
            # translation. Clear it as soon as safety releases—even if the next valid
            # action is turn-in-place—or the stale flag recreates a traffic hold that
            # prevents that recovery turn forever.
            self._stall_since = None

        if t - self._t_hb >= 1.0 / self.cfg.rates.heartbeat_hz:
            self._t_hb = t
            self._broadcast(t, sensors, outbox)

        for m in outbox:
            self.stats["msgs_sent"] += 1
            self.stats["bytes_sent"] += len(msg.encode(m))
            if m.type == msg.HEARTBEAT:
                self.stats["heartbeat_messages_sent"] += 1
            elif m.type == msg.INTENT:
                self.stats["intent_messages_sent"] += 1
            elif m.type in (msg.TASK_NEW, msg.BID, msg.AWARD, msg.TASK_DONE):
                self.stats["auction_messages_sent"] += 1
            else:
                self.stats["coordination_messages_sent"] += 1
            if m.type == msg.EXPERIENCE:
                self.stats["experience_messages_sent"] += 1
        return act, outbox

    def _record_decision(self, t: float, code: str, summary: str,
                         **details) -> None:
        """Record one bounded, machine-derived explanation for jury telemetry."""
        if self.policy != POLICY_BIOS_PIBT_V6:
            return
        event = {
            "t": round(float(t), 3),
            "robot": self.rid,
            "code": code,
            "summary": summary,
            "details": details,
        }
        self.decision_log.append(event)
        if len(self.decision_log) > 32:
            del self.decision_log[:-32]
        self.stats["decision_events"] += 1

    def _peer_stale_after_s(self) -> float:
        if self.policy == POLICY_BIOS_PIBT_V6:
            return self.cfg.traffic.v6_peer_stale_s
        return self.cfg.traffic.peer_stale_s

    def _v6_conflict_active(self, sensors: Sensors) -> bool:
        if self.blocked_since is not None or self._stall_since is not None:
            return True
        if self.state in (ST_BLOCKED, ST_RETREAT):
            return True
        radius = max(1, self.cfg.traffic.v6_conflict_radius_cells)
        return any(
            sensors.t - peer.last_seen <= self._peer_stale_after_s()
            and manhattan(sensors.cell, peer.cell) <= radius
            for peer in self.peers.values()
        )

    def _v6_heartbeat_due(self, t: float, sensors: Sensors,
                          signature: tuple) -> bool:
        if signature != self._last_heartbeat_signature:
            return True
        if self._v6_conflict_active(sensors):
            interval = 1.0 / self.cfg.rates.heartbeat_hz
        elif self.goal is None or self.state in (ST_IDLE, ST_CHARGING):
            interval = self.cfg.traffic.v6_idle_heartbeat_s
        else:
            interval = self.cfg.traffic.v6_cruise_heartbeat_s
        return t - self._last_heartbeat_broadcast >= interval

    def _v6_should_broadcast_bid(self, t: float, task: Task,
                                 cost: float) -> bool:
        key = (task.tid, task.auction_epoch)
        previous = self._v6_bid_broadcasts.get(key)
        if previous is not None:
            old_cost, sent_at = previous
            if (abs(old_cost - cost) < self.cfg.traffic.v6_bid_cost_delta
                    and t - sent_at < self.cfg.traffic.v6_bid_refresh_s):
                self.stats["bid_rebroadcasts_suppressed"] += 1
                return False
        self._v6_bid_broadcasts[key] = (cost, t)
        return True

    def _v6_observe_edge(self, start: Cell, end: Cell,
                         observed_delay: float, t: float) -> None:
        if observed_delay < 0.1:
            return
        key = (start, end)
        old_delay, samples, _updated = self._edge_experience.get(
            key, (0.0, 0, 0.0))
        alpha = self.cfg.traffic.v6_experience_alpha
        delay = (observed_delay if samples == 0
                 else (1.0 - alpha) * old_delay + alpha * observed_delay)
        self._edge_experience[key] = (delay, samples + 1, t)
        self._experience_dirty.add(key)
        self.stats["congestion_samples"] += 1

    def _v6_finish_wait_episode(self, t: float) -> None:
        if self._v6_wait_edge is not None and self._v6_wait_s >= 0.1:
            self._v6_observe_edge(
                self._v6_wait_edge[0], self._v6_wait_edge[1],
                self._v6_wait_s, t)
        self._v6_wait_edge = None
        self._v6_wait_s = 0.0

    def _v6_track_wait(self, sensors: Sensors, waiting: bool) -> None:
        nxt = self._next_cell()
        edge = ((sensors.cell, nxt)
                if nxt is not None and manhattan(sensors.cell, nxt) == 1
                else None)
        if edge != self._v6_wait_edge:
            self._v6_finish_wait_episode(sensors.t)
            self._v6_wait_edge = edge
        if waiting and edge is not None:
            self._v6_wait_s += 1.0 / self.cfg.rates.world_hz

    def _v6_edge_costs(self, t: float) -> dict[tuple[Cell, Cell], float]:
        if self.policy != POLICY_BIOS_PIBT_V6:
            return {}
        # A shared experience map is an efficiency hint, not authoritative state.
        # Under modeled packet loss or radio holes different robots can hold sharply
        # different samples; using those views to reshape a one-way circulation graph
        # amplified rather than relieved queues.  Degraded operation therefore keeps
        # the proven BIOS 5 routing and lets leases/PIBT provide the safe fallback.
        if self.cfg.net.loss > 0.0 or self.cfg.net.dead_zones:
            return {}
        nominal_s = self.cfg.cell_m / max(0.1, 0.8 * self.cfg.robot.v_max)
        tau = max(1.0, self.cfg.traffic.v6_experience_decay_s)
        cap = max(0.0, self.cfg.traffic.v6_experience_penalty_cap)
        costs: dict[tuple[Cell, Cell], float] = {}
        minimum = max(1, self.cfg.traffic.v6_experience_min_samples)
        for edge, (delay_s, samples, updated_t) in self._edge_experience.items():
            if samples < minimum:
                continue
            age = max(0.0, t - updated_t)
            decayed = delay_s * math.exp(-age / tau)
            penalty = min(cap, decayed / nominal_s)
            if penalty >= 0.05:
                costs[edge] = penalty
        return costs

    def _v6_broadcast_experience(self, t: float,
                                 outbox: list[msg.Message]) -> None:
        if (self.policy != POLICY_BIOS_PIBT_V6 or not self._experience_dirty
                or self.cfg.net.loss > 0.0 or self.cfg.net.dead_zones
                or t - self._last_experience_share
                < self.cfg.traffic.v6_experience_share_s):
            return
        count = max(1, self.cfg.traffic.v6_experience_max_records)
        keys = sorted(
            self._experience_dirty,
            key=lambda edge: (-self._edge_experience[edge][0], edge),
        )[:count]
        records = [
            (start, end, self._edge_experience[(start, end)][0],
             self._edge_experience[(start, end)][1])
            for start, end in keys
        ]
        outbox.append(msg.experience(
            self.rid, self._next_seq(), t, records))
        self._experience_dirty.difference_update(keys)
        self._last_experience_share = t

    def _v6_ingest_experience(self, t: float, source: str,
                              records: list) -> None:
        alpha = self.cfg.traffic.v6_experience_alpha
        for record in records:
            start = (int(record[0]), int(record[1]))
            end = (int(record[2]), int(record[3]))
            if manhattan(start, end) != 1:
                continue
            delay, samples = float(record[4]), int(record[5])
            seen_key = (source, start, end)
            if samples <= self._experience_seen.get(seen_key, 0):
                continue
            self._experience_seen[seen_key] = samples
            edge = (start, end)
            old_delay, old_samples, _updated = self._edge_experience.get(
                edge, (0.0, 0, t))
            merged = (delay if old_samples == 0
                      else (1.0 - alpha) * old_delay + alpha * delay)
            # The wire value is the sender's cumulative local counter, not evidence
            # that this receiver independently observed that many delays. Count one
            # authenticated, fresh report as one bounded observation. This prevents
            # a single packet with a forged large counter from instantly crossing the
            # experience threshold and applying the maximum route penalty.
            self._edge_experience[edge] = (
                merged, min(1_000_000, old_samples + 1), t)
            self.stats["experience_updates_received"] += 1

    def _v6_prediction_costs(self, t: float, start: Cell) -> dict[Cell, float]:
        """Return bounded soft costs for likely near-future occupancy.

        Moving anonymous detections are projected locally by the sensor loop. Peer
        occupancy is intentionally absent: PIBT and intent windows already arbitrate
        peers, and adding the same forecast to A* caused needless route oscillation.
        Predictions expire quickly and are never treated as walls.
        """
        if (self.policy != POLICY_BIOS_PIBT_V6
                or self.cfg.net.loss > 0.0 or self.cfg.net.dead_zones):
            return {}
        costs: dict[Cell, float] = {}
        for cell, (penalty, expiry, _source) in self._predicted_cell_cost.items():
            if expiry > t and cell not in (start, self.goal):
                costs[cell] = max(costs.get(cell, 0.0), penalty)
        return costs

    def _v6_select_charger(self, t: float, start: Cell) -> Cell:
        """Choose a reachable dock using fresh peer goals as a soft queue signal."""
        candidates: list[tuple[float, int, Cell]] = []
        for dock in sorted(self.env.docks):
            route = astar(
                self.env, start, dock,
                edge_allowed=(
                    lambda a, b: self.circulation.allows(self.env, a, b))
                if self.circulation.enabled else None)
            if not route:
                route = astar(self.env, start, dock)
            if not route:
                continue
            busy = 0
            intent = 0
            for peer in self.peers.values():
                if t - peer.last_seen > self._peer_stale_after_s():
                    continue
                if peer.goal == dock and peer.state == ST_CHARGING:
                    busy += 1
                elif peer.goal == dock or dock in peer.intent:
                    intent += 1
            score = (
                max(0, len(route) - 1)
                + busy * self.cfg.traffic.v6_charger_busy_penalty
                + intent * self.cfg.traffic.v6_charger_intent_penalty
            )
            candidates.append((score, len(route), dock))
        if not candidates:
            return min(self.env.docks, key=lambda d: manhattan(start, d))
        selected = min(candidates)[2]
        nearest = min(self.env.docks, key=lambda d: (manhattan(start, d), d))
        if selected != nearest:
            self.stats["charger_contentions_avoided"] += 1
            self._record_decision(
                t, "CHARGER_SELECTION",
                "Selected a less-contended charging dock",
                nearest_dock=list(nearest), selected_dock=list(selected))
        return selected

    # ================================================================== Layer 0

    def _safety(self, sensors: Sensors, act: Actuation) -> Actuation:
        """Protective stop. Local, unconditional, and deliberately ignorant.

        It reads one number - the distance to the nearest thing in the forward cone -
        and it does not care whether that thing is a peer, a human or a fallen pallet.
        No message can override it and no message is required to trigger it. This is
        the layer that makes the fleet safe against everything that does not broadcast,
        which is the entire category the problem statement's shared-intent protocol is
        structurally blind to.

        On real hardware this is a certified PLd/SIL2 safety scanner wired to the motor
        contactors, not Python. Modelling it in software is a simulation convenience;
        placing it below the network in the architecture is the actual engineering claim.
        """
        spec = self.cfg.robot

        # --- unexpected objects: full protective field, absolute authority ---
        # Cap the command at the fastest speed this clearance can still absorb. No
        # iteration and no tuning: it is the braking equation solved for v.
        # 360 degree guard first. Anything this close is already inside the footprint
        # envelope no matter which way it came from, and no route plan or schedule is
        # allowed to argue with it.
        if sensors.clearance_omni_m <= spec.omni_stop_m:
            self.stats["safety_stops"] += 1
            creeping = (self.policy in (POLICY_BIOS, *PIBT_POLICIES)
                        and sensors.t < self._creep_until
                        and act.v > 0.0)
            if (creeping and self.policy in V3_AUCTION_POLICIES
                    and not self._escape_motion_increases_clearance(sensors, act)):
                # A verified escape cell is not enough: while turning off-centre the
                # first centimetres of an otherwise valid move can still arc toward a
                # neighbouring chassis. V3 permits recovery motion only when the
                # instantaneous relative velocity increases every close-peer gap.
                return Actuation(v=0.0, omega=act.omega, safety_stop=True)
            if creeping:
                # BIOS_1.0.0 unstick: the target cell was verified free of peers and
                # we creep into it to break out of a jam. The forward cone still caps
                # the speed below (so shelf edge and unexpected obstacles are honoured),
                # but a known peer sitting within the omni guard no longer glues us in
                # place forever - which is exactly the "move any free way to get
                # unstuck" guarantee the policy makes.
                v_allowed = self._speed_limit_from_traffic(sensors)
                if v_allowed <= 0.02:
                    return Actuation(v=0.0, omega=act.omega, safety_stop=True)
                v = min(act.v, 0.20, v_allowed)
                return Actuation(v=v, omega=act.omega, safety_stop=False)
            return Actuation(v=0.0, omega=act.omega * 0.3, safety_stop=True)

        v_allowed = self._speed_limit_from_traffic(sensors)
        if v_allowed <= 0.02:
            self.stats["safety_stops"] += 1
            # Rotation survives the stop. A robot that may not turn cannot face away
            # from what stopped it, and a protective stop it cannot recover from is
            # just a slower way to gridlock.
            return Actuation(v=0.0, omega=act.omega, safety_stop=True)
        v = min(act.v, v_allowed)

        # --- mapped geometry: a crawl limit and a hard backstop ---
        # The planner only ever routes through cells it knows are free, so shelving is
        # not a hazard to stop for - it is a wall to slow down beside. The backstop
        # still exists because a map can be wrong, and when it fires the world records
        # a rack contact, so the mistake shows up in the results rather than hiding.
        stat = sensors.clearance_static_m
        if stat <= spec.safety_margin_m * 0.7:
            self.stats["safety_stops"] += 1
            return Actuation(v=0.0, omega=act.omega, safety_stop=True)
        if stat < 0.5:
            v = min(v, 0.35 * spec.v_max)
        return Actuation(v=v, omega=act.omega, safety_stop=act.safety_stop)

    def _escape_motion_increases_clearance(self, sensors: Sensors,
                                           act: Actuation) -> bool:
        """True only when a recovery translation separates from every close object."""
        if act.v <= 0.0:
            return False
        vx = act.v * math.cos(sensors.pose[2])
        vy = act.v * math.sin(sensors.pose[2])
        px, py, _ = sensors.pose
        checked = False
        for det in sensors.detections:
            dx, dy = det.x - px, det.y - py
            centre_distance = math.hypot(dx, dy)
            if centre_distance < 1e-9:
                return False
            gap = centre_distance - self.cfg.robot.radius_m - det.r
            if gap > self.cfg.robot.omni_stop_m + 0.05:
                continue
            checked = True
            ux, uy = dx / centre_distance, dy / centre_distance
            separation_rate = ((det.vx - vx) * ux
                               + (det.vy - vy) * uy)
            if separation_rate < -1e-6:
                return False
        return checked

    # ================================================================== Layer 1

    def _traffic_loop(self, t: float, sensors: Sensors,
                      outbox: list[msg.Message]) -> None:
        """Decide whether to enter the next cell. Advisory information only."""
        self._hold = False

        if self.policy in CENTRAL_POLICIES:
            # A purely centralised fleet does exactly what the manager scheduled, plus
            # Layer 0. No peer negotiation is layered on top - adding it would quietly
            # hand the baseline some of our own mechanism and flatter our result.
            self._hold = self._schedule_holds(t)
            self._track_block(t, False, None)
            return

        if self.state == ST_RETREAT:
            # A give-way manoeuvre must never be blocked by the robot it is giving way
            # to. That is a deadlock dressed as politeness: we back off *because* of
            # them, so waiting for them to clear first can never terminate. Layer 0
            # still protects the reverse, which is the guarantee that actually matters.
            self._track_block(t, False, None)
            return

        if self.policy == POLICY_STOP_WAIT:
            # The weak baseline, implemented faithfully rather than as a straw man:
            # no intent sharing, no priorities, no negotiation. Stop if something is
            # in the way and resume when it leaves. It is safe and it deadlocks - and
            # that deadlock is a real, reported result, not a rigged one.
            self._hold = self._traffic_ahead(sensors)
            blocker = self._peer_ahead(sensors) if self._hold else None
            self._track_block(t, self._hold, blocker)
            return

        if (self.policy in V3_AUCTION_POLICIES
                and self._repair_duplicate_cell(t, sensors)):
            return

        nxt = self._next_cell()
        if nxt is None:
            self._track_block(t, False, None)
            return
        if (self.policy in DIRECTED_POLICIES
                and nxt != sensors.cell and manhattan(sensors.cell, nxt) != 1):
            # Continuous turning can cross an adjacent quantisation boundary before
            # the old waypoint is consumed.  A grid route whose next step is now
            # diagonal/non-adjacent is invalid; following it cuts a rack corner and
            # can wedge the chassis permanently.  Repair from the measured cell.
            self._replan(t, sensors.cell)
            nxt = self._next_cell()
            if nxt is None or (nxt != sensors.cell
                               and manhattan(sensors.cell, nxt) != 1):
                self._hold = True
                self._track_block(t, True, "route-repair")
                return

        if self._schedule_holds(t):
            # Following a fresh central schedule. Waiting on the clock is not a
            # conflict, so it must not feed the deadlock timer.
            self._hold = True
            self._track_block(t, False, None)
            return

        my_key = self._arbitration_key()
        # Block-level exclusion first: it is the only rule that can prevent - rather
        # than merely detect - a head-on lock in a single-file aisle. V3 also stages
        # a same-direction follower one cell earlier when the mouth feeder is occupied,
        # leaving enough physical braking distance for the front robot to depart.
        staging_loser = self._v3_staging_conflict(t, sensors.cell)
        loser_to = (staging_loser
                    if staging_loser is not None
                    else self._block_conflict(t, sensors.cell, nxt, my_key))
        # Queueing for an aisle somebody else is legitimately driving through is not a
        # deadlock, it is traffic. A 13-cell block takes ~11 s to clear, so a 2.5 s
        # deadlock timer would fire mid-transit and send perfectly healthy robots into
        # give-way manoeuvres that create the very problem they are meant to fix.
        waiting_for_block = loser_to is not None

        # When a fresh central schedule is in hand, DO NOT also negotiate locally.
        # The schedule is already conflict-free; layering peer-intent yielding on top
        # means every robot defers to plans the optimiser has already deconflicted, and
        # the two mechanisms interfere - measured at roughly half the throughput of the
        # central baseline it was supposed to match. Local negotiation is the fallback
        # for when there is no schedule, which is exactly what makes this a hierarchy
        # rather than two coordination schemes running at once.
        coordinated = self.mode == MODE_CENTRAL and bool(self.path_times)

        if (loser_to is None and not coordinated
                and self.policy in DIRECTED_POLICIES
                and self.circulation.enabled):
            loser_to = self._bios_v2_coordinate(t, sensors, nxt)
        elif (loser_to is None and not coordinated
                and self.policy in V3_AUCTION_POLICIES):
            loser_to = self._bios_v3_cell_coordinate(t, sensors, nxt)
            if loser_to is None:
                loser_to = self._bios_pibt_coordinate(t, sensors, nxt)
        elif (loser_to is None and not coordinated
                and self.policy in PIBT_POLICIES):
            loser_to = self._bios_pibt_coordinate(t, sensors, nxt)
        elif loser_to is None and not coordinated:
            # Are we moving *within* a block we have already entered? Then peers still
            # outside it have no standing, however high their priority. A committed
            # robot that defers to a waiting one can never clear the aisle, the waiting
            # one can never enter, and the block stays locked by politeness. Right of
            # way inside a block belongs to whoever is already in it.
            here_cid = self.blocks.id_of(sensors.cell)
            committed = here_cid is not None and self.blocks.id_of(nxt) == here_cid
            for p in self.peers.values():
                if committed and self.blocks.id_of(p.cell) != here_cid:
                    continue
                occupies = p.cell == nxt
                intends = self._peer_intends(p, nxt, t)
                if not (occupies or intends):
                    continue
                if self._peer_outranks(p, my_key):
                    loser_to = p.rid
                    break

        # A Layer 0 stall that has lasted longer than a yield should is a conflict the
        # traffic layer never saw - typically two robots that met head-on in open floor,
        # where no block rule applies. Promote it so the deadlock breaker can run.
        stalled = (self._stall_since is not None
                   and t - self._stall_since > self.cfg.traffic.deadlock_wait_s)
        if (loser_to is None and stalled
                and self.policy in DIRECTED_POLICIES
                and self.circulation.enabled):
            self._creep_until = max(self._creep_until, t + 6.0)
            self._stall_since = None
            stalled = False
        if loser_to is None and stalled:
            loser_to = self._peer_ahead(sensors)
            yielded_peer = self.peers.get(loser_to or "")
            if (self.policy in PIBT_POLICIES and yielded_peer is not None
                    and yielded_peer.state == ST_RETREAT
                    and yielded_peer.cell != nxt):
                # The peer is in a passing bay and our reserved route does not consume
                # its cell. The conservative omni field can still touch both
                # footprints at the mouth, so cross the verified gap at recovery speed
                # instead of ordering the corridor owner to retreat back inside.
                self._creep_until = max(self._creep_until, t + 6.0)
                self._stall_since = None
                loser_to = None
                stalled = False
            if self.policy in PIBT_POLICIES and loser_to is not None:
                here_cid = self._controlled_block(sensors.cell)
                blocker = self.peers.get(loser_to)
                if (here_cid is not None and blocker is not None
                        and self.blocks.id_of(blocker.cell) != here_cid):
                    # The outside robot has room to pull aside. Reversing the inside
                    # robot into its followers only moves the jam deeper into the lane.
                    waiting_for_block = True

        if loser_to is not None:
            self._hold = True
            if (self.policy in V3_AUCTION_POLICIES
                    and not self.circulation.enabled):
                # Stop a yielding follower at its own cell centre, not wherever the
                # peer intent or block claim happened to reach it. Braking near the
                # boundary leaves less than the omnidirectional standstill gap and can
                # freeze the legitimate owner from behind—even one cell before a
                # corridor mouth. `_follow` recentres while Layer 0 verifies that
                # every close-peer separation is increasing.
                centre = cell_center(sensors.cell, self.cfg.cell_m)
                if dist((sensors.pose[0], sensors.pose[1]), centre) > 0.12:
                    self._creep_until = max(self._creep_until, t + 2.0)
            if self.blocked_since is None:
                self.stats["yields"] += 1
                outbox.append(msg.yield_to(self.rid, self._next_seq(), t, nxt, loser_to))
        self._track_block(t, self._hold or stalled, loser_to)

        if self.blocked_since is not None:
            waited = t - self.blocked_since
            # BIOS_1.0.0's defining safeguard: after a short pause the robot stops
            # trusting any protocol and simply edges into a free neighbouring cell.
            # Because it is always free and always adjacent, it always moves - so no
            # robot can ever settle still, which is the liveness guarantee behind
            # "no deadlock".
            if (self.policy in (POLICY_BIOS, POLICY_DECENTRALIZED)
                    and waited > self.cfg.traffic.bios_unstick_s):
                self._bios_unstick(t, sensors, nxt, outbox)
                return
            # Waiting at a mouth is fine unless we are waiting ON the way out. A robot
            # queued at the entrance stands exactly where the robot inside has to drive
            # to leave, so the two of them wait for each other with no cycle to detect
            # and no rule violated. Stepping aside is the only thing that breaks it.
            if (waiting_for_block
                    and waited > self.cfg.traffic.yield_aside_s
                    and self._blocker_is_inside(nxt)
                    and self.policy not in V3_AUCTION_POLICIES):
                bay = self._passing_bay(sensors.cell, nxt, sensors.pose)
                if bay is not None:
                    self.retreat_target = bay
                    self._retreat_for = self.blocked_on
                    self._retreat_block_cid = self.blocks.id_of(nxt)
                    self._retreat_origin = sensors.cell
                    self._retreat_contested = nxt
                    self.state = ST_RETREAT
                    self._retreat_since = t
                    self.path = [sensors.cell, bay]
                    self.path_times = []
                    self.pidx = 1
                    self.stats["retreats"] += 1
                    if self.policy in PIBT_POLICIES:
                        # The target was selected from currently free neighbours.  A
                        # short, speed-limited escape window lets the chassis move
                        # *away* from a close peer instead of remaining glued inside
                        # the omnidirectional standstill field.
                        self._creep_until = t + 6.0
                    self.blocked_since = None
                    return

            limit = (self.cfg.traffic.block_wait_s if waiting_for_block
                     else self.cfg.traffic.deadlock_wait_s)
            if self.policy in PIBT_POLICIES and waiting_for_block:
                # Lease expiry and physical ownership resolve this queue.  Running
                # the legacy generic deadlock breaker after the long block timeout
                # repeatedly injects sharp retreat paths at the aisle mouth, even
                # though no wait-for cycle exists.
                return
            if (self.policy in V3_AUCTION_POLICIES and waited > limit):
                # V3 never injects a physical reverse/retreat into live traffic. A
                # stale peer or merge disagreement is handled by an expiring lease and
                # a new legal A* route, preserving the directed-flow safety invariant.
                self.penalty[nxt] = self.penalty.get(nxt, 0.0) + \
                    self.cfg.traffic.replan_penalty
                self.blocked_since = t
                self._replan(t, sensors.cell)
                return
            if (self.policy == POLICY_BIOS_PIBT_V2
                    and self.circulation.enabled and waited > limit):
                # Directed traffic never reverses into a follower.  If a peer fails to
                # advance despite the available-hole invariant, route around that cell
                # and keep all recovery motion on legal directed edges.
                self.penalty[nxt] = self.penalty.get(nxt, 0.0) + \
                    self.cfg.traffic.replan_penalty
                self.blocked_since = t
                self._replan(t, sensors.cell)
                return
            if waited > limit:
                self._break_deadlock(t, sensors, nxt, outbox)

    def _speed_limit_from_traffic(self, sensors: Sensors) -> float:
        """Fastest speed that still leaves room to stop for every approaching object.

        Each detection is judged on the gap AND on how fast it is closing on us, which
        is the part a fixed protective field cannot express. Only things roughly ahead
        count: braking does not help for something overtaking from behind, and slowing
        for it would just make the fleet timid without making it safer.
        """
        spec = self.cfg.robot
        px, py, th = sensors.pose
        limit = spec.max_speed_for_clearance(sensors.clearance_static_m)
        for det in sensors.detections:
            dx, dy = det.x - px, det.y - py
            rng = math.hypot(dx, dy)
            if rng < 1e-6:
                return 0.0
            if abs(angle_diff(math.atan2(dy, dx), th)) > spec.safety_cone_rad:
                continue
            ux, uy = dx / rng, dy / rng
            # Component of THEIR velocity pointing back at us.
            closing = -(det.vx * ux + det.vy * uy)
            gap = rng - spec.radius_m - det.r
            limit = min(limit, spec.max_speed_for_clearance(gap, max(0.0, closing)))
        return limit

    def _traffic_ahead(self, sensors: Sensors) -> bool:
        """Is the next cell occupied? Stop-and-wait's entire decision rule.

        This is the textbook formulation, and implementing it faithfully matters: an
        over-conservative version that halts for anything within two metres would fail
        so early that beating it would prove nothing. It reads *detections* rather than
        clearance, so shelving does not stop it - a baseline that halts in front of
        every wall is a straw man, not a baseline.

        The pathology it does have is the real one: two robots approaching head-on in a
        single-file aisle each find the other in their next cell, both stop, and neither
        has any mechanism to break the tie. That deadlock is the honest result, and it
        is what the traffic layer in the other two policies exists to solve.
        """
        nxt = self._next_cell()
        if nxt is None:
            return False
        cm = self.cfg.cell_m
        for det in sensors.detections:
            if to_cell((det.x, det.y), cm) == nxt:
                return True
        return False

    def _schedule_holds(self, t: float) -> bool:
        """True while a central schedule says this cell is not ours yet.

        The tolerance matters. These timestamps are earliest-entry bounds computed from
        the fastest possible traversal, so a robot slowed by acceleration or a turn is
        normally *behind* them and never holds. Only a wait the planner deliberately
        inserted puts a bound far enough ahead to bite.
        """
        return (bool(self.path_times) and self.pidx < len(self.path_times)
                and t < self.path_times[self.pidx] - 0.05)

    def _block_conflict(self, t: float, here: Cell, nxt: Cell,
                        my_key: tuple[float, str]) -> str | None:
        """May we enter the single-file block that `nxt` belongs to?

        Two rules, and the asymmetry between them is the point:

        * Someone is **already inside** heading the other way -> wait, regardless of
          priority. Priority cannot create space the aisle does not have; the only
          thing outranking an oncoming robot buys is a head-on stand-off deeper in.
        * Someone **outranks us and wants in** -> wait, this time on priority, because
          both of us are still outside and either could go first.

        Robots following each other through in the same direction are not in conflict,
        so a block is not a naive one-robot-at-a-time mutex - that would serialise
        every picking aisle and cost more throughput than it saves.

        Waiting happens at the mouth, which is a junction by construction: there is
        room to pass there, so a waiting robot does not become the next obstruction.
        """
        cell_conflict = self._cell_lease_conflict(t, here, nxt)
        if cell_conflict is not None:
            return cell_conflict

        here_cid = self._controlled_block(here)
        cid = self._controlled_block(nxt)
        if here_cid is not None and self.blocks.id_of(nxt) != here_cid:
            # We are the traffic the exit-apron rule is trying to protect.  Applying
            # that rule to a robot already leaving the block makes it yield to a
            # follower behind it, so nobody can ever cross the mouth.
            return None
        if cid is None:
            # Not a block cell - but it may be the cell right outside somebody's exit.
            # Having pulled aside to let a robot out, the worst thing to do next is
            # step back onto the axis in front of it, which is exactly what replanning
            # a shortest path does. Keep the doorway clear until they are through.
            return self._exit_apron_conflict(nxt)
        if self.blocks.id_of(here) == cid:
            return None                     # already committed inside this block

        if self.policy in DECENTRAL_POLICIES:
            # The block token: either somebody physically inside, or an unexpired
            # claim some peer broadcast. Both close the race where two robots at
            # opposite mouths both see an empty block and both commit.
            lock = self._bios_lock(cid, t)
            if lock is not None and lock[0] != self.rid:
                self._gate_since.pop(cid, None)
                self._gate_committed.discard(cid)
                return lock[0]
        claim = self._claims.get(cid)
        owns_token = (claim is not None and claim[0] == self.rid
                      and claim[1] > t)
        if owns_token:
            if cid in self._gate_committed:
                return None
            # Keep the winner stationary for one propagation round, then latch the
            # admission.  Without the latch a new gate starts every reactive tick;
            # without the round, two opposite mouths can both move before hearing the
            # other's first claim.
            opened = self._gate_since.setdefault(cid, t)
            if t - opened < self.cfg.traffic.gate_commit_s:
                return "gate"
            self._gate_since.pop(cid, None)
            self._gate_committed.add(cid)
            return None

        entry = self.blocks.nearest_end(cid, nxt)
        ends = self.blocks.ends.get(cid, ())
        my_exit = next((e for e in ends if e != entry), None)

        for p in self.peers.values():
            if self.blocks.id_of(p.cell) == cid:
                if (self.policy in (POLICY_BIOS, POLICY_DECENTRALIZED)
                        or my_exit is None
                        or self._peer_exit(cid, p) != my_exit):
                    # BIOS_1.0.0 admits a controlled block STRICTLY one at a time.
                    # A one-lane tunnel cannot take a same-direction convoy: robots
                    # touch at standstill clearance and any follower freezes the fleet.
                    # Whoever is inside owns the block until they leave.
                    self._gate_since.pop(cid, None)
                    return p.rid
                continue                # (non-BIOS) travelling our way: follow through
            if not any(self.blocks.id_of(c) == cid for c in p.intent):
                continue

            # A contender is only a contender if it can actually go first. A peer
            # queued BEHIND us at the same mouth cannot: we are the thing in its way.
            # Yielding to it on priority is a textbook priority inversion - the robot
            # in front stops for the robot it is itself blocking, and the queue never
            # moves. Ageing makes this certain rather than unlikely, because the one
            # stuck at the back accrues priority fastest.
            #
            # So position decides among robots entering by the same mouth, and priority
            # only decides between robots arriving at *different* mouths, where both
            # genuinely could go first.
            p_entry = self.blocks.nearest_end(cid, p.cell)
            if p_entry != entry:
                if self._peer_outranks(p, my_key):
                    self._gate_since.pop(cid, None)
                    return p.rid
                continue
            mine, theirs = manhattan(here, entry), manhattan(p.cell, entry)
            if theirs < mine or (theirs == mine and self._peer_outranks(p, my_key)):
                self._gate_since.pop(cid, None)
                return p.rid

        # Nobody is contesting it *right now* - but "right now" is a peer table built
        # from 5 Hz broadcasts, so two robots at opposite mouths can both read an empty
        # block inside the same 200 ms window and both commit. Hence a commit round:
        # hold at the mouth while our own INTENT propagates, then re-check. Any
        # contender that appears during the round is resolved by the total order above.
        #
        # This shrinks the race window; it does not close it. Over an asynchronous
        # lossy channel no protocol can guarantee agreement (Fischer-Lynch-Paterson),
        # which is precisely why the collision guarantee lives in Layer 0 and not here.
        opened = self._gate_since.get(cid)
        if opened is None:
            self._gate_since[cid] = t
            return "gate"
        if t - opened < self.cfg.traffic.gate_commit_s:
            return "gate"
        self._gate_since.pop(cid, None)
        return None

    def _v3_staging_conflict(self, t: float, here: Cell) -> str | None:
        """Brake before a feeder cell can become a nose-to-tail safety latch.

        This is only needed on a bidirectional chokepoint. Directed circulation uses
        per-cell leases and should preserve normal convoy spacing. The three-cell
        horizon covers ``feeder -> mouth -> first block cell``; an actual occupant in
        the mouth then stops the follower while a full cell of braking room remains.
        """
        if self.policy not in V3_AUCTION_POLICIES or self.circulation.enabled:
            return None
        future = self._future_path_cells(3)
        if len(future) < 2 or not any(
            self._controlled_block(cell) is not None for cell in future
        ):
            return None
        after_next = future[1]
        for peer in self.peers.values():
            if peer.cell == after_next:
                # Stage behind a peer that is continuing into the controlled lane,
                # not one vacating the mouth toward us.  The latter is an ordinary
                # merge conflict for the cell gate/PIBT resolver.  Treating physical
                # mouth occupancy alone as same-direction admission creates a cycle:
                # we stage for the exiting peer while it yields to our next-cell
                # intent, so neither can leave.
                if any(self._controlled_block(cell) is not None
                       for cell in peer.intent):
                    return peer.rid
        cid = self._controlled_block(after_next)
        if cid is not None:
            lock = self._bios_lock(cid, t)
            if lock is not None and lock[0] != self.rid:
                return lock[0]
        return None

    def _cell_zone_id(self, cell: Cell) -> int:
        return CELL_ZONE_BASE + cell[1] * self.env.width + cell[0]

    def _is_cell_zone(self, cid: int) -> bool:
        return cid >= CELL_ZONE_BASE

    def _zone_contains(self, cid: int, cell: Cell) -> bool:
        if self._is_cell_zone(cid):
            return cid == self._cell_zone_id(cell)
        return self.blocks.id_of(cell) == cid

    def _cell_lease_conflict(self, t: float, here: Cell,
                             nxt: Cell) -> str | None:
        """Two-phase lease for every V2 destination cell."""
        if (self.policy not in DIRECTED_POLICIES or not self.circulation.enabled
                or nxt == here):
            return None
        cid = self._cell_zone_id(nxt)
        lock = self._bios_lock(cid, t)
        if lock is not None and lock[0] != self.rid:
            self._gate_since.pop(cid, None)
            self._gate_committed.discard(cid)
            return lock[0]
        claim = self._claims.get(cid)
        owns = claim is not None and claim[0] == self.rid and claim[1] > t
        if owns:
            if cid in self._gate_committed:
                return None
            opened = self._gate_since.setdefault(cid, t)
            if t - opened < self.cfg.traffic.gate_commit_s:
                return "gate"
            self._gate_since.pop(cid, None)
            self._gate_committed.add(cid)
            return None
        self._gate_since.setdefault(cid, t)
        return "gate"

    def _controlled_block(self, cell: Cell) -> int | None:
        """The block id of `cell`, but only if the block is long enough to be worth it.

        Measured, not assumed. Applying full block control to every two- and four-cell
        gap in a racking layout made the fleet markedly WORSE than doing nothing: 59
        blocks on the standard map, each costing a commit round to enter, turned a
        warehouse into a series of toll gates. Short gaps have passing room close by at
        both ends, so ordinary per-cell yielding resolves them at a fraction of the
        cost. Long single-file runs are where per-cell yielding fails and this pays.
        """
        cid = self.blocks.id_of(cell)
        if cid is None:
            return None
        if self.policy in DIRECTED_POLICIES and self.circulation.enabled:
            # Direction already makes opposing occupancy impossible; preserve
            # same-direction flow instead of locking every short rack segment.
            return None
        # V1 protected only long runs.  The standard warehouse has 24 four-cell
        # picking aisles and 35 two-cell rack gaps, so that threshold protected
        # precisely zero of its 59 non-passing segments.  Under load, opposing AMRs
        # entered those segments and PIBT could only request an impossible sideways
        # move.  V2 treats every maximal degree-two run as a traffic zone.  The extra
        # lease round is intentional backpressure, not planner latency.
        minimum = (2 if self.policy in DIRECTED_POLICIES
                   else self.cfg.traffic.min_controlled_block)
        if len(self.blocks.members[cid]) < minimum:
            return None
        return cid

    def _exit_apron_conflict(self, nxt: Cell) -> str | None:
        """Is `nxt` the doorstep of a block somebody is currently driving out of?

        Only enforced for genuinely long blocks. Keeping the doorway clear is worth a
        wait when the robot inside needs ten seconds to get out; on a four-cell gap it
        just adds another way to be stuck.
        """
        if self.policy in DIRECTED_POLICIES and self.circulation.enabled:
            # The directed route makes every block exit unidirectional.  Applying the
            # bidirectional apron rule here mistakes a follower for opposing traffic
            # and recreates the reciprocal wait that circulation removed.
            return None
        for n in self.env.neighbors(nxt):
            cid = self.blocks.id_of(n)
            if cid is None or n not in self.blocks.ends.get(cid, ()):
                continue
            minimum = (2 if self.policy in DIRECTED_POLICIES
                       else self.cfg.traffic.apron_block_len)
            if len(self.blocks.members[cid]) < minimum:
                continue
            for p in self.peers.values():
                if self.blocks.id_of(p.cell) == cid and self._peer_exit(cid, p) == n:
                    return p.rid
        return None

    def _blocker_is_inside(self, nxt: Cell) -> bool:
        """Is whoever we are waiting for physically inside the block we want to enter?

        If so we are queued at its exit, not merely behind it, and holding position is
        the one thing guaranteed not to help.
        """
        cid = self.blocks.id_of(nxt)
        if cid is None or self.blocked_on in (None, "gate"):
            return False
        p = self.peers.get(self.blocked_on)
        if p is None or self.blocks.id_of(p.cell) != cid:
            return False
        if self.policy in DIRECTED_POLICIES:
            if self.circulation.enabled:
                return False
        return True

    def _peer_ahead(self, sensors: Sensors) -> str | None:
        """Which peer is the thing Layer 0 stopped for? Matched by position.

        Detections carry no identity - that is the whole point of them - so we correlate
        the nearest one in front against the peer table. A match names a robot we can
        negotiate with; no match means the obstruction is a human, a pallet or an
        unmapped object, and there is nobody to negotiate with at all. Returning None
        there is correct rather than a gap: the answer to an obstacle that cannot talk
        is to route around it, which is what the caller does next.
        """
        best, best_d = None, 1.6 * self.cfg.cell_m
        for det in sensors.detections:
            gap = det.range_m - 2 * self.cfg.robot.radius_m
            if gap > best_d:
                continue
            if abs(angle_diff(math.atan2(det.y - sensors.pose[1],
                                         det.x - sensors.pose[0]),
                              sensors.pose[2])) > self.cfg.robot.safety_cone_rad:
                continue
            for p in self.peers.values():
                if dist((p.pose[0], p.pose[1]), (det.x, det.y)) < 0.4:
                    best, best_d = p.rid, gap
                    break
        return best

    def _peer_exit(self, cid: int, p: Peer) -> Cell | None:
        """Which mouth is peer `p` heading out of? Inferred from its published intent.

        Its intent is ordered, so the last cell still inside the block is the one
        nearest its exit. No extra protocol field is needed - and if the peer has gone
        quiet, this returns None and the caller treats it as opposing traffic, which is
        the safe way to be wrong.
        """
        inside = [c for c in p.intent if self.blocks.id_of(c) == cid]
        if not inside:
            return None
        return self.blocks.nearest_end(cid, inside[-1])

    def _peer_intends(self, p: Peer, cell: Cell, t: float) -> bool:
        """Does peer p plan to be in `cell` while we would be there?

        Time windows matter. Without them a robot yields to any peer whose route merely
        passes through the cell at some point, which in a busy aisle means yielding
        permanently - the classic way a naive intent protocol underperforms plain
        stop-and-wait.
        """
        horizon_end = t + 2.0
        for i, c in enumerate(p.intent):
            if c != cell:
                continue
            if i < len(p.windows):
                w0, w1 = p.windows[i]
                if w1 >= t and w0 <= horizon_end:
                    return True
            else:
                return True
        return False

    def _peer_outranks(self, peer: Peer,
                       legacy_my_key: tuple[float, str]) -> bool:
        """Use the rich frozen key for BIOS_PIBT policies and legacy key elsewhere."""
        if self.policy in PIBT_POLICIES:
            theirs = peer.priority_key or PriorityKey(robot_id=peer.rid)
            return theirs > self._pub_priority_key
        return (peer.priority, peer.rid) > legacy_my_key

    def _bios_pibt_coordinate(self, t: float, sensors: Sensors,
                              requested: Cell) -> str | None:
        """Run replicated PIBT from the locally known peer snapshot.

        No robot commands another and there is no elected coordinator.  Each edge node
        reconstructs the same small configuration from idempotent heartbeats and runs
        the same deterministic resolver.  A lower-priority robot can therefore receive
        an inherited move out of the way instead of merely waiting for the conflict to
        disappear.  Divergent snapshots remain possible under packet loss, so Layer 0
        still has absolute authority over the continuous motion.
        """
        # ``Sensors.cell`` changes as soon as the chassis crosses a grid boundary,
        # while the follower keeps the same waypoint until it reaches that cell's
        # centre.  At that point ``requested == sensors.cell`` is not a request to
        # reserve a new cell: it is the continuous controller finishing a transition
        # PIBT already admitted.  Treating it as a discrete "stay" decision sets the
        # hold flag before the centre is reached and strands every robot half a cell
        # into its route.
        if requested == sensors.cell:
            return None

        here_cid = self._controlled_block(sensors.cell)
        if here_cid is not None and self.blocks.id_of(requested) != here_cid:
            # The block owner must be allowed to clear its exit. Outside peers may
            # advertise a future path through the apron, but feeding those intents
            # into PIBT makes the inside robot yield back into the single-file lane.
            # Side-bay peers have already yielded physically; cross their verified
            # gap at the bounded recovery speed.
            if any(p.state == ST_RETREAT and p.cell != requested
                   and manhattan(p.cell, requested) <= 1
                   for p in self.peers.values()):
                self._creep_until = max(self._creep_until, t + 6.0)
            return None

        positions: dict[str, Cell] = {self.rid: sensors.cell}
        goals: dict[str, Cell] = {self.rid: self.goal or sensors.cell}
        priorities: dict[str, PriorityKey] = {
            self.rid: self._pub_priority_key,
        }
        preferred: dict[str, Cell] = {self.rid: requested}

        for p in self.peers.values():
            # _expire_peers clears intent after peer_stale_s.  A pose without a fresh
            # intent is still physical occupancy, so include it but ask it to stay.
            if p.cell in positions.values():
                continue
            positions[p.rid] = p.cell
            goals[p.rid] = p.goal or (p.intent[-1] if p.intent else p.cell)
            priorities[p.rid] = p.priority_key or PriorityKey(robot_id=p.rid)
            preferred[p.rid] = p.intent[0] if p.intent else p.cell

        t0 = time.perf_counter()
        try:
            decision = pibt_step(
                self.env, positions, goals, priorities, preferred,
                max_depth=self.cfg.traffic.priority_max_depth)
        except (ValueError, RuntimeError):
            # A contradictory/stale snapshot is not a licence to move.  Let the local
            # safety layer stop and the next fresh broadcast repair the view.
            self.stats["priority_waits"] += 1
            return self._peer_ahead(sensors) or "pibt-snapshot"
        cpu = time.perf_counter() - t0
        self.stats["plan_cpu_s"] += cpu
        self.stats["plan_calls"] += 1
        self.stats["plan_cpu_max_s"] = max(self.stats["plan_cpu_max_s"], cpu)
        self.stats["priority_decisions"] += 1
        self.stats["priority_backtracks"] += decision.backtracks

        inherited = decision.inherited_from.get(self.rid)
        if inherited is not None:
            self.stats["priority_inheritances"] += 1

        chosen = decision.next_cells[self.rid]
        if chosen == sensors.cell:
            self.stats["priority_waits"] += 1
            # A discrete "stay" means stay at the cell centre, not wherever braking
            # happened to stop the continuous chassis. Backing to that centre opens
            # the intersection clearance the winning robot needs.
            if dist((sensors.pose[0], sensors.pose[1]),
                    cell_center(sensors.cell, self.cfg.cell_m)) > 0.22 * self.cfg.cell_m:
                self._creep_until = max(self._creep_until, t + 6.0)
            return decision.blocked_by.get(self.rid) or inherited or "pibt-wait"

        occupant = next(
            (peer for peer in self.peers.values() if peer.cell == requested), None)
        if chosen == requested and occupant is not None:
            occupant_target = decision.next_cells.get(occupant.rid, occupant.cell)
            my_step = (requested[0] - sensors.cell[0],
                       requested[1] - sensors.cell[1])
            leader_step = (occupant_target[0] - occupant.cell[0],
                           occupant_target[1] - occupant.cell[1])
            physical_gap = (
                dist((sensors.pose[0], sensors.pose[1]),
                     (occupant.pose[0], occupant.pose[1]))
                - 2.0 * self.cfg.robot.radius_m
            )
            too_close_for_convoy = (
                physical_gap <= self.cfg.robot.omni_stop_m + 0.05
            )
            if (occupant_target != occupant.cell
                    and (leader_step != my_step or too_close_for_convoy)):
                # PIBT proves collision-free *cell endpoints*, not continuous swept
                # trajectories.  A turning leader can make the initial vectors close
                # the gap; even in a straight convoy, an off-centre leader and a
                # follower already half a cell forward may have no room for the leader
                # to recenter.  Stage the follower at its own centre first.  Once it
                # backs away, the leader's motion becomes clearance-increasing and the
                # convoy drains on the next tick.
                self._creep_until = max(self._creep_until, t + 6.0)
                return occupant.rid

        # PIBT can authorize a safe convoy transition where the rear robot enters the
        # front robot's current cell while the front robot simultaneously vacates it.
        # Both chassis may already sit inside the conservative omni standstill field;
        # without a locally bounded creep neither can execute the collision-free
        # discrete configuration.  The forward speed envelope is still enforced, so
        # the follower cannot close on the peer ahead.
        requested_occupied = occupant is not None
        current_will_be_filled = any(
            rid != self.rid and target == sensors.cell
            for rid, target in decision.next_cells.items()
        )
        if inherited is not None or requested_occupied or current_will_be_filled:
            self._creep_until = max(self._creep_until, t + 6.0)
            # The previous tick's protective stop describes the old configuration.
            # Promoting it immediately after PIBT has authorized a complete convoy
            # move recreates a traffic hold before the new command can be evaluated.
            # Clear only the latch; Layer 0 still receives the command below and its
            # relative-velocity check rejects any recovery motion that closes a gap.
            self._stall_since = None

        if chosen != requested:
            # Priority inheritance has actively displaced us.  Execute exactly one
            # cell, then the ordinary route loop replans toward the unchanged goal.
            # Inside a controlled single-file block, physical space—not priority—is
            # the limiting resource and its lease must drain the lane.  Graph degree
            # is not a valid proxy: an open-bay boundary corner also has degree two,
            # yet its perpendicular neighbour is exactly the safe escape PIBT found.
            if (self._controlled_block(sensors.cell) is not None
                    or self._controlled_block(chosen) is not None):
                self.stats["priority_waits"] += 1
                return inherited or "pibt-narrow"
            # Do not splice a side-step into a fast continuous trajectory: the grid
            # transition is safe, but a differential-drive chassis cannot rotate
            # instantly and actuator inertia can carry it into a rack.  Holding here
            # asks the shared follower to brake; the next 10 Hz decision recomputes
            # from the newer snapshot and commits once speed is low.
            if abs(sensors.v) > 0.25:
                self.stats["priority_waits"] += 1
                return "pibt-brake"
            self.path = [sensors.cell, chosen]
            self.path_times = []
            self.pidx = 1
            self.epoch += 1
            self.stats["priority_forced_moves"] += 1
            self._creep_until = t + 6.0
        return None

    def _bios_v2_coordinate(self, t: float, sensors: Sensors,
                            requested: Cell) -> str | None:
        """Directed-cell admission for V2 circulation maps.

        The route graph forbids reverse edges, so a robot can never wait on a peer that
        is waiting to enter its own cell.  Occupancy queues therefore propagate toward
        an empty cell.  At merges, contenders announce for one full heartbeat round and
        the frozen priority key selects exactly one winner on every edge node.
        """
        if requested == sensors.cell:
            return None
        if not self.circulation.allows(self.env, sensors.cell, requested):
            return "v2-direction"

        occupant = next((p for p in self.peers.values() if p.cell == requested), None)
        if occupant is not None:
            return occupant.rid

        # A junction has multiple legal predecessors.  Hold once so simultaneous
        # arrivals exchange intents before either crosses the boundary.
        if self.env.degree(requested) >= 3:
            opened = self._cell_gate_since.setdefault(requested, t)
            if t - opened < self.cfg.traffic.gate_commit_s:
                return "cell-gate"

        contenders = [p for p in self.peers.values()
                      if p.cell != requested and p.intent and p.intent[0] == requested]
        for peer in sorted(contenders,
                           key=lambda p: p.priority_key or PriorityKey(robot_id=p.rid),
                           reverse=True):
            if self._peer_outranks(peer, self._arbitration_key()):
                return peer.rid
        return None

    def _bios_v3_cell_coordinate(self, t: float, sensors: Sensors,
                                 requested: Cell) -> str | None:
        """Two-phase destination-cell gate on bidirectional V3 maps.

        PIBT resolves a complete snapshot, but packet loss can hide one contender.
        Waiting one multicast round at a merge before running the same frozen total
        order makes a same-cell split decision recoverable while the independent
        protective field remains authoritative.
        """
        if requested == sensors.cell:
            return None
        contenders = [
            peer for peer in self.peers.values()
            if peer.cell != requested and peer.intent
            and peer.intent[0] == requested
        ]
        # A commit round is useful only at an actual merge.  The previous V3 code
        # opened one at *every* degree-3/4 cell, even with no peer anywhere near it.
        # On an open floor that made almost every grid step pay 450 ms and produced
        # hundreds of false yields.  A heartbeat from an adjacent active peer is the
        # conservative fallback when its intent packet was lost; with neither an
        # intent contender nor a nearby active peer there is no distributed decision
        # to commit.  Layer 0 and duplicate-cell repair remain authoritative if both
        # heartbeat and intent are missing.
        # Occupancy alone is not a final V3 decision.  Two adjacent robots requesting
        # each other's cells would otherwise both return here, both name the other as
        # blocker, and never reach the PIBT resolver below.  Treat an occupant as a
        # reason to wait for one common snapshot, then let PIBT choose the winner (and
        # a free inherited side-step when the topology permits one).
        nearby_active = any(
            peer.goal is not None and manhattan(peer.cell, requested) <= 2
            for peer in self.peers.values()
        )
        if self.env.degree(requested) >= 3 and (contenders or nearby_active):
            opened = self._cell_gate_since.setdefault(requested, t)
            if t - opened < self.cfg.traffic.gate_commit_s:
                return "cell-gate"
        else:
            self._cell_gate_since.pop(requested, None)
        for peer in sorted(
            contenders,
            key=lambda p: p.priority_key or PriorityKey(robot_id=p.rid),
            reverse=True,
        ):
            if self._peer_outranks(peer, self._arbitration_key()):
                return peer.rid
        return None

    def _repair_duplicate_cell(self, t: float, sensors: Sensors) -> bool:
        """Restore V3's one-robot-per-cell invariant after a lossy merge race."""
        duplicates = [
            peer for peer in self.peers.values() if peer.cell == sensors.cell
        ]
        if not duplicates:
            return False
        # Freeze ownership by commissioned unique ID. Dynamic waiting age must not
        # change the winner while the loser is turning out of the shared cell.
        owner = min([self.rid, *(peer.rid for peer in duplicates)])
        if owner == self.rid:
            self._hold = True
            self._track_block(t, True, min(peer.rid for peer in duplicates))
            return True

        occupied = {peer.cell for peer in self.peers.values()}
        options = [
            cell for cell in self.env.neighbors(sensors.cell)
            if cell not in occupied
        ]
        if self._controlled_block(sensors.cell) is None:
            options = [
                cell for cell in options if self._controlled_block(cell) is None
            ]
        if not options:
            self._hold = True
            self._track_block(t, True, owner)
            return True

        def clearance(cell: Cell) -> tuple[float, int, Cell]:
            target = cell_center(cell, self.cfg.cell_m)
            nearest = min(
                dist(target, (peer.pose[0], peer.pose[1]))
                for peer in duplicates
            )
            goal_cost = manhattan(cell, self.goal) if self.goal is not None else 0
            return (nearest, -goal_cost, cell)

        target = max(options, key=clearance)
        self.path = [sensors.cell, target]
        self.path_times = []
        self.pidx = 1
        self.epoch += 1
        self._hold = False
        self._stall_since = None
        self.blocked_since = None
        self.blocked_on = None
        self.state = self._state_for_task()
        self._creep_until = max(self._creep_until, t + 6.0)
        self._cell_repair_target = target
        self.stats["priority_forced_moves"] += 1
        return True

    def _track_block(self, t: float, blocked: bool, on: str | None) -> None:
        if blocked:
            if self.blocked_since is None:
                self.blocked_since = (
                    self._priority_grace_since
                    if (self._priority_grace_since is not None
                        and t < self._priority_grace_until)
                    else t
                )
            self.blocked_on = on
            if self.state not in (ST_RETREAT, ST_CHARGING):
                self.state = ST_BLOCKED
        else:
            if (self.blocked_since is not None
                    and self.policy in V3_AUCTION_POLICIES):
                self._priority_grace_since = self.blocked_since
                self._priority_grace_until = max(
                    self._priority_grace_until, t + 6.0)
            self.blocked_since = None
            self.blocked_on = None
            if self.state == ST_BLOCKED:
                self.state = self._state_for_task()

    def _break_deadlock(self, t: float, sensors: Sensors, contested: Cell,
                        outbox: list[msg.Message]) -> None:
        """Cycle detection over the wait-for graph assembled from peer heartbeats.

        The honest caveats, both of which are in the report:

        1. Cycle detection needs global state. We approximate it from broadcasts, so it
           works exactly where the radio works - and fails where partitions make
           deadlock most likely. This is not a decentralisation success story.
        2. Breaking a cycle needs a total order, and ours ends in `robot_id`: a number
           handed out by a central authority at commissioning. Every practical
           distributed scheme needs one. "No central server" is never literally true.

        Deadlock is also not the only failure. Breaking a cycle by backing off converts
        it into livelock, so `_route_loop` carries a separate no-progress timer that
        escalates. The problem statement sets no liveness criterion at all; we set one
        and measure against it.
        """
        # Whatever we do below, restart the clock. Without this the breaker re-fires
        # every reactive tick and the fleet thrashes on replans instead of recovering.
        self.blocked_since = t

        cycle = self._find_cycle()
        cycle_loser = False
        if cycle:
            self.stats["deadlocks_detected"] += 1
            if self.policy in PIBT_POLICIES:
                keys = {
                    r: (self._pub_priority_key if r == self.rid else
                        (self.peers[r].priority_key or PriorityKey(robot_id=r)))
                    for r in cycle
                }
                loser = min(cycle, key=lambda r: keys[r])
            else:
                keys = [(self.peers[r].priority, r) if r in self.peers
                        else self._arbitration_key() for r in cycle]
                loser = min(keys)[1]
            # Lowest key in the cycle gives way. Everyone computes the same winner
            # from the same broadcast data, so the choice needs no agreement protocol.
            if loser != self.rid:
                return
            cycle_loser = True

        if not cycle_loser:
            self.penalty[contested] = self.penalty.get(contested, 0.0) + \
                self.cfg.traffic.replan_penalty
            before = list(self.path)
            self._replan(t, sensors.cell)
            if self.path and self.path != before:
                if self.policy in PIBT_POLICIES:
                    # The replacement first step is away from the contested cell, but
                    # a close peer can still sit inside the omni standstill field.
                    self._creep_until = t + 6.0
                return

        # No alternative route exists - a single-file aisle. Physically give way by
        # reversing into the nearest free side cell. This is what a human driver does
        # in a one-lane corridor, and no amount of messaging substitutes for it.
        blocker = self.peers.get(self.blocked_on or "")
        give_way_from = blocker.cell if cycle_loser and blocker is not None else contested
        bay = self._passing_bay(sensors.cell, give_way_from, sensors.pose)
        if bay is not None:
            self.retreat_target = bay
            self._retreat_for = self.blocked_on
            blocker_cid = (self.blocks.id_of(blocker.cell)
                           if blocker is not None else None)
            contested_cid = self.blocks.id_of(contested)
            if blocker_cid is not None:
                self._retreat_block_cid = blocker_cid
            elif contested_cid is not None:
                self._retreat_block_cid = contested_cid
            else:
                self._retreat_block_cid = self.blocks.id_of(sensors.cell)
            self._retreat_origin = sensors.cell
            self._retreat_contested = give_way_from
            self.state = ST_RETREAT
            self._retreat_since = t
            self.path = [sensors.cell, bay]
            self.pidx = 1
            self.stats["retreats"] += 1
            if self.policy in PIBT_POLICIES:
                self._creep_until = t + 6.0
            self.blocked_since = None

    def _bios_unstick(self, t: float, sensors: Sensors, contested: Cell,
                      outbox: list[msg.Message]) -> None:
        """The BIOS_1.0.0 liveness valve: an actual, guaranteed step sideways/back.

        Where the other policies wait for a plan to clear, this one accepts that no
        amount of coordination can replace physical space - so when we are stuck, we
        make some. A single step into any free adjacent cell is always executable:
        by definition the cell is empty right now, and choosing a cell no peer owns
        or is about to step into means we never walk into a head-on swap. Repeating
        this on every stale tick drives a blocked queue apart without ever needing
        to agree with anyone, which is exactly why it is decentralised.

        We prefer a perpendicular (pull-aside) step over retreating, and a step that
        keeps moving toward the goal over one that does not; but if the only free
        cell is behind us, we take it - movement beats posture.
        """
        # Restart the clock first so this does not re-fire inside the same tick.
        self.blocked_since = None
        self._hold = False

        here = sensors.cell
        occupied = {p.cell for p in self.peers.values() if p.cell is not None}
        # Avoid cells a peer is about to occupy in the next ~half second too.
        for p in self.peers.values():
            if p.intent:
                occupied.add(p.intent[0])

        options = []
        for n in self.env.neighbors(here):
            if n in occupied:
                continue
            nc = self.blocks.id_of(n)
            # Never creep into a single-lane block somebody else already owns: that
            # is exactly the pile-up the block token exists to stop.
            if nc is not None and self._controlled_block(n) is not None:
                lock = self._bios_lock(nc, sensors.t)
                if lock is not None and lock[0] != self.rid:
                    continue
            options.append(n)

        if not options:
            # Walled in with no free step: make the contested cell expensive so a
            # future replan detours, rather than standing still forever.
            self.penalty[contested] = self.penalty.get(contested, 0.0) + \
                self.cfg.traffic.replan_penalty
            self._replan(t, here)
            return

        # Direction we were trying to travel (from here toward the contested cell).
        axis = (contested[0] - here[0], contested[1] - here[1])
        goal = self.goal

        def rank(n: Cell) -> tuple:
            d = (n[0] - here[0], n[1] - here[1])
            perpendicular = 1 if (d[0] * axis[0] + d[1] * axis[1]) == 0 else 0
            closer = 0 if goal is not None and manhattan(n, goal) < manhattan(here, goal) else 1
            return (perpendicular, closer, manhattan(n, contested))

        options.sort(key=rank)
        target = options[0]

        self.path = [here, target]
        self.path_times = []
        self.pidx = 1
        self.blocked_on = None
        self._stall_since = None
        self.retreat_target = None
        self._retreat_for = None
        self._retreat_block_cid = None
        self._retreat_origin = None
        self._retreat_contested = None
        self.state = self._state_for_task()
        self.stats["retreats"] += 1
        # Arm Layer 0's creep so we can actually break out of the stick. Long enough
        # to cross the target cell at creep speed, not so long we keep driving blind.
        self._creep_until = sensors.t + 6.0

    def _bios_lock(self, cid: int, t: float) -> tuple[str, float] | None:
        """Who holds block `cid` right now? (owner, expiry) or None.

        Physical presence outranks a claim: if any peer's reported cell is inside the
        block they ARE the owner, regardless of what the token table says. Otherwise
        an unexpired claim we have heard reserves the block until it expires.
        """
        for p in self.peers.values():
            if p.rid != self.rid and self._zone_contains(cid, p.cell):
                return (p.rid, 1e18)
        owner, until, _priority, _epoch, _rich = self._claims.get(
            cid, (None, -1e9, 0.0, 0, None))
        if owner is not None and owner != self.rid and until > t:
            return (owner, until)
        return None

    def _bios_claim(self, t: float, sensors: Sensors, nxt: Cell,
                    outbox: list[msg.Message]) -> None:
        """Take or keep the block token for the controlled block we are in or entering.

        A lock at the mouth is not enough: the token must be *held* while we transit,
        or the robot behind us cannot tell our intention from our presence. We claim
        the moment we are cleared to enter (so a rival at the opposite mouth sees the
        reservation and waits before piling in), keep it re-broadcast while inside,
        and release it the instant we leave.
        """
        here = sensors.cell
        if self.policy in DIRECTED_POLICIES and self.circulation.enabled:
            c_here = None
            c_nxt = (self._cell_zone_id(nxt)
                     if nxt is not None and nxt != here else None)
            inside = False
            about_to = c_nxt is not None
        else:
            c_here = self.blocks.id_of(here)
            c_nxt = self.blocks.id_of(nxt) if nxt is not None else None
            inside = c_here is not None and self._controlled_block(here) is not None
            about_to = (c_nxt is not None and c_nxt != c_here
                        and self._controlled_block(nxt) is not None)
        cid = c_here if inside else c_nxt
        # A robot queued behind another at the same mouth must not claim the block.
        # The first traffic pass names that front robot in ``blocked_on``; only a gate
        # hold represents a legitimate two-phase claim attempt.
        eligible_at_mouth = not self._hold or self.blocked_on == "gate"
        take = inside or (about_to and eligible_at_mouth
                          and self._bios_lock(cid, t) is None)

        if take and cid is not None:
            if self._claim_cid != cid:
                self._claim_cid = cid
                # A lease request has one immutable rank.  Recomputing it on every
                # keep-alive lets a waiting robot's age or replan epoch repeatedly
                # steal the token before the current winner can cross the mouth.
                self._claim_priority_key = (
                    self._pub_priority_key
                    if self.policy in PIBT_POLICIES else None)
                self._last_claim_t = -1e9     # force an immediate claim broadcast
            if t - self._last_claim_t >= 0.5:  # keep-alive every ~2 heartbeats
                self._last_claim_t = t
                until = t + self.cfg.traffic.bios_claim_ttl_s
                self._claims[cid] = (self.rid, until, self._pub_priority, self.epoch,
                                     self._claim_priority_key)
                outbox.append(msg.block_claim(
                    self.rid, self._next_seq(), t, cid, until,
                    self._pub_priority, self.epoch,
                    ttl=self.cfg.traffic.bios_claim_ttl_s,
                    priority_key=(self._claim_priority_key.to_wire()
                                  if self._claim_priority_key else None)))
        elif self._claim_cid is not None:
            # Left the block (or lost the right to enter): release it for the next robot.
            outbox.append(msg.block_release(self.rid, self._next_seq(), t, self._claim_cid))
            self._claims.pop(self._claim_cid, None)
            self._gate_committed.discard(self._claim_cid)
            self._gate_since.pop(self._claim_cid, None)
            self._claim_cid = None
            self._claim_priority_key = None
            self._last_claim_t = -1e9

    def _find_cycle(self) -> list[str] | None:
        """Walk the wait-for chain from self; report the cycle if it returns to self."""
        seen: list[str] = [self.rid]
        cur = self.blocked_on
        while cur is not None and len(seen) <= len(self.peers) + 1:
            if cur == self.rid:
                return seen
            if cur in seen:
                return None                      # a cycle, but not one we are part of
            seen.append(cur)
            p = self.peers.get(cur)
            if p is None or p.state != ST_BLOCKED:
                return None
            cur = p.blocked_on
        return None

    def _passing_bay(self, here: Cell, contested: Cell,
                     pose: tuple[float, float, float] | None = None) -> Cell | None:
        """The free neighbour FURTHEST from the conflict - somewhere to get out of the way.

        Furthest, not nearest: the point of the manoeuvre is to open the cell the other
        robot needs, and edging towards it does the opposite. In a single-file aisle
        this resolves to "reverse the way you came", which is exactly what a driver
        does in a one-lane road, and no amount of messaging substitutes for it.
        """
        occupied = {p.cell for p in self.peers.values()}
        axis = (contested[0] - here[0], contested[1] - here[1])
        origin_xy = ((pose[0], pose[1]) if pose is not None
                     else cell_center(here, self.cfg.cell_m))
        best, best_key = None, (-1, -1.0, -1.0)
        for n in self.env.neighbors(here):
            if n == contested or n in occupied:
                continue
            step = (n[0] - here[0], n[1] - here[1])
            # Prefer stepping SIDEWAYS. Reversing along the same axis just relocates
            # the obstruction one cell down the lane the other robot is trying to use;
            # moving perpendicular actually clears it. This is pulling over, not
            # backing up, and it is the difference between giving way and giving way
            # slowly.
            perpendicular = 1 if (step[0] * axis[0] + step[1] * axis[1]) == 0 else 0
            target_xy = cell_center(n, self.cfg.cell_m)
            peer_clearance = min(
                (segment_point_distance(origin_xy, target_xy,
                                        (p.pose[0], p.pose[1]))
                 for p in self.peers.values()),
                default=99.0)
            key = (perpendicular, peer_clearance, float(manhattan(n, contested)))
            if key > best_key:
                best, best_key = n, key
        return best

    def _is_safe_retreat_bay(self, target: Cell | None) -> bool:
        """Whether a completed retreat is physically clear of controlled traffic.

        A degree-two cell inside a single-file block is a reverse waypoint, not a
        passing bay.  Waiting there for the winner to pass would leave the yielding
        robot parked in the shared lane and deadlock every entrance to it.
        """
        if target is None:
            return False
        if self._retreat_origin is not None and self._retreat_contested is not None:
            step = (target[0] - self._retreat_origin[0],
                    target[1] - self._retreat_origin[1])
            axis = (self._retreat_contested[0] - self._retreat_origin[0],
                    self._retreat_contested[1] - self._retreat_origin[1])
            if step[0] * axis[0] + step[1] * axis[1] != 0:
                return False
        if self._retreat_block_cid is not None:
            # A perpendicular aisle segment can itself have degree two; it is still a
            # valid bay when it belongs to a different corridor component.
            return self.blocks.id_of(target) != self._retreat_block_cid
        return self.env.degree(target) >= 3

    # ================================================================== Layer 2

    def _route_loop(self, t: float, sensors: Sensors,
                    outbox: list[msg.Message]) -> None:
        self.mode = (MODE_CENTRAL
                     if t - self._mgr_seen < self.cfg.traffic.central_timeout_s
                     else MODE_P2P)

        for c in list(self.penalty):
            self.penalty[c] *= 0.75
            if self.penalty[c] < 0.1:
                del self.penalty[c]

        if self.policy in CENTRAL_POLICIES:
            if self.mode != MODE_CENTRAL:
                # The single point of failure, demonstrated rather than argued. A
                # purely centralised fleet with an unreachable manager does not
                # degrade - it parks. That is the whole reason to build a fallback.
                self.path = []
                self.path_times = []
                self.state = ST_BLOCKED
                return
            if self.goal is not None:
                # Never plans locally: by construction this policy owns no autonomy
                # above Layer 0, which is exactly the architecture being criticised.
                outbox.append(msg.plan_req(self.rid, self._next_seq(), t,
                                           sensors.cell, self.goal,
                                           no_schedule=not self.path_times))
            return

        if self.state == ST_RETREAT:
            # Time-boxed. A give-way that cannot finish - the bay filled up, the map
            # was wrong, the robot was nudged - must expire rather than latch, or the
            # robot sits in a manoeuvre state forever while the fleet routes around it.
            done = self.retreat_target is None or sensors.cell == self.retreat_target
            retreat_age = t - self._retreat_since
            safe_bay = self._is_safe_retreat_bay(self.retreat_target)
            lane_occupied = (self._retreat_block_cid is not None and any(
                self.blocks.id_of(p.cell) == self._retreat_block_cid
                and p.goal is not None and p.state != ST_IDLE
                for p in self.peers.values()))
            # A true side bay waits for the controlled lane to drain. A waypoint
            # still inside a corridor must be released immediately so the robot can
            # continue reversing out. Peer records expire, so a lost heartbeat cannot
            # latch the yielding robot after the physical lane becomes clear.
            if done and safe_bay and lane_occupied:
                return
            if (done and not safe_bay and self._retreat_block_cid is not None
                    and self.blocks.id_of(sensors.cell) != self._retreat_block_cid):
                # Reversing out of a lane reaches its mouth but can still park on the
                # exit axis. Take one perpendicular step at the junction to become a
                # real passing bay before waiting for the lane to drain.
                previous = self._retreat_origin or sensors.cell
                extension = self._passing_bay(sensors.cell, previous, sensors.pose)
                if extension is not None:
                    self._retreat_origin = sensors.cell
                    self._retreat_contested = previous
                    self.retreat_target = extension
                    self.path = [sensors.cell, extension]
                    self.path_times = []
                    self.pidx = 1
                    self._retreat_since = t
                    self._creep_until = t + 6.0
                    return
            if done or retreat_age > 6.0:
                self.retreat_target = None
                self._retreat_for = None
                self._retreat_block_cid = None
                self._retreat_origin = None
                self._retreat_contested = None
                self.state = self._state_for_task()
                self._replan(t, sensors.cell)
            return

        if self.goal is None:
            return

        # Ask for a coordinated route on EVERY tick the manager is reachable, not only
        # when the local plan has run out. Requesting it lazily meant the robot spent
        # most of its time on a local shortest path with no schedule attached, fell
        # back to peer negotiation it did not need, and replanned four times as often
        # as the central baseline. Layer 2 prefers the optimiser; local A* is the
        # bootstrap and the fallback, not the steady state.
        if self.mode == MODE_CENTRAL:
            outbox.append(msg.plan_req(self.rid, self._next_seq(), t,
                                       sensors.cell, self.goal,
                                       no_schedule=not self.path_times))

        stuck = t - self._last_progress_t
        if not self.path or self.pidx >= len(self.path):
            self._replan(t, sensors.cell)
        elif stuck > self.cfg.traffic.livelock_progress_s:
            # Liveness escalation. Nothing has moved for a long time and no cycle was
            # detected, so the model of the world is wrong; throw the plan away.
            self.penalty.clear()
            self._last_progress_t = t
            self._replan(t, sensors.cell)

    def _replan(self, t: float, start: Cell) -> None:
        if self.goal is None:
            return
        t0 = time.perf_counter()
        blocked = {
            cell for cell, until in self._dynamic_blocked_until.items()
            if until > t and cell != start and cell != self.goal
        }
        edge_cost = self._v6_edge_costs(t)
        route_cost = dict(self.penalty)
        predictive_cost = self._v6_prediction_costs(t, start)
        for cell, cost in predictive_cost.items():
            route_cost[cell] = max(route_cost.get(cell, 0.0), cost)
        path = astar(
            self.env, start, self.goal, extra_cost=route_cost,
            edge_cost=edge_cost,
            blocked=blocked,
            edge_allowed=(lambda a, b: self.circulation.allows(self.env, a, b))
            if self.policy in DIRECTED_POLICIES else None)
        # A newly blocked aisle can make the normal one-way circulation temporarily
        # disconnected.  Local physical truth outranks the nominal traffic graph: use
        # an undirected detour rather than wait forever for a pallet to broadcast.
        if not path and blocked and self.policy in DIRECTED_POLICIES:
            path = astar(self.env, start, self.goal,
                         extra_cost=route_cost, edge_cost=edge_cost,
                         blocked=blocked)
        if path and (edge_cost or predictive_cost):
            base_path = astar(
                self.env, start, self.goal, extra_cost=self.penalty,
                blocked=blocked,
                edge_allowed=(lambda a, b: self.circulation.allows(self.env, a, b))
                if self.policy in DIRECTED_POLICIES else None)
            if base_path and base_path != path and edge_cost:
                avoided = sum(
                    edge_cost.get((a, b), 0.0)
                    for a, b in zip(base_path, base_path[1:]))
                chosen = sum(
                    edge_cost.get((a, b), 0.0)
                    for a, b in zip(path, path[1:]))
                if avoided > chosen + 0.05:
                    self.stats["experience_guided_replans"] += 1
                    self._record_decision(
                        t, "CONGESTION_REROUTE",
                        "Selected a lower-delay route from fleet experience",
                        avoided_delay_cells=round(avoided - chosen, 2),
                        direct_cells=max(0, len(base_path) - 1),
                        selected_cells=max(0, len(path) - 1))
            if base_path and base_path != path and predictive_cost:
                avoided = sum(predictive_cost.get(cell, 0.0)
                              for cell in base_path[1:])
                chosen = sum(predictive_cost.get(cell, 0.0)
                             for cell in path[1:])
                if avoided > chosen + 0.05:
                    self.stats["predictive_reroutes"] += 1
                    self._record_decision(
                        t, "PREDICTIVE_REROUTE",
                        "Rerouted before a predicted occupancy conflict",
                        avoided_risk=round(avoided - chosen, 2),
                        direct_cells=max(0, len(base_path) - 1),
                        selected_cells=max(0, len(path) - 1))
        cpu = time.perf_counter() - t0
        self.stats["plan_cpu_s"] += cpu
        self.stats["plan_calls"] += 1
        self.stats["plan_cpu_max_s"] = max(self.stats["plan_cpu_max_s"], cpu)
        self.stats["local_plans"] += 1
        self.stats["replans"] += 1
        self.epoch += 1
        self.path = path
        # A locally computed route carries no schedule; dropping the old times stops
        # the follower from honouring a timetable that belongs to a discarded plan.
        self.path_times = []
        self.pidx = 1 if len(path) > 1 else 0

    def _observe_dynamic_obstacles(self, t: float, sensors: Sensors) -> None:
        """Promote stationary anonymous lidar blobs into an expiring local map layer."""
        for cell in list(self._dynamic_blocked_until):
            if self._dynamic_blocked_until[cell] <= t:
                self._dynamic_blocked_until.pop(cell, None)
        for cell, (_first, last, _count) in list(self._dynamic_candidates.items()):
            if t - last > 0.5:
                self._dynamic_candidates.pop(cell, None)
        for cell, (_penalty, expiry, _source) in list(
                self._predicted_cell_cost.items()):
            if expiry <= t:
                self._predicted_cell_cost.pop(cell, None)

        peer_positions = [(peer.pose[0], peer.pose[1]) for peer in self.peers.values()
                          if t - peer.last_seen <= self._peer_stale_after_s()]
        route_blocked = False
        route_predicted = False
        remaining_path = set(self.path[self.pidx:])
        for detection in sensors.detections:
            moving_speed = math.hypot(detection.vx, detection.vy)
            matched_peer = any(
                dist((detection.x, detection.y), pose) < 0.35
                for pose in peer_positions)
            if moving_speed > 0.08:
                # A peer already publishes intent, so extrapolating the anonymous
                # lidar copy would double-count it.  Everything unmatched is handled
                # generically: worker, forklift, or any other moving obstacle.
                # A heartbeat pose may be up to one event-trigger interval behind a
                # moving chassis. Use a wider correlation gate only for moving blobs;
                # the tighter stationary gate below still distinguishes a pallet
                # parked beside a peer.
                moving_peer = matched_peer or any(
                    dist((detection.x, detection.y), pose) < 0.75
                    for pose in peer_positions)
                prediction_enabled = (
                    self.policy == POLICY_BIOS_PIBT_V6
                    and self.cfg.net.loss <= 0.0
                    and not self.cfg.net.dead_zones)
                if prediction_enabled and not moving_peer:
                    horizon = self.cfg.traffic.v6_prediction_horizon_s
                    step = max(0.2, self.cfg.traffic.v6_prediction_step_s)
                    sample_t = step
                    observed_new = False
                    while sample_t <= horizon + 1e-9:
                        cell = to_cell((
                            detection.x + detection.vx * sample_t,
                            detection.y + detection.vy * sample_t,
                        ), self.cfg.cell_m)
                        if (cell != sensors.cell and self.env.passable(cell)):
                            strength = max(
                                0.5,
                                self.cfg.traffic.v6_prediction_penalty
                                * (1.0 - 0.5 * sample_t / horizon))
                            previous = self._predicted_cell_cost.get(cell)
                            expiry = t + sample_t + step
                            if previous is None or previous[1] <= t:
                                observed_new = True
                            if previous is None or strength > previous[0]:
                                self._predicted_cell_cost[cell] = (
                                    strength, expiry, "moving-obstacle")
                            elif expiry > previous[1]:
                                self._predicted_cell_cost[cell] = (
                                    previous[0], expiry, previous[2])
                            if cell in remaining_path and cell != self.goal:
                                route_predicted = True
                        sample_t += step
                    if observed_new:
                        self.stats["predictive_hazards_seen"] += 1
                continue
            # A stopped peer is traffic, not a map mutation. Correlation is deliberately
            # approximate because lidar itself carries no identity.
            if matched_peer:
                self._dynamic_candidates.pop(
                    to_cell((detection.x, detection.y), self.cfg.cell_m), None)
                continue
            cell = to_cell((detection.x, detection.y), self.cfg.cell_m)
            if cell == sensors.cell or not self.env.passable(cell):
                continue
            first, _last, count = self._dynamic_candidates.get(
                cell, (t, t, 0))
            count += 1
            self._dynamic_candidates[cell] = (first, t, count)
            # Wait through at least one heartbeat interval before turning an anonymous
            # stopped object into map state. A peer will identify itself during that
            # window; a dropped pallet will not.
            if t - first < 0.3 or count < 3:
                continue
            if cell not in self._dynamic_blocked_until:
                self.stats["dynamic_obstacles_detected"] += 1
            self._dynamic_blocked_until[cell] = t + 2.0
            if cell in remaining_path and cell != self.goal:
                route_blocked = True

        if route_blocked and self.goal is not None:
            before = list(self.path)
            self._replan(t, sensors.cell)
            if self.path and self.path != before:
                self.stats["dynamic_reroutes"] += 1
        elif (route_predicted and self.goal is not None
              and t - self._last_predictive_replan
              >= self.cfg.traffic.v6_prediction_replan_s):
            self._last_predictive_replan = t
            self._replan(t, sensors.cell)

    # ================================================================== tasks

    def _auction_enabled(self) -> bool:
        """Whether this robot owns the peer-auction allocation responsibility."""
        return self.allocation_policy in (
            ALLOCATION_AUCTION, ALLOCATION_AUCTION_BUNDLE) or self.use_auction

    def _future_allocation_enabled(self) -> bool:
        """Whether this BIOS 6 robot may reserve one bounded future task."""
        return (self.policy == POLICY_BIOS_PIBT_V6
                and self.allocation_policy == ALLOCATION_AUCTION_BUNDLE)

    def _future_network_healthy(self, t: float) -> bool:
        """Require a stable, complete-enough peer view before reserving future work.

        Configured loss/dead-zones disable the optimization outright. For live partitions,
        every peer learned earlier must still be fresh; active execution never depends on
        this advisory test.
        """
        # One-step lookahead is useful on open, bursty layouts. Controlled single-file
        # blocks make traffic-wave admission more important than speculative locality;
        # paired benchmarks showed no benefit there, so retain ordinary BIOS 6.
        if (self.blocks.members or self.cfg.net.loss > 0.0
                or self.cfg.net.dead_zones):
            self._future_network_candidate_since = None
            return False
        if not self._known_peer_ids:
            self._future_network_candidate_since = None
            return False
        stale = self._peer_stale_after_s()
        raw_healthy = all(
            rid in self.peers and t - self.peers[rid].last_seen <= stale
            for rid in self._known_peer_ids
        )
        if not raw_healthy:
            self._future_network_candidate_since = None
            return False
        if self._future_network_candidate_since is None:
            self._future_network_candidate_since = t
        return (t - self._future_network_candidate_since
                >= max(0.0, self.cfg.traffic.bundle_network_recovery_s))

    def _safe_incoming_epoch(self, current: int, incoming: int) -> bool:
        return (0 <= incoming < msg.MAX_AUCTION_EPOCH
                and incoming <= current
                + max(1, self.cfg.traffic.bundle_epoch_max_advance))

    def _cache_unknown_bid(self, t: float, source: str, body: dict) -> None:
        """Defer reordered bids in a small TTL cache; never make them authoritative."""
        ttl = max(0.0, self.cfg.traffic.auction_unknown_bid_cache_ttl_s)
        for tid, entries in list(self._pending_unknown_bids.items()):
            fresh = [entry for entry in entries if t - entry[0] <= ttl]
            if fresh:
                self._pending_unknown_bids[tid] = fresh
            else:
                self._pending_unknown_bids.pop(tid, None)
        context = None
        if body.get("future") and self._future_allocation_enabled():
            context = (
                str(body["active"]), int(body.get("ae", 0)),
                int(body.get("bv", 0)))
        entry = (
            t, source, int(body.get("e", 0)), float(body["cost"]), context)
        self._pending_unknown_bids.setdefault(str(body["task"]), []).append(entry)
        self.stats["deferred_unknown_bids"] += 1
        limit = max(1, self.cfg.traffic.auction_unknown_bid_cache_max)
        while sum(len(entries) for entries in self._pending_unknown_bids.values()) > limit:
            oldest_tid = min(
                self._pending_unknown_bids,
                key=lambda tid: (self._pending_unknown_bids[tid][0][0], tid))
            self._pending_unknown_bids[oldest_tid].pop(0)
            self.stats["rejected_unknown_bids"] += 1
            if not self._pending_unknown_bids[oldest_tid]:
                self._pending_unknown_bids.pop(oldest_tid, None)

    def _admit_deferred_bids(self, tid: str, t: float) -> None:
        task = self.open_tasks.get(tid)
        if task is None:
            return
        ttl = max(0.0, self.cfg.traffic.auction_unknown_bid_cache_ttl_s)
        for seen_t, source, epoch, cost, context in self._pending_unknown_bids.pop(
                tid, []):
            if t - seen_t > ttl or not self._safe_incoming_epoch(
                    task.auction_epoch, epoch) or epoch < task.auction_epoch:
                self.stats["rejected_unknown_bids"] += 1
                continue
            if epoch > task.auction_epoch:
                task.auction_epoch = epoch
                task.bid_deadline = t + self.cfg.traffic.auction_bid_window_s
                self._bids.pop(tid, None)
                self._bid_opened.pop(tid, None)
            self._bids.setdefault(tid, {})[(epoch, source)] = cost
            self._bid_seen_t[(tid, epoch, source)] = seen_t
            if context is not None:
                self._future_bid_contexts[(tid, epoch, source)] = context

    def _task_loop(self, t: float, sensors: Sensors,
                   outbox: list[msg.Message]) -> None:
        charge_trigger = (self.cfg.traffic.energy_charge_trigger_frac
                          if self.policy in ENERGY_AUCTION_POLICIES else 0.15)
        if self.task is None and sensors.battery_frac < charge_trigger and self.env.docks:
            if self.state != ST_CHARGING or self.goal not in self.env.docks:
                self.goal = (
                    self._v6_select_charger(t, sensors.cell)
                    if self.policy == POLICY_BIOS_PIBT_V6
                    else min(self.env.docks,
                             key=lambda d: manhattan(sensors.cell, d))
                )
            self.state = ST_CHARGING
        if self.state == ST_CHARGING:
            rejoin = (self.cfg.traffic.energy_rejoin_frac
                      if self.policy in ENERGY_AUCTION_POLICIES else 0.9)
            if sensors.battery_frac > rejoin:
                self.state = ST_IDLE
                self.goal = None
            else:
                return

        if self._future_allocation_enabled():
            self._consume_future_nomination(t, sensors, outbox)
            if self.future_task is not None:
                if not self._future_network_healthy(t):
                    if not self._future_needs_reconcile:
                        self.stats["future_network_fallbacks"] += 1
                    self._future_needs_reconcile = True
                elif (t - self._last_future_revalidate
                      >= self.cfg.traffic.bundle_revalidate_s):
                    self._last_future_revalidate = t
                    if (self.future_task.tid not in self.open_tasks
                            or self.future_task.tid in self.completed_tasks):
                        self._release_future(
                            t, outbox, "future task was cancelled or completed",
                            reauction=False)
                    elif not self._future_sequence_feasible(
                            self.future_task, sensors, t)[0]:
                        self._release_future(
                            t, outbox, "future sequence became infeasible")
                    else:
                        self._future_needs_reconcile = False

        if self.task is None:
            if self._needs_duplicate_vacate:
                if self.goal is None:
                    self._force_duplicate_vacate(t, sensors.cell)
                elif self._arrived(sensors, self.goal):
                    self.goal = None
                    self.path = []
                    self.path_times = []
                    self.pidx = 0
                    self._needs_duplicate_vacate = False
                if self._needs_duplicate_vacate:
                    return
            if self._auction_reposition_target is not None:
                target = self._auction_reposition_target
                if self._arrived(sensors, target):
                    self.goal = None
                    self.path = []
                    self.path_times = []
                    self.pidx = 0
                    self._auction_reposition_target = None
                else:
                    if self.goal != target:
                        self.goal = target
                        self._replan(t, sensors.cell)
                    return
            # Parking/vacate motion is independent of task allocation. This must run
            # before the auction branch: otherwise an auction-enabled idle robot that
            # reaches its one-cell vacate target keeps that goal forever, never calls
            # `_vacate_if_in_the_way` again, and becomes a permanent wall in a bay.
            if self.goal is not None and self._arrived(sensors, self.goal):
                # Directed circulation does not make a parking target permanent.
                # Keeping the goal after arrival prevented `_vacate_if_in_the_way`
                # from ever running, so an idle AMR could occupy a shared drop/charge
                # bay forever while the final loaded robot waited behind it.
                self.goal = None
                self.path = []
                self.path_times = []
                self.pidx = 0
            assigned = next((tid for tid in sorted(self._awarded)
                             if tid in self.open_tasks), None)
            if assigned is not None:
                self._awarded.remove(assigned)
                task = self.open_tasks[assigned]
                feasible, _required, _reserve = self._energy_feasible(
                    task, sensors, t=t)
                if (feasible and self.allocation_policy
                        in (None, ALLOCATION_HUNGARIAN)):
                    self._accept_task(t, task, sensors.cell)
                else:
                    self.stats["rejected_directed_awards"] += 1
            else:
                nominated = next((
                    (tid, nomination)
                    for tid, nomination in sorted(self._peer_nominations.items())
                    if tid in self.open_tasks
                ), None)
                if nominated is not None:
                    tid, (epoch, cost, lease_until) = nominated
                    task = self.open_tasks[tid]
                    claim = self._task_claims.get(tid)
                    eligible = (
                        claim is not None
                        and claim[0] == epoch
                        and math.isclose(claim[1], cost, rel_tol=1e-9,
                                         abs_tol=5e-4)
                        and claim[2] == self.rid
                        and claim[3] == lease_until
                        and lease_until > t
                        and self._energy_feasible(task, sensors, t=t)[0]
                    )
                    self._peer_nominations.pop(tid, None)
                    if eligible:
                        self._accept_task(
                            t, task, sensors.cell,
                            lease_until=lease_until, bid_cost=cost)
                    elif self._task_claims.get(tid) == claim:
                        # Do not let an ineligible nomination reserve this robot or
                        # suppress a valid peer winner for the rest of the lease.
                        self._task_claims.pop(tid, None)
                        task.lease_owner = None
                        task.lease_until = 0.0
            if self.task is None and self.queue:
                self._accept_task(t, self.queue.pop(0), sensors.cell)
            elif self.task is None and self._auction_enabled():
                self._run_auction(t, sensors, outbox)
            if self.goal is None:
                self._vacate_if_in_the_way(t, sensors)
            return

        if (self._future_allocation_enabled()
                and self.future_task is None
                and self._future_network_healthy(t)):
            self._run_v3_batch_auction(t, sensors, outbox)

        if self.state in (ST_TO_PICK, ST_BLOCKED) and self._arrived(sensors, self.task.pick) \
                and self.goal == self.task.pick:
            self.state = ST_TO_DROP
            self.goal = self.task.drop
            self._replan(t, sensors.cell)
        elif self.goal == self.task.drop and self._arrived(sensors, self.task.drop):
            completed_tid = self.task.tid
            self.completed.append((self.task.tid, self._task_started_t, t))
            outbox.append(msg.task_done(
                self.rid, self._next_seq(), t, self.task.tid,
                epoch=self.task.auction_epoch))
            self._completion_proofs[self.task.tid] = (
                self.task.auction_epoch, self.rid)
            self.open_tasks.pop(self.task.tid, None)
            self.completed_tasks.add(self.task.tid)
            self._task_claims.pop(self.task.tid, None)
            self._bids.pop(self.task.tid, None)
            self._bid_opened.pop(self.task.tid, None)
            self._awarded.discard(self.task.tid)
            self._record_decision(
                t, "TASK_COMPLETED", f"Completed {completed_tid}",
                task=completed_tid,
                elapsed_s=round(t - self._task_started_t, 2),
                battery_pct=round(100.0 * sensors.battery_frac, 1))
            self.task = None
            self.state = ST_IDLE
            self._priority_grace_since = None
            self._priority_grace_until = -1e9
            if self._future_allocation_enabled() and self.future_task is not None:
                if self._promote_future(t, sensors, outbox):
                    return
            # Clear the station. A drop point is a shared resource, and a robot that
            # finishes its last job and simply stops where it stands is parked on top
            # of it - permanently, since nothing will ever ask it to move. That single
            # behaviour stranded whole runs here: one idle robot sitting on a station
            # made every remaining task targeting that station unreachable, and the
            # symptom looked like a planner deadlock rather than a parking bug.
            # An auction robot stays on the delivery side. Returning to its original
            # home would consume the same scarce corridor empty, oppose the admitted
            # task wave, and then make the next reverse-direction pickup farther away.
            # It will vacate this exact drop cell below if another peer needs it.
            self.goal = (None if (self._auction_enabled()
                                  and not self.circulation.enabled)
                         else (None if self.queue else self._idle_parking_cell()))

    def _idle_parking_cell(self) -> Cell:
        """Choose a deterministic off-aisle dock instead of blocking a worker route."""
        if not self.env.docks:
            return self.home
        digits = "".join(character for character in self.rid if character.isdigit())
        index = max(0, int(digits or "1") - 1)
        return sorted(self.env.docks)[index % len(self.env.docks)]

    def _force_duplicate_vacate(self, t: float, here: Cell) -> None:
        """Move a canceled duplicate out of traffic before bidding again."""
        taken = {peer.cell for peer in self.peers.values()} | {
            cell for peer in self.peers.values() for cell in peer.intent
        }
        cid = self._controlled_block(here)
        targets: list[Cell] = []
        if cid is not None:
            for end in self.blocks.ends.get(cid, ()):
                targets.extend(
                    neighbor for neighbor in self.env.neighbors(end)
                    if self._controlled_block(neighbor) != cid
                    and neighbor not in taken)
        else:
            targets = [
                neighbor for neighbor in self.env.neighbors(here)
                if self._controlled_block(neighbor) is None
                and neighbor not in taken
            ]
        if not targets:
            # Normal idle-vacate logic will retry when peer intents move.
            self._needs_duplicate_vacate = False
            return
        target = min(set(targets), key=lambda cell: (manhattan(here, cell), cell))
        self.goal = target
        self.state = ST_IDLE
        self._replan(t, here)

    def _arrived(self, sensors: Sensors, target: Cell) -> bool:
        """Task service occurs at the cell centre, not at its quantised boundary."""
        if sensors.cell != target:
            return False
        if (self.policy not in V3_AUCTION_POLICIES
                and (self.policy not in DIRECTED_POLICIES
                     or not self.circulation.enabled)):
            return True
        return dist((sensors.pose[0], sensors.pose[1]),
                    cell_center(target, self.cfg.cell_m)) < 0.16

    def _vacate_if_in_the_way(self, t: float, sensors: Sensors) -> None:
        """Parked on somebody's destination? Move.

        Without this an idle robot is a permanent wall. Every other mechanism here -
        yielding, block control, deadlock breaking - assumes both parties are trying to
        go somewhere; none of them can prompt a robot that has already arrived and has
        no reason to move again. The failure is silent and total: the peer waiting for
        that cell simply never completes, and it reads as a planner deadlock.
        """
        here = sensors.cell
        blockers_requesting_clearance = [
            p for p in self.peers.values()
            if (p.goal == here or here in p.intent
                or (self.policy == POLICY_BIOS_PIBT_V6
                    and p.blocked_on == self.rid))
        ]
        if not blockers_requesting_clearance:
            return
        taken = {p.cell for p in self.peers.values()} | {
            p.goal for p in self.peers.values() if p.goal} | {
            cell for p in self.peers.values() for cell in p.intent
        }
        options = [n for n in self.env.neighbors(here) if n not in taken]
        explicit_blockers = [
            peer for peer in blockers_requesting_clearance
            if peer.blocked_on == self.rid
        ]
        if (self.policy == POLICY_BIOS_PIBT_V6 and self.circulation.enabled
                and explicit_blockers):
            # A one-cell sidestep is insufficient when every adjacent cell is part of
            # the same active route.  Select another mapped dock and let the normal
            # traffic protocol move the idle chassis completely out of the corridor.
            # This is triggered only by an explicit peer clearance request and only on
            # a directed circulation map. Crossing a bidirectional single-file block
            # while idle would inject exactly the opposing traffic this rule prevents.
            # In a deterministic geographic partition, a parking route through the
            # radio hole would turn an idle clearance into uncoordinated traffic. Such
            # routes are rejected below; an outside route remains valid and the
            # bounded adjacent clearance is the fallback when none exists.
            parking_routes: list[tuple[int, Cell]] = []
            for dock in sorted(self.env.docks):
                if dock == here or dock in taken:
                    continue
                route = astar(
                    self.env, here, dock,
                    edge_allowed=(
                        lambda a, b: self.circulation.allows(self.env, a, b))
                    if self.circulation.enabled else None)
                if route and self._v6_remote_idle_vacate_allowed(route):
                    parking_routes.append((len(route), dock))
            if parking_routes:
                self.goal = min(parking_routes)[1]
                self._record_decision(
                    t, "IDLE_VACATE", "Moving to a clear parking dock",
                    from_cell=list(here), to_cell=list(self.goal),
                    requesting_robots=sorted(
                        p.rid for p in explicit_blockers))
                self._replan(t, here)
                return
        if self.policy in V3_AUCTION_POLICIES and not self.circulation.enabled:
            here_cid = self._controlled_block(here)
            if here_cid is None:
                # Parking motion never consumes a bidirectional traffic block. Only
                # a task-owning robot may enter under its directional wave and lease.
                local = [n for n in options if self._controlled_block(n) is None]
                options = local
            else:
                # A stale pose/old version may still leave an idle AMR inside. Keep it
                # moving monotonically toward the nearest mouth until the block clears.
                exit_cell = self.blocks.nearest_end(here_cid, here)
                if exit_cell is not None:
                    options.sort(key=lambda n: (
                        self.blocks.id_of(n) == here_cid,
                        manhattan(n, exit_cell), n))
        if options:
            self.goal = min(options, key=lambda c: manhattan(c, self.home))
            if (self.policy in V3_AUCTION_POLICIES
                    and not self.circulation.enabled
                    and self._controlled_block(here) is not None):
                self.goal = options[0]
            self._record_decision(
                t, "IDLE_VACATE", "Clearing a lane for an active peer",
                from_cell=list(here), to_cell=list(self.goal),
                requesting_robots=sorted(p.rid for p in blockers_requesting_clearance))
            self._replan(t, here)

    def _route_crosses_radio_dead_zone(self, route: list[Cell]) -> bool:
        """Whether any route cell centre lies inside a configured radio hole."""
        return any(
            (cell[0] + 0.5 - zone_x) ** 2
            + (cell[1] + 0.5 - zone_y) ** 2 <= radius ** 2
            for cell in route
            for zone_x, zone_y, radius in self.cfg.net.dead_zones
        )

    def _v6_remote_idle_vacate_allowed(self, route: list[Cell]) -> bool:
        """Keep parking traffic bounded under a stable geographic partition."""
        if self.cfg.net.loss > 0.0 or not self.cfg.net.dead_zones:
            return True
        unfinished = sum(
            task.tid not in self.completed_tasks
            for task in self.open_tasks.values())
        # When unfinished work already exceeds mapped parking capacity, sending an
        # idle chassis across the warehouse injects another route into the busiest
        # phase. Clear one adjacent cell instead. Under lighter load a remote dock is
        # useful, provided its route never enters the radio hole.
        if unfinished > max(1, len(self.env.docks)):
            return False
        return not self._route_crosses_radio_dead_zone(route)

    def _run_auction(self, t: float, sensors: Sensors,
                     outbox: list[msg.Message]) -> None:
        """Run one deterministic, single-task auction without an auctioneer.

        Every robot sees the same task epoch and deadline, records the bids it has
        heard, and applies the same ``(cost, robot_id)`` ordering. A lease makes a
        missing winner or a crashed winner recoverable; a network partition may create
        temporary duplicate winners, but the higher epoch and deterministic claim
        ordering converge when the partition heals.
        """
        if self.policy in V3_AUCTION_POLICIES:
            self._run_v3_batch_auction(t, sensors, outbox)
            return

        available = []
        for task in self.open_tasks.values():
            if task.tid in self.completed_tasks:
                continue
            claim = self._task_claims.get(task.tid)
            if claim is not None and claim[3] > t:
                continue
            if self.task is not None and self.task.tid == task.tid:
                continue
            available.append(task)
        if not available:
            return

        target = min(available, key=lambda k: (k.announced_t, k.tid))
        if target.bid_deadline <= 0.0:
            target.bid_deadline = t + self.cfg.traffic.auction_bid_window_s

        opened = self._bid_opened.get(target.tid)
        if opened is None:
            self._bid_opened[target.tid] = t
            if (self.policy in ENERGY_AUCTION_POLICIES
                    and not self._energy_feasible(target, sensors, t=t)[0]):
                self.stats["energy_bids_suppressed"] += 1
                return
            cost = self._bid_cost(target, sensors)
            self._bids.setdefault(target.tid, {})[
                (target.auction_epoch, self.rid)] = cost
            self._bid_seen_t[(target.tid, target.auction_epoch, self.rid)] = t
            outbox.append(msg.bid(
                self.rid, self._next_seq(), t, target.tid, cost,
                epoch=target.auction_epoch))
            self.stats["auction_bids_sent"] += 1
            return

        if t < target.bid_deadline:
            return

        bids = [
            (cost, rid)
            for (epoch, rid), cost in self._bids.get(target.tid, {}).items()
            if epoch == target.auction_epoch
        ]
        if not bids:
            self._restart_auction(target, t)
            return

        winner_cost, winner = min(bids, key=lambda item: (item[0], item[1]))
        lease_until = t + self._task_lease_duration(target)
        claim = (target.auction_epoch, winner_cost, winner, lease_until)
        self._record_task_claim(target.tid, claim)
        self._bid_opened.pop(target.tid, None)

        if winner == self.rid:
            self._accept_task(t, target, sensors.cell,
                              lease_until=lease_until,
                              bid_cost=winner_cost)
            outbox.append(msg.award(
                self.rid, self._next_seq(), t, target.tid, winner_cost,
                epoch=target.auction_epoch, lease_until=lease_until))

    def _run_v3_batch_auction(self, t: float, sensors: Sensors,
                              outbox: list[msg.Message]) -> None:
        """Allocate a congestion-safe batch using replicated peer bids.

        There is no auctioneer. Every ordinary-auction AMR remains idle-only. In the
        experimental ``auction_bundle`` mode, a busy AMR with an empty future slot may
        advertise exactly one bundle-version-bound future bid. Every participant runs the
        same deterministic
        greedy matching over the bid messages it heard. A robot may win at most one
        task per round and each physical drop cell admits only a bounded number of
        active tasks. Awards remain expiring peer claims, so incomplete views converge
        after communication resumes rather than requiring a coordinator.
        """
        # The WMS only announces work. This method is reached through `_task_loop`,
        # but the guard is explicit so no direct caller can make a working or charging
        # robot participate in another auction.
        busy_future = (
            self._future_allocation_enabled()
            and self.task is not None
            and self.state not in (ST_IDLE, ST_CHARGING)
            and self.future_task is None
            and (self._future_bid is None
                 or self._v3_round_started is not None)
            and t >= self._future_retry_after
            and self._future_network_healthy(t)
        )
        if self.task is not None and not busy_future:
            return
        if self.task is None and self.state != ST_IDLE:
            return
        if self.policy in ENERGY_AUCTION_POLICIES and t < self._energy_retry_after:
            return
        available = [
            task for task in self.open_tasks.values()
            if task.tid not in self.completed_tasks
            and not (self._task_claims.get(task.tid)
                     and self._task_claims[task.tid][3] > t)
        ]
        if not available:
            self._v3_round_started = None
            self._remote_winner_since = None
            self._remote_winner_fingerprint = None
            if self._future_bid is not None:
                self._future_bid = None
                self._future_generation = (
                    self._future_generation + 1) % msg.MAX_AUCTION_EPOCH
            return

        if self._v3_round_started is None:
            self._v3_round_started = t
            if busy_future:
                started = time.perf_counter()
                ranked_future = []
                for task in available:
                    self.stats["future_candidates_evaluated"] += 1
                    feasible, _required, _reserve, _reason = (
                        self._future_sequence_feasible(task, sensors, t))
                    if not feasible:
                        continue
                    future_cost = self._future_bid_cost(task, sensors, t)
                    idle_cost = self._best_fresh_idle_cost(task, t)
                    threshold = max(
                        0.0, self.cfg.traffic.bundle_reassignment_threshold)
                    if (idle_cost is not None
                            and future_cost >= idle_cost * (1.0 - threshold)):
                        self.stats["future_hysteresis_prevented"] += 1
                        continue
                    ranked_future.append((
                        self._task_urgency(task, t),
                        future_cost,
                        task.tid, task))
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                self.allocation_compute_ms.append(elapsed_ms)
                if len(self.allocation_compute_ms) > 2048:
                    del self.allocation_compute_ms[:-2048]
                if not ranked_future:
                    self._v3_round_started = None
                    self._future_retry_after = (
                        t + self.cfg.traffic.bundle_bid_retry_s)
                    return
                _urgency, cost, _tid, task = min(
                    ranked_future, key=lambda item: (item[0], item[1], item[2]))
                context = (
                    self.task.tid, self.task.auction_epoch,
                    self._future_generation)
                key = (task.auction_epoch, self.rid)
                self._bids.setdefault(task.tid, {})[key] = cost
                self._bid_seen_t[(task.tid, task.auction_epoch, self.rid)] = t
                self._future_bid_contexts[
                    (task.tid, task.auction_epoch, self.rid)] = context
                self._future_bid = (
                    task.tid, task.auction_epoch,
                    context[0], context[1], context[2], cost)
                outbox.append(msg.bid(
                    self.rid, self._next_seq(), t, task.tid, cost,
                    epoch=task.auction_epoch,
                    active_task=context[0], active_epoch=context[1],
                    bundle_version=context[2]))
                self.stats["auction_bids_sent"] += 1
                self.stats["future_bids_sent"] += 1
                return
            eligible = []
            for task in available:
                if self.policy in ENERGY_AUCTION_POLICIES:
                    feasible, _required, _reserve = self._energy_feasible(
                        task, sensors, t=t)
                    if (not feasible
                            or not self._energy_candidate(task, t, sensors)):
                        self.stats["energy_bids_suppressed"] += 1
                        continue
                eligible.append((
                    self._task_urgency(task, t),
                    self._v3_bid_cost(task, sensors), task.tid, task))
            ranked = sorted(eligible, key=lambda item: (item[0], item[1], item[2]))
            if not ranked and self.policy in ENERGY_AUCTION_POLICIES:
                self.stats["energy_no_eligible_rounds"] += 1
                self._energy_retry_after = t + max(
                    5.0, 2.0 * self.cfg.traffic.auction_bid_window_s)
                self._v3_round_started = None
                return
            limit = (min(len(ranked),
                         max(1, self.cfg.traffic.energy_bid_bundle))
                     if self.policy in ENERGY_AUCTION_POLICIES
                     else max(1, self.cfg.traffic.auction_batch_bids))
            for _urgency, cost, _tid, task in ranked[:limit]:
                key = (task.auction_epoch, self.rid)
                self._bids.setdefault(task.tid, {})[key] = cost
                self._bid_seen_t[(task.tid, task.auction_epoch, self.rid)] = t
                if (self.policy != POLICY_BIOS_PIBT_V6
                        or self._v6_should_broadcast_bid(t, task, cost)):
                    outbox.append(msg.bid(
                        self.rid, self._next_seq(), t, task.tid, cost,
                        epoch=task.auction_epoch))
                    self.stats["auction_bids_sent"] += 1
            return

        if t - self._v3_round_started < self.cfg.traffic.auction_bid_window_s:
            return

        # Claims for in-flight work consume the destination's physical service slot.
        drop_load: dict[Cell, int] = {}
        corridor_phase: dict[int, Cell | None] = {}
        corridor_load: dict[int, int] = {}
        corridor_anchor: dict[int, str] = {}
        corridor_allowed_tasks: dict[int, set[str]] = {}
        for tid, claim in self._task_claims.items():
            task = self.open_tasks.get(tid)
            if task is not None and claim[3] > t:
                drop_load[task.drop] = drop_load.get(task.drop, 0) + 1
                for cid, entry in self._task_corridor_directions(task).items():
                    previous = corridor_phase.get(cid, entry)
                    # A mixed phase can only be inherited from an older/incomplete
                    # view. Admit nothing else until those leases expire or finish.
                    corridor_phase[cid] = entry if previous == entry else None
                    corridor_load[cid] = corridor_load.get(cid, 0) + 1

        # Every bidirectional block has a deterministic bounded wave. Active claims
        # above keep an admitted batch in its direction until those robots finish.
        # Unclaimed membership must NOT be persisted: under loss, peers can first see
        # different completion subsets and cache opposite waves forever even after
        # their completion catalogs converge. Re-deriving members from the canonical
        # smallest unfinished task makes that disagreement self-healing.
        tasks_by_corridor: dict[int, list[tuple[str, Cell]]] = {}
        for task in sorted(self.open_tasks.values(), key=lambda item: item.tid):
            if task.tid in self.completed_tasks:
                continue
            for cid, entry in self._task_corridor_directions(task).items():
                tasks_by_corridor.setdefault(cid, []).append((task.tid, entry))
        corridor_capacity = max(1, self.cfg.traffic.auction_corridor_capacity)
        for cid, options in tasks_by_corridor.items():
            unfinished = [
                (tid, direction) for tid, direction in options
                if tid not in self.completed_tasks
            ]
            if not unfinished:
                self._v3_corridor_waves.pop(cid, None)
                continue
            _anchor_tid, entry = unfinished[0]
            members = tuple(
                tid for tid, direction in unfinished if direction == entry
            )[:corridor_capacity]
            wave = (entry, members)
            self._v3_corridor_waves[cid] = wave
            entry, members = wave
            if cid in corridor_phase and corridor_phase[cid] != entry:
                corridor_phase[cid] = None
            else:
                corridor_phase[cid] = entry
            unfinished_members = [
                tid for tid in members if tid not in self.completed_tasks
            ]
            if unfinished_members:
                corridor_anchor[cid] = unfinished_members[0]
            corridor_allowed_tasks[cid] = set(members)

        available_by_id = {task.tid: task for task in available}
        freshness_s = (
            self.cfg.traffic.v6_bid_cache_s
            if self.policy == POLICY_BIOS_PIBT_V6
            else self.cfg.traffic.auction_bid_window_s
        )
        fresh_after = self._v3_round_started - freshness_s
        candidates: list[tuple[float, str, str, int]] = []
        for tid, task in available_by_id.items():
            for (epoch, rid), cost in self._bids.get(tid, {}).items():
                if epoch != task.auction_epoch:
                    continue
                if self._bid_seen_t.get((tid, epoch, rid), -1e9) < fresh_after:
                    continue
                future_context = self._future_bid_contexts.get((tid, epoch, rid))
                if rid != self.rid:
                    peer = self.peers.get(rid)
                    if peer is None or t - peer.last_seen > self._peer_stale_after_s():
                        continue
                    if future_context is None:
                        if peer.state != ST_IDLE or peer.goal is not None:
                            continue
                    elif (not self._future_allocation_enabled()
                          or peer.state in (ST_IDLE, ST_CHARGING)
                          or peer.task_id != future_context[0]):
                        continue
                elif future_context is None:
                    if self.task is not None or self.state != ST_IDLE:
                        continue
                elif (self.task is None or self.future_task is not None
                      or self.task.tid != future_context[0]
                      or self.task.auction_epoch != future_context[1]
                      or self._future_generation != future_context[2]):
                    continue
                candidates.append((cost, tid, rid, epoch))

        used_robots: set[str] = set()
        used_tasks: set[str] = set()
        assignments: list[tuple[str, str, float, int]] = []
        capacity = max(1, self.cfg.traffic.auction_drop_capacity)
        anchor_tasks = set(corridor_anchor.values())

        # Contract-net winner per task. Do not cascade a task to its second-best
        # bidder merely because the best bidder also won another task: asynchronous
        # peers can then build different reassignment chains from the same messages.
        # A robot takes at most one task it actually won; the rest return in the next
        # short auction round after that winner advertises itself busy.
        best_by_task: dict[str, tuple[float, str, str, int]] = {}
        for candidate in candidates:
            cost, tid, rid, epoch = candidate
            current = best_by_task.get(tid)
            if current is None or (cost, rid) < (current[0], current[2]):
                best_by_task[tid] = candidate
        matching_candidates = (candidates if self.circulation.enabled
                               else best_by_task.values())
        ordered_candidates = sorted(
            matching_candidates,
            key=lambda item: (
                item[1] not in anchor_tasks,
                self._task_urgency(available_by_id[item[1]], t),
                item[0], item[1], item[2], item[3]))
        for cost, tid, rid, epoch in ordered_candidates:
            if rid in used_robots or tid in used_tasks:
                continue
            task = available_by_id[tid]
            if drop_load.get(task.drop, 0) >= capacity:
                continue
            directions = self._task_corridor_directions(task)
            if any(tid not in corridor_allowed_tasks.get(cid, {tid})
                   for cid in directions):
                continue
            bid_context = self._future_bid_contexts.get((tid, epoch, rid))
            if bid_context is not None:
                active_record = self.open_tasks.get(bid_context[0])
                if active_record is None:
                    continue
                robot_cell = active_record.drop
            else:
                robot_cell = (sensors.cell if rid == self.rid
                              else self.peers[rid].cell)
            if self._approach_crosses_task_corridor(task, robot_cell):
                # Do not send an AMR through the admitted wave empty merely to reach
                # a pickup on the far side.  BIOS 5 handles the all-robots-stranded
                # case below as a separate, idle repositioning phase; awarding first
                # would make the empty approach oppose the task's loaded direction.
                continue
            if any(
                cid in corridor_phase and corridor_phase[cid] != entry
                for cid, entry in directions.items()
            ):
                continue
            if any(corridor_load.get(cid, 0) >= corridor_capacity
                   for cid in directions):
                continue
            used_robots.add(rid)
            used_tasks.add(tid)
            drop_load[task.drop] = drop_load.get(task.drop, 0) + 1
            for cid, entry in directions.items():
                corridor_phase.setdefault(cid, entry)
                corridor_load[cid] = corridor_load.get(cid, 0) + 1
            assignments.append((rid, tid, cost, epoch))

        won: tuple[str, float, int, float, tuple[str, int, int] | None] | None = None
        for rid, tid, cost, epoch in assignments:
            task_record = self.open_tasks.get(tid)
            lease_until = t + (
                self._task_lease_duration(task_record)
                if task_record is not None
                else self.cfg.traffic.auction_lease_s)
            if self.circulation.enabled:
                # On the strongly connected one-way graph, replicated greedy
                # matching fills the batch in one round. Recording its remote slots
                # supplies immediate station backpressure; the actual owner's AWARD
                # refreshes the lease, and a disagreement expires harmlessly.
                self._record_task_claim(tid, (epoch, cost, rid, lease_until))
            if rid == self.rid:
                won = (
                    tid, cost, epoch, lease_until,
                    self._future_bid_contexts.get((tid, epoch, rid)))

        nominate_remote = (
            self.policy == POLICY_BIOS_PIBT_V6
            and not self.circulation.enabled
            and won is None and bool(assignments)
        )
        if nominate_remote:
            fingerprint = tuple(sorted(
                (tid, epoch, rid, round(cost, 6))
                for rid, tid, cost, epoch in assignments
            ))
            if (self._remote_winner_fingerprint != fingerprint
                    or self._remote_winner_since is None):
                self._remote_winner_fingerprint = fingerprint
                self._remote_winner_since = t
            elif (t - self._remote_winner_since
                    >= self.cfg.traffic.auction_lease_s):
                # Independent auction windows need not close on the same control
                # tick. If every local view names another robot for a full lease, no
                # self-award exists and all robots can remain idle forever. Publish
                # the nominations only after that condition persists. Receivers still
                # apply the epoch/cost/ID total order; the WMS selects nobody.
                for rid, tid, cost, epoch in assignments:
                    task_record = self.open_tasks.get(tid)
                    nomination_until = t + (
                        self._task_lease_duration(task_record)
                        if task_record is not None
                        else self.cfg.traffic.auction_lease_s)
                    self._record_task_claim(
                        tid, (epoch, cost, rid, nomination_until))
                    context = self._future_bid_contexts.get((tid, epoch, rid))
                    outbox.append(msg.award(
                        self.rid, self._next_seq(), t, tid, cost,
                        epoch=epoch, lease_until=nomination_until,
                        winner=rid,
                        active_task=context[0] if context else None,
                        active_epoch=context[1] if context else 0,
                        bundle_version=context[2] if context else 0))
                self._remote_winner_since = t
        else:
            self._remote_winner_since = None
            self._remote_winner_fingerprint = None

        self._v3_round_started = None
        if won is None:
            if busy_future and self._future_bid is not None:
                self.stats["future_bids_lost"] += 1
                self._future_bid = None
                self._future_generation = (
                    self._future_generation + 1) % msg.MAX_AUCTION_EPOCH
                self._future_retry_after = (
                    t + self.cfg.traffic.bundle_bid_retry_s)
            if self.policy in ENERGY_AUCTION_POLICIES and not assignments:
                self._v5_reposition_stranded_bidder(
                    t, sensors, available_by_id, candidates, best_by_task,
                    corridor_load, corridor_allowed_tasks, anchor_tasks)
            return
        tid, cost, epoch, lease_until, future_context = won
        task = self.open_tasks.get(tid)
        if task is None:
            return
        if future_context is not None:
            if not self._future_sequence_feasible(task, sensors, t)[0] \
                    or not self._reserve_future(
                        t, task, cost, lease_until, future_context):
                self.stats["stale_future_awards_rejected"] += 1
                self._future_bid = None
                self._future_generation = (
                    self._future_generation + 1) % msg.MAX_AUCTION_EPOCH
                return
        else:
            self._accept_task(t, task, sensors.cell,
                              lease_until=lease_until, bid_cost=cost)
        outbox.append(msg.award(
            self.rid, self._next_seq(), t, tid, cost,
            epoch=epoch, lease_until=lease_until,
            active_task=future_context[0] if future_context else None,
            active_epoch=future_context[1] if future_context else 0,
            bundle_version=future_context[2] if future_context else 0))

    def _v5_reposition_stranded_bidder(
            self, t: float, sensors: Sensors, available: dict[str, Task],
            candidates: list[tuple[float, str, str, int]],
            best_by_task: dict[str, tuple[float, str, str, int]],
            corridor_load: dict[int, int],
            corridor_allowed_tasks: dict[int, set[str]],
            anchor_tasks: set[str]) -> bool:
        """Move one idle winner to a stranded pickup before awarding cargo work.

        If every fresh bidder for a task is on the wrong side of its loaded corridor,
        the normal auction correctly refuses to award it.  Once the corridor is empty,
        the same deterministic best bid elects one AMR to cross *while still idle*.
        It bids again only after reaching the pickup side.  Block leases continue to
        serialize imperfect views, so this remains peer-only and partition tolerant.
        """
        by_task: dict[str, list[tuple[float, str, str, int]]] = {}
        for candidate in candidates:
            by_task.setdefault(candidate[1], []).append(candidate)
        # Never launch speculative motion while a visible peer still owns motion.
        # The age guard spans two claim leases, so a just-announced batch has time to
        # form and circulate even when this receiver missed some of its first bids.
        if any(
            t - peer.last_seen <= self._peer_stale_after_s()
            and (peer.state != ST_IDLE or peer.goal is not None)
            for peer in self.peers.values()
        ):
            return False
        settle_s = 2.0 * self.cfg.traffic.auction_lease_s
        ordered_tasks = sorted(
            available.values(),
            key=lambda task: (
                task.tid not in anchor_tasks,
                self._task_urgency(task, t), task.tid))
        for task in ordered_tasks:
            if t - task.announced_t < settle_s:
                continue
            directions = self._task_corridor_directions(task)
            if not directions:
                continue
            if any(task.tid not in corridor_allowed_tasks.get(cid, {task.tid})
                   for cid in directions):
                continue
            if any(corridor_load.get(cid, 0) > 0 for cid in directions):
                continue
            task_candidates = by_task.get(task.tid, [])
            if not task_candidates:
                continue
            if any(
                not self._approach_crosses_task_corridor(
                    task,
                    sensors.cell if rid == self.rid else self.peers[rid].cell)
                for _cost, _tid, rid, _epoch in task_candidates
            ):
                continue
            elected = best_by_task.get(task.tid)
            if elected is None:
                continue
            if elected[2] == self.rid:
                self._auction_reposition_target = task.pick
                self.goal = task.pick
                self.state = ST_IDLE
                self._replan(t, sensors.cell)
                return True
            # Every peer evaluates the same highest-ranked serviceable task before
            # considering another corridor, limiting speculative empty motion.
            return False
        return False

    def _bid_cost(self, task: Task, sensors: Sensors) -> float:
        """Estimate total local work using the same A* model as navigation."""
        edge_cost = self._v6_edge_costs(sensors.t)
        to_pick = astar(
            self.env, sensors.cell, task.pick, extra_cost=self.penalty,
            edge_cost=edge_cost)
        to_drop = astar(
            self.env, task.pick, task.drop, extra_cost=self.penalty,
            edge_cost=edge_cost)
        if not to_pick or not to_drop:
            return 1e9
        distance = float(
            max(0, len(to_pick) - 1) + max(0, len(to_drop) - 1))
        if edge_cost:
            distance += sum(
                edge_cost.get((a, b), 0.0)
                for path in (to_pick, to_drop)
                for a, b in zip(path, path[1:]))
        battery_penalty = max(0.0, 0.25 - sensors.battery_frac) * 20.0
        handling_cells = 0.0
        if self.policy in ENERGY_AUCTION_POLICIES:
            cargo_factor = self._cargo_factor(task.cargo_type)
            if cargo_factor is None:
                return 1e9
            cruise_mps = max(0.1, 0.65 * self.cfg.robot.v_max)
            handling_cells = (
                self.cfg.traffic.energy_service_s * cargo_factor
                * cruise_mps / self.cfg.cell_m)
        return float(distance) + handling_cells + battery_penalty

    def _task_urgency(self, task: Task, t: float) -> tuple:
        """Stable priority/deadline order shared by every auction participant.

        Priority is the business rule and the earliest hard deadline breaks equal
        priorities. Cargo does not alter urgency; it changes feasibility and cost.
        No receiver-local arrival timestamp participates in this order.
        """
        return (
            -max(1, int(task.priority)),
            task.deadline is None,
            task.deadline if task.deadline is not None else math.inf,
        )

    def _cargo_factor(self, cargo_type: str) -> float | None:
        traffic = self.cfg.traffic
        return {
            "normal": traffic.cargo_normal_factor,
            "fragile": traffic.cargo_fragile_factor,
            "heavy": traffic.cargo_heavy_factor,
            "hazardous": traffic.cargo_hazardous_factor,
        }.get(cargo_type)

    def _energy_feasible(self, task: Task,
                         sensors: Sensors, t: float = 0.0
                         ) -> tuple[bool, float, float]:
        """Predict full-commitment energy and the reserve left after the nearest dock.

        This is the complete BIOS 5 eligibility gate: path, identical-model payload
        capacity, cargo-adjusted task energy, return-to-dock reserve and hard delivery
        deadline. Caller state separately guarantees that only an idle robot bids.
        """
        if (task.cargo_weight < 0.0
                or task.cargo_weight > self.cfg.robot.max_payload_kg):
            return False, 1.0, -1.0
        estimate = self._task_estimate(
            task, sensors.cell, extra_cost=self.penalty,
            edge_cost=self._v6_edge_costs(t))
        if estimate is None:
            return False, 1.0, -1.0
        required, completion_s = estimate
        projected_reserve = sensors.battery_frac - required
        battery_ok = projected_reserve >= self.cfg.traffic.energy_reserve_frac
        deadline_ok = (task.deadline is None
                       or t + completion_s <= task.deadline)
        return battery_ok and deadline_ok, required, projected_reserve

    def _energy_required(self, task: Task, start: Cell,
                         extra_cost: dict[Cell, float] | None = None) -> float | None:
        """Return predicted battery fraction for task completion plus docking."""
        estimate = self._task_estimate(task, start, extra_cost=extra_cost)
        return None if estimate is None else estimate[0]

    def _task_estimate(self, task: Task, start: Cell,
                       extra_cost: dict[Cell, float] | None = None,
                       edge_cost: dict[tuple[Cell, Cell], float] | None = None,
                       ) -> tuple[float, float] | None:
        """Return ``(battery fraction through docking, seconds through drop)``."""
        cache_key = (
            start, task.tid, task.pick, task.drop,
            task.cargo_type, float(task.cargo_weight))
        cacheable = extra_cost is None and not edge_cost
        if cacheable and cache_key in self._energy_required_cache:
            return self._energy_required_cache[cache_key]

        cargo_factor = self._cargo_factor(task.cargo_type)
        if cargo_factor is None or task.cargo_weight < 0.0:
            if cacheable:
                self._energy_required_cache[cache_key] = None
            return None
        to_pick = astar(
            self.env, start, task.pick, extra_cost=extra_cost,
            edge_cost=edge_cost)
        to_drop = astar(
            self.env, task.pick, task.drop, extra_cost=extra_cost,
            edge_cost=edge_cost)
        if not to_pick or not to_drop:
            if cacheable:
                self._energy_required_cache[cache_key] = None
            return None
        charger_cells = []
        for dock in self.env.docks:
            path = astar(self.env, task.drop, dock)
            if path:
                charger_cells.append(max(0, len(path) - 1))
        charger_steps = min(charger_cells) if charger_cells else 0
        loaded_steps = max(0, len(to_drop) - 1)
        spec = self.cfg.robot
        traffic = self.cfg.traffic
        cruise_mps = max(0.1, 0.65 * spec.v_max)
        approach_steps = max(0, len(to_pick) - 1)
        approach_s = approach_steps * self.cfg.cell_m / cruise_mps
        dock_s = charger_steps * self.cfg.cell_m / cruise_mps
        loaded_s = loaded_steps * self.cfg.cell_m / cruise_mps
        handling_s = traffic.energy_service_s * cargo_factor
        weight_factor = 1.0 + traffic.cargo_full_payload_energy_premium * (
            task.cargo_weight / max(1e-9, spec.max_payload_kg))
        # Cargo modifies the committed task motion (approach + loaded leg), while the
        # post-drop trip to a charger is correctly unladen.
        energy_wh = (
            spec.draw_move_w
            * (approach_s + traffic.energy_loaded_multiplier * loaded_s)
            * cargo_factor * weight_factor / 3600.0
            + spec.draw_move_w * dock_s / 3600.0
            + spec.draw_idle_w * handling_s / 3600.0
        )
        required = energy_wh / spec.battery_full_wh
        required *= 1.0 + traffic.energy_uncertainty_frac
        result = (required, approach_s + loaded_s + handling_s)
        if cacheable:
            self._energy_required_cache[cache_key] = result
        return result

    def _future_sequence_estimate(
            self, future: Task, sensors: Sensors, t: float
            ) -> tuple[float, float, float] | None:
        """Return energy, active completion, and future completion for ACTIVE -> FUTURE."""
        active = self.task
        if active is None or future.tid == active.tid:
            return None
        spec = self.cfg.robot
        traffic = self.cfg.traffic
        if any(task.cargo_weight < 0.0
               or task.cargo_weight > spec.max_payload_kg
               for task in (active, future)):
            return None
        active_factor = self._cargo_factor(active.cargo_type)
        future_factor = self._cargo_factor(future.cargo_type)
        if active_factor is None or future_factor is None:
            return None

        edge_cost = self._v6_edge_costs(t)

        def path(a: Cell, b: Cell, *, learned: bool = True) -> list[Cell]:
            return astar(
                self.env, a, b, extra_cost=self.penalty,
                edge_cost=edge_cost if learned else None)

        if self.state == ST_TO_DROP or self.goal == active.drop:
            active_approach_steps = 0
            active_loaded = path(sensors.cell, active.drop)
            active_handling_s = 0.0
        else:
            active_approach = path(sensors.cell, active.pick)
            active_loaded = path(active.pick, active.drop)
            if not active_approach:
                return None
            active_approach_steps = max(0, len(active_approach) - 1)
            active_handling_s = traffic.energy_service_s * active_factor
        transition = path(active.drop, future.pick)
        future_loaded = path(future.pick, future.drop)
        if not active_loaded or not transition or not future_loaded:
            return None

        charger_paths = [
            path(future.drop, dock, learned=False) for dock in self.env.docks
        ]
        charger_steps = [max(0, len(route) - 1)
                         for route in charger_paths if route]
        if not self.env.docks or not charger_steps:
            return None

        active_loaded_steps = max(0, len(active_loaded) - 1)
        transition_steps = max(0, len(transition) - 1)
        future_loaded_steps = max(0, len(future_loaded) - 1)
        charger_step_count = min(charger_steps) if charger_steps else 0
        cruise_mps = max(0.1, 0.65 * spec.v_max)
        seconds_per_step = self.cfg.cell_m / cruise_mps
        active_approach_s = active_approach_steps * seconds_per_step
        active_loaded_s = active_loaded_steps * seconds_per_step
        transition_s = transition_steps * seconds_per_step
        future_loaded_s = future_loaded_steps * seconds_per_step
        charger_s = charger_step_count * seconds_per_step
        future_handling_s = traffic.energy_service_s * future_factor
        active_completion_s = (
            active_approach_s + active_loaded_s + active_handling_s)
        future_completion_s = (
            active_completion_s + transition_s
            + future_loaded_s + future_handling_s)

        active_weight = 1.0 + traffic.cargo_full_payload_energy_premium * (
            active.cargo_weight / max(1e-9, spec.max_payload_kg))
        future_weight = 1.0 + traffic.cargo_full_payload_energy_premium * (
            future.cargo_weight / max(1e-9, spec.max_payload_kg))
        energy_wh = (
            spec.draw_move_w
            * (active_approach_s
               + traffic.energy_loaded_multiplier * active_loaded_s)
            * active_factor * active_weight / 3600.0
            + spec.draw_idle_w * active_handling_s / 3600.0
            + spec.draw_move_w
            * (transition_s
               + traffic.energy_loaded_multiplier * future_loaded_s)
            * future_factor * future_weight / 3600.0
            + spec.draw_idle_w * future_handling_s / 3600.0
            + spec.draw_move_w * charger_s / 3600.0
        )
        required = energy_wh / spec.battery_full_wh
        required *= 1.0 + traffic.energy_uncertainty_frac
        return required, active_completion_s, future_completion_s

    def _future_sequence_feasible(
            self, future: Task, sensors: Sensors, t: float
            ) -> tuple[bool, float, float, str]:
        estimate = self._future_sequence_estimate(future, sensors, t)
        if estimate is None:
            self.stats["future_charger_rejections"] += 1
            return False, 1.0, -1.0, "path_or_charger"
        required, active_completion_s, future_completion_s = estimate
        reserve = sensors.battery_frac - required
        if reserve < self.cfg.traffic.energy_reserve_frac:
            self.stats["future_energy_rejections"] += 1
            return False, required, reserve, "energy"
        active = self.task
        if (active is not None and active.deadline is not None
                and t + active_completion_s > active.deadline):
            self.stats["future_deadline_rejections"] += 1
            return False, required, reserve, "active_deadline"
        if (future.deadline is not None
                and t + future_completion_s > future.deadline):
            self.stats["future_deadline_rejections"] += 1
            return False, required, reserve, "future_deadline"
        return True, required, reserve, "ok"

    def _future_bid_cost(self, future: Task, sensors: Sensors, t: float) -> float:
        estimate = self._future_sequence_estimate(future, sensors, t)
        if estimate is None:
            return 1e9
        required, _active_completion_s, future_completion_s = estimate
        cruise_mps = max(0.1, 0.65 * self.cfg.robot.v_max)
        equivalent_cells = future_completion_s * cruise_mps / self.cfg.cell_m
        deadline_risk = 0.0
        if future.deadline is not None:
            slack = max(0.0, future.deadline - (t + future_completion_s))
            deadline_risk = min(30.0, 30.0 / max(1.0, slack))
        reserve = sensors.battery_frac - required
        reserve_slack = max(
            0.0, reserve - self.cfg.traffic.energy_reserve_frac)
        reserve_risk = max(0.0, 0.20 - reserve_slack) * 10.0
        return float(
            equivalent_cells
            + self.cfg.traffic.bundle_energy_weight * required * 100.0
            + self.cfg.traffic.bundle_deadline_risk_weight * deadline_risk
            + self.cfg.traffic.bundle_reserve_risk_weight * reserve_risk)

    def _best_fresh_idle_cost(self, task: Task, t: float) -> float | None:
        """Comparable lower bid from a currently idle peer, if one is visible."""
        edge_cost = self._v6_edge_costs(t)
        best = math.inf
        for peer in self.peers.values():
            if (t - peer.last_seen > self._peer_stale_after_s()
                    or peer.state != ST_IDLE or peer.goal is not None
                    or peer.battery_frac
                    < self.cfg.traffic.energy_charge_trigger_frac):
                continue
            estimate = self._task_estimate(
                task, peer.cell, extra_cost=self.penalty,
                edge_cost=edge_cost)
            if estimate is None:
                continue
            required, completion_s = estimate
            if (peer.battery_frac - required
                    < self.cfg.traffic.energy_reserve_frac):
                continue
            if task.deadline is not None and t + completion_s > task.deadline:
                continue
            cruise_mps = max(0.1, 0.65 * self.cfg.robot.v_max)
            equivalent_cells = completion_s * cruise_mps / self.cfg.cell_m
            battery_penalty = max(0.0, 0.25 - peer.battery_frac) * 20.0
            best = min(best, equivalent_cells + battery_penalty)
        return None if math.isinf(best) else float(best)

    def _reserve_future(
            self, t: float, task: Task, cost: float, lease_until: float,
            context: tuple[str, int, int]) -> bool:
        if (not self._future_allocation_enabled() or self.task is None
                or self.future_task is not None):
            self.stats["future_capacity_rejections"] += 1
            return False
        expected = (self.task.tid, self.task.auction_epoch,
                    self._future_generation)
        if context != expected:
            self.stats["future_version_mismatches"] += 1
            return False
        if not self._record_task_claim(
                task.tid,
                (task.auction_epoch, cost, self.rid, lease_until)):
            self.stats["stale_future_awards_rejected"] += 1
            return False
        self.future_task = task
        self._future_context = context
        self._future_bid = None
        self._future_needs_reconcile = False
        self.stats["future_bids_won"] += 1
        self._record_decision(
            t, "FUTURE_RESERVED",
            f"Reserved {task.tid} after {context[0]}",
            active=context[0], future=task.tid,
            bundle_version=context[2], bid_cost=round(cost, 3))
        return True

    def _release_future(self, t: float, outbox: list[msg.Message], reason: str,
                        *, reauction: bool = True) -> None:
        task = self.future_task
        if task is None:
            return
        claim = self._task_claims.get(task.tid)
        if claim is not None and claim[2] == self.rid:
            self._task_claims.pop(task.tid, None)
        self.future_task = None
        self._future_context = None
        self._future_bid = None
        self._future_generation = (self._future_generation + 1) % msg.MAX_AUCTION_EPOCH
        self._future_needs_reconcile = False
        self.stats["future_invalidations"] += 1
        if reauction:
            self._restart_auction(task, t)
            outbox.append(msg.task_new(
                self.rid, self._next_seq(), t, task.tid, task.pick, task.drop,
                epoch=task.auction_epoch,
                bid_until=t + self.cfg.traffic.auction_bid_window_s,
                cargo_type=task.cargo_type, cargo_weight=task.cargo_weight,
                priority=task.priority, deadline=task.deadline))
        self._record_decision(
            t, "FUTURE_RELEASED", f"Released {task.tid}: {reason}",
            future=task.tid, reason=reason)

    def _promote_future(
            self, t: float, sensors: Sensors, outbox: list[msg.Message]) -> bool:
        future = self.future_task
        context = self._future_context
        if future is None or context is None:
            return False
        claim = self._task_claims.get(future.tid)
        feasible, _required, _reserve = self._energy_feasible(
            future, sensors, t=t)
        valid = (
            not self._future_needs_reconcile
            and self._future_network_healthy(t)
            and future.tid in self.open_tasks
            and future.tid not in self.completed_tasks
            and claim is not None
            and claim[0] == future.auction_epoch
            and claim[2] == self.rid
            and claim[3] > t
            and feasible
        )
        if not valid:
            self.stats["future_promotion_failures"] += 1
            self._release_future(
                t, outbox, "promotion revalidation failed",
                reauction=(future.tid in self.open_tasks
                           and future.tid not in self.completed_tasks))
            return False
        lease_until = claim[3]
        cost = claim[1]
        self.future_task = None
        self._future_context = None
        self._future_generation = (self._future_generation + 1) % msg.MAX_AUCTION_EPOCH
        self._accept_task(
            t, future, sensors.cell,
            lease_until=lease_until, bid_cost=cost)
        self.stats["future_promotions"] += 1
        self._record_decision(
            t, "FUTURE_PROMOTED", f"Promoted {future.tid} to active",
            future=future.tid, previous_active=context[0])
        return True

    def _consume_future_nomination(
            self, t: float, sensors: Sensors, outbox: list[msg.Message]) -> None:
        if self.task is None or self.future_task is not None:
            return
        nomination = next(iter(sorted(self._peer_future_nominations.items())), None)
        if nomination is None:
            return
        tid, (epoch, cost, lease_until, active_tid, active_epoch, version) = nomination
        self._peer_future_nominations.pop(tid, None)
        task = self.open_tasks.get(tid)
        expected_bid = self._future_bid
        context = (active_tid, active_epoch, version)
        matching = (
            task is not None
            and expected_bid is not None
            and expected_bid[:5] == (
                tid, epoch, active_tid, active_epoch, version)
            and self._future_network_healthy(t)
            and self._future_sequence_feasible(task, sensors, t)[0]
            and lease_until > t
        )
        if not matching or not self._reserve_future(
                t, task, cost, lease_until, context):
            self.stats["stale_future_awards_rejected"] += 1
            self._future_generation = (
                self._future_generation + 1) % msg.MAX_AUCTION_EPOCH
            self._future_bid = None

    def _energy_candidate(self, task: Task, t: float, sensors: Sensors) -> bool:
        """Whether this robot is among the nearest healthy candidates for a task.

        This is a traffic optimization, not a safety rule. Missing/stale peers are
        omitted, which widens participation during loss instead of suppressing work.
        """
        candidates = [(manhattan(sensors.cell, task.pick), self.rid)]
        for peer in self.peers.values():
            if (t - peer.last_seen > self._peer_stale_after_s()
                    or peer.state != ST_IDLE or peer.goal is not None
                    or peer.battery_frac < self.cfg.traffic.energy_charge_trigger_frac):
                continue
            required = self._energy_required(task, peer.cell)
            if (required is None
                    or peer.battery_frac - required
                    < self.cfg.traffic.energy_reserve_frac):
                continue
            estimate = self._task_estimate(task, peer.cell)
            if (estimate is None
                    or (task.deadline is not None
                        and t + estimate[1] > task.deadline)):
                continue
            candidates.append((manhattan(peer.cell, task.pick), peer.rid))
        candidates.sort()
        # Busy, charging, failed and stale peers naturally leave this live set, so the
        # next feasible robot enters top-k without turning an old task into an open
        # auction that every robot fights over forever.
        count = max(1, self.cfg.traffic.energy_candidate_bids)
        return self.rid in {rid for _distance, rid in candidates[:count]}

    def _task_corridor_directions(self, task: Task) -> dict[int, Cell]:
        """Return each bidirectional block and the mouth used to enter it.

        Directed circulation maps already prevent opposing traffic structurally.  On
        maps with one unavoidable two-way chokepoint, however, task allocation must
        release work in directional waves; otherwise even a perfect block mutex forms
        two growing queues at opposite mouths.  The pickup-to-drop A* path supplies a
        deterministic direction without adding a coordinator or a new wire message.
        """
        if self.circulation.enabled:
            return {}
        path = astar(self.env, task.pick, task.drop)
        directions: dict[int, Cell] = {}
        for cell in path:
            cid = self._controlled_block(cell)
            if cid is None or cid in directions:
                continue
            entry = self.blocks.nearest_end(cid, cell)
            if entry is not None:
                directions[cid] = entry
        return directions

    def _v3_bid_cost(self, task: Task, sensors: Sensors) -> float:
        """Prefer an AMR already on the pickup side of every chokepoint.

        Sending a robot across an exclusive block empty and then immediately back
        loaded consumes two scarce traversals and opposes the task's admitted traffic
        phase.  It remains a finite penalty so a task is still serviceable if every
        surviving robot starts on the other side.
        """
        cost = self._bid_cost(task, sensors)
        task_directions = self._task_corridor_directions(task)
        if not task_directions or cost >= 1e9:
            return cost
        approach = astar(self.env, sensors.cell, task.pick,
                         extra_cost=self.penalty)
        crossed = {self._controlled_block(cell) for cell in approach}
        for cid in task_directions:
            if cid in crossed:
                cost += 20.0 + 4.0 * len(self.blocks.members[cid])
        return cost

    def _approach_crosses_task_corridor(self, task: Task, start: Cell) -> bool:
        """Whether reaching the pickup consumes a block used by the loaded trip."""
        task_blocks = set(self._task_corridor_directions(task))
        if not task_blocks:
            return False
        approach = astar(self.env, start, task.pick, extra_cost=self.penalty)
        return any(self._controlled_block(cell) in task_blocks
                   for cell in approach)

    def _record_task_claim(self, tid: str,
                           claim: tuple[int, float, str, float]) -> bool:
        old = self._task_claims.get(tid)
        if old is not None:
            same_owner = (old[0], old[1], old[2]) == (claim[0], claim[1], claim[2])
            if old[3] > claim[3] and not same_owner:
                return False
            if not same_owner and not self._claim_wins(claim, old):
                return False
            if (old[2] == self.rid and claim[2] != self.rid
                    and self.task is not None and self.task.tid == tid):
                self._drop_current_task()
                if self.future_task is not None:
                    self._task_claims.pop(self.future_task.tid, None)
                    self.future_task = None
                    self._future_context = None
                    self._future_bid = None
                    self._future_generation = (
                        self._future_generation + 1) % msg.MAX_AUCTION_EPOCH
                    self.stats["future_invalidations"] += 1
            if (old[2] == self.rid and claim[2] != self.rid
                    and self.future_task is not None
                    and self.future_task.tid == tid):
                self.future_task = None
                self._future_context = None
                self._future_bid = None
                self._future_generation = (
                    self._future_generation + 1) % msg.MAX_AUCTION_EPOCH
                self.stats["future_invalidations"] += 1
        self._task_claims[tid] = claim
        task = self.open_tasks.get(tid)
        if task is not None:
            task.lease_owner = claim[2]
            task.lease_until = claim[3]
        return True

    def _task_lease_duration(self, task: Task) -> float:
        """Return bounded per-task backoff for transient asymmetric loss."""
        base = self.cfg.traffic.auction_lease_s
        if (self.policy != POLICY_BIOS_PIBT_V6
                or self.cfg.net.loss <= 0.0
                or not self.cfg.net.dead_zones
                or task.auction_epoch < self.cfg.traffic.v6_churn_epoch):
            return base
        failed_epochs = task.auction_epoch - self.cfg.traffic.v6_churn_epoch + 1
        return min(
            self.cfg.traffic.v6_churn_lease_max_s,
            base + failed_epochs * self.cfg.traffic.v6_churn_lease_step_s,
        )

    @staticmethod
    def _claim_wins(new: tuple[int, float, str, float],
                    old: tuple[int, float, str, float]) -> bool:
        if new[0] != old[0]:
            return new[0] > old[0]
        return (new[1], new[2]) < (old[1], old[2])

    def _restart_auction(self, task: Task, t: float) -> None:
        task.auction_epoch = min(
            msg.MAX_AUCTION_EPOCH - 1, task.auction_epoch + 1)
        task.bid_deadline = t + self.cfg.traffic.auction_bid_window_s
        self._bid_opened.pop(task.tid, None)
        self._bids.pop(task.tid, None)
        self._task_claims.pop(task.tid, None)
        task.lease_owner = None
        task.lease_until = 0.0
        self._awarded.discard(task.tid)
        self._peer_nominations.pop(task.tid, None)
        self._peer_future_nominations.pop(task.tid, None)
        for key in [key for key in self._future_bid_contexts if key[0] == task.tid]:
            self._future_bid_contexts.pop(key, None)

    def _expire_task_claims(self, t: float) -> None:
        for tid, claim in list(self._task_claims.items()):
            if claim[3] > t:
                continue
            if self.task is not None and self.task.tid == tid \
                    and claim[2] == self.rid:
                self._drop_current_task()
            if (self.future_task is not None
                    and self.future_task.tid == tid
                    and claim[2] == self.rid):
                self.future_task = None
                self._future_context = None
                self._future_bid = None
                self._future_generation = (
                    self._future_generation + 1) % msg.MAX_AUCTION_EPOCH
                self.stats["future_lease_expiries"] += 1
            self._task_claims.pop(tid, None)
            self._peer_nominations.pop(tid, None)
            task = self.open_tasks.get(tid)
            if task is not None and tid not in self.completed_tasks:
                if claim[2] != self.rid:
                    self.stats["task_reassignments"] += 1
                self._restart_auction(task, t)

    def _drop_current_task(self) -> None:
        self.task = None
        self.goal = None
        self.path = []
        self.path_times = []
        self.pidx = 0
        self.state = ST_IDLE
        self._task_started_t = 0.0
        self._priority_grace_since = None
        self._priority_grace_until = -1e9

    def _accept_task(self, t: float, task: Task, here: Cell,
                     lease_until: float | None = None,
                     bid_cost: float | None = None) -> None:
        self.task = task
        if lease_until is not None:
            if bid_cost is None:
                bid_cost = self._bids.get(task.tid, {}).get(
                    (task.auction_epoch, self.rid), 1e9)
            self._record_task_claim(
                task.tid, (task.auction_epoch, bid_cost, self.rid, lease_until))
        self._awarded.discard(task.tid)
        self._peer_nominations.pop(task.tid, None)
        self._task_started_t = t
        self._priority_grace_since = None
        self._priority_grace_until = -1e9
        self.state = ST_TO_PICK
        self.goal = task.pick
        estimate = self._task_estimate(
            task, here, extra_cost=self.penalty,
            edge_cost=self._v6_edge_costs(t))
        self._record_decision(
            t, "TASK_ACCEPTED", f"Selected {task.tid} through peer auction",
            task=task.tid, priority=task.priority,
            deadline_s=(None if task.deadline is None
                        else round(max(0.0, task.deadline - t), 2)),
            bid_cost=(None if bid_cost is None else round(bid_cost, 3)),
            predicted_completion_s=(None if estimate is None
                                    else round(estimate[1], 2)),
            cargo_type=task.cargo_type,
            cargo_weight_kg=round(task.cargo_weight, 2))
        self._replan(t, here)

    # ================================================================== follower

    def _follow(self, t: float, sensors: Sensors) -> Actuation:
        """Pure-pursuit-ish waypoint follower. Shared by every policy, on purpose."""
        spec = self.cfg.robot
        if self._hold:
            # PIBT's discrete "wait" is executed at the current cell centre. Braking
            # can leave a chassis close to the boundary, where it blocks the admitted
            # perpendicular move despite occupying a different grid cell. A bounded
            # move back to its own centre restores the geometry without entering any
            # cell the resolver denied.
            pos = (sensors.pose[0], sensors.pose[1])
            centre = cell_center(sensors.cell, self.cfg.cell_m)
            if (self.policy in PIBT_POLICIES
                    and sensors.t < self._creep_until
                    and dist(pos, centre) > 0.12):
                err = angle_diff(bearing(pos, centre), sensors.pose[2])
                if abs(err) > 0.35:
                    if abs(sensors.v) > 0.08:
                        return Actuation(0.0, 0.0)
                    return Actuation(
                        0.0, clamp(2.2 * err, -spec.omega_max, spec.omega_max))
                return Actuation(
                    min(0.20, math.sqrt(2 * spec.a_max * dist(pos, centre))),
                    clamp(1.8 * err, -spec.omega_max, spec.omega_max))
            # A hold forbids translation, not steering. Keeping the wheels locked at
            # their old heading can leave the named peer forever inside the forward
            # cone, so Layer 0 keeps reporting the same stall and the hold becomes
            # self-sustaining. Turning in place cannot consume the reserved cell.
            if self.path and self.pidx < len(self.path):
                target = cell_center(self.path[self.pidx], self.cfg.cell_m)
                err = angle_diff(bearing(pos, target), sensors.pose[2])
                if abs(err) > 0.08:
                    return Actuation(
                        0.0, clamp(2.2 * err, -spec.omega_max, spec.omega_max))
            return Actuation(0.0, 0.0)
        if not self.path or self.pidx >= len(self.path):
            return Actuation(0.0, 0.0)

        pos = (sensors.pose[0], sensors.pose[1])
        target_cell = self.path[self.pidx]
        target = cell_center(target_cell, self.cfg.cell_m)
        if (self.policy in DIRECTED_POLICIES and target_cell != sensors.cell
                and manhattan(target_cell, sensors.cell) != 1):
            # Traffic loop will rebuild this discontinuous route.  Never execute a
            # diagonal shortcut meanwhile: the planner's rack-clearance proof only
            # covers centre-to-centre adjacent motion.
            return Actuation(0.0, 0.0)

        # Conflict recovery can stop a chassis off the centreline.  A* assumes each
        # transition begins at the source-cell centre; driving diagonally from an
        # off-axis pose to the next waypoint can cut a rack corner and then repeat the
        # same rejected motion forever.  Re-acquire the current cell's centreline
        # first.  Progress along the intended axis is deliberately ignored, otherwise
        # every normal cell-boundary crossing would look like an offset to undo.
        recentering = False
        if (target_cell != sensors.cell
                and manhattan(target_cell, sensors.cell) == 1
                and self._cell_repair_target != target_cell):
            centre = cell_center(sensors.cell, self.cfg.cell_m)
            dx = target_cell[0] - sensors.cell[0]
            lateral_error = (abs(pos[1] - centre[1]) if dx
                             else abs(pos[0] - centre[0]))
            lateral_limit = (0.08 if self.policy in DIRECTED_POLICIES else 0.22)
            if lateral_error > lateral_limit * self.cfg.cell_m:
                target = centre
                recentering = True

        if not recentering and dist(pos, target) < 0.12:
            self.pidx += 1
            if self.pidx >= len(self.path):
                return Actuation(0.0, 0.0)
            target = cell_center(self.path[self.pidx], self.cfg.cell_m)

        err = angle_diff(bearing(pos, target), sensors.pose[2])
        if abs(err) > 0.35:
            # Turn in place. A differential-drive AMR that arcs into a narrow aisle
            # clips the shelving; the planner assumes cell-centre travel and the
            # controller has to actually deliver it.
            if abs(sensors.v) > 0.08:
                return Actuation(0.0, 0.0)
            return Actuation(0.0, clamp(2.2 * err, -spec.omega_max, spec.omega_max))

        # Brake for the next TURN, not just for the final waypoint. A differential
        # drive cannot round a 90 degree corner at speed: the follower commands
        # turn-in-place, the chassis still needs a_max to shed 1.2 m/s, and it slides
        # most of a metre into the shelving while it does. Decelerating against the
        # distance to the end of the current straight run is what makes cell-centre
        # travel - which the planner assumes - actually happen.
        remaining = dist(pos, target) if recentering else self._straight_run_m(pos)
        v_profile = math.sqrt(max(0.0, 2 * spec.a_max * remaining) + spec.v_turn ** 2)
        v = min(spec.v_max * max(0.2, math.cos(err)), v_profile)
        if recentering:
            v = min(v, 0.25)
        return Actuation(v, clamp(1.8 * err, -spec.omega_max, spec.omega_max))

    def _straight_run_m(self, pos) -> float:
        """Metres to the end of the current straight segment (a turn, or the goal)."""
        path, i = self.path, self.pidx
        rem = dist(pos, cell_center(path[i], self.cfg.cell_m))
        if i == 0:
            return rem
        d0 = (path[i][0] - path[i - 1][0], path[i][1] - path[i - 1][1])
        k = i
        while k + 1 < len(path):
            dk = (path[k + 1][0] - path[k][0], path[k + 1][1] - path[k][1])
            if dk != d0:
                break
            rem += self.cfg.cell_m
            k += 1
        return rem

    def _next_cell(self) -> Cell | None:
        future = self._future_path_cells(1)
        return future[0] if future else None

    def _future_path_cells(self, limit: int) -> list[Cell]:
        """Upcoming distinct occupancy cells after the measured current cell."""
        if not self.path or self.pidx >= len(self.path) or limit <= 0:
            return []
        i = self.pidx
        # The follower may still be centring itself in the cell its pose estimator
        # already reports. Traffic coordination must look one cell further: waiting
        # until that centring waypoint is consumed starts a corridor stop only after
        # the chassis has reached the mouth, too late for its braking distance.
        while (self._last_cell is not None and i < len(self.path)
               and self.path[i] == self._last_cell):
            i += 1
        return self.path[i:i + limit]

    def _state_for_task(self) -> str:
        if self.task is None:
            return ST_CHARGING if self.state == ST_CHARGING else ST_IDLE
        return ST_TO_DROP if self.task.drop == self.goal else ST_TO_PICK

    def _arbitration_key(self) -> tuple[float, str]:
        """The key both sides of a conflict compare. Published, not live.

        This is the subtle one, and getting it wrong produces a livelock that looks
        like a deadlock. Our priority ages while we wait, so the live value is always
        a little higher than the value peers last heard. If we compare *our live* key
        against *their published* key, then so do they - and inside one heartbeat
        period both robots can conclude they are the loser. Both yield, forever, and
        the wait-for graph shows a mutual block that no cycle-breaker can fix because
        neither robot is actually wrong.

        Comparing published against published makes the relation antisymmetric: both
        robots evaluate the same two numbers and exactly one of them yields.
        """
        return (self._pub_priority, self.rid)

    def _priority(self, t: float) -> float:
        """Total-order key, with ageing so nobody starves.

        A loaded robot outranks an empty one (its task is already half-paid-for), and
        every second spent blocked raises the key. Ageing is what stops a fixed
        priority order from starving a low-ranked robot forever, and it is the reason
        the deadlock breaker terminates instead of oscillating.
        """
        base = 1000.0 if self.task is not None and self.goal == self.task.drop else 0.0
        waited = 0.0 if self.blocked_since is None else (t - self.blocked_since)
        # Ageing is DISCRETE, in five-second steps, and that is not a detail. Continuous
        # ageing makes two waiting robots swap rank several times a second, so neither
        # ever holds the lead long enough to finish a commit round and both thrash at
        # the mouth forever. Stepping keeps the order stable for far longer than a
        # round takes, while still guaranteeing a long-suffering robot eventually
        # outranks a loaded one and nobody starves.
        return base + 50.0 * float(int(waited / 5.0))

    def _priority_key(self, t: float, sensors: Sensors) -> PriorityKey:
        """Build the published BIOS_PIBT key from operational facts, not weights."""
        wait_started = self.blocked_since
        if (wait_started is None and self._priority_grace_since is not None
                and t < self._priority_grace_until):
            wait_started = self._priority_grace_since
        waited = 0.0 if wait_started is None else t - wait_started
        service = 0.0 if self.task is None else t - self._task_started_t
        distance = 0 if self.goal is None else manhattan(sensors.cell, self.goal)
        return PriorityKey(
            emergency=int(sensors.battery_frac < 0.10),
            exiting_branch=int(self.topology.leaving_branch(sensors.cell, self.goal)),
            waiting_age=int(max(0.0, waited) /
                            self.cfg.traffic.priority_age_quantum_s),
            service_age=int(max(0.0, service) /
                            self.cfg.traffic.priority_age_quantum_s),
            loaded=int(self.task is not None and self.goal == self.task.drop),
            distance_bias=-distance,
            robot_id=self.rid,
        )

    # ================================================================== comms

    def _broadcast(self, t: float, sensors: Sensors,
                   outbox: list[msg.Message]) -> None:
        if self.policy in (POLICY_STOP_WAIT, *CENTRAL_POLICIES):
            # Heartbeats only. The dashboard has to work for every baseline or the
            # comparison quietly becomes "with telemetry vs without", and the manager
            # needs poses to plan. Neither baseline shares *intent* with peers - that
            # is our mechanism, and lending it to them would flatter our own result.
            outbox.append(msg.heartbeat(
                self.rid, self._next_seq(), t, sensors.pose, sensors.cell,
                sensors.battery_frac, self.mode, self.state,
                self.task.tid if self.task else None))
            self._broadcast_auction_lease(t, outbox)
            self._broadcast_future_lease(t, outbox)
            self._broadcast_task_catalog(t, outbox)
            return

        # Latch the key at the moment we publish it, so peers and we are comparing
        # the same number for the whole heartbeat period.
        self._pub_priority = self._priority(t)
        wire_key = None
        if self.policy in PIBT_POLICIES:
            self._pub_priority_key = self._priority_key(t, sensors)
            wire_key = self._pub_priority_key.to_wire()
        heartbeat_signature = (
            sensors.cell, self.state, self.task.tid if self.task else None,
            self.goal, self.blocked_on if self.blocked_on != "gate" else None,
            int(sensors.battery_frac * 20.0), tuple(wire_key or ()),
        )
        heartbeat_due = (
            self.policy != POLICY_BIOS_PIBT_V6
            or self._v6_heartbeat_due(t, sensors, heartbeat_signature)
        )
        if heartbeat_due:
            outbox.append(msg.heartbeat(
                self.rid, self._next_seq(), t, sensors.pose, sensors.cell,
                sensors.battery_frac, self.mode, self.state,
                self.task.tid if self.task else None,
                priority=self._pub_priority,
                blocked_on=self.blocked_on if self.blocked_on != "gate" else None,
                goal=self.goal,
                priority_key=wire_key))
            self._last_heartbeat_broadcast = t
            self._last_heartbeat_signature = heartbeat_signature
        else:
            self.stats["heartbeat_messages_suppressed"] += 1

        self._broadcast_auction_lease(t, outbox)
        self._broadcast_future_lease(t, outbox)
        self._broadcast_task_catalog(t, outbox)
        self._broadcast_completion_catalog(t, outbox)
        self._v6_broadcast_experience(t, outbox)

        cells, windows = self._intent_horizon(t)
        if cells:
            intent_signature = (tuple(cells), self.epoch)
            intent_due = (
                self.policy != POLICY_BIOS_PIBT_V6
                or intent_signature != self._last_intent_signature
                or self._v6_conflict_active(sensors)
                or t - self._last_intent_broadcast
                >= self.cfg.traffic.v6_intent_refresh_s
            )
            if intent_due:
                outbox.append(msg.intent(
                    self.rid, self._next_seq(), t, cells, windows,
                    self._pub_priority, self.epoch))
                self._last_intent_broadcast = t
                self._last_intent_signature = intent_signature
            else:
                self.stats["intent_messages_suppressed"] += 1

    def _broadcast_auction_lease(self, t: float,
                                  outbox: list[msg.Message]) -> None:
        """Renew a peer-auction award independently of the motion policy."""
        if not self._auction_enabled() or self.task is None:
            return
        refresh_s = (self.cfg.traffic.v6_lease_refresh_s
                     if self.policy == POLICY_BIOS_PIBT_V6
                     else 1.0 / self.cfg.rates.heartbeat_hz)
        if (self.policy == POLICY_BIOS_PIBT_V6
                and (self.cfg.net.loss > 0.0 or self.cfg.net.dead_zones)):
            refresh_s = self.cfg.traffic.v6_degraded_lease_refresh_s
        if t - self._last_lease_broadcast < refresh_s:
            if self.policy == POLICY_BIOS_PIBT_V6:
                self.stats["lease_renewals_suppressed"] += 1
            return
        claim = self._task_claims.get(self.task.tid)
        if claim is None or claim[2] != self.rid:
            return
        self._last_lease_broadcast = t
        lease_until = t + self._task_lease_duration(self.task)
        self._record_task_claim(
            self.task.tid, (claim[0], claim[1], self.rid, lease_until))
        outbox.append(msg.award(
            self.rid, self._next_seq(), t, self.task.tid, claim[1],
            epoch=claim[0], lease_until=lease_until))

    def _broadcast_future_lease(self, t: float,
                                outbox: list[msg.Message]) -> None:
        if (not self._future_allocation_enabled() or self.future_task is None
                or self._future_context is None):
            return
        refresh_s = self.cfg.traffic.v6_lease_refresh_s
        if t - self._last_future_lease_broadcast < refresh_s:
            return
        task = self.future_task
        claim = self._task_claims.get(task.tid)
        if claim is None or claim[2] != self.rid:
            return
        active_tid, active_epoch, version = self._future_context
        lease_until = t + self.cfg.traffic.bundle_future_lease_s
        self._record_task_claim(
            task.tid, (claim[0], claim[1], self.rid, lease_until))
        outbox.append(msg.award(
            self.rid, self._next_seq(), t, task.tid, claim[1],
            epoch=claim[0], lease_until=lease_until,
            active_task=active_tid, active_epoch=active_epoch,
            bundle_version=version))
        self._last_future_lease_broadcast = t
        self.stats["future_lease_renewals"] += 1

    def _broadcast_task_catalog(self, t: float,
                                outbox: list[msg.Message]) -> None:
        """Gossip one unfinished task so missed WMS announcements eventually heal."""
        period = (self.cfg.traffic.v6_catalog_gossip_s
                  if self.policy == POLICY_BIOS_PIBT_V6
                  else self.cfg.traffic.task_gossip_period_s)
        if (self.policy not in V3_AUCTION_POLICIES or not self._auction_enabled()
                or t - self._last_catalog_broadcast < period):
            return
        tasks = sorted(
            (task for task in self.open_tasks.values()
             if task.tid not in self.completed_tasks),
            key=lambda task: task.tid)
        if not tasks:
            return
        task = tasks[self._catalog_cursor % len(tasks)]
        self._catalog_cursor += 1
        self._last_catalog_broadcast = t
        outbox.append(msg.task_new(
            self.rid, self._next_seq(), t, task.tid, task.pick, task.drop,
            epoch=task.auction_epoch,
            bid_until=max(task.bid_deadline,
                          t + self.cfg.traffic.auction_bid_window_s),
            cargo_type=task.cargo_type, cargo_weight=task.cargo_weight,
            priority=task.priority, deadline=task.deadline))

    def _broadcast_completion_catalog(self, t: float,
                                      outbox: list[msg.Message]) -> None:
        """Gossip one completion so a lost one-shot TASK_DONE cannot stall a wave."""
        period = (self.cfg.traffic.v6_catalog_gossip_s
                  if self.policy == POLICY_BIOS_PIBT_V6
                  else self.cfg.traffic.completion_gossip_period_s)
        if (self.policy not in V3_AUCTION_POLICIES or not self._auction_enabled()
                or (self.circulation.enabled and self.cfg.net.loss <= 0.0
                    and not self.cfg.net.dead_zones)
                or t - self._last_completion_broadcast < period):
            return
        completed = sorted(self.completed_tasks)
        if not completed:
            return
        tid = completed[self._completion_cursor % len(completed)]
        self._completion_cursor += 1
        self._last_completion_broadcast = t
        epoch, owner = self._completion_proofs.get(tid, (0, self.rid))
        outbox.append(msg.task_done(
            self.rid, self._next_seq(), t, tid,
            epoch=epoch, owner=(owner if owner != self.rid else None)))

    def _intent_horizon(self, t: float) -> tuple[list[Cell], list[tuple[float, float]]]:
        # A completed task can leave its final waypoint in ``path`` until the next
        # route loop.  With no live goal those cells are history, not intent.  Sending
        # them after the idle heartbeat would immediately recreate the ghost route the
        # heartbeat lifecycle just cleared on every peer.
        if self.goal is None:
            return [], []
        h = self.cfg.traffic.intent_horizon
        cells = self.path[self.pidx:self.pidx + h]
        if (self.policy in DIRECTED_POLICIES and self.circulation.enabled
                and self._last_cell is not None):
            # Pose quantisation changes cell before the continuous follower reaches its
            # centre.  Publishing that same cell as the first future intent tells every
            # peer that this AMR plans to stay, so a priority-inheritance chain cannot
            # push through it.  Wire intent must describe future occupancy only.
            while cells and cells[0] == self._last_cell:
                cells = cells[1:]
        if not cells:
            return [], []
        v_nom = 0.8 * self.cfg.robot.v_max
        step = self.cfg.cell_m / v_nom
        windows = []
        for i in range(len(cells)):
            enter = t + i * step
            windows.append((enter, enter + step + 0.4))     # margin for accel/turns
        return list(cells), windows

    def _ingest(self, t: float, inbox: list[msg.Message]) -> None:
        for m in inbox:
            self.stats["msgs_recv"] += 1
            if m.src == self.rid:
                continue
            b = m.body

            if m.type == msg.HEARTBEAT:
                p = self.peers.setdefault(m.src, Peer(m.src))
                self._known_peer_ids.add(m.src)
                p.cell = msg.as_cell(b["c"])
                p.pose = tuple(b["p"])
                p.priority = b.get("pr", 0.0)
                p.blocked_on = b.get("bo")
                p.state = b.get("s", ST_IDLE)
                p.goal = msg.as_cell(b["g"]) if b.get("g") else None
                p.priority_key = PriorityKey.from_wire(b.get("pk"), m.src)
                p.battery_frac = float(b.get("b", p.battery_frac))
                p.task_id = b.get("task")
                p.last_seen = t
                # INTENT is sent only while a route has cells to advertise.  An idle
                # peer therefore sends no empty INTENT packet to overwrite its last
                # active one.  Clear it from the authoritative heartbeat lifecycle;
                # otherwise a fresh idle heartbeat keeps a stale route alive forever
                # and different robots run PIBT against different ghost reservations.
                if p.state == ST_IDLE or p.goal is None:
                    p.intent = []
                    p.windows = []
            elif m.type == msg.INTENT:
                p = self.peers.setdefault(m.src, Peer(m.src))
                p.intent = [msg.as_cell(c) for c in b["cells"]]
                # Wire windows are offsets from receipt, never sender-absolute times.
                # Separate edge nodes have unrelated monotonic-clock epochs.
                p.windows = [(t + float(w[0]), t + float(w[1]))
                             for w in b.get("w", [])]
                p.priority = b.get("pr", p.priority)
                p.last_seen = t
            elif m.type == msg.EXPERIENCE:
                if self.policy == POLICY_BIOS_PIBT_V6:
                    self._v6_ingest_experience(t, m.src, b.get("edges", []))
            elif m.type == msg.TASK_NEW:
                tid = b["task"]
                if tid in self.completed_tasks:
                    continue
                epoch = int(b.get("e", 0))
                cargo_type = str(b.get("ct", "normal"))
                cargo_weight = float(b.get("cw", 0.0))
                task_priority = int(b.get("pr", 1))
                due = b.get("due")
                task_deadline = (None if due is None
                                 else t + float(due))
                ttl = b.get("ttl")
                if ttl is not None:
                    deadline = t + min(float(ttl),
                                       4.0 * self.cfg.traffic.auction_bid_window_s)
                elif b.get("dl") is not None:  # version-0 trace compatibility
                    deadline = t + max(0.0, min(
                        float(b["dl"]) - m.t,
                        4.0 * self.cfg.traffic.auction_bid_window_s))
                else:
                    deadline = t + self.cfg.traffic.auction_bid_window_s
                current = self.open_tasks.get(tid)
                current_epoch = current.auction_epoch if current is not None else 0
                if not self._safe_incoming_epoch(current_epoch, epoch):
                    self.stats["rejected_epoch_jumps"] += 1
                    continue
                incoming_descriptor = (
                    msg.as_cell(b["pk"]), msg.as_cell(b["dp"]),
                    cargo_type, round(cargo_weight, 6), task_priority)
                known_descriptor = self._task_descriptors.get(tid)
                descriptor_conflict = (
                    known_descriptor is not None
                    and known_descriptor != incoming_descriptor)
                if descriptor_conflict and (
                        m.src != "WMS" or tid in self._task_descriptor_from_wms):
                    self.stats["rejected_task_conflicts"] += 1
                    continue
                if m.src == "WMS":
                    self._task_descriptor_from_wms.add(tid)
                self._task_descriptors[tid] = incoming_descriptor
                if current is None:
                    self.open_tasks[tid] = Task(
                        tid=tid, pick=msg.as_cell(b["pk"]),
                        drop=msg.as_cell(b["dp"]), announced_t=t,
                        auction_epoch=epoch, bid_deadline=float(deadline),
                        cargo_type=cargo_type, cargo_weight=cargo_weight,
                        priority=task_priority, deadline=task_deadline)
                elif epoch > current.auction_epoch:
                    current.auction_epoch = epoch
                    current.bid_deadline = float(deadline)
                    current.cargo_type = cargo_type
                    current.cargo_weight = cargo_weight
                    current.priority = task_priority
                    current.deadline = task_deadline
                    self._bids.pop(tid, None)
                    self._bid_opened.pop(tid, None)
                else:
                    # Same-epoch catalog repair may come from an older peer that knew
                    # only the legacy fields. Enrich a legacy record once, but never
                    # let a later legacy repeat erase cargo semantics.
                    incoming_extended = (
                        cargo_type != "normal" or cargo_weight != 0.0
                        or task_priority != 1 or task_deadline is not None)
                    current_extended = (
                        current.cargo_type != "normal"
                        or current.cargo_weight != 0.0
                        or current.priority != 1 or current.deadline is not None)
                    if incoming_extended and not current_extended:
                        current.cargo_type = cargo_type
                        current.cargo_weight = cargo_weight
                        current.priority = task_priority
                    if current.deadline is None and task_deadline is not None:
                        current.deadline = task_deadline
                if descriptor_conflict and m.src == "WMS" and current is not None:
                    # A valid WMS announcement is the only allowed correction to a
                    # descriptor learned provisionally from peer gossip. Apply the
                    # complete immutable descriptor even when the epoch is unchanged.
                    current.pick = msg.as_cell(b["pk"])
                    current.drop = msg.as_cell(b["dp"])
                    current.cargo_type = cargo_type
                    current.cargo_weight = cargo_weight
                    current.priority = task_priority
                    current.deadline = task_deadline
                claim = self._task_claims.get(tid)
                task_record = self.open_tasks.get(tid)
                if claim is not None and task_record is not None:
                    task_record.lease_owner = claim[2]
                    task_record.lease_until = claim[3]
                self._admit_deferred_bids(tid, t)
            elif m.type == msg.BID:
                tid = b["task"]
                epoch = int(b.get("e", 0))
                task = self.open_tasks.get(tid)
                if task is None:
                    self._cache_unknown_bid(t, m.src, b)
                    continue
                if not self._safe_incoming_epoch(task.auction_epoch, epoch):
                    self.stats["rejected_epoch_jumps"] += 1
                    continue
                if epoch < task.auction_epoch:
                    continue
                if epoch > task.auction_epoch:
                    task.auction_epoch = epoch
                    task.bid_deadline = t + self.cfg.traffic.auction_bid_window_s
                    self._bids.pop(tid, None)
                    self._bid_opened.pop(tid, None)
                self._bids.setdefault(tid, {})[(epoch, m.src)] = float(b["cost"])
                self._bid_seen_t[(tid, epoch, m.src)] = t
                if b.get("future"):
                    if not self._future_allocation_enabled():
                        self._bids[tid].pop((epoch, m.src), None)
                        self._bid_seen_t.pop((tid, epoch, m.src), None)
                        continue
                    self._future_bid_contexts[(tid, epoch, m.src)] = (
                        str(b["active"]), int(b.get("ae", 0)),
                        int(b.get("bv", 0)))
            elif m.type == msg.AWARD:
                tid = b["task"]
                epoch = int(b.get("e", 0))
                owner = str(b.get("winner") or b.get("dst") or m.src)
                cost = float(b.get("cost", 1e9))
                if b.get("ttl") is not None:
                    lease_until = t + min(float(b["ttl"]),
                                          2.0 * self.cfg.traffic.auction_lease_s)
                elif b.get("u") is not None:  # version-0 trace compatibility
                    lease_until = t + max(0.0, min(
                        float(b["u"]) - m.t,
                        2.0 * self.cfg.traffic.auction_lease_s))
                else:
                    lease_until = t + self.cfg.traffic.auction_lease_s
                task = self.open_tasks.get(tid)
                directed = b.get("dst") is not None
                if directed:
                    # Only the configured Hungarian manager may issue a directed task
                    # assignment. A shared fleet PSK authenticates membership, not role.
                    if (m.src != "FM0"
                            or self.allocation_policy
                            not in (None, ALLOCATION_HUNGARIAN)):
                        self.stats["rejected_directed_awards"] += 1
                        continue
                    if b.get("dst") == self.rid:
                        self._awarded.add(tid)
                    continue
                if task is None:
                    continue
                if not self._safe_incoming_epoch(task.auction_epoch, epoch):
                    self.stats["rejected_epoch_jumps"] += 1
                    continue
                if epoch < task.auction_epoch:
                    continue
                if epoch > task.auction_epoch:
                    task.auction_epoch = epoch
                    task.bid_deadline = t
                    self._bids.pop(tid, None)
                    self._bid_opened.pop(tid, None)
                # A directed award is from the optional manager and is consumed by
                # the destination robot. Only peer-auction awards create expiring
                # claims; otherwise a central assignment could vanish mid-task when
                # the manager's one-shot message is older than the lease.
                local_bid = self._bids.get(tid, {}).get((epoch, self.rid))
                local_bid_t = self._bid_seen_t.get(
                    (tid, epoch, self.rid), -1e9)
                matching_local_bid = (
                    local_bid is not None
                    and round(local_bid, 3) == round(cost, 3)
                    and t - local_bid_t
                    <= max(2.0 * self.cfg.traffic.auction_lease_s,
                           self._task_lease_duration(task))
                )
                context = None
                if b.get("future"):
                    context = (
                        str(b["active"]), int(b.get("ae", 0)),
                        int(b.get("bv", 0)))
                    matching_local_bid = (
                        matching_local_bid
                        and self._future_bid_contexts.get(
                            (tid, epoch, self.rid)) == context)
                # A peer can nominate this robot only for a task/epoch/cost and bundle
                # context this robot actually bid recently.
                recorded = False
                if owner != self.rid or matching_local_bid:
                    recorded = self._record_task_claim(
                        tid, (epoch, cost, owner, lease_until))
                if recorded and owner == self.rid:
                    if context is not None:
                        if (self.future_task is not None
                                and self.future_task.tid == tid
                                and self._future_context == context):
                            # This is a lease refresh for an already-consumed award,
                            # not a second nomination for the one available slot.
                            pass
                        else:
                            self._peer_future_nominations[tid] = (
                                epoch, cost, lease_until,
                                context[0], context[1], context[2])
                    elif self.task is None and self.state == ST_IDLE:
                        self._peer_nominations[tid] = (
                            epoch, cost, lease_until)
            elif m.type == msg.TASK_DONE:
                tid = b["task"]
                task = self.open_tasks.get(tid)
                if task is None:
                    continue
                epoch = int(b.get("e", 0))
                owner = str(b.get("owner") or m.src)
                claim = self._task_claims.get(tid)
                validated_relay = (
                    bool(b.get("relay"))
                    and owner != m.src
                    and m.src in self._known_peer_ids
                    and owner in self._known_peer_ids
                )
                valid_completion = (
                    epoch == task.auction_epoch
                    # Direct self-attestation is required for duplicate-winner
                    # convergence: partitioned peers can each hold a different
                    # same-epoch claim, but a completed physical job must not be
                    # executed again. Transport authentication establishes source
                    # membership; stale epochs and third-party owner names that do not
                    # match the locally replicated claim remain rejected. A relay is
                    # accepted only while it carries that same owner/epoch proof,
                    # preserving completion convergence under packet loss.
                    and (owner == m.src
                         or (claim is not None and claim[0] == epoch
                             and claim[2] == owner)
                         or validated_relay)
                )
                if not valid_completion:
                    self.stats["rejected_task_completions"] += 1
                    continue
                # A partition or asymmetric bid view can briefly create duplicate
                # winners. Completion is the convergence point: a robot still
                # executing that same logical task must cancel its stale copy or it
                # remains live traffic forever and can block unrelated final work.
                if (self.policy in ENERGY_AUCTION_POLICIES
                        and self.task is not None and self.task.tid == tid):
                    self._drop_current_task()
                    self._needs_duplicate_vacate = True
                if self.future_task is not None and self.future_task.tid == tid:
                    self.future_task = None
                    self._future_context = None
                    self._future_bid = None
                    self._future_generation = (
                        self._future_generation + 1) % msg.MAX_AUCTION_EPOCH
                self.open_tasks.pop(tid, None)
                self.completed_tasks.add(tid)
                self._completion_proofs[tid] = (epoch, owner)
                self._task_claims.pop(tid, None)
                self._bids.pop(tid, None)
                self._bid_opened.pop(tid, None)
                self._awarded.discard(tid)
                self._peer_nominations.pop(tid, None)
                self._peer_future_nominations.pop(tid, None)
                for key in [key for key in self._bid_seen_t if key[0] == tid]:
                    self._bid_seen_t.pop(key, None)
            elif m.type == msg.MGR_BEACON:
                self._mgr_seen = t
            elif m.type in (msg.CLAIM, msg.RELEASE) and b.get("b"):
                # BIOS_1.0.0 block token. A claim reserves a whole single-lane block
                # for one robot; it expires by its own timestamp and a release only
                # helps early. We keep the longest unexpired claim we have heard.
                cid = int(b["g"])
                if m.type == msg.RELEASE:
                    current = self._claims.get(cid)
                    if current is not None and current[0] == m.src:
                        self._claims.pop(cid, None)
                else:
                    if b.get("ttl") is not None:
                        ttl = float(b["ttl"])
                    elif b.get("u") is not None:  # version-0 trace compatibility
                        ttl = max(0.0, float(b["u"]) - m.t)
                    else:  # wire validation rejects this; defensive for direct tests
                        ttl = self.cfg.traffic.bios_claim_ttl_s
                    ttl = max(0.0, min(
                        ttl, 2.0 * self.cfg.traffic.bios_claim_ttl_s))
                    until = t + ttl
                    rich = (PriorityKey.from_wire(b.get("pk"), m.src)
                            if b.get("pk") is not None else None)
                    candidate = (m.src, until, float(b.get("pr", 0.0)),
                                 int(b.get("e", 0)), rich)
                    current = self._claims.get(cid)
                    cand_key = ((1, candidate[4]) if candidate[4] is not None
                                else (0, candidate[2], candidate[3], candidate[0]))
                    cur_key = (((1, current[4]) if current[4] is not None
                                else (0, current[2], current[3], current[0]))
                               if current is not None and current[1] > t
                               else None)
                    if cur_key is None or cand_key > cur_key:
                        self._claims[cid] = candidate
            elif m.type == msg.PLAN_RSP:
                # A central route is advice about where to go next; it must not
                # overwrite a give-way already in progress. Doing so leaves the robot
                # executing the manager's path while still flagged as retreating, so
                # the manoeuvre never completes and the state never clears.
                if b.get("dst") == self.rid and self.state != ST_RETREAT:
                    self._mgr_seen = t
                    cells = [msg.as_cell(c) for c in b["cells"]]
                    if cells:
                        self.path = cells
                        self.path_times = [t + float(x) for x in b.get("w", [])]
                        self.pidx = 1 if len(cells) > 1 else 0
                        self.epoch = b.get("e", self.epoch)
                        self.stats["central_plans"] += 1

    def _expire_peers(self, t: float) -> None:
        """Silence is information: a peer we have not heard from is a peer whose intent
        we must stop trusting. Holding stale intents is how a fleet politely gridlocks
        around a robot that died ten seconds ago."""
        stale = self._peer_stale_after_s()
        for rid in list(self.peers):
            if t - self.peers[rid].last_seen > stale:
                self.peers[rid].intent = []
                self.peers[rid].windows = []
            if t - self.peers[rid].last_seen > stale * 6:
                del self.peers[rid]

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq
