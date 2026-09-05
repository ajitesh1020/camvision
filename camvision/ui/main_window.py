"""CamVision main window — assembles the panels and wires them to the machine.

Compact layout tuned to sit beside the AXIS GUI on a 1920x1080 screen:

* **Left column** — the live camera view, a thin action bar under it (camera
  up/down + set X/Y zero), and the Teach/Simulate/Setup tabs *below* the frame.
* **Right column** — the jog controls (step selector, XYZ cross, speed).

One :class:`~camvision.camera.service.CameraService` feeds the view and the
in-GUI fiducial cycle; one
:class:`~camvision.machine.linuxcnc_interface.MachineController` handles motion.
"""

from __future__ import annotations

import logging

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..camera.service import CameraService
from ..config import ConfigManager
from ..fiducial_cycle import FiducialCycle
from ..machine.linuxcnc_interface import MachineController
from .camera_view import CameraView
from .jog_panel import JogPanel
from .setup_panel import SetupPanel
from .simulate_panel import SimulatePanel
from .teach_panel import TeachPanel

log = logging.getLogger("camvision.ui.main")


class MainWindow(QMainWindow):
    def __init__(self, config_path: str):
        super().__init__()
        self.setWindowTitle("CamVision — PCB Depaneling")

        self.config = ConfigManager(config_path)
        self.controller = MachineController()

        self.camera = CameraService(device=self.config.camera_device_spec)
        self.camera.flip_x = self.config.get("Camera_Settings", "flip_x", False)
        self.camera.flip_y = self.config.get("Camera_Settings", "flip_y", False)
        self.camera.rotation_angle = int(self.config.get("Camera_Settings", "rotation_angle", 0))

        self._build_ui()
        self._wire()
        self.camera.start()

        # Live machine-position readout
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._update_status)
        self._status_timer.start(200)

    # -- construction -----------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(8)

        # Jog panel first so the camera view can share its Step selector.
        self.jog_panel = JogPanel(self.controller, self.config)
        self.jog_panel.setFixedWidth(200)

        # -- left column: camera + action bar + tabs ----------------------
        left = QVBoxLayout()
        left.setSpacing(4)
        self.camera_view = CameraView(self.controller, self.config)
        self.camera_view.jog_step_getter = self.jog_panel.current_step
        left.addWidget(self.camera_view)
        left.addWidget(self._camera_action_bar())

        self.tabs = QTabWidget()
        self.teach_panel = TeachPanel(self.controller, self.config)
        self.simulate_panel = SimulatePanel(self.teach_panel, self.camera_view, self.config)
        self.setup_panel = SetupPanel(self.controller, self.config, self.camera)
        self.tabs.addTab(self.teach_panel, "Teach")
        self.tabs.addTab(self.simulate_panel, "Simulate")
        self.tabs.addTab(self.setup_panel, "Setup")
        self.tabs.setToolTip("Teach a program, preview it in Simulate, and configure the "
                             "machine/camera in Setup.")
        left.addWidget(self.tabs, 1)
        root.addLayout(left, 1)

        # -- right column: jog -------------------------------------------
        right = QVBoxLayout()
        right.addWidget(self.jog_panel)
        right.addStretch(1)
        root.addLayout(right)

        self.status = QLabel()
        self.statusBar().addWidget(self.status)
        if self.controller.simulated:
            self.statusBar().addPermanentWidget(QLabel("SIMULATED (no LinuxCNC)"))

        # Fit beside AXIS without clipping on a 1080p screen.
        self.resize(880, 1000)

    def _camera_action_bar(self) -> QWidget:
        """Thin bar under the camera: camera cylinder up/down + set work zero."""
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)

        self.btn_cam_down = QPushButton("Camera ▼ Down")
        self.btn_cam_down.setToolTip("Deploy the camera (pneumatic cylinder down) to inspect — M64 P0.")
        self.btn_cam_up = QPushButton("Camera ▲ Up")
        self.btn_cam_up.setToolTip("Retract the camera (cylinder up) for cutting — M65 P0.")
        self.btn_set_zero = QPushButton("Set X/Y Zero")
        self.btn_set_zero.setToolTip(
            "Set the current position as the G54 work zero (align the crosshair to "
            "the PCB edge first) — G10 L20 P0 X0 Y0."
        )
        self.btn_cam_down.clicked.connect(lambda: self._machine_action(self.controller.camera_down))
        self.btn_cam_up.clicked.connect(lambda: self._machine_action(self.controller.camera_up))
        self.btn_set_zero.clicked.connect(lambda: self._machine_action(self.controller.set_work_zero_xy))
        for b in (self.btn_cam_down, self.btn_cam_up, self.btn_set_zero):
            row.addWidget(b)
        return bar

    def _machine_action(self, fn) -> None:
        ok = fn()
        if ok is False:
            self._show_status("Machine not ready (check power / homing / e-stop).")

    def _wire(self) -> None:
        self.camera.frame_ready.connect(self.camera_view.update_frame)
        self.camera.error.connect(self._show_status)
        self.camera.reconnected.connect(self._show_status)
        self.camera_view.status.connect(self._show_status)
        self.camera_view.roi_selected.connect(self._on_roi)

        self.setup_panel.request_roi.connect(self.camera_view.start_roi_selection)
        self.setup_panel.request_fiducial_cycle.connect(self._run_fiducial_cycle)
        self.setup_panel.overlays_changed.connect(self._apply_overlays)
        self._apply_overlays()

    # -- slots ------------------------------------------------------------
    def _apply_overlays(self) -> None:
        self.camera_view.show_crosshair = self.config.checkbox("enable_crosshair", True)
        self.camera_view.show_roi = self.config.checkbox("enable_roi", True)
        self.camera_view.update()

    def _on_roi(self, roi) -> None:
        self.config.roi = roi
        self.config.save()

    def _run_fiducial_cycle(self) -> None:
        self._show_status("Running fiducial cycle…")
        cycle = FiducialCycle(
            self.controller, self.camera, self.config,
            pump_events=QApplication.processEvents,
        )
        angle = cycle.run()
        if angle is None:
            self._show_status("Fiducial cycle: no correction applied.")
        else:
            self._show_status(f"Fiducial cycle: applied {angle:.2f}° rotation.")

    def _update_status(self) -> None:
        try:
            x, y, z = self.controller.work_position()
            self.status.setText(f"Work X {x:8.3f}  Y {y:8.3f}  Z {z:8.3f} mm")
        except Exception as exc:  # pragma: no cover
            self.status.setText(f"status error: {exc}")

    def _show_status(self, message: str) -> None:
        self.status.setText(message)

    # -- teardown ---------------------------------------------------------
    def closeEvent(self, event):  # noqa: N802
        self.camera.stop()
        self.config.save()
        event.accept()
