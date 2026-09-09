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
import subprocess
import sys

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QApplication,
    QDoubleSpinBox,
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
from ..audit import AuditLog
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
        self.controller.set_development_mode(
            self.config.checkbox("development_mode", False)
        )
        # AuditLog owns a background SQLite writer.  It receives snapshots from
        # the existing status timer and never polls or commands LinuxCNC itself.
        self.audit = AuditLog()
        self.controller.audit_callback = self._audit_controller_event

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

        # -- right column: jog + grouped machine/tool actions -------------
        right = QVBoxLayout()
        right.addWidget(self.jog_panel)
        right.addWidget(self._camera_action_group())
        right.addWidget(self._work_action_group())
        right.addWidget(self._cutting_tool_group())
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
        self.dev_mode_label = QLabel("DEV MODE: UNHOMED MOTION")
        self.dev_mode_label.setStyleSheet("color:#b35c00;font-weight:bold;")
        self.dev_mode_label.setToolTip(
            "Development mode is enabled. CamVision permits unhomed motion; LinuxCNC "
            "still requires [TRAJ] NO_FORCE_HOMING = 1."
        )
        self.dev_mode_label.setVisible(self.controller.allow_unhomed_motion)
        self.statusBar().addPermanentWidget(self.state_label)
        self.statusBar().addPermanentWidget(self.dev_mode_label)
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

    def _camera_action_group(self) -> QWidget:
        """Camera deployment and camera-to-spindle verification."""
        box = QGroupBox("Camera / Spindle")
        col = QVBoxLayout(box)
        self.btn_cam_down = QPushButton("Camera ▼ Down")
        self.btn_cam_down.setToolTip("Deploy the camera (pneumatic cylinder down) to inspect — M64 P0.")
        self.btn_cam_up = QPushButton("Camera ▲ Up")
        self.btn_cam_up.setToolTip("Retract the camera (cylinder up) for cutting — M65 P0.")
        self.btn_check_spindle = QPushButton("Check Spindle Position")
        self.btn_check_spindle.setToolTip(
            "With the camera crosshair on a feature, move to Safe Z, retract the "
            "camera, and rapid the spindle over the same feature using the saved offset."
        )
        self.btn_cam_down.clicked.connect(lambda: self._machine_action(self.controller.camera_down))
        self.btn_cam_up.clicked.connect(lambda: self._machine_action(self.controller.camera_up))
        self.btn_check_spindle.clicked.connect(self._check_spindle_position)
        for button in (self.btn_cam_down, self.btn_cam_up, self.btn_check_spindle):
            col.addWidget(button)
        return box

    def _work_action_group(self) -> QWidget:
        """Work-coordinate and Z-height setup actions."""
        box = QGroupBox("Work Setup")
        col = QVBoxLayout(box)
        self.btn_set_zero = QPushButton("Set Camera Zero + Spindle G55")
        self.btn_set_zero.setToolTip(
            "With the camera crosshair on the PCB reference, set G54 X/Y camera zero "
            "and G55 X/Y spindle zero from the saved offset. G55 uses the same Z "
            "reference as G54."
        )
        self.btn_set_safe_z = QPushButton("Set Safe Z (here)")
        self.btn_set_safe_z.setToolTip(
            "Record the current Z as the safe travel height. The tool returns here "
            "between cuts; the separate Retract Z setting controls pre-plunge clearance."
        )
        self.btn_go_safe_z = QPushButton("Go to Safe Z")
        self.btn_go_safe_z.setToolTip(
            "Rapid only the Z axis to the saved Safe Z before teaching with the camera."
        )
        self.btn_set_zero.clicked.connect(self._set_camera_and_spindle_zero)
        self.btn_set_safe_z.clicked.connect(self._set_safe_z)
        self.btn_go_safe_z.clicked.connect(self._go_to_safe_z)
        for button in (self.btn_set_zero, self.btn_set_safe_z, self.btn_go_safe_z):
            col.addWidget(button)
        return box

    def _cutting_tool_group(self) -> QWidget:
        """Editable cutting-tool diameter used by the overlay and G-code."""
        box = QGroupBox("Cutting Tool")
        row = QHBoxLayout(box)
        row.addWidget(QLabel("Diameter"))
        self.tool_dia = QDoubleSpinBox()
        self.tool_dia.setRange(0.05, 100.0)
        self.tool_dia.setDecimals(3)
        self.tool_dia.setSingleStep(0.1)
        self.tool_dia.setSuffix(" mm")
        self.tool_dia.setValue(self.config.gcode_params()["tool_dia"])
        self.tool_dia.setToolTip(
            "Select with the arrows or type the installed cutting-tool diameter. "
            "The camera centre circle and exported G-code comment update together."
        )
        self.tool_dia.valueChanged.connect(self._apply_tool_diameter)
        row.addWidget(self.tool_dia)
        return box

    def _apply_tool_diameter(self, diameter: float) -> None:
        self.config.set("Gcode_Param", "tool_dia", float(diameter))
        self.config.save()
        self.teach_panel.set_tool_diameter(diameter)
        self.camera_view.set_tool_diameter(diameter)
        self._notify(f"Cutting tool diameter set to {diameter:.3f} mm.")

    def _sync_tool_diameter_from_program(self) -> None:
        """Reflect a loaded program's tool diameter in the control and overlay."""
        diameter = float(self.teach_panel.program.tool_dia)
        if abs(self.tool_dia.value() - diameter) > 1e-9:
            self.tool_dia.blockSignals(True)
            self.tool_dia.setValue(diameter)
            self.tool_dia.blockSignals(False)
        if abs(self.config.gcode_params()["tool_dia"] - diameter) > 1e-9:
            self.config.set("Gcode_Param", "tool_dia", diameter)
            self.config.save()
        self.camera_view.set_tool_diameter(diameter)

    def _set_safe_z(self) -> None:
        """Store the current work Z as the safe height (z_safe) used by programs/sim."""
        try:
            _x, _y, z = self.controller.work_position()
        except Exception as exc:  # pragma: no cover
            self._notify(f"Could not read Z: {exc}", "warn")
            return
        self.config.data["Gcode_Param"]["z_safe"] = round(float(z), 4)
        self.config.save()
        self.teach_panel.set_safe_z(z)
        self.audit.record("machine", "safe_z_set", message=f"Safe Z set to {z:.4f} mm.",
                          operator=self.teach_panel.program.operator,
                          program_name=self.teach_panel.program.program_name,
                          details={"safe_z": round(float(z), 4)})
        self._notify(f"Safe Z set to {z:.3f} mm.")

    def _set_camera_and_spindle_zero(self) -> None:
        """Touch off camera G54 and offset G55 while retaining G54's Z reference."""
        reason = self.controller.not_ready_reason()
        if reason:
            self._notify(reason, "warn")
            return
        off = self.config.camera_offset
        try:
            _x, _y, g54_z = self.controller.work_position()
        except Exception:
            g54_z = None
        if not self.controller.set_camera_and_spindle_zero(off.x, off.y):
            self._notify("Could not set Camera G54 and Spindle G55 zero.", "warn")
            return
        self.setup_panel.enable_spindle_zero_export()
        self.audit.record("machine", "g54_g55_touch_off",
                          message="Camera G54 and spindle G55 zero set.",
                          operator=self.teach_panel.program.operator,
                          program_name=self.teach_panel.program.program_name,
                          details={"g54_xy": [0.0, 0.0], "g55_xy_offset": [off.x, off.y],
                                   "g54_z_reference": g54_z})
        self._notify(
            f"Camera G54 X/Y zero and Spindle G55 zero set. G55 Z matches G54; "
            f"offset X{off.x:.3f} Y{off.y:.3f} mm."
        )

    def _set_development_mode(self, enabled: bool) -> None:
        """Apply the persisted test-only unhomed-motion gate immediately."""
        self.controller.set_development_mode(enabled)
        self.dev_mode_label.setVisible(enabled)
        self.audit.record("security", "development_mode_changed", severity="warning" if enabled else "info",
                          message="Development mode enabled." if enabled else "Development mode disabled.",
                          details={"enabled": bool(enabled)})
        self._notify(
            "Development mode enabled: unhomed motion is allowed by CamVision."
            if enabled else "Development mode disabled: homing is required by CamVision."
        )

    def _go_to_safe_z(self) -> None:
        """Rapid Z to the saved Safe Z while preserving the current X/Y position."""
        reason = self.controller.not_ready_reason()
        if reason:
            self._notify(reason, "warn")
            return
        safe_z = self.config.gcode_params()["z_safe"]
        if self.controller.mdi(f"G0 Z{safe_z:.4f}"):
            self._notify(f"Moved to Safe Z {safe_z:.3f} mm.")
        else:
            self._notify("Could not move to Safe Z.", "warn")

    def _check_spindle_position(self) -> None:
        """Move the spindle over the feature currently under the camera crosshair."""
        reason = self.controller.not_ready_reason()
        if reason:
            self._notify(reason, "warn")
            return
        try:
            camera_x, camera_y, _z = self.controller.work_position()
        except Exception as exc:  # pragma: no cover
            self._notify(f"Could not read camera position: {exc}", "warn")
            return

        target_x, target_y = self.config.camera_offset.compensate(camera_x, camera_y)
        safe_z = self.config.gcode_params()["z_safe"]
        if not self.controller.mdi(f"G0 Z{safe_z:.4f}"):
            self._notify("Could not move to Safe Z.", "warn")
            return
        if not self.controller.camera_up():
            self._notify("Could not retract the camera.", "warn")
            return
        if not self.controller.move_work_xy(target_x, target_y):
            self._notify("Could not move the spindle to the camera position.", "warn")
            return
        self._notify(
            f"Spindle check position: X{target_x:.3f} Y{target_y:.3f} at Safe Z."
        )

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
        self.setup_panel.arc_teaching_changed.connect(self.teach_panel.set_arc_teaching_visible)
        self.setup_panel.retract_changed.connect(self.teach_panel.set_retract)
        self.setup_panel.development_mode_changed.connect(self._set_development_mode)
        self.setup_panel.view_logs_requested.connect(self._open_log_viewer)
        self.setup_panel.calibration_changed.connect(
            lambda: self.camera_view.set_tool_diameter(self.tool_dia.value())
        )
        self.teach_panel.program_changed.connect(self._sync_tool_diameter_from_program)
        self.teach_panel.audit_event.connect(self._record_program_event)
        self._apply_overlays()

    def _record_program_event(self, action: str, payload: object) -> None:
        """Record saved/loaded/exported program evidence, never captured points."""
        data = payload if isinstance(payload, dict) else {}
        program = data.get("program")
        if program is None:
            return
        details = {
            "gcode_parameters": {
                "depth": program.depth, "retract_z": program.retract,
                "safe_z": program.z_safe, "z_feed": program.z_feed,
                "xy_feed": program.xy_feed, "spindle_rpm": program.spindle_rpm,
                "tool_dia": program.tool_dia,
            },
            "segments": len(program.segments),
            "use_spindle_zero": data.get("use_spindle_zero"),
            "apply_offset": data.get("apply_offset"),
        }
        self.audit.record("program", action, message=f"Program {action}: {program.program_name or 'unnamed'}.",
                          operator=program.operator, program_name=program.program_name,
                          program_path=str(data.get("path", "")), details=details)

    def _open_log_viewer(self) -> None:
        """Launch the read-only viewer in a separate process, never on the status loop."""
        try:
            subprocess.Popen([sys.executable, "-m", "camvision.log_viewer", "--config", self.config.path])
        except OSError as exc:
            self._notify(f"Could not open audit log viewer: {exc}", "error")

    def _audit_controller_event(self, action: str) -> None:
        if action == "abort":
            self.audit.record("program", "abort", severity="warning",
                              message="Abort requested from CamVision.",
                              operator=self.teach_panel.program.operator,
                              program_name=self.teach_panel.program.program_name)

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

        # No additional stat.poll(): observe the snapshot already refreshed by
        # the existing status/readiness work above.
        self.audit.observe_status(
            self.controller.stat, self.controller.linuxcnc,
            operator=self.teach_panel.program.operator,
            program_name=self.teach_panel.program.program_name,
        )

        # 3. Surface any LinuxCNC operator error (same channel AXIS reads).
        err = self.controller.poll_error()
        if err:
            self.audit.record_error(
                err, self.controller.stat, operator=self.teach_panel.program.operator,
                program_name=self.teach_panel.program.program_name,
            )
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
        self.audit.close()
        event.accept()
