"""Fiducial detection and the pixel->machine math used to correct panel skew.

Ported from the legacy ``fiducial_detector.py`` / ``autoFiducialRun_Script.py``
but split into two layers:

* **pure math** (``fiducial_center_offset``, ``pixel_to_machine``,
  ``solve_rotation_translation``) — numpy only, unit-tested against the legacy
  homogeneous-transform oracle; and
* **detection** (:class:`FiducialDetector`) — wraps ``cv2.HoughCircles`` with the
  multi-sample voting that made the legacy detector stable. OpenCV is imported
  lazily so the math layer imports on any machine.

The whole fiducial *cycle* (moving the machine, deploying the camera cylinder,
applying ``G10 L2``) now lives in the GUI, which owns the camera in-process; this
module only finds circles and does the coordinate math. No shared memory.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from .geometry import FRAME_HEIGHT, FRAME_WIDTH

Point = Tuple[float, float]


# --------------------------------------------------------------------------- #
# Pure coordinate math                                                        #
# --------------------------------------------------------------------------- #
def fiducial_center_offset(
    roi: Tuple[int, int, int, int],
    fiducial_in_roi: Point,
    frame_center: Point,
) -> Point:
    """Pixel offset of a fiducial (given in ROI-local pixels) from frame centre.

    Reproduces the legacy homogeneous-transform chain: translate ROI-local pixel
    into full-frame pixels, then into centre-relative pixels.
    """
    x1, y1, _x2, _y2 = roi
    cx, cy = frame_center

    t1 = np.array([[1, 0, x1], [0, 1, y1], [0, 0, 1]], dtype=float)      # ROI -> frame
    t2_inv = np.array([[1, 0, -cx], [0, 1, -cy], [0, 0, 1]], dtype=float)  # frame -> centre
    t = t2_inv @ t1

    f = np.array([fiducial_in_roi[0], fiducial_in_roi[1], 1.0])
    fx, fy = (t @ f)[:2]
    return float(fx), float(fy)


def pixel_to_machine(
    roi: Tuple[int, int, int, int],
    fiducial_in_roi: Point,
    frame_center: Point,
    mm_per_pixel: float,
    machine_xy: Point,
) -> Point:
    """Absolute machine XY (mm) of a fiducial seen at ``fiducial_in_roi``.

    Matches the legacy ``calculate_cnc_values``: take the centre-relative pixel
    offset, use its magnitude (abs) scaled to mm, and add the machine position
    the camera was at when the frame was grabbed.
    """
    fx, fy = fiducial_center_offset(roi, fiducial_in_roi, frame_center)
    dx_mm = abs(fx) * mm_per_pixel
    dy_mm = abs(fy) * mm_per_pixel
    return machine_xy[0] + dx_mm, machine_xy[1] + dy_mm


def solve_rotation_translation(
    f1_ref: Point, f2_ref: Point, f1_new: Point, f2_new: Point
) -> Tuple[float, Point]:
    """Rotation (degrees, absolute) and translation mapping reference -> new.

    Ports the legacy ``calculate_transformation``: the angle between the taught
    fiducial vector and the freshly detected one is applied via ``G10 L2 P0 R``.
    """
    v_ref = (f2_ref[0] - f1_ref[0], f2_ref[1] - f1_ref[1])
    v_new = (f2_new[0] - f1_new[0], f2_new[1] - f1_new[1])

    a_ref = math.atan2(round(v_ref[1], 2), round(v_ref[0], 2))
    a_new = math.atan2(round(v_new[1], 2), round(v_new[0], 2))
    rot = a_new - a_ref

    x1r = f1_new[0] * math.cos(-rot) - f1_new[1] * math.sin(-rot)
    y1r = f1_new[0] * math.sin(-rot) + f1_new[1] * math.cos(-rot)
    tx = f1_ref[0] - x1r
    ty = f1_ref[1] - y1r
    return abs(math.degrees(rot)), (tx, ty)


# --------------------------------------------------------------------------- #
# Detection (OpenCV)                                                          #
# --------------------------------------------------------------------------- #
@dataclass
class HoughParams:
    """HoughCircles parameters (defaults match the legacy ``config.json``)."""

    param1: int = 200
    param2: int = 15
    min_dist: int = 100000
    min_radius: int = 5
    max_radius: int = 10


@dataclass
class FiducialDetector:
    """Detect a single stable fiducial circle inside an ROI via sample voting.

    Feed successive frames to :meth:`update`; it accumulates detections and only
    reports a result once the same ``(x, y, r)`` has been seen enough times, the
    same debouncing the legacy detector relied on to reject flicker.
    """

    roi: Tuple[int, int, int, int]
    params: HoughParams = field(default_factory=HoughParams)
    samples_required: int = 5
    consistent_threshold: int = 5

    _votes: Dict[Tuple[int, int, int], int] = field(default_factory=lambda: defaultdict(int))
    _sample_count: int = 0
    _consistent: int = 0

    def reset(self) -> None:
        self._votes.clear()
        self._sample_count = 0
        self._consistent = 0

    def update(self, frame) -> Optional[Tuple[int, int, int]]:
        """Process one BGR frame; return ``(x, y, r)`` in ROI pixels when stable."""
        import cv2  # lazy: keeps the math layer import-clean off-machine

        x1, y1, x2, y2 = self.roi
        roi_frame = frame[y1:y2, x1:x2]
        gray = cv2.cvtColor(roi_frame, cv2.COLOR_BGR2GRAY)
        blurred = cv2.medianBlur(gray, 5)
        circles = cv2.HoughCircles(
            blurred,
            cv2.HOUGH_GRADIENT,
            dp=1,
            minDist=self.params.min_dist,
            param1=self.params.param1,
            param2=self.params.param2,
            minRadius=self.params.min_radius,
            maxRadius=self.params.max_radius,
        )
        if circles is None:
            return None

        circles = np.uint16(np.around(circles))
        for x, y, r in circles[0, :]:
            self._votes[(int(x), int(y), int(r))] += 1
        self._sample_count += 1

        if self._sample_count < self.samples_required:
            return None

        best = max(self._votes, key=self._votes.get)
        if self._votes[best] >= self.samples_required:
            self._consistent += 1
            if self._consistent >= self.consistent_threshold:
                self.reset()
                return best
        else:
            # No dominant sample yet — start a fresh voting window.
            self._votes.clear()
            self._sample_count = 0
            self._consistent = 0
        return None


def draw_detection(frame, roi, circle) -> None:
    """Overlay the detected circle + crosshair on ``frame`` (in place)."""
    import cv2

    x1, y1, _x2, _y2 = roi
    x, y, r = circle
    cx, cy = x1 + x, y1 + y
    cv2.circle(frame, (cx, cy), r, (0, 0, 255), 1, cv2.LINE_AA)
    cv2.line(frame, (cx - 20, cy), (cx + 20, cy), (255, 0, 0), 1, cv2.LINE_AA)
    cv2.line(frame, (cx, cy - 20), (cx, cy + 20), (255, 0, 0), 1, cv2.LINE_AA)
