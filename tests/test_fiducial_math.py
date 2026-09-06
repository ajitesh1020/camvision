"""Pixel->machine transform and two-fiducial rotation solver."""

import math

from camvision.vision.fiducial import (
    fiducial_center_offset,
    pixel_to_machine,
    solve_rotation_translation,
)
from camvision.vision.geometry import frame_center


def test_fiducial_center_offset_matches_legacy_transform():
    # ROI at (100, 50); fiducial at ROI-local (10, 5) => frame (110, 55).
    # Frame centre (320, 240) => offset (110-320, 55-240) = (-210, -185).
    roi = (100, 50, 200, 150)
    fx, fy = fiducial_center_offset(roi, (10, 5), frame_center())
    assert math.isclose(fx, -210.0)
    assert math.isclose(fy, -185.0)


def test_pixel_to_machine_adds_position_and_scales():
    roi = (100, 50, 200, 150)
    mm_per_px = 0.1
    machine_xy = (50.0, 60.0)
    x, y = pixel_to_machine(roi, (10, 5), frame_center(), mm_per_px, machine_xy)
    # abs(-210)*0.1 + 50 = 71 ; abs(-185)*0.1 + 60 = 78.5
    assert math.isclose(x, 71.0)
    assert math.isclose(y, 78.5)


def test_solve_rotation_zero_when_identical():
    ref1, ref2 = (0.0, 0.0), (10.0, 0.0)
    angle, _t = solve_rotation_translation(ref1, ref2, ref1, ref2)
    assert math.isclose(angle, 0.0, abs_tol=1e-6)


def test_solve_rotation_detects_90_degrees():
    ref1, ref2 = (0.0, 0.0), (10.0, 0.0)      # vector points +X
    new1, new2 = (0.0, 0.0), (0.0, 10.0)      # vector points +Y => 90 deg
    angle, _t = solve_rotation_translation(ref1, ref2, new1, new2)
    assert math.isclose(angle, 90.0, abs_tol=1e-6)
