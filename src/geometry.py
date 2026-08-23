"""Tiny 2D helpers. Deliberately stdlib-only so the agent runs on a bare Pi image."""

from __future__ import annotations

import math

Cell = tuple[int, int]
Vec = tuple[float, float]


def wrap_angle(a: float) -> float:
    """Fold an angle into (-pi, pi]."""
    return math.atan2(math.sin(a), math.cos(a))


def angle_diff(target: float, current: float) -> float:
    return wrap_angle(target - current)


def dist(a: Vec, b: Vec) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def dist_sq(a: Vec, b: Vec) -> float:
    dx, dy = a[0] - b[0], a[1] - b[1]
    return dx * dx + dy * dy


def manhattan(a: Cell, b: Cell) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def cell_center(c: Cell, cell_m: float = 1.0) -> Vec:
    """Cell (x, y) -> the metric point at its centre."""
    return ((c[0] + 0.5) * cell_m, (c[1] + 0.5) * cell_m)


def to_cell(p: Vec, cell_m: float = 1.0) -> Cell:
    return (int(math.floor(p[0] / cell_m)), int(math.floor(p[1] / cell_m)))


def bearing(frm: Vec, to: Vec) -> float:
    return math.atan2(to[1] - frm[1], to[0] - frm[0])


def in_cone(origin: Vec, heading: float, half_width: float, point: Vec) -> bool:
    """Is `point` inside the forward cone from `origin`? Used by the safety layer."""
    return abs(angle_diff(bearing(origin, point), heading)) <= half_width


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def segment_point_distance(a: Vec, b: Vec, p: Vec) -> float:
    """Shortest distance from p to segment ab. Used for swept-collision checks."""
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    if denom < 1e-12:
        return math.hypot(px - ax, py - ay)
    t = clamp(((px - ax) * dx + (py - ay) * dy) / denom, 0.0, 1.0)
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def segments_min_distance(a0: Vec, a1: Vec, b0: Vec, b1: Vec) -> float:
    """Minimum distance between two moving points over one tick.

    Each robot sweeps a0->a1 and b0->b1 in the same tick. Treat the relative motion
    as a single segment from (a0-b0) to (a1-b1) and measure its closest approach to
    the origin. This catches the pass-through case that a naive endpoint check misses.
    """
    r0 = (a0[0] - b0[0], a0[1] - b0[1])
    r1 = (a1[0] - b1[0], a1[1] - b1[1])
    return segment_point_distance(r0, r1, (0.0, 0.0))
