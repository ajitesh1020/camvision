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
    QLabel,
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
    arc_teaching_changed = pyqtSignal(bool)

    def __init__(self, controller, config, camera_service, parent=None):
        super().__init__("Setup", parent)
        self.controller = controller
        self.config = config
        self.camera_service = camera_service

        root = QVBoxLayout(self)
        root.addWidget(self._camera_group())
        root.addWidget(self._offset_group())
        root.addWidget(self._fiducial_group())
        # Camera up/down and Set X/Y Zero now live on the main camera bar.

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
        self.chk_arc_teach = QCheckBox("Enable arc / circle teaching")
        self.chk_arc_teach.setToolTip("Show the 3-Point Arc, Add Circle and Circle-R controls on "
                                      "the Teach tab. Off = straight-line teaching only.")
        self.chk_arc_teach.setChecked(self.config.checkbox("enable_arc_teaching", False))
        self.chk_arc_teach.stateChanged.connect(self._apply_arc_teaching)

        self.flip_x.setToolTip("Mirror the camera image horizontally so it matches machine +X.")
        self.flip_y.setToolTip("Mirror the camera image vertically so it matches machine +Y.")
        self.rotation.setToolTip("Rotate the camera image by 0/90/180/270° to match the mount.")
        self.device.setToolTip("Numeric /dev/videoN index (unstable). Prefer the Stable handle below.")
        self.device_handle.setToolTip(
            "Stable camera handle that survives the port renumbering: a "
            "/dev/v4l/by-id/... symlink, a device path, or a name/serial fragment. "
            "Empty = use the numeric index."
        )
        self.btn_detect.setToolTip(
            "List cameras and pin the selected index to its stable /dev/v4l/by-id handle."
        )
        self.chk_crosshair.setToolTip("Show/hide the centre crosshair used for zeroing.")
        self.chk_roi.setToolTip("Show/hide the fiducial region-of-interest rectangle.")
        self.chk_autodetect.setToolTip("Continuously overlay detected fiducial circles in the ROI.")

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
        form.addRow(self.chk_arc_teach)
        return box

    def _apply_arc_teaching(self) -> None:
        self.config.set_checkbox("enable_arc_teaching", self.chk_arc_teach.isChecked())
        self.config.save()
        self.arc_teaching_changed.emit(self.chk_arc_teach.isChecked())

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

        self.off_x.setToolTip("Camera→spindle X offset (mm) = camera-mark X minus spindle-mark X. "
                              "Subtracted from taught points so the tool cuts where the camera saw. "
                              "Use the Measure buttons below to set it (and its sign) correctly.")
        self.off_y.setToolTip("Camera→spindle Y offset (mm) = camera-mark Y minus spindle-mark Y.")
        self.mm_per_px.setToolTip("Millimetres per camera pixel, from calibration. Used to scale "
                                  "the view and fiducial positions.")
        self.inspect_z.setToolTip("Machine Z the camera drops to when inspecting (before fixture "
                                  "and baseplate thickness are added).")

        for w in (self.off_x, self.off_y, self.inspect_z):
            w.valueChanged.connect(self._apply_offsets)
        self.mm_per_px.editingFinished.connect(self._apply_offsets)

        form.addRow("Offset X (mm)", self.off_x)
        form.addRow("Offset Y (mm)", self.off_y)
        form.addRow("mm / pixel", self.mm_per_px)
        form.addRow("Inspect Z (machine mm)", self.inspect_z)

        # Guided offset measurement — the reliable way to get the sign right.
        self._cam_mark = None
        self._spindle_mark = None
        self.btn_mark_cam = QPushButton("1) Mark with camera")
        self.btn_mark_cam.setToolTip("Jog so the CROSSHAIR sits on a distinct feature, then click. "
                                     "Records the position with the camera over the feature.")
        self.btn_mark_spindle = QPushButton("2) Mark with spindle")
        self.btn_mark_spindle.setToolTip("Now jog so the SPINDLE TIP sits on the SAME feature, then "
                                         "click. The offset (and its sign) is computed and saved.")
        self.lbl_measure = QLabel("Offset unmeasured. Use 1) then 2) on the same feature.")
        self.lbl_measure.setWordWrap(True)
        self.btn_mark_cam.clicked.connect(self._mark_camera)
        self.btn_mark_spindle.clicked.connect(self._mark_spindle)
        mrow = QHBoxLayout()
        mrow.addWidget(self.btn_mark_cam)
        mrow.addWidget(self.btn_mark_spindle)
        form.addRow(mrow)
        form.addRow(self.lbl_measure)
        return box

    def _mark_camera(self) -> None:
        try:
            x, y, _z = self.controller.work_position()
        except Exception as exc:  # pragma: no cover
            self.lbl_measure.setText(f"Could not read position: {exc}")
            return
        self._cam_mark = (x, y)
        self.lbl_measure.setText(f"Camera mark: X{x:.3f} Y{y:.3f}. Now jog the spindle to the "
                                 f"same feature and press 2).")

    def _mark_spindle(self) -> None:
        if self._cam_mark is None:
            self.lbl_measure.setText("Do 1) Mark with camera first.")
            return
        try:
            x, y, _z = self.controller.work_position()
        except Exception as exc:  # pragma: no cover
            self.lbl_measure.setText(f"Could not read position: {exc}")
            return
        self._spindle_mark = (x, y)
        # gcode subtracts the stored offset, and offset = camera_mark - spindle_mark
        # gives the correctly-signed value: cutting a taught point T yields
        # T - (cam - spindle) = the true feature position.
        ox = round(self._cam_mark[0] - x, 4)
        oy = round(self._cam_mark[1] - y, 4)
        self.off_x.setValue(ox)
        self.off_y.setValue(oy)  # triggers _apply_offsets -> saves
        self.lbl_measure.setText(f"Saved camera→spindle offset: X{ox:.3f} Y{oy:.3f} mm. "
                                 f"Verify with the Spindle-path simulation before cutting.")

    # -- fiducial ---------------------------------------------------------
    def _fiducial_group(self) -> QGroupBox:
        box = QGroupBox("Fiducial correction")
        form = QFormLayout(box)
        self.chk_fiducial = QCheckBox("Enable fiducial check before run")
        self.chk_fiducial.setChecked(self.config.checkbox("enable_fiducial_check", False))
        self.chk_fiducial.setToolTip("When on, the exported program includes the fiducial "
                                     "correction hooks and you run the cycle before cutting.")
        self.chk_fiducial.stateChanged.connect(self._apply_overlays)

        h = self.config.hough_params
        self.p1 = QSpinBox(); self.p1.setRange(1, 500); self.p1.setValue(h.param1)
        self.p1.setToolTip("HoughCircles param1: Canny edge high threshold. Lower finds more edges.")
        self.p2 = QSpinBox(); self.p2.setRange(1, 500); self.p2.setValue(h.param2)
        self.p2.setToolTip("HoughCircles param2: accumulator threshold. Lower detects more (weaker) circles.")
        self.min_r = QSpinBox(); self.min_r.setRange(1, 500); self.min_r.setValue(h.min_radius)
        self.min_r.setToolTip("Smallest fiducial circle radius to detect, in pixels.")
        self.max_r = QSpinBox(); self.max_r.setRange(1, 500); self.max_r.setValue(h.max_radius)
        self.max_r.setToolTip("Largest fiducial circle radius to detect, in pixels.")
        for w in (self.p1, self.p2, self.min_r, self.max_r):
            w.valueChanged.connect(self._apply_fiducial)

        form.addRow(self.chk_fiducial)
        form.addRow("param1", self.p1)
        form.addRow("param2", self.p2)
        form.addRow("min radius", self.min_r)
        form.addRow("max radius", self.max_r)

        btns = QHBoxLayout()
        self.btn_set_roi = QPushButton("Set ROI")
        self.btn_set_roi.setToolTip("Then drag a rectangle on the camera view to set the fiducial "
                                    "search region.")
        self.btn_run_cycle = QPushButton("Run fiducial cycle")
        self.btn_run_cycle.setToolTip("Move to both taught fiducials, detect them, and apply the "
                                      "rotation correction (G10 L2 P0 R). Homed machine required.")
        btns.addWidget(self.btn_set_roi)
        btns.addWidget(self.btn_run_cycle)
        form.addRow(btns)
        self.btn_set_roi.clicked.connect(self.request_roi.emit)
        self.btn_run_cycle.clicked.connect(self.request_fiducial_cycle.emit)
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
