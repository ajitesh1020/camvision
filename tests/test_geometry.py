"""Quadrant jog-sign mapping used by camera click-jog."""

from camvision.vision.geometry import FRAME_HEIGHT, FRAME_WIDTH, frame_center, quadrant_jog_signs


def test_quadrant_signs_all_four():
    cx, cy = frame_center()
    # top-right: right of centre (X+), above centre (Y+)
    assert quadrant_jog_signs(cx + 50, cy - 50) == (1, 1)
    # top-left: left (X-), above (Y+)
    assert quadrant_jog_signs(cx - 50, cy - 50) == (-1, 1)
    # bottom-right: right (X+), below (Y-)
    assert quadrant_jog_signs(cx + 50, cy + 50) == (1, -1)
    # bottom-left: left (X-), below (Y-)
    assert quadrant_jog_signs(cx - 50, cy + 50) == (-1, -1)


def test_quadrant_signs_corners():
    assert quadrant_jog_signs(FRAME_WIDTH - 1, 0) == (1, 1)
    assert quadrant_jog_signs(0, FRAME_HEIGHT - 1) == (-1, -1)
