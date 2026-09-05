"""Setup panel: camera orientation, offsets, thicknesses, fiducial + zeroing.

Groups the machine/vision configuration the operator touches during setup and
writes every change back through :class:`~camvision.config.ConfigManager`. Also
hosts the crosshair-zero button (align the crosshair to the PCB edge, then set
G54 X/Y zero) and the camera cylinder up/down and fiducial-enable toggle.
"""

from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)


class SetupPanel(QGroupBox):
    """Configuration + setup actions, all persisted to config.json."""

    request_roi = pyqtSignal()
    request_fiducial_cycle = pyqtSignal()
    overlays_changed = pyqtSignal()

    def __init__(self, controller, config, camera_service, parent=None):
        super().__init__("Setup", parent)
        self.controller = controller
        self.config = config
        self.camera_service = camera_service

        root = QVBoxLayout(self)
        root.addWidget(self._camera_group())
        root.addWidget(self._offset_group())
        root.addWidget(self._fiducial_group())
        root.addWidget(self._action_group())

    # -- camera / overlays -----------------------------------------------
    def _camera_group(self) -> QGroupBox:
        box = QGroupBox("Camera & overlays")
        form = QFormLayout(box)

        self.flip_x = QCheckBox()
        self.flip_x.setChecked(self.config.get("Camera_Settings", "flip_x", False))
        self.flip_y = QCheckBox()
        self.flip_y.setChecked(self.config.get("Camera_Settings", "flip_y", False))
        self.rotation = QSpinBox()
        self.rotation.setRange(0, 270)
        self.rotation.setSingleStep(90)
        self.rotation.setValue(int(self.config.get("Camera_Settings", "rotation_angle", 0)))
        self.device = QSpinBox()
        self.device.setRange(0, 10)
        self.device.setValue(int(self.config.get("Camera_Settings", "video_input_port", 0)))

        # Stable device handle (recommended). Empty => use the numeric port above.
        self.device_handle = QLineEdit(self.config.get("Camera_Settings", "device", ""))
        self.device_handle.setPlaceholderText("/dev/v4l/by-id/usb-...-video-index0  (recommended)")
        self.btn_detect = QPushButton("Detect / pin camera")
        self.btn_detect.clicked.connect(self._detect_camera)

        self.chk_crosshair = QCheckBox("Crosshair")
        self.chk_crosshair.setChecked(self.config.checkbox("enable_crosshair", True))
        self.chk_roi = QCheckBox("ROI")
        self.chk_roi.setChecked(self.config.checkbox("enable_roi", True))
        self.chk_autodetect = QCheckBox("Fiducial autodetect overlay")
        self.chk_autodetect.setChecked(self.config.checkbox("enable_autodetection", False))

        self.flip_x.stateChanged.connect(self._apply_camera)
        self.flip_y.stateChanged.connect(self._apply_camera)
        self.rotation.valueChanged.connect(self._apply_camera)
        self.device.valueChanged.connect(self._apply_camera)
        self.device_handle.editingFinished.connect(self._apply_camera)
        for c in (self.chk_crosshair, self.chk_roi, self.chk_autodetect):
            c.stateChanged.connect(self._apply_overlays)

        form.addRow("Flip X", self.flip_x)
        form.addRow("Flip Y", self.flip_y)
        form.addRow("Rotation", self.rotation)
        form.addRow("Video device (index)", self.device)
        form.addRow("Stable handle", self.device_handle)
        form.addRow(self.btn_detect)
        overlays = QHBoxLayout()
        overlays.addWidget(self.chk_crosshair)
        overlays.addWidget(self.chk_roi)
        overlays.addWidget(self.chk_autodetect)
        form.addRow(overlays)
        return box

    # -- offset / calibration --------------------------------------------
    def _offset_group(self) -> QGroupBox:
        box = QGroupBox("Camera-to-spindle offset & calibration")
        form = QFormLayout(box)
        off = self.config.camera_offset
        self.off_x = QDoubleSpinBox()
        self.off_x.setRange(-1000, 1000)
        self.off_x.setDecimals(3)
        self.off_x.setValue(off.x)
        self.off_y = QDoubleSpinBox()
        self.off_y.setRange(-1000, 1000)
        self.off_y.setDecimals(3)
        self.off_y.setValue(off.y)
        self.mm_per_px = QLineEdit(f"{self.config.mm_per_pixel:.6f}")
        self.inspect_z = QDoubleSpinBox()
        self.inspect_z.setRange(0, 200)
        self.inspect_z.setDecimals(3)
        self.inspect_z.setValue(self.config.inspect_z)

        for w in (self.off_x, self.off_y, self.inspect_z):
            w.valueChanged.connect(self._apply_offsets)
        self.mm_per_px.editingFinished.connect(self._apply_offsets)

        form.addRow("Offset X (mm)", self.off_x)
        form.addRow("Offset Y (mm)", self.off_y)
        form.addRow("mm / pixel", self.mm_per_px)
        form.addRow("Inspect Z (machine mm)", self.inspect_z)
        return box

    # -- fiducial ---------------------------------------------------------
    def _fiducial_group(self) -> QGroupBox:
        box = QGroupBox("Fiducial correction")
        form = QFormLayout(box)
        self.chk_fiducial = QCheckBox("Enable fiducial check before run")
        self.chk_fiducial.setChecked(self.config.checkbox("enable_fiducial_check", False))
        self.chk_fiducial.stateChanged.connect(self._apply_overlays)

        h = self.config.hough_params
        self.p1 = QSpinBox(); self.p1.setRange(1, 500); self.p1.setValue(h.param1)
        self.p2 = QSpinBox(); self.p2.setRange(1, 500); self.p2.setValue(h.param2)
        self.min_r = QSpinBox(); self.min_r.setRange(1, 500); self.min_r.setValue(h.min_radius)
        self.max_r = QSpinBox(); self.max_r.setRange(1, 500); self.max_r.setValue(h.max_radius)
        for w in (self.p1, self.p2, self.min_r, self.max_r):
            w.valueChanged.connect(self._apply_fiducial)

        form.addRow(self.chk_fiducial)
        form.addRow("param1", self.p1)
        form.addRow("param2", self.p2)
        form.addRow("min radius", self.min_r)
        form.addRow("max radius", self.max_r)

        btns = QHBoxLayout()
        self.btn_set_roi = QPushButton("Set ROI")
        self.btn_run_cycle = QPushButton("Run fiducial cycle")
        btns.addWidget(self.btn_set_roi)
        btns.addWidget(self.btn_run_cycle)
        form.addRow(btns)
        self.btn_set_roi.clicked.connect(self.request_roi.emit)
        self.btn_run_cycle.clicked.connect(self.request_fiducial_cycle.emit)
        return box

    # -- machine actions --------------------------------------------------
    def _action_group(self) -> QGroupBox:
        box = QGroupBox("Machine actions")
        row = QHBoxLayout(box)
        self.btn_zero = QPushButton("Set X/Y Zero (crosshair)")
        self.btn_cam_down = QPushButton("Camera Down")
        self.btn_cam_up = QPushButton("Camera Up")
        for b in (self.btn_zero, self.btn_cam_down, self.btn_cam_up):
            row.addWidget(b)
        self.btn_zero.clicked.connect(lambda: self.controller.set_work_zero_xy())
        self.btn_cam_down.clicked.connect(lambda: self.controller.camera_down())
        self.btn_cam_up.clicked.connect(lambda: self.controller.camera_up())
        return box

    # -- persistence ------------------------------------------------------
    def _apply_camera(self) -> None:
        self.config.set("Camera_Settings", "flip_x", self.flip_x.isChecked())
        self.config.set("Camera_Settings", "flip_y", self.flip_y.isChecked())
        self.config.set("Camera_Settings", "rotation_angle", int(self.rotation.value()))
        self.config.set("Camera_Settings", "video_input_port", int(self.device.value()))
        self.config.set("Camera_Settings", "device", self.device_handle.text().strip())
        if self.camera_service is not None:
            self.camera_service.flip_x = self.flip_x.isChecked()
            self.camera_service.flip_y = self.flip_y.isChecked()
            self.camera_service.rotation_angle = int(self.rotation.value())
            # Live-update the device spec so the next (re)open uses it.
            self.camera_service.device = self.config.camera_device_spec
        self.config.save()

    def _detect_camera(self) -> None:
        """List cameras and pin the current numeric port to its stable by-id handle."""
        from ..camera.resolver import list_cameras, stable_handle_for_index

        cams = list_cameras()
        if not cams:
            QMessageBox.information(self, "Cameras", "No /dev/video* devices found.")
            return

        lines = [c.label for c in cams]
        pinned = stable_handle_for_index(int(self.device.value()))
        if pinned:
            self.device_handle.setText(pinned)
            self._apply_camera()
            msg = f"Pinned device index {self.device.value()} to a stable handle:\n{pinned}\n\n"
        else:
            msg = ("No /dev/v4l/by-id handle found for the selected index "
                   "(the camera may not expose a serial). You can paste a "
                   "/dev/v4l/by-path/... handle instead.\n\n")
        QMessageBox.information(self, "Cameras detected", msg + "Available:\n" + "\n".join(lines))

    def _apply_overlays(self) -> None:
        self.config.set_checkbox("enable_crosshair", self.chk_crosshair.isChecked())
        self.config.set_checkbox("enable_roi", self.chk_roi.isChecked())
        self.config.set_checkbox("enable_autodetection", self.chk_autodetect.isChecked())
        self.config.set_checkbox("enable_fiducial_check", self.chk_fiducial.isChecked())
        self.config.save()
        self.overlays_changed.emit()

    def _apply_offsets(self) -> None:
        self.config.set("Camera_offset", "camera_to_spindle_x_offset", float(self.off_x.value()))
        self.config.set("Camera_offset", "camera_to_spindle_y_offset", float(self.off_y.value()))
        self.config.set("Camera_Detact_Z_Pos", "Machine_Z_Pos", float(self.inspect_z.value()))
        try:
            self.config.mm_per_pixel = float(self.mm_per_px.text())
        except ValueError:
            pass
        self.config.save()

    def _apply_fiducial(self) -> None:
        f = self.config.data["Fiducials_Settings"]
        f["param1"] = str(self.p1.value())
        f["param2"] = str(self.p2.value())
        f["minRadius"] = str(self.min_r.value())
        f["maxRadius"] = str(self.max_r.value())
        self.config.save()
