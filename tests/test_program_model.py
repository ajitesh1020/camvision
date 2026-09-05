"""Program model round-trip and 3-point arc geometry."""

import math

from camvision.program.model import (
    ArcDirection,
    Program,
    Segment,
    SegmentType,
    arc_direction_from_three_points,
    center_from_three_points,
)


def test_program_roundtrip_with_arc_and_circle():
    p = Program(program_name="panel-A", operator="Ada")
    p.add_line((0, 0), (10, 0), z=-2)
    p.add_arc((10, 0), (10, 10), (10, 5), z=-2, direction=ArcDirection.CCW)
    p.add_circle((20, 20), 4.0, z=-1)

    restored = Program.from_dict(p.to_dict())
    assert restored.program_name == "panel-A"
    assert restored.operator == "Ada"
    assert len(restored.segments) == 3
    assert restored.segments[0].type == SegmentType.LINE
    assert restored.segments[1].type == SegmentType.ARC
    assert restored.segments[1].direction == ArcDirection.CCW
    assert restored.segments[2].type == SegmentType.CIRCLE
    assert math.isclose(restored.segments[2].radius, 4.0)


def test_center_from_three_points_unit_circle():
    cx, cy = center_from_three_points((1, 0), (0, 1), (-1, 0))
    assert math.isclose(cx, 0.0, abs_tol=1e-9)
    assert math.isclose(cy, 0.0, abs_tol=1e-9)


def test_center_from_three_points_collinear_raises():
    try:
        center_from_three_points((0, 0), (1, 1), (2, 2))
    except ValueError:
        return
    raise AssertionError("collinear points should raise")


def test_arc_direction():
    # Going (1,0)->(0,1)->(-1,0) turns counter-clockwise
    assert arc_direction_from_three_points((1, 0), (0, 1), (-1, 0)) == ArcDirection.CCW
    assert arc_direction_from_three_points((-1, 0), (0, 1), (1, 0)) == ArcDirection.CW


def test_validate_rejects_incomplete_line():
    p = Program()
    p.segments.append(Segment(type=SegmentType.LINE, z=-1))
    try:
        p.validate()
    except ValueError:
        return
    raise AssertionError("incomplete LINE should fail validation")
