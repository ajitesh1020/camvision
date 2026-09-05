"""Pixel <-> millimetre conversion and camera/spindle offset geometry.

These helpers are the single source of truth for turning a point seen by the
camera into a machine coordinate, and for compensating the fixed offset between
the camera's optical axis and the spindle. They are pure functions of numbers
(no OpenCV, Qt or LinuxCNC), so they are trivially unit-testable and are reused
by the GUI, the simulator and the G-code generator alike.

Legacy note
-----------
The legacy code stored the conversion factor as ``pixels_per_mm`` but actually
used it as *millimetres per pixel* (``mm = pixels * factor``). To avoid carrying
that confusion forward every function here takes an explicit ``mm_per_pixel``
argument; :func:`CameraCalibration.mm_per_pixel` reads the legacy key so stored
calibrations keep working.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

# The camera streams at this fixed resolution (matches the legacy producer).
FRAME_WIDTH = 640
FRAME_HEIGHT = 480


def frame_center(width: int = FRAME_WIDTH, height: int = FRAME_HEIGHT) -> Tuple[int, int]:
    """Return the (x, y) pixel at the centre of the frame / crosshair."""
    return width // 2, height // 2


def pixels_to_mm(pixels: float, mm_per_pixel: float) -> float:
    """Convert a pixel distance to millimetres."""
    return pixels * mm_per_pixel


def mm_to_pixels(mm: float, mm_per_pixel: float) -> float:
    """Convert a millimetre distance to pixels (inverse of :func:`pixels_to_mm`)."""
    if mm_per_pixel == 0:
        raise ValueError("mm_per_pixel must be non-zero")
    return mm / mm_per_pixel


def pixel_offset_from_center(
    pixel_x: float,
    pixel_y: float,
    width: int = FRAME_WIDTH,
    height: int = FRAME_HEIGHT,
) -> Tuple[float, float]:
    """Signed pixel offset of a point from the frame centre.

    Returns ``(dx, dy)`` where +x is right and +y is *down* in image space, i.e.
    the raw OpenCV convention. Machine mapping is applied by
    :func:`camera_pixel_to_machine_delta`, which flips Y so it grows the way the
    CNC Y axis does.
    """
    cx, cy = frame_center(width, height)
    return pixel_x - cx, pixel_y - cy


def quadrant_jog_signs(
    pixel_x: float,
    pixel_y: float,
    width: int = FRAME_WIDTH,
    height: int = FRAME_HEIGHT,
) -> Tuple[int, int]:
    """Machine (x_sign, y_sign) for a click, decided purely by its quadrant.

    A click to the **right** of the crosshair jogs X+, to the left X-; a click
    **above** the crosshair jogs Y+, below Y- (machine +Y is up, image +y down).
    Every click therefore jogs *both* axes by one fixed step toward its quadrant,
    which is deterministic — unlike scaling the raw pixel distance into a move.
    """
    cx, cy = frame_center(width, height)
    x_sign = 1 if pixel_x >= cx else -1
    y_sign = 1 if pixel_y <= cy else -1
    return x_sign, y_sign


def camera_pixel_to_machine_delta(
    pixel_x: float,
    pixel_y: float,
    mm_per_pixel: float,
    width: int = FRAME_WIDTH,
    height: int = FRAME_HEIGHT,
) -> Tuple[float, float]:
    """Machine-frame (dx, dy) in mm to bring a clicked pixel under the crosshair.

    Image +Y points down, machine +Y points up (this 6060 homes Y at the top and
    counts down toward the operator), so the Y component is negated. The returned
    delta is what to *add* to the current work position so the feature at
    ``(pixel_x, pixel_y)`` ends up at the centre of the view.
    """
    dx_px, dy_px = pixel_offset_from_center(pixel_x, pixel_y, width, height)
    dx_mm = pixels_to_mm(dx_px, mm_per_pixel)
    dy_mm = pixels_to_mm(-dy_px, mm_per_pixel)
    return dx_mm, dy_mm


@dataclass(frozen=True)
class CameraOffset:
    """Fixed offset between the camera optical axis and the spindle tip, in mm.

    The camera sees a cut line at some work coordinate; the spindle that must cut
    it sits ``(x, y)`` away. Cutting moves therefore use ``taught - offset``.
    Legacy values for the 6060: x=109.448, y=9.91.
    """

    x: float = 0.0
    y: float = 0.0

    def compensate(self, x: float, y: float) -> Tuple[float, float]:
        """Return the spindle target for a point the camera taught at (x, y)."""
        return x - self.x, y - self.y

    def to_dict(self) -> dict:
        return {"camera_to_spindle_x_offset": self.x, "camera_to_spindle_y_offset": self.y}

    @classmethod
    def from_dict(cls, data: dict) -> "CameraOffset":
        return cls(
            x=float(data.get("camera_to_spindle_x_offset", 0.0)),
            y=float(data.get("camera_to_spindle_y_offset", 0.0)),
        )
