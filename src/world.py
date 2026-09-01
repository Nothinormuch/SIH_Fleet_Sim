"""Ground truth: kinematics, sensing, and collision detection.

This module is the *referee*, and it is deliberately dumb. It knows nothing about
plans, tasks, priorities or messages, and it never forwards a packet between robots.
That separation is load-bearing for the whole submission: if the world relayed comms,
"decentralised" would be a claim about our own code rather than something a judge can
verify by watching real datagrams on a real socket.

Two consequences worth stating out loud:

* The world reports **near-misses**, not just contacts. "Zero collisions over N runs"
  is unfalsifiable - absence over finitely many trials is not evidence. A distribution
  of minimum separations is evidence, and it is what metrics.py turns into a rate with
  a confidence interval.
* Collision checks are **swept**, not endpoint. Two robots exchanging cells in one tick
  never share a position sample yet certainly hit each other. Endpoint checking is the
  single easiest way to accidentally report zero collisions.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .environment import RACK, Warehouse
from .geometry import (Vec, Cell, angle_diff, clamp, dist, segments_min_distance,
                       to_cell, wrap_angle)
from .planner import astar
from .settings import Config


@dataclass
class Actuation:
    """What the agent asks the wheels for. The only channel from brain to world."""

    v: float = 0.0            # m/s, forward positive
    omega: float = 0.0        # rad/s, CCW positive
    # Set by Layer 0 only. The world does not treat it specially - it is recorded so
    # the report can count how often the certified layer had to intervene, which is a
    # far more interesting number than the collision count.
    safety_stop: bool = False


@dataclass
class Detection:
    """An obstacle as the lidar sees it: a position and a size, and *no identity*.

    This is the point of the class. A robot cannot tell whether a blob is a peer, a
    human, a forklift or a dropped pallet. Any protocol that resolves conflicts purely
    by exchanging intent is structurally blind to everything that does not broadcast -
    so the reactive layer must work off these, not off the peer table.
    """

    x: float
    y: float
    r: float
    range_m: float
    # Estimated velocity. A real 2D safety lidar plus a tracker gives range rate, and
    # the safety layer genuinely needs it: a stationary obstacle and one closing at
    # 1.2 m/s at the same distance are not the same hazard.
    vx: float = 0.0
    vy: float = 0.0


@dataclass
class Sensors:
    t: float
    pose: tuple[float, float, float]
    v: float
    omega: float
    battery_frac: float
    cell: Cell
    clearance_m: float               # nearest obstacle of any kind, forward cone
    # Split because they are answered by different safety mechanisms on real hardware.
    # Mapped shelving and walls are known geometry the navigation stack plans around,
    # and a certified scanner switches to a smaller field set when approaching them.
    # Unexpected objects - peers, people, a dropped pallet - get the full speed-scaled
    # protective field. Collapsing the two is what freezes a robot one metre from a
    # wall it was always going to drive past.
    clearance_static_m: float = 99.0
    clearance_dynamic_m: float = 99.0
    # Nearest unexpected object in ANY direction. A forward cone cannot see a robot
    # merging from the side at a junction - the two approach at ninety degrees, each
    # sits outside the other's cone, and they meet in the middle with nothing having
    # triggered. Real AMRs carry 360 degree protective coverage for exactly this.
    clearance_omni_m: float = 99.0
    detections: list[Detection] = field(default_factory=list)
    on_dock: bool = False


@dataclass
class RobotState:
    rid: str
    x: float
    y: float
    theta: float
    v: float = 0.0
    omega: float = 0.0
    battery_wh: float = 0.0
    carrying: str | None = None
    dist_travelled: float = 0.0
    safety_stops: int = 0


@dataclass
class HumanState:
    """A non-broadcasting warehouse worker following a mapped pedestrian route.

    The worker does not participate in fleet negotiation, but remains a physical
    person: they walk only through passable space and do not deliberately step through
    a stopped robot.  Treating a human as an unstoppable ghost made the old showcase
    both visually impossible (walking through racks) and physically meaningless.
    """

    hid: str
    waypoints: list[Vec]
    speed: float = 1.15              # m/s, controlled walking pace in an active aisle
    radius: float = 0.30
    x: float = 0.0
    y: float = 0.0
    idx: int = 0
    direction: int = 1
    paused: bool = False
    yield_ticks: int = 0
    theta: float = 0.0

    def velocity(self) -> Vec:
        if len(self.waypoints) < 2 or self.paused:
            return (0.0, 0.0)
        tx, ty = self.waypoints[self.idx]
        dx, dy = tx - self.x, ty - self.y
        d = math.hypot(dx, dy)
        if d < 1e-9:
            return (0.0, 0.0)
        return (dx / d * self.speed, dy / d * self.speed)

    def step(self, dt: float) -> None:
        if len(self.waypoints) < 2:
            return
        remaining = self.speed * dt
        # Consume the complete timestep even when it begins exactly on a waypoint.
        # Returning early at a cell centre created a zero-motion candidate that the
        # personal-space guard rejected forever after a worker reversed direction.
        for _ in range(len(self.waypoints) + 1):
            tx, ty = self.waypoints[self.idx]
            dx, dy = tx - self.x, ty - self.y
            d = math.hypot(dx, dy)
            if d < 1e-9:
                self.idx = (self.idx + self.direction) % len(self.waypoints)
                continue
            travel = min(remaining, d)
            self.theta = math.atan2(dy, dx)
            self.x += dx / d * travel
            self.y += dy / d * travel
            remaining -= travel
            if d - travel < 1e-9:
                self.idx = (self.idx + self.direction) % len(self.waypoints)
            if remaining <= 1e-9:
                return


@dataclass
class ObstacleState:
    """A non-communicating pallet, spill, or dropped box in a free map cell."""

    oid: str
    x: float
    y: float
    radius: float = 0.40


@dataclass
class ContactEvent:
    t: float
    kind: str                        # robot-robot | robot-human | robot-rack
    a: str
    b: str
    separation: float


class World:
    def __init__(self, env: Warehouse, cfg: Config, seed: int = 0) -> None:
        self.env = env
        self.cfg = cfg
        self.rng = random.Random(seed)
        self.t = 0.0
        self.robots: dict[str, RobotState] = {}
        self.humans: dict[str, HumanState] = {}
        self.obstacles: dict[str, ObstacleState] = {}
        self.contacts: list[ContactEvent] = []
        # pair -> last contact time, so one physical touch is one event, not fifty
        self._contact_cooldown: dict[tuple[str, str], float] = {}
        # The evidence base for the safety claim: every pairwise closest approach.
        self.min_separations: list[float] = []
        self._pair_min: dict[tuple[str, str], float] = {}

    # ------------------------------------------------------------------ setup

    def add_robot(self, rid: str, cell: Cell, theta: float = 0.0) -> RobotState:
        cm = self.cfg.cell_m
        st = RobotState(rid, (cell[0] + 0.5) * cm, (cell[1] + 0.5) * cm, theta,
                        battery_wh=self.cfg.robot.battery_full_wh)
        self.robots[rid] = st
        return st

    def add_human(self, hid: str, waypoints: list[Cell], speed: float = 1.15) -> HumanState:
        """Add a worker and expand sparse waypoints into a closed, rack-safe route.

        Scenario authors may provide only the meaningful endpoints. Every segment,
        including the return segment, is resolved by the same A* map used by the AMRs.
        Invalid endpoints fail loudly instead of silently creating a person who walks
        through shelving.
        """
        if len(waypoints) < 2:
            raise ValueError(f"human {hid!r} requires at least two route cells")
        for cell in waypoints:
            if not self.env.passable(cell):
                raise ValueError(
                    f"human {hid!r} route cell {cell!r} is not passable")

        expanded: list[Cell] = []
        pairs = zip(waypoints, waypoints[1:] + waypoints[:1])
        for start, goal in pairs:
            segment = astar(self.env, start, goal)
            if not segment:
                raise ValueError(
                    f"human {hid!r} has no valid route from {start!r} to {goal!r}")
            expanded.extend(segment[:-1])
        if len(expanded) < 2:
            raise ValueError(f"human {hid!r} route does not contain a walkable loop")

        cm = self.cfg.cell_m
        # Perimeter routes represent marked pedestrian lanes beside the AMR travel
        # centreline. Offset them toward the warehouse boundary so a worker is visibly
        # present without occupying the same geometric lane as a robot.
        offset_x = offset_y = 0.0
        lane_offset = min(0.95, cm * 0.68)
        if all(cell[1] == 1 for cell in expanded):
            offset_y = -lane_offset
        elif all(cell[1] == self.env.height - 2 for cell in expanded):
            offset_y = lane_offset
        elif all(cell[0] == 1 for cell in expanded):
            offset_x = -lane_offset
        elif all(cell[0] == self.env.width - 2 for cell in expanded):
            offset_x = lane_offset
        pts = [
            ((cell[0] + 0.5) * cm + offset_x,
             (cell[1] + 0.5) * cm + offset_y)
            for cell in expanded
        ]
        h = HumanState(hid, pts, speed=speed, x=pts[0][0], y=pts[0][1], idx=1 % len(pts))
        self.humans[hid] = h
        return h

    def add_obstacle(self, oid: str, cell: Cell,
                     radius: float = 0.40) -> ObstacleState | None:
        """Add a physical obstacle once its footprint is genuinely unoccupied.

        A timed scenario event represents a pallet falling into a cell, not matter
        materialising inside an AMR or worker.  When the target footprint is occupied,
        return ``None`` so the scenario runner retries on a later physics tick.
        """
        if not self.env.passable(cell):
            raise ValueError(f"dynamic obstacle {oid!r} is not in a passable cell")
        cm = self.cfg.cell_m
        obstacle = ObstacleState(
            oid, (cell[0] + 0.5) * cm, (cell[1] + 0.5) * cm, radius)
        centre = (obstacle.x, obstacle.y)
        if any(
            dist(centre, (robot.x, robot.y))
            < radius + self.cfg.robot.radius_m + 0.05
            for robot in self.robots.values()
        ):
            return None
        if any(
            dist(centre, (human.x, human.y)) < radius + human.radius + 0.05
            for human in self.humans.values()
        ):
            return None
        self.obstacles[oid] = obstacle
        return obstacle

    def remove_obstacle(self, oid: str) -> None:
        self.obstacles.pop(oid, None)

    # ------------------------------------------------------------------ physics

    def step(self, dt: float, cmds: dict[str, Actuation]) -> list[ContactEvent]:
        spec = self.cfg.robot
        prev: dict[str, Vec] = {r.rid: (r.x, r.y) for r in self.robots.values()}
        prev_h: dict[str, Vec] = {h.hid: (h.x, h.y) for h in self.humans.values()}

        for rid, st in self.robots.items():
            cmd = cmds.get(rid, Actuation())
            if cmd.safety_stop:
                st.safety_stops += 1

            # Rate-limit to the actuator envelope. A planner that assumes instant
            # velocity change is a planner whose collision guarantees do not transfer
            # to hardware, so the limits live here and the agent must live with them.
            dv = clamp(cmd.v - st.v, -spec.a_max * dt, spec.a_max * dt)
            st.v = clamp(st.v + dv, -0.35 * spec.v_max, spec.v_max)
            dw = clamp(cmd.omega - st.omega, -spec.alpha_max * dt, spec.alpha_max * dt)
            st.omega = clamp(st.omega + dw, -spec.omega_max, spec.omega_max)

            nx = st.x + st.v * math.cos(st.theta) * dt
            ny = st.y + st.v * math.sin(st.theta) * dt
            ntheta = wrap_angle(st.theta + st.omega * dt)

            if self._hits_rack((nx, ny)):
                # Physically blocked. Record it and stop dead rather than tunnelling -
                # a sim that lets robots clip through shelving flatters every policy.
                self._record(self.t, "robot-rack", rid, "rack", 0.0)
                st.v = 0.0
                st.theta = ntheta
            else:
                st.dist_travelled += math.hypot(nx - st.x, ny - st.y)
                st.x, st.y, st.theta = nx, ny, ntheta

            moving = abs(st.v) > 0.05
            draw = spec.draw_move_w if moving else spec.draw_idle_w
            if self._on_dock(st):
                st.battery_wh = min(spec.battery_full_wh,
                                    st.battery_wh + spec.charge_w * dt / 3600.0)
            else:
                st.battery_wh = max(0.0, st.battery_wh - draw * dt / 3600.0)

        # Workers use their own local perception and refuse to walk through fixtures,
        # pallets, AMRs, or one another. They still publish no intent and receive no
        # fleet messages: robots must detect them with the independent safety layer.
        accepted_human_segments: list[tuple[Vec, Vec, float]] = []
        for hid in sorted(self.humans):
            h = self.humans[hid]
            old_x, old_y, old_idx, old_direction = h.x, h.y, h.idx, h.direction
            h.paused = False
            h.step(dt)
            candidate = (h.x, h.y)
            blocked = self._human_hits_static(candidate, h.radius)
            if not blocked:
                for rid, robot in self.robots.items():
                    # Preserve a personal-space margin without turning the pedestrian
                    # model into an invisible wall wider than the robot's own local
                    # braking field. Layer 0 remains responsible for the final stop.
                    threshold = h.radius + spec.radius_m + 0.27
                    start_distance = dist(prev_h[hid], (robot.x, robot.y))
                    end_distance = dist(candidate, (robot.x, robot.y))
                    clearance = segments_min_distance(
                        prev_h[hid], candidate, prev[rid], (robot.x, robot.y))
                    escaping = (start_distance < threshold
                                and end_distance > start_distance + 1e-6)
                    if clearance < threshold and not escaping:
                        blocked = True
                        break
            if not blocked:
                for other_start, other_end, other_radius in accepted_human_segments:
                    threshold = h.radius + other_radius + 0.08
                    start_distance = dist(prev_h[hid], other_end)
                    end_distance = dist(candidate, other_end)
                    clearance = segments_min_distance(
                        prev_h[hid], candidate, other_start, other_end)
                    escaping = (start_distance < threshold
                                and end_distance > start_distance + 1e-6)
                    if clearance < threshold and not escaping:
                        blocked = True
                        break
            if blocked:
                h.x, h.y = old_x, old_y
                h.direction = -old_direction
                h.idx = (old_idx - old_direction) % len(h.waypoints)
                h.paused = True
                h.yield_ticks += 1
            accepted_human_segments.append(
                (prev_h[hid], (h.x, h.y), h.radius))

        self.t += dt
        return self._check_contacts(prev, prev_h)

    def _hits_rack(self, p: Vec) -> bool:
        """Circle-vs-cell test over the 3x3 neighbourhood of the robot centre."""
        cm = self.cfg.cell_m
        r = self.cfg.robot.radius_m
        cx, cy = to_cell(p, cm)
        for gy in range(cy - 1, cy + 2):
            for gx in range(cx - 1, cx + 2):
                if not self.env.in_bounds((gx, gy)):
                    # Outside the map is a wall too.
                    continue
                if self.env.grid[gy][gx] != RACK:
                    continue
                nx = clamp(p[0], gx * cm, (gx + 1) * cm)
                ny = clamp(p[1], gy * cm, (gy + 1) * cm)
                if (p[0] - nx) ** 2 + (p[1] - ny) ** 2 < r * r:
                    return True
        # Map boundary
        if not (r <= p[0] <= self.env.width * cm - r):
            return True
        if not (r <= p[1] <= self.env.height * cm - r):
            return True
        if any(dist(p, (obstacle.x, obstacle.y)) < r + obstacle.radius
               for obstacle in self.obstacles.values()):
            return True
        return False

    def _human_hits_static(self, p: Vec, radius: float) -> bool:
        """Circle-vs-map collision check for pedestrians and dynamic obstacles."""
        cm = self.cfg.cell_m
        cx, cy = to_cell(p, cm)
        for gy in range(cy - 1, cy + 2):
            for gx in range(cx - 1, cx + 2):
                if not self.env.in_bounds((gx, gy)):
                    continue
                if self.env.grid[gy][gx] != RACK:
                    continue
                nx = clamp(p[0], gx * cm, (gx + 1) * cm)
                ny = clamp(p[1], gy * cm, (gy + 1) * cm)
                if (p[0] - nx) ** 2 + (p[1] - ny) ** 2 < radius * radius:
                    return True
        if not (radius <= p[0] <= self.env.width * cm - radius):
            return True
        if not (radius <= p[1] <= self.env.height * cm - radius):
            return True
        return any(
            dist(p, (obstacle.x, obstacle.y)) < radius + obstacle.radius
            for obstacle in self.obstacles.values()
        )

    def _on_dock(self, st: RobotState) -> bool:
        return to_cell((st.x, st.y), self.cfg.cell_m) in set(self.env.docks)

    def _check_contacts(self, prev: dict[str, Vec],
                        prev_h: dict[str, Vec]) -> list[ContactEvent]:
        r = self.cfg.robot.radius_m
        events: list[ContactEvent] = []
        ids = sorted(self.robots)

        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = self.robots[ids[i]], self.robots[ids[j]]
                sep = segments_min_distance(prev[a.rid], (a.x, a.y),
                                            prev[b.rid], (b.x, b.y))
                key = (a.rid, b.rid)
                self._pair_min[key] = min(self._pair_min.get(key, 1e9), sep)
                if sep < 2 * r:
                    ev = self._record(self.t, "robot-robot", a.rid, b.rid, sep)
                    if ev:
                        events.append(ev)

        for rid in ids:
            a = self.robots[rid]
            for hid, h in self.humans.items():
                sep = segments_min_distance(prev[rid], (a.x, a.y),
                                            prev_h[hid], (h.x, h.y))
                key = (rid, hid)
                self._pair_min[key] = min(self._pair_min.get(key, 1e9), sep)
                if sep < r + h.radius:
                    ev = self._record(self.t, "robot-human", rid, hid, sep)
                    if ev:
                        events.append(ev)
        return events

    def _record(self, t: float, kind: str, a: str, b: str,
                sep: float) -> ContactEvent | None:
        key = (a, b) if a < b else (b, a)
        last = self._contact_cooldown.get(key, -1e9)
        if t - last < 1.0:
            return None
        self._contact_cooldown[key] = t
        ev = ContactEvent(t, kind, a, b, sep)
        self.contacts.append(ev)
        return ev

    def finalize(self) -> None:
        """Flush per-pair closest approaches into the sample the statistics use."""
        self.min_separations.extend(v for v in self._pair_min.values() if v < 1e8)

    # ------------------------------------------------------------------ sensing

    def sense(self, rid: str, pose_noise_m: float = 0.0) -> Sensors:
        """What one robot's onboard sensors report. No identities, no global state.

        `pose_noise_m` exists because localisation error - not network latency - is
        what actually causes warehouse collisions. At 1.2 m/s a 50 ms round trip is
        6 cm of travel; a 10 cm localisation error is 10 cm of *position*, all the
        time, and it does not care how the fleet is coordinated.
        """
        spec = self.cfg.robot
        st = self.robots[rid]
        px, py = st.x, st.y
        if pose_noise_m > 0:
            px += self.rng.gauss(0.0, pose_noise_m)
            py += self.rng.gauss(0.0, pose_noise_m)

        dets: list[Detection] = []
        for other in self.robots.values():
            if other.rid == rid:
                continue
            d = dist((st.x, st.y), (other.x, other.y))
            if d <= spec.sense_radius_m:
                dets.append(Detection(other.x, other.y, spec.radius_m, d,
                                      other.v * math.cos(other.theta),
                                      other.v * math.sin(other.theta)))
        for h in self.humans.values():
            d = dist((st.x, st.y), (h.x, h.y))
            if d <= spec.sense_radius_m:
                hv = h.velocity()
                dets.append(Detection(h.x, h.y, h.radius, d, hv[0], hv[1]))
        for obstacle in self.obstacles.values():
            d = dist((st.x, st.y), (obstacle.x, obstacle.y))
            if d <= spec.sense_radius_m:
                dets.append(Detection(
                    obstacle.x, obstacle.y, obstacle.radius, d, 0.0, 0.0))

        stat, dyn = self._cone_clearance(st, dets)
        omni = min([d.range_m - spec.radius_m - d.r for d in dets], default=99.0)
        return Sensors(
            t=self.t,
            pose=(px, py, st.theta),
            v=st.v,
            omega=st.omega,
            battery_frac=st.battery_wh / spec.battery_full_wh,
            cell=to_cell((px, py), self.cfg.cell_m),
            clearance_m=min(stat, dyn),
            clearance_static_m=stat,
            clearance_dynamic_m=dyn,
            clearance_omni_m=max(0.0, omni),
            detections=dets,
            on_dock=self._on_dock(st),
        )

    def _cone_clearance(self, st: RobotState,
                        dets: list[Detection]) -> tuple[float, float]:
        """Nearest obstruction inside the forward cone.

        Rays are cast with Amanatides-Woo grid traversal - one iteration per cell
        crossed rather than one per fixed-length sample. That is the difference between
        this loop costing about four iterations per ray and costing eighty, and at
        50 Hz x N robots it decides whether the benchmark runs in seconds or minutes.
        It is also the honest thing to profile: this is the loop that would run on the
        Pi, so `plan_cpu_max_ms` in the report is measuring the real hot path.

        The cast stops at `safety_slow_m` plus a margin. Nothing above that distance can
        change a control decision, so resolving it would be work the robot never uses.
        """
        spec = self.cfg.robot
        # Resolve out to the largest field the robot can ever ask for; beyond that no
        # reading can change a control decision, so computing it would be wasted work.
        reach = spec.slow_field_m(spec.v_max) + 0.3
        best = reach            # mapped geometry: racks and the map boundary
        dyn = reach             # everything the map does not know about

        # Mapped geometry is probed with a NARROW cone - only what the robot would
        # actually drive into. A wide cone at a wall-hugging pose always reads a few
        # centimetres, so it would freeze every robot parked beside shelving, which is
        # where picks happen. Lateral clearance is guaranteed by the map instead: a
        # The configured cell pitch and a 0.35 m radius leave room to rotate in place.
        rays = 3
        for k in range(rays):
            frac = (k / (rays - 1)) * 2 - 1 if rays > 1 else 0.0
            ang = st.theta + frac * spec.static_cone_rad
            hit = self._cast(st.x, st.y, math.cos(ang), math.sin(ang), best)
            if hit is not None:
                best = min(best, max(0.0, hit - spec.radius_m))

        for det in dets:
            if abs(angle_diff(math.atan2(det.y - st.y, det.x - st.x),
                              st.theta)) <= spec.safety_cone_rad:
                dyn = min(dyn, max(0.0, det.range_m - spec.radius_m - det.r))
        return best, dyn

    def _cast(self, ox: float, oy: float, dx: float, dy: float,
              max_d: float) -> float | None:
        """Distance from (ox, oy) along (dx, dy) to the first rack cell or the map edge.

        Standard voxel traversal: step to whichever axis boundary is nearer, so every
        iteration crosses exactly one cell and none are missed or visited twice.
        """
        cm = self.cfg.cell_m
        gx, gy = to_cell((ox, oy), cm)
        step_x = 1 if dx > 0 else -1
        step_y = 1 if dy > 0 else -1
        inf = float("inf")

        if abs(dx) < 1e-12:
            t_max_x, t_delta_x = inf, inf
        else:
            bound_x = (gx + (1 if dx > 0 else 0)) * cm
            t_max_x = (bound_x - ox) / dx
            t_delta_x = cm / abs(dx)
        if abs(dy) < 1e-12:
            t_max_y, t_delta_y = inf, inf
        else:
            bound_y = (gy + (1 if dy > 0 else 0)) * cm
            t_max_y = (bound_y - oy) / dy
            t_delta_y = cm / abs(dy)

        while True:
            if t_max_x < t_max_y:
                t_max_x += t_delta_x
                gx += step_x
                travelled = t_max_x - t_delta_x
            else:
                t_max_y += t_delta_y
                gy += step_y
                travelled = t_max_y - t_delta_y
            if travelled > max_d:
                return None
            if not self.env.in_bounds((gx, gy)):
                return travelled
            if self.env.grid[gy][gx] == RACK:
                return travelled

    # ------------------------------------------------------------------ export

    def snapshot(self) -> dict:
        return {
            "t": round(self.t, 3),
            "robots": [
                {"id": r.rid, "x": round(r.x, 3), "y": round(r.y, 3),
                 "th": round(r.theta, 3), "v": round(r.v, 3),
                 "batt": round(r.battery_wh / self.cfg.robot.battery_full_wh, 3),
                 "carry": r.carrying}
                for r in self.robots.values()
            ],
            "humans": [
                {
                    "id": h.hid,
                    "x": round(h.x, 3),
                    "y": round(h.y, 3),
                    "th": round(h.theta, 3),
                    "paused": h.paused,
                    "yield_ticks": h.yield_ticks,
                }
                for h in self.humans.values()
            ],
            "obstacles": [
                {"id": obstacle.oid, "x": round(obstacle.x, 3),
                 "y": round(obstacle.y, 3), "r": obstacle.radius}
                for obstacle in self.obstacles.values()
            ],
            "contacts": len(self.contacts),
        }
