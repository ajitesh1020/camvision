"""Single in-process owner of the camera feed.

The legacy design had the GUI publish frames into POSIX shared memory so a
*separate* fiducial subprocess could read them — the brittle "camera integration
with a different module" the rewrite exists to kill. Here there is exactly one
camera owner: :class:`CameraService` grabs frames on a background thread and
emits them as a Qt signal. The live view, the fiducial cycle and any snapshot all
consume the same in-memory frame. No shared memory, no second process.

If no camera is present (dev box / CI) the service emits a synthetic test frame
so the GUI still renders and the headless smoke test still runs.
"""

from __future__ import annotations

import logging

import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal

from ..vision.geometry import FRAME_HEIGHT, FRAME_WIDTH

log = logging.getLogger("camvision.camera")


def _synthetic_frame() -> np.ndarray:
    """A labelled gradient frame used when no camera is available."""
    frame = np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 3), dtype=np.uint8)
    xs = np.linspace(0, 255, FRAME_WIDTH, dtype=np.uint8)
    frame[:, :, 0] = xs[None, :]
    frame[:, :, 1] = np.linspace(0, 255, FRAME_HEIGHT, dtype=np.uint8)[:, None]
    try:
        import cv2

        cv2.putText(frame, "NO CAMERA", (FRAME_WIDTH // 2 - 120, FRAME_HEIGHT // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)
    except Exception:
        pass
    return frame


class CameraService(QThread):
    """Background frame grabber. Emits ``frame_ready`` with each BGR frame."""

    frame_ready = pyqtSignal(np.ndarray)
    error = pyqtSignal(str)

    def __init__(self, device: int = 0, fps: int = 30, parent=None):
        super().__init__(parent)
        self.device = device
        self.interval_ms = max(1, int(1000 / max(1, fps)))
        self._running = False
        self._cap = None

        # Orientation, mutated live from the settings panel.
        self.flip_x = False
        self.flip_y = False
        self.rotation_angle = 0

        self._latest: np.ndarray | None = None

    # -- lifecycle --------------------------------------------------------
    def run(self) -> None:  # QThread entry point
        import cv2

        self._running = True
        self._cap = cv2.VideoCapture(self.device)
        use_synthetic = not self._cap.isOpened()
        if use_synthetic:
            self.error.emit(
                "Failed to open camera — check video_input_port in Settings or the USB plug."
            )

        while self._running:
            if use_synthetic:
                frame = _synthetic_frame()
            else:
                ok, frame = self._cap.read()
                if not ok:
                    frame = _synthetic_frame()
                else:
                    frame = cv2.resize(frame, (FRAME_WIDTH, FRAME_HEIGHT))
            frame = self._orient(frame)
            self._latest = frame
            self.frame_ready.emit(frame)
            self.msleep(self.interval_ms)

        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def stop(self) -> None:
        self._running = False
        self.wait(1000)

    # -- orientation ------------------------------------------------------
    def _orient(self, frame: np.ndarray) -> np.ndarray:
        import cv2

        if self.flip_x:
            frame = cv2.flip(frame, 1)
        if self.flip_y:
            frame = cv2.flip(frame, 0)
        if self.rotation_angle:
            rot = {
                90: cv2.ROTATE_90_CLOCKWISE,
                180: cv2.ROTATE_180,
                270: cv2.ROTATE_90_COUNTERCLOCKWISE,
            }.get(self.rotation_angle % 360)
            if rot is not None:
                frame = cv2.rotate(frame, rot)
        return frame

    def latest_frame(self) -> np.ndarray | None:
        """Most recent frame (a copy), for the in-GUI fiducial cycle / snapshots."""
        return None if self._latest is None else self._latest.copy()
