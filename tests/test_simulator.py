"""Simulator flattening: offset application, arc discretisation, bbox."""

import math

from camvision.program.model import ArcDirection, Program
from camvision.program.simulator import bounding_box, flatten_points, simulate
from camvision.vision.geometry import CameraOffset


def test_line_offset_applied():
    p = Program()
    p.add_line((10, 20), (30, 20), z=-1)
    sim = simulate(p, offset=CameraOffset(5, 2), apply_offset=True)
    assert len(sim) == 1
    assert sim[0].cut_points[0] == (5.0, 18.0)
    assert sim[0].cut_points[-1] == (25.0, 18.0)


def test_arc_points_lie_on_circle():
    p = Program()
    p.add_arc((10, 0), (0, 10), (0, 0), z=-1, direction=ArcDirection.CCW)
    sim = simulate(p, apply_offset=False, arc_segments=32)
    pts = sim[0].cut_points
    for x, y in pts:
        assert math.isclose(math.hypot(x, y), 10.0, abs_tol=1e-6)
    assert math.isclose(pts[0][0], 10.0, abs_tol=1e-6)
    assert math.isclose(pts[-1][1], 10.0, abs_tol=1e-6)


def test_lead_in_between_segments():
    p = Program()
    p.add_line((0, 0), (10, 0), z=-1)
    p.add_line((10, 10), (20, 10), z=-1)
    sim = simulate(p, apply_offset=False)
    assert sim[0].lead_in is None
    assert sim[1].lead_in == ((10.0, 0.0), (10.0, 10.0))


def test_bounding_box_and_flatten():
    p = Program()
    p.add_line((0, 0), (10, 5), z=-1)
    sim = simulate(p, apply_offset=False)
    assert bounding_box(sim) == (0.0, 0.0, 10.0, 5.0)
    assert flatten_points(sim) == [(0.0, 0.0), (10.0, 5.0)]
