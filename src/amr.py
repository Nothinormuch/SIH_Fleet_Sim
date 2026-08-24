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
from .task_allocation import ALLOCATION_AUCTION, validate_allocation_policy
from .topology import analyse_topology, directed_circulation
from .world import Actuation, Sensors

# ---------------------------------------------------------------------- constants

POLICY_STOP_WAIT = "stop_and_wait"
POLICY_CENTRAL = "central"
POLICY_HIERARCHICAL = "hierarchical"
POLICY_BIOS = "BIOS_1.0.0"
POLICY_BIOS_PIBT = "BIOS_PIBT.1"
POLICY_BIOS_PIBT_V2 = "BIOS_PIBT.2"
POLICY_DECENTRALIZED = "decentralized"
PIBT_POLICIES = (POLICY_BIOS_PIBT, POLICY_BIOS_PIBT_V2)
DECENTRAL_POLICIES = (POLICY_BIOS, POLICY_DECENTRALIZED, *PIBT_POLICIES)
CENTRAL_POLICIES = (POLICY_CENTRAL,)
POLICIES = (POLICY_STOP_WAIT, POLICY_CENTRAL, POLICY_HIERARCHICAL,
            POLICY_BIOS, POLICY_DECENTRALIZED, *PIBT_POLICIES)

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


class AMRBrain:
    """One robot's entire decision-making. Pure: no sockets, no clocks, no globals."""

    def __init__(self, rid: str, env: Warehouse, cfg: Config,
                 policy: str = POLICY_HIERARCHICAL, home: Cell = (0, 0),
                 allocation_policy: str | None = None) -> None:
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
        self._bid_opened: dict[str, float] = {}
        self._last_lease_broadcast = -1e9

        # Cells this robot has learned to avoid, with a decaying penalty. Contested
        # cells become expensive, never impassable - marking them impassable is how a
        # jam turns into an unsolvable map.
        self.penalty: dict[Cell, float] = {}

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
        # The priority we last BROADCAST. Arbitration must use this, never the live
        # value - see _arbitration_key.
        self._pub_priority = 0.0
        self._pub_priority_key = PriorityKey(robot_id=rid)
        self.blocked_since: float | None = None
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
            self._cell_gate_since.pop(cell, None)
            self._last_cell = cell
            self._last_progress_t = t

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
        return act, outbox

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
            self._track_block(t, self._hold, None)
            return

        nxt = self._next_cell()
        if nxt is None:
            self._track_block(t, False, None)
            return
        if (self.policy == POLICY_BIOS_PIBT_V2
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
        # than merely detect - a head-on lock in a single-file aisle.
        loser_to = self._block_conflict(t, sensors.cell, nxt, my_key)
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
                and self.policy == POLICY_BIOS_PIBT_V2
                and self.circulation.enabled):
            loser_to = self._bios_v2_coordinate(t, sensors, nxt)
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
                and self.policy == POLICY_BIOS_PIBT_V2
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
            if self.policy in DECENTRAL_POLICIES and waited > self.cfg.traffic.bios_unstick_s:
                self._bios_unstick(t, sensors, nxt, outbox)
                return
            # Waiting at a mouth is fine unless we are waiting ON the way out. A robot
            # queued at the entrance stands exactly where the robot inside has to drive
            # to leave, so the two of them wait for each other with no cycle to detect
            # and no rule violated. Stepping aside is the only thing that breaks it.
            if (waiting_for_block
                    and waited > self.cfg.traffic.yield_aside_s
                    and self._blocker_is_inside(nxt)):
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
                if (self.policy in DECENTRAL_POLICIES
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
        if (self.policy != POLICY_BIOS_PIBT_V2 or not self.circulation.enabled
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
        if self.policy == POLICY_BIOS_PIBT_V2 and self.circulation.enabled:
            # Direction already makes opposing occupancy impossible; preserve
            # same-direction flow instead of locking every short rack segment.
            return None
        # V1 protected only long runs.  The standard warehouse has 24 four-cell
        # picking aisles and 35 two-cell rack gaps, so that threshold protected
        # precisely zero of its 59 non-passing segments.  Under load, opposing AMRs
        # entered those segments and PIBT could only request an impossible sideways
        # move.  V2 treats every maximal degree-two run as a traffic zone.  The extra
        # lease round is intentional backpressure, not planner latency.
        minimum = (2 if self.policy == POLICY_BIOS_PIBT_V2
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
        if self.policy == POLICY_BIOS_PIBT_V2 and self.circulation.enabled:
            # The directed route makes every block exit unidirectional.  Applying the
            # bidirectional apron rule here mistakes a follower for opposing traffic
            # and recreates the reciprocal wait that circulation removed.
            return None
        for n in self.env.neighbors(nxt):
            cid = self.blocks.id_of(n)
            if cid is None or n not in self.blocks.ends.get(cid, ()):
                continue
            minimum = (2 if self.policy == POLICY_BIOS_PIBT_V2
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
        if self.policy == POLICY_BIOS_PIBT_V2:
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

        # PIBT can authorize a safe convoy transition where the rear robot enters the
        # front robot's current cell while the front robot simultaneously vacates it.
        # Both chassis may already sit inside the conservative omni standstill field;
        # without a locally bounded creep neither can execute the collision-free
        # discrete configuration.  The forward speed envelope is still enforced, so
        # the follower cannot close on the peer ahead.
        requested_occupied = any(p.cell == requested for p in self.peers.values())
        if inherited is not None or requested_occupied:
            self._creep_until = max(self._creep_until, t + 6.0)

        if chosen != requested:
            # Priority inheritance has actively displaced us.  Execute exactly one
            # cell, then the ordinary route loop replans toward the unchanged goal.
            # Inside rack-lined degree-two lanes, physical space—not priority—is the
            # limiting resource.  Corridor leases handle those cells; inherited
            # side-steps are executed only on junction/open-floor cells where a
            # differential-drive chassis has room to turn.
            if self.env.degree(sensors.cell) < 3 or self.env.degree(chosen) < 3:
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

    def _track_block(self, t: float, blocked: bool, on: str | None) -> None:
        if blocked:
            if self.blocked_since is None:
                self.blocked_since = t
            self.blocked_on = on
            if self.state not in (ST_RETREAT, ST_CHARGING):
                self.state = ST_BLOCKED
        else:
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
        if self.policy == POLICY_BIOS_PIBT_V2 and self.circulation.enabled:
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
        path = astar(
            self.env, start, self.goal, extra_cost=self.penalty,
            edge_allowed=(lambda a, b: self.circulation.allows(self.env, a, b))
            if self.policy == POLICY_BIOS_PIBT_V2 else None)
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

    # ================================================================== tasks

    def _auction_enabled(self) -> bool:
        """Whether this robot owns the peer-auction allocation responsibility."""
        return self.allocation_policy == ALLOCATION_AUCTION or self.use_auction

    def _task_loop(self, t: float, sensors: Sensors,
                   outbox: list[msg.Message]) -> None:
        if self.task is None and sensors.battery_frac < 0.15 and self.env.docks:
            self.goal = min(self.env.docks, key=lambda d: manhattan(sensors.cell, d))
            self.state = ST_CHARGING
        if self.state == ST_CHARGING:
            if sensors.battery_frac > 0.9:
                self.state = ST_IDLE
                self.goal = None
            else:
                return

        if self.task is None:
            assigned = next((tid for tid in sorted(self._awarded)
                             if tid in self.open_tasks), None)
            if assigned is not None:
                self._awarded.remove(assigned)
                self._accept_task(t, self.open_tasks[assigned], sensors.cell)
            elif self.queue:
                self._accept_task(t, self.queue.pop(0), sensors.cell)
            elif self._auction_enabled():
                self._run_auction(t, sensors, outbox)
            elif self.goal is not None and self._arrived(sensors, self.goal):
                self.goal = None            # parked clear of the working aisles
            if self.goal is None:
                self._vacate_if_in_the_way(t, sensors)
            return

        if self.state in (ST_TO_PICK, ST_BLOCKED) and self._arrived(sensors, self.task.pick) \
                and self.goal == self.task.pick:
            self.state = ST_TO_DROP
            self.goal = self.task.drop
            self._replan(t, sensors.cell)
        elif self.goal == self.task.drop and self._arrived(sensors, self.task.drop):
            self.completed.append((self.task.tid, self._task_started_t, t))
            outbox.append(msg.task_done(
                self.rid, self._next_seq(), t, self.task.tid,
                epoch=self.task.auction_epoch))
            self.open_tasks.pop(self.task.tid, None)
            self.completed_tasks.add(self.task.tid)
            self._task_claims.pop(self.task.tid, None)
            self._bids.pop(self.task.tid, None)
            self._bid_opened.pop(self.task.tid, None)
            self._awarded.discard(self.task.tid)
            self.task = None
            self.state = ST_IDLE
            # Clear the station. A drop point is a shared resource, and a robot that
            # finishes its last job and simply stops where it stands is parked on top
            # of it - permanently, since nothing will ever ask it to move. That single
            # behaviour stranded whole runs here: one idle robot sitting on a station
            # made every remaining task targeting that station unreachable, and the
            # symptom looked like a planner deadlock rather than a parking bug.
            self.goal = None if self.queue else self.home

    def _arrived(self, sensors: Sensors, target: Cell) -> bool:
        """Task service occurs at the cell centre, not at its quantised boundary."""
        if sensors.cell != target:
            return False
        if self.policy != POLICY_BIOS_PIBT_V2 or not self.circulation.enabled:
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
        if not any(p.goal == here or here in p.intent
                   for p in self.peers.values()):
            return
        taken = {p.cell for p in self.peers.values()} | {
            p.goal for p in self.peers.values() if p.goal}
        options = [n for n in self.env.neighbors(here) if n not in taken]
        if options:
            self.goal = min(options, key=lambda c: manhattan(c, self.home))
            self._replan(t, here)

    def _run_auction(self, t: float, sensors: Sensors,
                     outbox: list[msg.Message]) -> None:
        """Run one deterministic, single-task auction without an auctioneer.

        Every robot sees the same task epoch and deadline, records the bids it has
        heard, and applies the same ``(cost, robot_id)`` ordering. A lease makes a
        missing winner or a crashed winner recoverable; a network partition may create
        temporary duplicate winners, but the higher epoch and deterministic claim
        ordering converge when the partition heals.
        """
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
            cost = self._bid_cost(target, sensors)
            self._bids.setdefault(target.tid, {})[
                (target.auction_epoch, self.rid)] = cost
            outbox.append(msg.bid(
                self.rid, self._next_seq(), t, target.tid, cost,
                epoch=target.auction_epoch))
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
        lease_until = t + self.cfg.traffic.auction_lease_s
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

    def _bid_cost(self, task: Task, sensors: Sensors) -> float:
        """Estimate total local work using the same A* model as navigation."""
        to_pick = astar(self.env, sensors.cell, task.pick, extra_cost=self.penalty)
        to_drop = astar(self.env, task.pick, task.drop, extra_cost=self.penalty)
        if not to_pick or not to_drop:
            return 1e9
        distance = (max(0, len(to_pick) - 1) + max(0, len(to_drop) - 1))
        battery_penalty = max(0.0, 0.25 - sensors.battery_frac) * 20.0
        return float(distance) + battery_penalty

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
        self._task_claims[tid] = claim
        return True

    @staticmethod
    def _claim_wins(new: tuple[int, float, str, float],
                    old: tuple[int, float, str, float]) -> bool:
        if new[0] != old[0]:
            return new[0] > old[0]
        return (new[1], new[2]) < (old[1], old[2])

    def _restart_auction(self, task: Task, t: float) -> None:
        task.auction_epoch += 1
        task.bid_deadline = t + self.cfg.traffic.auction_bid_window_s
        self._bid_opened.pop(task.tid, None)
        self._bids.pop(task.tid, None)
        self._task_claims.pop(task.tid, None)
        self._awarded.discard(task.tid)

    def _expire_task_claims(self, t: float) -> None:
        for tid, claim in list(self._task_claims.items()):
            if claim[3] > t:
                continue
            if self.task is not None and self.task.tid == tid \
                    and claim[2] == self.rid:
                self._drop_current_task()
            self._task_claims.pop(tid, None)
            task = self.open_tasks.get(tid)
            if task is not None and tid not in self.completed_tasks:
                self._restart_auction(task, t)

    def _drop_current_task(self) -> None:
        self.task = None
        self.goal = None
        self.path = []
        self.path_times = []
        self.pidx = 0
        self.state = ST_IDLE
        self._task_started_t = 0.0

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
        self._task_started_t = t
        self.state = ST_TO_PICK
        self.goal = task.pick
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
        if (self.policy == POLICY_BIOS_PIBT_V2 and target_cell != sensors.cell
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
        if target_cell != sensors.cell and manhattan(target_cell, sensors.cell) == 1:
            centre = cell_center(sensors.cell, self.cfg.cell_m)
            dx = target_cell[0] - sensors.cell[0]
            lateral_error = (abs(pos[1] - centre[1]) if dx
                             else abs(pos[0] - centre[0]))
            lateral_limit = (0.08 if self.policy == POLICY_BIOS_PIBT_V2 else 0.22)
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
        if not self.path or self.pidx >= len(self.path):
            return None
        i = self.pidx
        # The follower may still be centring itself in the cell its pose estimator
        # already reports. Traffic coordination must look one cell further: waiting
        # until that centring waypoint is consumed starts a corridor stop only after
        # the chassis has reached the mouth, too late for its braking distance.
        while (self._last_cell is not None and i < len(self.path)
               and self.path[i] == self._last_cell):
            i += 1
        return self.path[i] if i < len(self.path) else None

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
        waited = 0.0 if self.blocked_since is None else t - self.blocked_since
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
            return

        # Latch the key at the moment we publish it, so peers and we are comparing
        # the same number for the whole heartbeat period.
        self._pub_priority = self._priority(t)
        wire_key = None
        if self.policy in PIBT_POLICIES:
            self._pub_priority_key = self._priority_key(t, sensors)
            wire_key = self._pub_priority_key.to_wire()
        outbox.append(msg.heartbeat(
            self.rid, self._next_seq(), t, sensors.pose, sensors.cell,
            sensors.battery_frac, self.mode, self.state,
            self.task.tid if self.task else None,
            priority=self._pub_priority,
            blocked_on=self.blocked_on if self.blocked_on != "gate" else None,
            goal=self.goal,
            priority_key=wire_key))

        self._broadcast_auction_lease(t, outbox)

        cells, windows = self._intent_horizon(t)
        if cells:
            outbox.append(msg.intent(self.rid, self._next_seq(), t, cells, windows,
                                     self._pub_priority, self.epoch))

    def _broadcast_auction_lease(self, t: float,
                                  outbox: list[msg.Message]) -> None:
        """Renew a peer-auction award independently of the motion policy."""
        if (not self._auction_enabled() or self.task is None
                or t - self._last_lease_broadcast < 1.0 /
                self.cfg.rates.heartbeat_hz):
            return
        claim = self._task_claims.get(self.task.tid)
        if claim is None or claim[2] != self.rid:
            return
        self._last_lease_broadcast = t
        lease_until = t + self.cfg.traffic.auction_lease_s
        self._task_claims[self.task.tid] = (
            claim[0], claim[1], self.rid, lease_until)
        outbox.append(msg.award(
            self.rid, self._next_seq(), t, self.task.tid, claim[1],
            epoch=claim[0], lease_until=lease_until))

    def _intent_horizon(self, t: float) -> tuple[list[Cell], list[tuple[float, float]]]:
        h = self.cfg.traffic.intent_horizon
        cells = self.path[self.pidx:self.pidx + h]
        if (self.policy == POLICY_BIOS_PIBT_V2 and self.circulation.enabled
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
                p.cell = msg.as_cell(b["c"])
                p.pose = tuple(b["p"])
                p.priority = b.get("pr", 0.0)
                p.blocked_on = b.get("bo")
                p.state = b.get("s", ST_IDLE)
                p.goal = msg.as_cell(b["g"]) if b.get("g") else None
                p.priority_key = PriorityKey.from_wire(b.get("pk"), m.src)
                p.last_seen = t
            elif m.type == msg.INTENT:
                p = self.peers.setdefault(m.src, Peer(m.src))
                p.intent = [msg.as_cell(c) for c in b["cells"]]
                p.windows = [(w[0], w[1]) for w in b.get("w", [])]
                p.priority = b.get("pr", p.priority)
                p.last_seen = t
            elif m.type == msg.TASK_NEW:
                tid = b["task"]
                if tid in self.completed_tasks:
                    continue
                epoch = int(b.get("e", 0))
                deadline = b.get("dl")
                if deadline is None:
                    deadline = t + self.cfg.traffic.auction_bid_window_s
                current = self.open_tasks.get(tid)
                if current is None:
                    self.open_tasks[tid] = Task(
                        tid, msg.as_cell(b["pk"]), msg.as_cell(b["dp"]),
                        m.t, epoch, float(deadline))
                elif epoch > current.auction_epoch:
                    current.auction_epoch = epoch
                    current.bid_deadline = float(deadline)
                    self._bids.pop(tid, None)
                    self._bid_opened.pop(tid, None)
            elif m.type == msg.BID:
                tid = b["task"]
                epoch = int(b.get("e", 0))
                task = self.open_tasks.get(tid)
                if task is not None and epoch < task.auction_epoch:
                    continue
                if task is not None and epoch > task.auction_epoch:
                    task.auction_epoch = epoch
                    task.bid_deadline = t + self.cfg.traffic.auction_bid_window_s
                    self._bids.pop(tid, None)
                    self._bid_opened.pop(tid, None)
                self._bids.setdefault(tid, {})[(epoch, m.src)] = float(b["cost"])
            elif m.type == msg.AWARD:
                tid = b["task"]
                epoch = int(b.get("e", 0))
                owner = str(b.get("winner") or b.get("dst") or m.src)
                cost = float(b.get("cost", 1e9))
                lease_until = float(b.get(
                    "u", t + self.cfg.traffic.auction_lease_s))
                task = self.open_tasks.get(tid)
                if task is not None and epoch < task.auction_epoch:
                    continue
                if task is not None and epoch > task.auction_epoch:
                    task.auction_epoch = epoch
                    task.bid_deadline = t
                    self._bids.pop(tid, None)
                    self._bid_opened.pop(tid, None)
                # A directed award is from the optional manager and is consumed by
                # the destination robot. Only peer-auction awards create expiring
                # claims; otherwise a central assignment could vanish mid-task when
                # the manager's one-shot message is older than the lease.
                if b.get("dst") is None:
                    self._record_task_claim(
                        tid, (epoch, cost, owner, lease_until))
                if b.get("dst") == self.rid:
                    self._awarded.add(tid)
            elif m.type == msg.TASK_DONE:
                tid = b["task"]
                self.open_tasks.pop(tid, None)
                self.completed_tasks.add(tid)
                self._task_claims.pop(tid, None)
                self._bids.pop(tid, None)
                self._bid_opened.pop(tid, None)
                self._awarded.discard(tid)
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
                    ttl = max(0.0, min(float(b.get(
                        "ttl", self.cfg.traffic.bios_claim_ttl_s)),
                        2.0 * self.cfg.traffic.bios_claim_ttl_s))
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
                        self.path_times = [float(x) for x in b.get("w", [])]
                        self.pidx = 1 if len(cells) > 1 else 0
                        self.epoch = b.get("e", self.epoch)
                        self.stats["central_plans"] += 1

    def _expire_peers(self, t: float) -> None:
        """Silence is information: a peer we have not heard from is a peer whose intent
        we must stop trusting. Holding stale intents is how a fleet politely gridlocks
        around a robot that died ten seconds ago."""
        stale = self.cfg.traffic.peer_stale_s
        for rid in list(self.peers):
            if t - self.peers[rid].last_seen > stale:
                self.peers[rid].intent = []
                self.peers[rid].windows = []
            if t - self.peers[rid].last_seen > stale * 6:
                del self.peers[rid]

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq
