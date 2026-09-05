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

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QApplication,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
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
        self.setWindowTitle(f"CamVision v{__version__} — PCB Depaneling")

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
        # The main window itself does NOT scroll — the camera stays put so the
        # mouse wheel over the video adjusts the centre circle, not the page.
        # Only tall tab pages (Setup) scroll, inside their own scroll area.
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(8)

        # Jog panel first so the camera view can share its Step selector.
        self.jog_panel = JogPanel(self.controller, self.config)
        self.jog_panel.setFixedWidth(210)

        # -- left column: camera + tabs (tab bar on the RIGHT edge) --------
        left = QVBoxLayout()
        left.setSpacing(4)
        self.camera_view = CameraView(self.controller, self.config)
        self.camera_view.jog_step_getter = self.jog_panel.current_step
        left.addWidget(self.camera_view, 0, Qt.AlignHCenter)

        self.tabs = QTabWidget()
        self.tabs.setTabPosition(QTabWidget.East)  # tabs on the right side
        self.teach_panel = TeachPanel(self.controller, self.config)
        self.simulate_panel = SimulatePanel(
            self.teach_panel, self.camera_view, self.config, self.controller
        )
        self.setup_panel = SetupPanel(self.controller, self.config, self.camera)
        self.tabs.addTab(self._scrollable(self.teach_panel), "Teach")
        self.tabs.addTab(self._scrollable(self.simulate_panel), "Simulate")
        self.tabs.addTab(self._scrollable(self.setup_panel), "Setup")
        self.tabs.setToolTip("Teach a program, preview it in Simulate, and configure the "
                             "machine/camera in Setup.")
        left.addWidget(self.tabs, 1)
        root.addLayout(left, 1)

        # -- right column: jog + camera/machine action buttons ------------
        right = QVBoxLayout()
        right.addWidget(self.jog_panel)
        right.addWidget(self._machine_action_group())
        right.addStretch(1)
        root.addLayout(right)

        self.setCentralWidget(central)

        # Notification bar: transient messages / LinuxCNC errors on the left,
        # live DRO + machine state pinned on the right.
        self.status = QLabel("Ready.")
        self.status.setToolTip("Notifications and LinuxCNC error messages appear here.")
        self.statusBar().addWidget(self.status, 1)
        self.dro = QLabel("")
        self.state_label = QLabel("")
        self.statusBar().addPermanentWidget(self.state_label)
        self.statusBar().addPermanentWidget(self.dro)
        self.statusBar().addPermanentWidget(QLabel(f"v{__version__}"))
        if self.controller.simulated:
            self.statusBar().addPermanentWidget(QLabel("SIMULATED"))

        # Size to the screen so the window (and its status bar) always fit;
        # the scroll area handles anything taller than this.
        self._fit_to_screen()

    def _fit_to_screen(self) -> None:
        avail = QApplication.primaryScreen().availableGeometry()
        self.resize(min(940, avail.width()), min(900, avail.height()))
        self.move(avail.x() + max(0, avail.width() - self.width()), avail.y())

    @staticmethod
    def _scrollable(widget) -> QScrollArea:
        """Wrap a tab page so only that page scrolls (never the whole window)."""
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.NoFrame)
        area.setWidget(widget)
        return area

    def _machine_action_group(self) -> QWidget:
        """Camera cylinder up/down + set work zero + set safe Z, on the jog side."""
        box = QGroupBox("Machine")
        col = QVBoxLayout(box)
        self.btn_cam_down = QPushButton("Camera ▼ Down")
        self.btn_cam_down.setToolTip("Deploy the camera (pneumatic cylinder down) to inspect — M64 P0.")
        self.btn_cam_up = QPushButton("Camera ▲ Up")
        self.btn_cam_up.setToolTip("Retract the camera (cylinder up) for cutting — M65 P0.")
        self.btn_set_zero = QPushButton("Set X/Y Zero")
        self.btn_set_zero.setToolTip(
            "Set the current position as the G54 work zero (align the crosshair to "
            "the PCB edge first) — G10 L20 P0 X0 Y0."
        )
        self.btn_set_safe_z = QPushButton("Set Safe Z (here)")
        self.btn_set_safe_z.setToolTip(
            "Record the current Z as the safe height. Programs retract to it, and "
            "the simulation dry-runs stay at it so nothing touches the PCB."
        )
        self.btn_cam_down.clicked.connect(lambda: self._machine_action(self.controller.camera_down))
        self.btn_cam_up.clicked.connect(lambda: self._machine_action(self.controller.camera_up))
        self.btn_set_zero.clicked.connect(lambda: self._machine_action(self.controller.set_work_zero_xy))
        self.btn_set_safe_z.clicked.connect(self._set_safe_z)
        for b in (self.btn_cam_down, self.btn_cam_up, self.btn_set_zero, self.btn_set_safe_z):
            col.addWidget(b)
        return box

    def _set_safe_z(self) -> None:
        """Store the current work Z as the safe height (z_safe) used by programs/sim."""
        try:
            _x, _y, z = self.controller.work_position()
        except Exception as exc:  # pragma: no cover
            self._notify(f"Could not read Z: {exc}", "warn")
            return
        self.config.data["Gcode_Param"]["z_safe"] = round(float(z), 4)
        self.config.save()
        self._notify(f"Safe Z set to {z:.3f} mm.")

    def _machine_action(self, fn) -> None:
        """Run a machine command, first notifying if the machine isn't ready."""
        reason = self.controller.not_ready_reason()
        if reason:
            self._notify(reason, "warn")
            return
        if fn() is False:
            self._notify("Machine not ready (check power / homing / e-stop).", "warn")

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
        reason = self.controller.not_ready_reason()
        if reason:
            self._notify(reason, "warn")
            return
        self._notify("Running fiducial cycle…")
        cycle = FiducialCycle(
            self.controller, self.camera, self.config,
            pump_events=QApplication.processEvents,
        )
        angle = cycle.run()
        if angle is None:
            self._notify("Fiducial cycle: no correction applied.", "warn")
        else:
            self._notify(f"Fiducial cycle: applied {angle:.2f}° rotation.")

    def _update_status(self) -> None:
        # 1. Watchdog: if LinuxCNC has shut down, close CamVision too.
        if not self.controller.alive():
            self._linuxcnc_gone()
            return

        # 2. Live DRO + machine state.
        try:
            x, y, z = self.controller.work_position()
            self.dro.setText(f"X {x:8.3f}  Y {y:8.3f}  Z {z:8.3f}")
        except Exception:  # pragma: no cover
            pass
        reason = self.controller.not_ready_reason()
        self.state_label.setText("NOT READY" if reason else "READY")
        self.state_label.setStyleSheet("color:#c00;font-weight:bold;" if reason
                                       else "color:#080;font-weight:bold;")

        # 3. Surface any LinuxCNC operator error (same channel AXIS reads).
        err = self.controller.poll_error()
        if err:
            self._notify(err, "error")

    # -- notifications ----------------------------------------------------
    def _notify(self, message: str, level: str = "info") -> None:
        """Show a message in the bottom bar; warnings/errors persist longer."""
        colour = {"info": "#036", "warn": "#a60", "error": "#c00"}.get(level, "#036")
        self.status.setStyleSheet(f"color:{colour};" + ("font-weight:bold;" if level != "info" else ""))
        self.status.setText(message)
        # Auto-clear transient messages so the bar returns to "Ready.".
        if not hasattr(self, "_clear_timer"):
            self._clear_timer = QTimer(self)
            self._clear_timer.setSingleShot(True)
            self._clear_timer.timeout.connect(self._clear_notify)
        self._clear_timer.start(8000 if level == "info" else 12000)

    def _clear_notify(self) -> None:
        self.status.setStyleSheet("")
        self.status.setText("Ready.")

    # kept for the camera/view signals that emit plain strings
    def _show_status(self, message: str) -> None:
        self._notify(message, "info")

    def _linuxcnc_gone(self) -> None:
        self._status_timer.stop()
        log.warning("LinuxCNC connection lost — closing CamVision.")
        self.close()

    # -- teardown ---------------------------------------------------------
    def closeEvent(self, event):  # noqa: N802
        self.camera.stop()
        self.config.save()
        event.accept()
