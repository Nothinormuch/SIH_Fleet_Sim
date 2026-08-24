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

from .environment import Warehouse
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
