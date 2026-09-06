"""Toolpath simulation: turn a Program into drawable / animatable geometry.

The simulator flattens the taught program into an ordered list of XY points that
trace the **actual cutting path** — i.e. with the camera-to-spindle offset
applied, exactly like the emitted G-code — so what the operator sees animated is
what the spindle will cut. Z is not drawn (it rides safe between segments and
plunges per cut); the panel reports the per-segment cut depth separately.

Pure geometry (numpy-free, math only) so it is testable and cheap to run inside
the Qt paint loop.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Tuple

from ..vision.geometry import CameraOffset
from .model import ArcDirection, Program, Segment, SegmentType

Point = Tuple[float, float]


@dataclass
class SimSegment:
    """One flattened segment: a rapid (dashed) lead-in plus the cut polyline."""

    lead_in: Tuple[Point, Point] | None  # rapid from previous end to this start
    cut_points: List[Point]              # the cutting polyline (>= 2 points)
    z: float


def _arc_points(
    start: Point, end: Point, center: Point, direction: ArcDirection, segments: int
) -> List[Point]:
    """Discretise an arc from ``start`` to ``end`` about ``center``."""
    cx, cy = center
    r = math.hypot(start[0] - cx, start[1] - cy)
    a0 = math.atan2(start[1] - cy, start[0] - cx)
    a1 = math.atan2(end[1] - cy, end[0] - cx)

    # Sweep sign: CCW (G3) increases angle, CW (G2) decreases it.
    if direction == ArcDirection.CCW:
        if a1 <= a0:
            a1 += 2 * math.pi
        sweep = a1 - a0
    else:  # CW
        if a1 >= a0:
            a1 -= 2 * math.pi
        sweep = a1 - a0

    n = max(2, int(abs(sweep) / (2 * math.pi) * segments) + 1)
    pts: List[Point] = []
    for i in range(n + 1):
        a = a0 + sweep * (i / n)
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def _circle_points(center: Point, radius: float, direction: ArcDirection, segments: int) -> List[Point]:
    cx, cy = center
    step = 1 if direction == ArcDirection.CCW else -1
    pts: List[Point] = []
    for i in range(segments + 1):
        a = step * 2 * math.pi * (i / segments)
        pts.append((cx + radius * math.cos(a), cy + radius * math.sin(a)))
    return pts


def simulate(
    program: Program,
    offset: CameraOffset | None = None,
    apply_offset: bool = True,
    arc_segments: int = 64,
) -> List[SimSegment]:
    """Flatten ``program`` into :class:`SimSegment` objects for drawing/animation."""
    program.validate()
    off = offset or CameraOffset()

    def comp(pt: Point) -> Point:
        return off.compensate(*pt) if apply_offset else pt

    result: List[SimSegment] = []
    prev_end: Point | None = None

    for seg in program.segments:
        if seg.type == SegmentType.LINE:
            cut = [comp(seg.start), comp(seg.end)]
        elif seg.type == SegmentType.ARC:
            cut = _arc_points(
                comp(seg.start), comp(seg.end), comp(seg.center), seg.direction, arc_segments
            )
        else:  # CIRCLE
            c = comp(seg.center)
            cut = _circle_points(c, seg.radius, seg.direction, arc_segments)

        lead = (prev_end, cut[0]) if prev_end is not None else None
        result.append(SimSegment(lead_in=lead, cut_points=cut, z=seg.z))
        prev_end = cut[-1]

    return result


def flatten_points(sim: List[SimSegment]) -> List[Point]:
    """All cut points in order — a single polyline for a simple animation."""
    pts: List[Point] = []
    for s in sim:
        pts.extend(s.cut_points)
    return pts


def bounding_box(sim: List[SimSegment]) -> Tuple[float, float, float, float]:
    """Return ``(min_x, min_y, max_x, max_y)`` over every drawn point.

    Includes lead-in endpoints so the view frames the whole motion. Returns a
    unit box when there is nothing to draw.
    """
    xs: List[float] = []
    ys: List[float] = []
    for s in sim:
        for (x, y) in s.cut_points:
            xs.append(x)
            ys.append(y)
        if s.lead_in:
            for (x, y) in s.lead_in:
                xs.append(x)
                ys.append(y)
    if not xs:
        return 0.0, 0.0, 1.0, 1.0
    return min(xs), min(ys), max(xs), max(ys)
