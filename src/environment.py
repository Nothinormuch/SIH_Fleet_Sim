"""The warehouse map: a 4-connected grid of cells, plus the fixtures on it.

The map is deliberately the *only* thing every layer agrees on. The planner searches
it, the world collides against it, the dashboard draws it, and the benchmark pins it.
"20% faster than stop-and-wait" is meaningless without a fixed map and a fixed task
stream, so scenarios.py pins both and this module makes them reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass
import functools
from typing import Iterable, Iterator

from .geometry import Cell

FREE = 0
RACK = 1        # static shelving - never passable
STATION = 2     # pick / drop station - passable, tasks target these
DOCK = 3        # charge pad - passable


@dataclass(frozen=True)
class Warehouse:
    width: int
    height: int
    grid: tuple[tuple[int, ...], ...]     # grid[y][x]
    stations: tuple[Cell, ...]
    docks: tuple[Cell, ...]
    name: str = "warehouse"

    # ---------------------------------------------------------------- queries

    def in_bounds(self, c: Cell) -> bool:
        x, y = c
        return 0 <= x < self.width and 0 <= y < self.height

    def passable(self, c: Cell) -> bool:
        if not self.in_bounds(c):
            return False
        return self.grid[c[1]][c[0]] != RACK

    def neighbors(self, c: Cell) -> Iterator[Cell]:
        """4-connected. AMRs are differential-drive; diagonal moves would need a
        clearance check this grid cannot express, so they are simply not offered."""
        x, y = c
        for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if self.passable(n):
                yield n

    def degree(self, c: Cell) -> int:
        return sum(1 for _ in self.neighbors(c))

    def free_cells(self) -> Iterator[Cell]:
        for y in range(self.height):
            for x in range(self.width):
                if self.grid[y][x] != RACK:
                    yield (x, y)

    def chokepoints(self) -> frozenset[Cell]:
        """Passable cells with at most two exits - a robot that stops here blocks
        the aisle. These are exactly the cells where stop-and-wait deadlocks, so the
        benchmark reports conflicts *at chokepoints* separately from conflicts overall.
        """
        return frozenset(c for c in self.free_cells() if self.degree(c) <= 2)

    # ---------------------------------------------------------------- rendering

    def ascii(self, marks: dict[Cell, str] | None = None) -> str:
        marks = marks or {}
        glyph = {FREE: ".", RACK: "#", STATION: "P", DOCK: "C"}
        rows = []
        for y in range(self.height - 1, -1, -1):     # +Y is up, as in the asset spec
            rows.append("".join(marks.get((x, y), glyph[self.grid[y][x]])
                                for x in range(self.width)))
        return "\n".join(rows)

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "grid": [list(row) for row in self.grid],
            "stations": [list(s) for s in self.stations],
            "docks": [list(d) for d in self.docks],
        }


def _build(width: int, height: int, racks: Iterable[Cell],
           stations: Iterable[Cell], docks: Iterable[Cell], name: str) -> Warehouse:
    g = [[FREE] * width for _ in range(height)]
    for x, y in racks:
        g[y][x] = RACK
    st, dk = tuple(stations), tuple(docks)
    for x, y in st:
        g[y][x] = STATION
    for x, y in dk:
        g[y][x] = DOCK
    return Warehouse(width, height, tuple(tuple(r) for r in g), st, dk, name)


def classic_warehouse(width: int = 31, height: int = 21, rack_w: int = 2,
                      rack_h: int = 4, name: str = "classic") -> Warehouse:
    """Rack blocks separated by single-cell aisles, with a free perimeter ring.

    Single-cell aisles are the point: they make head-on conflict unavoidable and give
    the traffic layer something real to solve. Wider aisles turn any policy into a
    winner and make the 20% claim vacuous.
    """
    racks: list[Cell] = []
    for y in range(2, height - 2):
        for x in range(2, width - 2):
            in_rack_col = (x - 2) % (rack_w + 1) < rack_w
            in_rack_row = (y - 2) % (rack_h + 1) < rack_h
            if in_rack_col and in_rack_row:
                racks.append((x, y))

    rack_set = set(racks)
    stations = tuple((0, y) for y in range(2, height - 2, 4) if (0, y) not in rack_set)
    docks = tuple((width - 1, y) for y in range(2, min(height - 2, 2 + 4 * 4), 4))
    return _build(width, height, racks, stations, docks, name)


def chokepoint_warehouse(length: int = 15, name: str = "chokepoint") -> Warehouse:
    """The pinned stress map: two open bays joined by one single-file corridor.

    Every crossing pair must negotiate the same cells. This is the scenario the 20%
    number is quoted against, because it is the only one where the coordination policy
    - rather than the map - decides the outcome.
    """
    bay = 6
    width = bay * 2 + length
    height = 9
    mid = height // 2
    racks: list[Cell] = []
    for y in range(height):
        for x in range(bay, bay + length):
            if y != mid:
                racks.append((x, y))
    stations = tuple((0, y) for y in (1, mid, height - 2))
    docks = tuple((width - 1, y) for y in (1, mid, height - 2))
    return _build(width, height, racks, stations, docks, name)


def open_floor(width: int = 20, height: int = 20, name: str = "open") -> Warehouse:
    """Control map with no structure. Used to show the honest result: with no
    chokepoints, every policy ties, and any speedup claim comes from the map."""
    stations = tuple((0, y) for y in range(2, height - 2, 5))
    docks = tuple((width - 1, y) for y in range(2, height - 2, 5))
    return _build(width, height, (), stations, docks, name)


@functools.lru_cache(maxsize=8)
def corridors(env: Warehouse) -> "CorridorMap":
    """Decompose the map into single-file blocks - the traffic-control primitive.

    A chokepoint is not a cell, it is a *run* of cells with no room to pass. Resolving
    conflict cell-by-cell cannot fix one: two robots that meet halfway down a one-lane
    aisle have both already committed, and no amount of yielding creates space that the
    map does not have. One of them has to reverse out, which is a failure, not a plan.

    So corridors are treated as exclusive blocks, exactly like single-track railway
    signalling: a robot acquires the whole block before entering, and waits at the
    mouth - a junction, where there IS room to pass - if it cannot. This is also what
    real fleet managers call traffic zones or one-way segments, and it is the direct
    answer to "resolving deadlocks at narrow intersections or choke points".

    A block is a maximal connected run of cells with at most two exits. Length-1 blocks
    are dropped: a single cell is just a cell, and locking it would serialise ordinary
    corners for nothing.
    """
    corridor_cells = {c for c in env.free_cells() if env.degree(c) <= 2}
    seen: set[Cell] = set()
    of: dict[Cell, int] = {}
    members: dict[int, frozenset[Cell]] = {}
    ends: dict[int, tuple[Cell, ...]] = {}
    cid = 0

    for start in sorted(corridor_cells):
        if start in seen:
            continue
        stack, comp = [start], []
        seen.add(start)
        while stack:
            cur = stack.pop()
            comp.append(cur)
            for n in env.neighbors(cur):
                if n in corridor_cells and n not in seen:
                    seen.add(n)
                    stack.append(n)
        if len(comp) < 2:
            continue
        block = frozenset(comp)
        # An end is a block cell that touches something outside the block - the mouth
        # a robot enters and leaves by.
        block_ends = tuple(sorted(
            c for c in comp if any(n not in block for n in env.neighbors(c))))
        for c in comp:
            of[c] = cid
        members[cid] = block
        ends[cid] = block_ends
        cid += 1
    return CorridorMap(of, members, ends)


@dataclass(frozen=True)
class CorridorMap:
    of: dict[Cell, int]
    members: dict[int, frozenset[Cell]]
    ends: dict[int, tuple[Cell, ...]]

    def id_of(self, cell: Cell) -> int | None:
        return self.of.get(cell)

    def nearest_end(self, cid: int, cell: Cell) -> Cell | None:
        """Which mouth of the block is `cell` closest to? Identifies direction of travel."""
        e = self.ends.get(cid)
        if not e:
            return None
        return min(e, key=lambda c: abs(c[0] - cell[0]) + abs(c[1] - cell[1]))


MAPS = {
    "classic": classic_warehouse,
    "chokepoint": chokepoint_warehouse,
    "open": open_floor,
}
