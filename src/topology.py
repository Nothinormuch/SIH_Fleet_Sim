"""Topology facts used by decentralised traffic priority.

PIBT has a finite-reachability guarantee on biconnected graphs, but warehouse maps
also contain tree-shaped loading spurs and dead ends.  This module identifies those
tree appendages without any third-party dependency.  A robot leaving such a branch
receives temporary exit priority; admitting more traffic into the branch first is the
classic way to turn a local conflict into a permanent deadlock.

The decomposition is the graph 2-core: repeatedly remove vertices with fewer than two
remaining neighbours.  Removed connected components are tree appendages.  This is a
linear O(V + E) preprocessing pass and is cached per immutable Warehouse instance.
"""

from __future__ import annotations

from dataclasses import dataclass
import functools

from .environment import Warehouse, corridors
from .geometry import Cell


@dataclass(frozen=True)
class TopologyMap:
    """Cycle-rich core plus tree-shaped appendages attached to it."""

    core: frozenset[Cell]
    branch_of: dict[Cell, int]
    branches: dict[int, frozenset[Cell]]
    roots: dict[int, tuple[Cell, ...]]

    def same_branch(self, a: Cell, b: Cell) -> bool:
        branch = self.branch_of.get(a)
        return branch is not None and self.branch_of.get(b) == branch

    def leaving_branch(self, cell: Cell, goal: Cell | None) -> bool:
        """True when the robot must leave a tree appendage to reach ``goal``."""
        if goal is None or cell not in self.branch_of:
            return False
        return not self.same_branch(cell, goal)


@functools.lru_cache(maxsize=16)
def analyse_topology(env: Warehouse) -> TopologyMap:
    """Return the deterministic 2-core/tree decomposition of ``env``."""
    vertices = set(env.free_cells())
    neighbours = {v: set(env.neighbors(v)) for v in vertices}
    degree = {v: len(ns) for v, ns in neighbours.items()}
    queue = sorted(v for v, d in degree.items() if d < 2)
    removed: set[Cell] = set()

    # A sorted list is fast enough for warehouse grids and makes the result stable.
    while queue:
        cur = queue.pop(0)
        if cur in removed:
            continue
        removed.add(cur)
        for nxt in sorted(neighbours[cur]):
            if nxt in removed:
                continue
            degree[nxt] -= 1
            if degree[nxt] == 1:
                queue.append(nxt)
        queue.sort()

    core = vertices - removed
    branch_of: dict[Cell, int] = {}
    branches: dict[int, frozenset[Cell]] = {}
    roots: dict[int, tuple[Cell, ...]] = {}
    seen: set[Cell] = set()

    for start in sorted(removed):
        if start in seen:
            continue
        stack = [start]
        seen.add(start)
        component: set[Cell] = set()
        while stack:
            cur = stack.pop()
            component.add(cur)
            for nxt in neighbours[cur]:
                if nxt in removed and nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)

        bid = len(branches)
        branch = frozenset(component)
        root_cells = tuple(sorted({n for c in component for n in neighbours[c]
                                   if n in core}))
        branches[bid] = branch
        roots[bid] = root_cells
        for cell in component:
            branch_of[cell] = bid

    return TopologyMap(frozenset(core), branch_of, branches, roots)


@dataclass(frozen=True)
class CirculationMap:
    """Deterministic one-way directions for a grid warehouse's narrow segments.

    Alternating vertical and horizontal aisle directions turn the rack layout into a
    strongly connected circulation graph.  AMRs may take a longer loop, but can never
    meet head-on; destination-cell leases serialize merges.
    """

    enabled: bool
    upstream: dict[int, Cell]
    downstream: dict[int, Cell]

    def allows(self, env: Warehouse, a: Cell, b: Cell) -> bool:
        if a == b or not self.enabled:
            return True
        # Strongly connected one-way perimeter orientation for the standard map.
        # Together with the alternating rack aisles, this removes every head-on edge
        # while preserving a directed route between every pair of free cells.
        horizontal = {0: -1, 1: -1, env.height - 2: -1, env.height - 1: 1}
        vertical = {0: 1, 1: -1, env.width - 3: 1,
                    env.width - 2: -1, env.width - 1: -1}
        if a[1] == b[1] and a[1] in horizontal:
            return (b[0] - a[0]) * horizontal[a[1]] > 0
        if a[0] == b[0] and a[0] in vertical:
            return (b[1] - a[1]) * vertical[a[0]] > 0
        if 2 <= a[0] <= env.width - 3 and a[0] == b[0]:
            if {a[1], b[1]} == {0, 1}:
                return (a[1], b[1]) == (0, 1)
            if {a[1], b[1]} == {env.height - 2, env.height - 1}:
                return (a[1], b[1]) == (env.height - 1, env.height - 2)
        # Station/dock transfer rungs alternate direction.  The two corner rungs use
        # the orientation that closes the outer circulation loop.  This exact directed
        # graph is strongly connected: every free cell reaches every other free cell,
        # but no edge also admits its reverse.
        if a[1] == b[1]:
            if {a[0], b[0]} == {0, 1}:
                wanted = ((0, 1) if a[1] == env.height - 1
                          else ((1, 0) if a[1] % 2 == 0 else (0, 1)))
                return (a[0], b[0]) == wanted
            if {a[0], b[0]} == {env.width - 2, env.width - 1}:
                wanted = ((env.width - 1, env.width - 2) if a[1] == 0
                          else ((env.width - 2, env.width - 1)
                                if a[1] % 2 == 0
                                else (env.width - 1, env.width - 2)))
                return (a[0], b[0]) == wanted
            if {a[0], b[0]} == {env.width - 3, env.width - 2}:
                wanted = ((env.width - 3, env.width - 2)
                          if a[1] % 2 == 0
                          else (env.width - 2, env.width - 3))
                return (a[0], b[0]) == wanted
        blocks = corridors(env)
        ca, cb = blocks.id_of(a), blocks.id_of(b)
        if ca is None and cb is None:
            return True
        if ca is not None and ca == cb:
            end = self.downstream[ca]
            before = abs(a[0] - end[0]) + abs(a[1] - end[1])
            after = abs(b[0] - end[0]) + abs(b[1] - end[1])
            return after < before
        if ca is None and cb is not None:
            return b == self.upstream[cb]
        if ca is not None and cb is None:
            return a == self.downstream[ca]
        return False


@functools.lru_cache(maxsize=16)
def directed_circulation(env: Warehouse) -> CirculationMap:
    """Build an alternating aisle circulation when the topology supports one.

    A single chokepoint is intentionally excluded: making it permanently one-way
    would make reverse-direction tasks unreachable.  The classic racking map has many
    parallel vertical and horizontal lanes; alternating both sets is strongly
    connected and every free cell remains reachable from every other free cell.
    """
    blocks = corridors(env)
    vertical: dict[int, int] = {}
    horizontal: dict[int, int] = {}
    for cid, members in blocks.members.items():
        xs = {c[0] for c in members}
        ys = {c[1] for c in members}
        if len(xs) == 1:
            vertical[cid] = next(iter(xs))
        elif len(ys) == 1:
            horizontal[cid] = next(iter(ys))

    vx = sorted(set(vertical.values()))
    hy = sorted(set(horizontal.values()))
    if len(vx) < 2 or len(hy) < 2:
        return CirculationMap(False, {}, {})

    upstream: dict[int, Cell] = {}
    downstream: dict[int, Cell] = {}
    for cid, axis in vertical.items():
        ends = blocks.ends[cid]
        low, high = min(ends, key=lambda c: c[1]), max(ends, key=lambda c: c[1])
        upstream[cid], downstream[cid] = ((low, high) if vx.index(axis) % 2 == 0
                                          else (high, low))
    for cid, axis in horizontal.items():
        ends = blocks.ends[cid]
        low, high = min(ends, key=lambda c: c[0]), max(ends, key=lambda c: c[0])
        upstream[cid], downstream[cid] = ((low, high) if hy.index(axis) % 2 == 0
                                          else (high, low))
    return CirculationMap(True, upstream, downstream)
