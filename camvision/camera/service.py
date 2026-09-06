"""Single in-process owner of the camera feed.

The legacy design had the GUI publish frames into POSIX shared memory so a
*separate* fiducial subprocess could read them — the brittle "camera integration
with a different module" the rewrite exists to kill. Here there is exactly one
camera owner: :class:`CameraService` grabs frames on a background thread and
emits them as a Qt signal. The live view, the fiducial cycle and any snapshot all
consume the same in-memory frame. No shared memory, no second process.

Two robustness features address the legacy "camera port changed after 10-15 min"
failure (the kernel re-enumerating the USB camera under a different
``/dev/videoN``):

* the device is opened through :mod:`camvision.camera.resolver`, so a stable
  ``/dev/v4l/by-id`` handle in the config always follows the same physical
  camera; and
* if frames stop arriving, the service **auto-reconnects** — it releases,
  re-resolves the device (picking up the new node) and reopens — instead of
  silently freezing.

If no camera is present (dev box / CI) the service emits a synthetic test frame
so the GUI still renders and the headless smoke test still runs.
"""

from __future__ import annotations

import logging
import time
from typing import Union

import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal

from ..vision.geometry import FRAME_HEIGHT, FRAME_WIDTH
from .resolver import resolve

log = logging.getLogger("camvision.camera")

# Reconnect policy
MAX_FAILED_READS = 30       # consecutive bad grabs (~1 s at 30 fps) before reopen
REOPEN_BACKOFF_S = 2.0      # wait between reopen attempts while disconnected


def _synthetic_frame(text: str = "NO CAMERA") -> np.ndarray:
    """A labelled gradient frame used when no camera is available."""
    frame = np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 3), dtype=np.uint8)
    xs = np.linspace(0, 255, FRAME_WIDTH, dtype=np.uint8)
    frame[:, :, 0] = xs[None, :]
    frame[:, :, 1] = np.linspace(0, 255, FRAME_HEIGHT, dtype=np.uint8)[:, None]
    try:
        import cv2

        cv2.putText(frame, text, (FRAME_WIDTH // 2 - 140, FRAME_HEIGHT // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)
    except Exception:
        pass
    return frame


class CameraService(QThread):
    """Background frame grabber with a self-healing capture connection.

    ``device`` may be an int index (legacy, unstable), a device path, a
    ``/dev/v4l/by-id`` symlink (recommended — survives re-enumeration), or a
    name/serial fragment; it is resolved on every (re)open.
    """

    frame_ready = pyqtSignal(np.ndarray)
    error = pyqtSignal(str)
    reconnected = pyqtSignal(str)

    def __init__(self, device: Union[int, str] = 0, fps: int = 30, parent=None):
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

    # -- capture connection ----------------------------------------------
    def _open(self):
        """(Re)resolve the device and open a capture. Returns the cv2 capture or None."""
        import cv2

        target = resolve(self.device)
        cap = cv2.VideoCapture(target)
        if cap.isOpened():
            log.info("Camera opened: spec=%r -> %r", self.device, target)
            return cap
        cap.release()
        log.warning("Camera open failed: spec=%r -> %r", self.device, target)
        return None

    # -- lifecycle --------------------------------------------------------
    def run(self) -> None:  # QThread entry point
        import cv2

        self._running = True
        self._cap = self._open()
        if self._cap is None:
            self.error.emit(
                "Failed to open camera — check the device in Settings or the USB plug. "
                "Retrying…"
            )
        failed = 0
        last_reopen = 0.0

        while self._running:
            if self._cap is None:
                # Disconnected: show a placeholder and periodically try to recover.
                self._emit(_synthetic_frame("RECONNECTING…"))
                now = time.time()
                if now - last_reopen >= REOPEN_BACKOFF_S:
                    last_reopen = now
                    self._cap = self._open()
                    if self._cap is not None:
                        failed = 0
                        self.reconnected.emit("Camera reconnected.")
                self.msleep(self.interval_ms)
                continue

            ok, frame = self._cap.read()
            if not ok or frame is None:
                failed += 1
                if failed >= MAX_FAILED_READS:
                    # The device likely re-enumerated under a new /dev/videoN.
                    log.warning("Lost camera frames (%d fails) — reopening", failed)
                    self.error.emit("Lost camera — reconnecting…")
                    self._release()
                    failed = 0
                    last_reopen = time.time()
                self._emit(_synthetic_frame("RECONNECTING…"))
                self.msleep(self.interval_ms)
                continue

            failed = 0
            frame = cv2.resize(frame, (FRAME_WIDTH, FRAME_HEIGHT))
            self._emit(self._orient(frame))
            self.msleep(self.interval_ms)

        self._release()

    def _emit(self, frame: np.ndarray) -> None:
        self._latest = frame
        self.frame_ready.emit(frame)

    def _release(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

    def stop(self) -> None:
        self._running = False
        self.wait(2000)

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
