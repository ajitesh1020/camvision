"""Interactive camera view: live frame, crosshair, ROI, and mouse-click jog.

Fixed at the camera's native 640x480 so a widget pixel equals an image pixel and
no scaling maths is needed to map a click to a machine move. Overlays (crosshair,
centre circle, ROI, optional simulation path) are drawn on top of the pixmap.

Mouse-click jog:

* **Short click** → a single **fixed-step** jog. The click's quadrant (relative
  to the crosshair) sets the direction and the machine moves **one Step on both X
  and Y** toward it — e.g. a click in the top-right jogs X+ and Y+ by the Step.
  Deterministic: every click moves the same known distance, chosen with the Step
  selector in the jog panel.
* **Long press** (held) → a **continuous rapid jog** toward the clicked quadrant
  (both axes) until the button is released.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import numpy as np
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QImage, QPainter, QPen, QColor, QPixmap
from PyQt5.QtWidgets import QLabel

from ..vision.geometry import (
    FRAME_HEIGHT,
    FRAME_WIDTH,
    frame_center,
    quadrant_jog_signs,
)

log = logging.getLogger("camvision.ui.camera")

LONG_PRESS_MS = 250     # hold longer than this => continuous rapid jog
STEP_FEED_MM_MIN = 600  # feed for a single fixed-step click jog


class CameraView(QLabel):
    """QLabel that shows the feed and turns clicks into jog commands."""

    roi_selected = pyqtSignal(tuple)  # (x1, y1, x2, y2)
    status = pyqtSignal(str)

    def __init__(self, controller, config, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.config = config

        self.setFixedSize(FRAME_WIDTH, FRAME_HEIGHT)
        self.setMouseTracking(True)
        self.setStyleSheet("background:#111;")
        # No tooltip on the video: a tooltip popping up over the continuously
        # repainting frame leaves a black tear line. Click-jog help lives in the
        # status bar / Jog panel instead.

        # Fixed step (mm) for a single click jog; set by MainWindow to share the
        # jog panel's Step selector. Falls back to 1 mm.
        self.jog_step_getter = lambda: 1.0

        # Overlay toggles
        self.show_crosshair = True
        self.show_roi = True
        self.center_circle_diameter = 30

        # ROI drawing state
        self._roi_mode = False
        self._roi_start: Optional[Tuple[int, int]] = None
        self._roi_end: Optional[Tuple[int, int]] = None
        self.roi: Optional[Tuple[int, int, int, int]] = config.roi

        # Simulation overlay: list of (points_in_machine_mm) polylines + transform
        self._sim_polylines: List[List[Tuple[float, float]]] = []
        self._sim_marker: Optional[Tuple[float, float]] = None
        self._sim_bbox: Optional[Tuple[float, float, float, float]] = None

        # Mouse-jog press state
        self._press_pos: Optional[Tuple[int, int]] = None
        self._press_timer = QTimer(self)
        self._press_timer.setSingleShot(True)
        self._press_timer.timeout.connect(self._begin_continuous_jog)
        self._jogging_axes: List[str] = []

        self._frame: Optional[np.ndarray] = None

    # -- frame update -----------------------------------------------------
    def update_frame(self, frame: np.ndarray) -> None:
        """Slot for :pyattr:`CameraService.frame_ready` — store and repaint."""
        self._frame = frame
        self.update()

    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        super().paintEvent(event)
        if self._frame is None:
            return
        img = self._to_qimage(self._frame)
        painter = QPainter(self)
        painter.drawPixmap(0, 0, QPixmap.fromImage(img))
        self._draw_overlays(painter)
        painter.end()

    @staticmethod
    def _to_qimage(frame: np.ndarray) -> QImage:
        import cv2

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        # Force a C-contiguous buffer and pass the real row stride. If the array
        # has padding (or is a non-contiguous view after flip/rotate) a computed
        # 3*w stride is wrong and paints a torn horizontal black line during rapid
        # updates — using strides[0] on a contiguous copy avoids that.
        rgb = np.ascontiguousarray(rgb)
        h, w, _ch = rgb.shape
        return QImage(rgb.data, w, h, rgb.strides[0], QImage.Format_RGB888).copy()

    # -- overlays ---------------------------------------------------------
    def _draw_overlays(self, painter: QPainter) -> None:
        cx, cy = frame_center()
        if self.show_crosshair:
            painter.setPen(QPen(QColor(255, 0, 0), 1))
            painter.drawLine(0, cy, FRAME_WIDTH, cy)
            painter.drawLine(cx, 0, cx, FRAME_HEIGHT)
            painter.setPen(QPen(QColor(0, 255, 0), 1))
            r = self.center_circle_diameter // 2
            painter.drawEllipse(cx - r, cy - r, 2 * r, 2 * r)

        roi = self._live_roi()
        if self.show_roi and roi:
            x1, y1, x2, y2 = roi
            painter.setPen(QPen(QColor(0, 255, 0), 2))
            painter.drawRect(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))

        if self._sim_polylines and self._sim_bbox:
            self._draw_simulation(painter)

    def _draw_simulation(self, painter: QPainter) -> None:
        """Draw the offset-compensated cut path fit into the view."""
        min_x, min_y, max_x, max_y = self._sim_bbox
        span_x = max(1e-6, max_x - min_x)
        span_y = max(1e-6, max_y - min_y)
        margin = 40
        scale = min((FRAME_WIDTH - 2 * margin) / span_x, (FRAME_HEIGHT - 2 * margin) / span_y)

        def to_px(pt):
            px = margin + (pt[0] - min_x) * scale
            # invert Y so machine +Y is up on screen
            py = FRAME_HEIGHT - (margin + (pt[1] - min_y) * scale)
            return int(px), int(py)

        painter.setPen(QPen(QColor(0, 200, 255), 2))
        for poly in self._sim_polylines:
            for i in range(len(poly) - 1):
                a = to_px(poly[i])
                b = to_px(poly[i + 1])
                painter.drawLine(a[0], a[1], b[0], b[1])

        if self._sim_marker is not None:
            painter.setPen(QPen(QColor(255, 255, 0), 2))
            mx, my = to_px(self._sim_marker)
            painter.drawEllipse(mx - 5, my - 5, 10, 10)

    def set_simulation(self, polylines, bbox) -> None:
        self._sim_polylines = polylines
        self._sim_bbox = bbox
        self.update()

    def set_simulation_marker(self, point) -> None:
        self._sim_marker = point
        self.update()

    def clear_simulation(self) -> None:
        self._sim_polylines = []
        self._sim_marker = None
        self._sim_bbox = None
        self.update()

    # -- ROI selection ----------------------------------------------------
    def start_roi_selection(self) -> None:
        self._roi_mode = True
        self._roi_start = None
        self._roi_end = None
        self.status.emit("Drag on the image to set the fiducial ROI.")

    def _live_roi(self):
        if self._roi_mode and self._roi_start and self._roi_end:
            return (*self._roi_start, *self._roi_end)
        return self.roi

    # -- mouse handling ---------------------------------------------------
    def mousePressEvent(self, event):  # noqa: N802
        if event.button() != Qt.LeftButton:
            return
        pos = (int(event.x()), int(event.y()))
        if self._roi_mode:
            self._roi_start = pos
            self._roi_end = pos
            return
        self._press_pos = pos
        self._press_timer.start(LONG_PRESS_MS)

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._roi_mode and self._roi_start:
            self._roi_end = (int(event.x()), int(event.y()))
            self.update()

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() != Qt.LeftButton:
            return
        if self._roi_mode and self._roi_start:
            self._roi_end = (int(event.x()), int(event.y()))
            self._roi_mode = False
            x1, y1 = self._roi_start
            x2, y2 = self._roi_end
            self.roi = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
            self.roi_selected.emit(self.roi)
            self.status.emit(f"ROI set to {self.roi}")
            self.update()
            return

        if self._jogging_axes:
            # A long press was in progress — stop the continuous jog.
            self._stop_continuous_jog()
        elif self._press_timer.isActive():
            # Released before the threshold => short click => one fixed step.
            self._press_timer.stop()
            self._jog_step(self._press_pos)
        self._press_pos = None

    def mouseDoubleClickEvent(self, event):  # noqa: N802 — keep double-clicks from double-jogging
        pass

    # -- jog actions ------------------------------------------------------
    def _jog_step(self, pos) -> None:
        """One fixed-step jog on both X and Y toward the clicked quadrant."""
        if pos is None:
            return
        xs, ys = quadrant_jog_signs(pos[0], pos[1])
        step = self.jog_step_getter()
        dx, dy = xs * step, ys * step
        self.status.emit(
            f"Step jog: X{'+' if xs > 0 else '−'}{step:g} Y{'+' if ys > 0 else '−'}{step:g} mm"
        )
        self.controller.jog_to_work_xy(dx, dy, STEP_FEED_MM_MIN)

    def _begin_continuous_jog(self) -> None:
        """Timer fired while still held => rapid continuous jog toward the quadrant."""
        if self._press_pos is None:
            return
        xs, ys = quadrant_jog_signs(self._press_pos[0], self._press_pos[1])
        speed = self.config.jog_speed
        self._jogging_axes = []
        self.controller.jog_continuous("X", xs, speed)
        self.controller.jog_continuous("Y", ys, speed)
        self._jogging_axes = ["X", "Y"]
        self.status.emit("Rapid jog (hold)…")

    def _stop_continuous_jog(self) -> None:
        for axis in self._jogging_axes:
            self.controller.jog_stop(axis)
        self._jogging_axes = []
        self.status.emit("Jog stopped.")

    def wheelEvent(self, event):  # noqa: N802 — mouse wheel resizes the centre circle
        delta = event.angleDelta().y()
        self.center_circle_diameter = max(10, min(500, self.center_circle_diameter + (2 if delta > 0 else -2)))
        self.update()
