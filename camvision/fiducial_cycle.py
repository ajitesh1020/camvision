"""In-GUI fiducial correction cycle (replaces the legacy M101 subprocess).

The GUI owns the camera, so the whole cycle runs in-process against live frames —
no POSIX shared memory, no second Python interpreter. For each of the two taught
fiducials it: rapids over the stored machine position, deploys the camera
cylinder, drops to the inspect Z, and votes on HoughCircles detections until a
circle is stable; then it solves the rotation between the taught and detected
fiducial pair and applies it with ``G10 L2 P0 R``.

This module is orchestration glue (machine + camera + vision). The detection and
coordinate maths it calls live in :mod:`camvision.vision.fiducial` and are unit
tested there; this file is exercised on the machine.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional, Tuple

from .vision.fiducial import FiducialDetector, pixel_to_machine, solve_rotation_translation
from .vision.geometry import frame_center

log = logging.getLogger("camvision.fiducial_cycle")

DETECT_TIMEOUT_S = 8.0


class FiducialCycle:
    def __init__(self, controller, camera_service, config,
                 pump_events: Optional[Callable[[], None]] = None):
        self.controller = controller
        self.camera = camera_service
        self.config = config
        # A callable that lets the Qt event loop deliver camera frames while we
        # block here (typically ``QApplication.processEvents``).
        self.pump_events = pump_events or (lambda: None)

    def _detect_at(self, machine_xy: Tuple[float, float]) -> Optional[Tuple[float, float]]:
        """Detect the fiducial near the current view; return its machine XY (mm)."""
        detector = FiducialDetector(roi=self.config.roi, params=self.config.hough_params)
        deadline = time.time() + DETECT_TIMEOUT_S
        while time.time() < deadline:
            self.pump_events()
            frame = self.camera.latest_frame()
            if frame is None:
                time.sleep(0.02)
                continue
            circle = detector.update(frame)
            if circle is not None:
                x, y, _r = circle
                return pixel_to_machine(
                    self.config.roi, (x, y), frame_center(),
                    self.config.mm_per_pixel, machine_xy,
                )
            time.sleep(0.02)
        log.warning("Fiducial detection timed out at %s", machine_xy)
        return None

    def _inspect_z(self) -> float:
        return self.config.inspect_z + self.config.fixture_thickness + self.config.baseplate_thickness

    def _go_and_detect(self, fiducial_ref: Tuple[float, float]) -> Optional[Tuple[float, float]]:
        # Approach 2 mm short of the taught fiducial, as the legacy routine did.
        tx, ty = fiducial_ref[0] - 2.0, fiducial_ref[1] - 2.0
        if not self.controller.mdi(f"G53 G0 X{tx:.4f} Y{ty:.4f}"):
            return None
        self.controller.camera_down()
        self.controller.mdi(f"G53 G0 Z{self._inspect_z():.4f}")
        return self._detect_at((tx, ty))

    def run(self) -> Optional[float]:
        """Execute the cycle; return the applied rotation in degrees, or None."""
        f1_ref = tuple(self.config.data["Fiducial_Positions"]["fiducial1_mm"])
        f2_ref = tuple(self.config.data["Fiducial_Positions"]["fiducial2_mm"])
        if not self.controller.ok_for_mdi():
            log.error("Machine not ready for fiducial cycle")
            return None

        f1_new = self._go_and_detect(f1_ref)
        f2_new = self._go_and_detect(f2_ref)
        self.controller.camera_up()

        if f1_new is None or f2_new is None:
            log.warning("Fiducial cycle incomplete — leaving rotation unchanged")
            self.controller.apply_rotation(0.0)
            return None

        angle, _translation = solve_rotation_translation(f1_ref, f2_ref, f1_new, f2_new)
        angle = round(angle, 2)
        self.controller.apply_rotation(angle)
        log.info("Applied fiducial rotation correction: %.2f deg", angle)
        return angle
