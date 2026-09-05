"""Teaching program data model: segments + metadata, with JSON round-trip.

A :class:`Program` is what the operator builds by jogging the machine and
capturing points. It is deliberately independent of both Qt and G-code: the
teach panel edits it, the simulator draws it, and :mod:`camvision.program.gcode`
turns it into ``.ngc``. Persisted as ``.cvprog`` JSON so a taught program can be
reloaded and edited later (a capability the legacy ``coordinates.txt`` lacked).

All coordinates are **work coordinates in millimetres**, exactly as captured
from LinuxCNC — the camera-to-spindle offset is applied later (at G-code /
simulation time), never stored in the model, so the same taught program stays
correct if the offset is re-measured.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Tuple


def center_from_three_points(
    p1: Tuple[float, float], p2: Tuple[float, float], p3: Tuple[float, float]
) -> Tuple[float, float]:
    """Circumcentre of three non-collinear points (used for 3-point arc teaching).

    Raises ``ValueError`` if the points are collinear (no finite centre).
    """
    ax, ay = p1
    bx, by = p2
    cx, cy = p3
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        raise ValueError("The three points are collinear; cannot form an arc")
    a2 = ax * ax + ay * ay
    b2 = bx * bx + by * by
    c2 = cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    return ux, uy


def arc_direction_from_three_points(
    p1: Tuple[float, float], p2: Tuple[float, float], p3: Tuple[float, float]
) -> "ArcDirection":
    """Whether the path p1->p2->p3 turns clockwise (G2) or counter-clockwise (G3)."""
    cross = (p2[0] - p1[0]) * (p3[1] - p1[1]) - (p2[1] - p1[1]) * (p3[0] - p1[0])
    return ArcDirection.CCW if cross > 0 else ArcDirection.CW


class SegmentType(str, Enum):
    LINE = "line"
    ARC = "arc"
    CIRCLE = "circle"


class ArcDirection(str, Enum):
    CW = "cw"   # G2
    CCW = "ccw"  # G3


@dataclass
class Segment:
    """A single taught cut.

    * ``LINE``   uses ``start`` and ``end``.
    * ``ARC``    uses ``start``, ``end`` and ``center`` plus ``direction``.
    * ``CIRCLE`` uses ``center`` and ``radius`` (a full closed circle); ``start``
      is taken as ``(center_x + radius, center_y)`` if not given.

    ``z`` is the cut depth for the segment (negative into the material). It is
    captured per segment because panels of differing thickness can be mixed.
    """

    type: SegmentType
    z: float
    start: Optional[Tuple[float, float]] = None
    end: Optional[Tuple[float, float]] = None
    center: Optional[Tuple[float, float]] = None
    radius: Optional[float] = None
    direction: ArcDirection = ArcDirection.CW

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type.value
        d["direction"] = self.direction.value
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Segment":
        def pt(v):
            return tuple(v) if v is not None else None

        return cls(
            type=SegmentType(data["type"]),
            z=float(data["z"]),
            start=pt(data.get("start")),
            end=pt(data.get("end")),
            center=pt(data.get("center")),
            radius=(float(data["radius"]) if data.get("radius") is not None else None),
            direction=ArcDirection(data.get("direction", ArcDirection.CW.value)),
        )

    # -- validation -------------------------------------------------------
    def validate(self) -> None:
        """Raise ``ValueError`` if the segment is missing data it needs."""
        if self.type == SegmentType.LINE:
            if self.start is None or self.end is None:
                raise ValueError("LINE segment requires start and end points")
        elif self.type == SegmentType.ARC:
            if self.start is None or self.end is None or self.center is None:
                raise ValueError("ARC segment requires start, end and center points")
        elif self.type == SegmentType.CIRCLE:
            if self.center is None or self.radius is None:
                raise ValueError("CIRCLE segment requires center and radius")


@dataclass
class Program:
    """A named, operator-attributed sequence of taught segments + run parameters."""

    program_name: str = ""
    operator: str = ""
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    segments: List[Segment] = field(default_factory=list)

    # Run parameters (mirror the legacy Gcode_Param block; overridable per program).
    depth: float = -2.0
    retract: float = 10.0
    z_safe: float = 25.0
    spindle_rpm: float = 24000.0
    tool_dia: float = 0.5
    z_feed: float = 800.0
    xy_feed: float = 600.0
    fiducial_check: bool = False

    def add_line(self, start: Tuple[float, float], end: Tuple[float, float], z: float) -> Segment:
        seg = Segment(type=SegmentType.LINE, start=start, end=end, z=z)
        self.segments.append(seg)
        return seg

    def add_arc(
        self,
        start: Tuple[float, float],
        end: Tuple[float, float],
        center: Tuple[float, float],
        z: float,
        direction: ArcDirection = ArcDirection.CW,
    ) -> Segment:
        seg = Segment(
            type=SegmentType.ARC, start=start, end=end, center=center, z=z, direction=direction
        )
        self.segments.append(seg)
        return seg

    def add_circle(
        self,
        center: Tuple[float, float],
        radius: float,
        z: float,
        direction: ArcDirection = ArcDirection.CW,
    ) -> Segment:
        start = (center[0] + radius, center[1])
        seg = Segment(
            type=SegmentType.CIRCLE,
            center=center,
            radius=radius,
            start=start,
            end=start,
            z=z,
            direction=direction,
        )
        self.segments.append(seg)
        return seg

    def validate(self) -> None:
        for seg in self.segments:
            seg.validate()

    # -- persistence ------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "program_name": self.program_name,
            "operator": self.operator,
            "created": self.created,
            "parameters": {
                "depth": self.depth,
                "retract": self.retract,
                "z_safe": self.z_safe,
                "spindle_rpm": self.spindle_rpm,
                "tool_dia": self.tool_dia,
                "z_feed": self.z_feed,
                "xy_feed": self.xy_feed,
                "fiducial_check": self.fiducial_check,
            },
            "segments": [s.to_dict() for s in self.segments],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Program":
        params = data.get("parameters", {})
        return cls(
            program_name=data.get("program_name", ""),
            operator=data.get("operator", ""),
            created=data.get("created", datetime.now(timezone.utc).isoformat()),
            segments=[Segment.from_dict(s) for s in data.get("segments", [])],
            depth=float(params.get("depth", -2.0)),
            retract=float(params.get("retract", 10.0)),
            z_safe=float(params.get("z_safe", 25.0)),
            spindle_rpm=float(params.get("spindle_rpm", 24000.0)),
            tool_dia=float(params.get("tool_dia", 0.5)),
            z_feed=float(params.get("z_feed", 800.0)),
            xy_feed=float(params.get("xy_feed", 600.0)),
            fiducial_check=bool(params.get("fiducial_check", False)),
        )
