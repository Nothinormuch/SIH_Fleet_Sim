"""Deterministic Priority Inheritance with Backtracking (PIBT) primitives.

Every AMR can run this module from the same map plus peer broadcasts.  It performs no
I/O, owns no clock and has no global singleton, so the decision code is identical in a
headless simulation and on an edge node.  It resolves only the next grid-cell move;
the existing A* route remains the long-range heuristic and the certified local safety
layer remains authoritative.

This is intentionally a small, auditable implementation of the core PIBT mechanism:
unique dynamic priorities, transitive inheritance, vertex/edge collision prevention
and backtracking when a pushed robot cannot vacate its cell.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping

from .environment import Warehouse
from .geometry import Cell, manhattan


@dataclass(frozen=True, order=True)
class PriorityKey:
    """A stable lexicographic priority token; larger keys move first.

    The key is frozen when broadcast.  Comparing published tokens on both sides avoids
    the symmetric-yield bug caused by comparing a live ageing value against a stale
    peer value.  ``robot_id`` is the final total-order tiebreaker only.
    """

    emergency: int = 0
    exiting_branch: int = 0
    waiting_age: int = 0
    service_age: int = 0
    loaded: int = 0
    distance_bias: int = 0
    robot_id: str = ""

    def to_wire(self) -> list[int | str]:
        return [self.emergency, self.exiting_branch, self.waiting_age,
                self.service_age, self.loaded, self.distance_bias, self.robot_id]

    @classmethod
    def from_wire(cls, value, fallback_id: str) -> "PriorityKey":
        if not isinstance(value, (list, tuple)) or len(value) != 7:
            return cls(robot_id=fallback_id)
        try:
            return cls(*(int(x) for x in value[:6]), robot_id=str(value[6]))
        except (TypeError, ValueError):
            return cls(robot_id=fallback_id)


@dataclass
class StepDecision:
    """One collision-free discrete configuration produced by PIBT."""

    next_cells: dict[str, Cell]
    effective_priorities: dict[str, PriorityKey]
    inherited_from: dict[str, str] = field(default_factory=dict)
    blocked_by: dict[str, str] = field(default_factory=dict)
    backtracks: int = 0


def _candidates(env: Warehouse, rid: str, positions: Mapping[str, Cell],
                goals: Mapping[str, Cell], preferred: Mapping[str, Cell],
                edge_allowed: Callable[[Cell, Cell], bool] | None = None) -> list[Cell]:
    current = positions[rid]
    goal = goals.get(rid, current)
    wanted = preferred.get(rid)
    cells = [current, *(c for c in env.neighbors(current)
                        if edge_allowed is None or edge_allowed(current, c))]

    # Prefer the robot's existing A* waypoint, then progress toward its goal.  Waiting
    # loses deterministic ties so an inherited robot actively looks for room to vacate.
    return sorted(set(cells), key=lambda c: (
        0 if c == wanted else 1,
        manhattan(c, goal),
        1 if c == current else 0,
        c[1], c[0],
    ))


def pibt_step(env: Warehouse, positions: Mapping[str, Cell],
              goals: Mapping[str, Cell], priorities: Mapping[str, PriorityKey],
              preferred: Mapping[str, Cell] | None = None,
              max_depth: int = 64,
              edge_allowed: Callable[[Cell, Cell], bool] | None = None) -> StepDecision:
    """Resolve a collision-free next cell for every known robot.

    ``positions`` is the locally reconstructed fleet snapshot.  When a high-priority
    robot selects an occupied cell, the occupant recursively inherits that priority and
    tries to vacate.  If the chain has no legal end, its tentative assignments are
    rolled back and the requester tries its next candidate.

    The function rejects duplicate current cells because there is no valid discrete
    configuration to resolve from; the caller must then fall back to the physical
    safety layer rather than manufacture a plan from an impossible state.
    """
    preferred = preferred or {}
    if len(set(positions.values())) != len(positions):
        raise ValueError("PIBT requires one robot per current cell")

    base = {rid: priorities.get(rid, PriorityKey(robot_id=rid)) for rid in positions}
    effective = dict(base)
    assigned: dict[str, Cell] = {}
    reserved: dict[Cell, str] = {}
    inherited_from: dict[str, str] = {}
    backtracks = 0

    occupied_now = {cell: rid for rid, cell in positions.items()}

    def assign(rid: str, inherited: PriorityKey | None = None,
               parent: str | None = None, depth: int = 0) -> bool:
        nonlocal backtracks
        if rid in assigned:
            return True
        if depth > max_depth:
            return False

        if inherited is not None and inherited > effective[rid]:
            effective[rid] = inherited
            if parent is not None:
                inherited_from[rid] = parent

        current = positions[rid]
        for target in _candidates(env, rid, positions, goals, preferred, edge_allowed):
            next_owner = reserved.get(target)
            if next_owner is not None and next_owner != rid:
                continue

            occupant = occupied_now.get(target)
            if occupant is not None and occupant != rid:
                # Ban a head-on edge swap.  Rotations of length >= 3 remain legal.
                if assigned.get(occupant) == current:
                    continue

            # Recursion is tiny for warehouse conflicts, so snapshots make rollback
            # explicit and reliable rather than depending on subtle mutation order.
            snap_assigned = dict(assigned)
            snap_reserved = dict(reserved)
            snap_effective = dict(effective)
            snap_inherited = dict(inherited_from)

            assigned[rid] = target
            reserved[target] = rid
            valid = True
            if occupant is not None and occupant != rid and occupant not in assigned:
                valid = assign(occupant, effective[rid], rid, depth + 1)

            if valid:
                return True

            assigned.clear()
            assigned.update(snap_assigned)
            reserved.clear()
            reserved.update(snap_reserved)
            effective.clear()
            effective.update(snap_effective)
            inherited_from.clear()
            inherited_from.update(snap_inherited)
            backtracks += 1

        # Waiting is the safe failure outcome, but only if no earlier assignment has
        # reserved this cell.  If it has, return failure so that requester backtracks.
        if current not in reserved:
            assigned[rid] = current
            reserved[current] = rid
            return False
        return False

    for rid in sorted(positions, key=lambda r: (base[r], r), reverse=True):
        if rid not in assigned:
            assign(rid)

    # A correct backtrack always leaves every robot assigned exactly once.  Refuse to
    # output a partial configuration; stopping is safer than inventing occupancy.
    if set(assigned) != set(positions) or len(set(assigned.values())) != len(assigned):
        raise RuntimeError("PIBT failed to construct a complete configuration")

    # Defensive property check for edge swaps.
    for a, a_to in assigned.items():
        for b, b_to in assigned.items():
            if a < b and a_to == positions[b] and b_to == positions[a]:
                raise RuntimeError("PIBT produced a forbidden edge swap")

    blocked_by: dict[str, str] = {}
    for rid, target in assigned.items():
        wanted = preferred.get(rid)
        if target != positions[rid] or wanted is None or wanted == positions[rid]:
            continue
        blocker = occupied_now.get(wanted) or reserved.get(wanted)
        if blocker is not None and blocker != rid:
            blocked_by[rid] = blocker

    return StepDecision(assigned, effective, inherited_from, blocked_by, backtracks)
