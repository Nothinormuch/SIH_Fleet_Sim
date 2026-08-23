"""Tunable constants for the whole system.

Everything physical is SI. One grid cell is CELL_M metres square; the planner works
in cells, the world works in metres, and this file is the only place the two meet.

Numbers here are not invented: they are the operating envelope of commercial warehouse
AMRs (Locus Origin, 6 River Chuck, Geek+ P-series) so that the latency and throughput
arguments in the report survive arithmetic.
"""

from dataclasses import dataclass, field, asdict


@dataclass(frozen=True)
class RobotSpec:
    """Physical envelope of one AMR."""

    radius_m: float = 0.35          # footprint radius; two robots touch at 0.70 m
    v_max: float = 1.2              # m/s  (commercial AMRs run 1.0-2.0 in aisles)
    a_max: float = 0.8              # m/s^2
    omega_max: float = 1.6          # rad/s  (~92 deg/s turn-in-place)
    alpha_max: float = 3.2          # rad/s^2

    # --- Layer 0: protective stop. Certified, local, never network-dependent. ---
    # The protective field is SPEED-DEPENDENT, which is how real AMR safety scanners
    # work (ISO 3691-4 / EN ISO 13849 field switching) and not an optimisation. A fixed
    # field is wrong in both directions: too small at speed to stop in time, and so
    # large at rest that a stationary robot one metre from shelving can never set off -
    # which is exactly the deadlock a fixed 1.8 m cone produced here.
    reaction_s: float = 0.10        # sense-to-brake latency allowance
    safety_margin_m: float = 0.15   # standstill clearance the robot never gives up
    omni_stop_m: float = 0.30       # 360 deg guard: stop if anything is this close
    safety_cone_rad: float = 1.05   # +/- 60 deg: unexpected objects (people, peers)
    static_cone_rad: float = 0.35   # +/- 20 deg: mapped shelving directly in the path
    v_turn: float = 0.20            # speed carried through a 90 deg direction change

    sense_radius_m: float = 4.0     # onboard lidar range for *unlabelled* obstacles
    battery_full_wh: float = 480.0
    draw_move_w: float = 210.0
    draw_idle_w: float = 45.0
    charge_w: float = 900.0

    def stop_field_m(self, v: float) -> float:
        """Distance needed to stop from speed `v`, plus reaction and margin.

        v^2/(2a) is the braking distance; v*reaction covers the delay between the
        scanner seeing something and the brakes biting. At v_max this is ~1.17 m and
        at standstill it collapses to the 0.15 m margin, so a parked robot can pull
        away from a wall while a robot at full speed cannot drive into one.
        """
        v = abs(v)
        return v * v / (2 * self.a_max) + v * self.reaction_s + self.safety_margin_m

    def slow_field_m(self, v: float) -> float:
        """Warning field: start derating here so the stop is smooth, not a slam."""
        return 2.0 * self.stop_field_m(v)

    def max_speed_for_clearance(self, clearance_m: float,
                                v_closing: float = 0.0) -> float:
        """The fastest speed from which this clearance is still enough to stop.

        This is `stop_field_m` inverted, and using it instead of a linear taper is what
        separates a fleet that flows from a fleet that crawls. A taper between a stop
        field and an arbitrary warning distance has no physical meaning: it made a
        robot with 1.3 m of clear space in front of it creep at 0.04 m/s, because the
        speed it *wanted* sized the field that then throttled it - and the field never
        shrank, because it never got to move.

        Solving v^2/(2a) + v*tau + margin = clearance for v gives the honest answer:
        with 1.3 m ahead this robot may travel at full speed, and it still stops in
        time. Braking authority is a fact about the chassis, not a tuning knob.
        """
        # Braking is the only tool we have, and it only slows US. Anything approaching
        # keeps approaching for the whole time we are stopping, so its contribution is
        # a distance we must already own before we start. Ignoring it is why two robots
        # meeting head-on in an aisle each brake correctly for their own speed and hit
        # anyway: they close at up to 2 x v_max while each budgets for 1 x.
        #
        #   gap >= v*tau + v^2/(2a) + v_close*(tau + v/a) + margin
        #
        # Solved for v. The v_close*v/a term is why this is not just a bigger constant:
        # the faster we go the longer we take to stop and the further they get.
        a = self.a_max
        A = 1.0 / (2.0 * a)
        B = self.reaction_s + max(0.0, v_closing) / a
        C = max(0.0, v_closing) * self.reaction_s + self.safety_margin_m - clearance_m
        if C >= 0:
            return 0.0
        disc = B * B - 4.0 * A * C
        if disc <= 0:
            return 0.0
        v = (-B + disc ** 0.5) / (2.0 * A)
        return max(0.0, min(v, self.v_max))


@dataclass(frozen=True)
class Rates:
    """The three control loops the problem statement conflates into one.

    Keeping them separate is the whole argument: only the slowest one was ever
    a candidate for a central server, and it is the one least hurt by latency.
    """

    world_hz: float = 50.0          # ground-truth integration
    safety_hz: float = 50.0         # Layer 0 - onboard, certified, no network
    reactive_hz: float = 10.0       # Layer 1 - local avoidance / yielding
    route_hz: float = 1.0           # Layer 2 - global route; central when reachable
    heartbeat_hz: float = 5.0       # pose/intent broadcast
    telemetry_hz: float = 10.0      # dashboard push (passive listener, not a coordinator)


@dataclass(frozen=True)
class NetSpec:
    """Model of the radio, used identically by the batch sim and the UDP runner.

    Defaults describe a healthy 5 GHz warehouse WLAN. `loss` and `dead_zones` are what
    the sweeps move. Latency is deliberately small: on a real LAN it is 1-5 ms, and
    saying so out loud is half the critique.
    """

    latency_mean_s: float = 0.004   # 4 ms - on-prem LAN, not cloud
    latency_jitter_s: float = 0.003
    loss: float = 0.0               # uniform packet loss probability [0,1]
    mtu_bytes: int = 1400
    # Radio holes are geometric, and they take out peer-to-peer traffic exactly as
    # hard as they take out server traffic. Each entry is (cx, cy, radius) in cells.
    dead_zones: tuple = ()
    # Infrastructure-mode Wi-Fi relays "peer to peer" frames through the AP. When True
    # (the honest default) a robot inside a dead zone cannot reach peers either.
    peer_traffic_via_ap: bool = True


@dataclass(frozen=True)
class TrafficSpec:
    """Layer 1 / Layer 2 coordination parameters."""

    intent_horizon: int = 6         # cells of path published in an INTENT message
    peer_stale_s: float = 1.0       # drop a peer from the table after this silence
    yield_backoff_s: float = 0.4
    # Blocked at a mouth this long with the blocker inside -> pull off the axis.
    yield_aside_s: float = 2.0
    deadlock_wait_s: float = 4.0    # blocked this long -> run cycle detection
    # Waiting for a single-file block to clear is normal traffic, not deadlock. Must
    # comfortably exceed the transit time of the longest aisle on the map.
    block_wait_s: float = 30.0
    # Block control costs a commit round per entry, so it is only applied where a
    # head-on meeting would actually be unrecoverable. Both thresholds are empirical:
    # applying block control everywhere made throughput worse than no control at all.
    min_controlled_block: int = 6   # cells; shorter gaps use plain per-cell yielding
    apron_block_len: int = 8        # cells; keep the doorway clear only for long runs
    livelock_progress_s: float = 12.0  # no net progress this long -> escalate
    central_timeout_s: float = 1.5  # no fleet-manager reply -> DEGRADED_P2P
    central_retry_s: float = 3.0
    # Two heartbeat periods: long enough for our intent to reach every peer and for
    # theirs to reach us, short enough not to dominate travel time through a block.
    gate_commit_s: float = 0.45
    replan_penalty: float = 6.0     # cost added to a contested cell when detouring
    # BIOS_1.0.0 only: a robot held still longer than this edges into ANY free
    # adjacent cell so it can never settle permanently. Deliberately short: the
    # point is a liveness guarantee, not polite traffic theory.
    bios_unstick_s: float = 2.0
    # BIOS_1.0.0 block-token lifetime. Held only while physically inside the block
    # (re-broadcast every heartbeat), this just needs to cover entry + propagation.
    bios_claim_ttl_s: float = 4.0
    # Distributed task allocation. The task injector publishes the deadline so every
    # robot can close the same auction without an auctioneer choosing the winner.
    auction_bid_window_s: float = 0.6
    auction_lease_s: float = 20.0


@dataclass(frozen=True)
class Config:
    cell_m: float = 1.0
    robot: RobotSpec = field(default_factory=RobotSpec)
    rates: Rates = field(default_factory=Rates)
    net: NetSpec = field(default_factory=NetSpec)
    traffic: TrafficSpec = field(default_factory=TrafficSpec)
    seed: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


DEFAULT = Config()
