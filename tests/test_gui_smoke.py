"""Headless GUI smoke test — builds the whole window with no hardware.

Runs under the offscreen Qt platform against the LinuxCNC stub and the synthetic
camera frame, so it verifies the panels assemble and the teach->simulate path
works end to end without a CNC or a camera. Skips cleanly if PyQt5/OpenCV aren't
installed.
"""

import os

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("cv2")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Force stub machine so the test is deterministic even where the real
# linuxcnc/hal modules import (e.g. on the LinuxCNC PC itself).
os.environ["CAMVISION_FORCE_STUB"] = "1"


@pytest.fixture(scope="module")
def qapp():
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def test_mainwindow_builds_and_teaches(qapp, tmp_path, monkeypatch):
    from camvision.ui.main_window import MainWindow
    from camvision.program.gcode import generate_gcode

    win = MainWindow(str(tmp_path / "config.json"))
    try:
        # Forced stub mode above, so this holds on any box incl. the LinuxCNC PC.
        assert win.controller.simulated is True
        assert win.tabs.count() == 3

        teach = win.teach_panel
        # Each pair of clicks creates one independent cutting segment.
        taught = iter([
            (0.0, 0.0, 0.0),
            (0.0, 10.0, 0.0),
            (0.0, 20.0, 0.0),
            (0.0, 30.0, 0.0),
        ])
        monkeypatch.setattr(win.controller, "work_position", lambda: next(taught))
        teach.add_point()
        assert "START point captured" in teach.arc_status.text()
        assert "X0.000" in teach.arc_status.text()
        assert "Y0.000" in teach.arc_status.text()
        assert "Z0.000" in teach.arc_status.text()
        assert "font-weight: bold" in teach.arc_status.styleSheet()
        assert teach._capture_notice_timer.isActive()
        teach._clear_capture_notification()
        assert teach.arc_status.text() == "Jog to the cut END and click Add Point again."
        assert teach.arc_status.styleSheet() == ""

        teach.add_point()
        assert "END point captured" in teach.arc_status.text()
        assert "Y10.000" in teach.arc_status.text()
        for _ in range(2):
            teach.add_point()
        assert len(teach.program.segments) == 2

        # Insert a new cut after the first cut. The selected insertion
        # location is remembered even if table selection changes between clicks.
        teach.table.selectRow(0)
        inserted = iter([(5.0, 12.0, 0.0), (5.0, 16.0, 0.0)])
        monkeypatch.setattr(win.controller, "work_position", lambda: next(inserted))
        teach.insert_point()
        assert teach.btn_insert.text() == "Finish Insert Cut (END)"
        teach.table.selectRow(1)
        teach.insert_point()

        assert len(teach.program.segments) == 3
        assert teach.program.segments[1].start == (5.0, 12.0)
        assert teach.program.segments[1].end == (5.0, 16.0)
        assert teach.program.segments[2].start == (0.0, 20.0)
        assert teach.program.segments[2].end == (0.0, 30.0)
        assert getattr(teach.program.segments[1], "_highlight", False) is True

        gcode = generate_gcode(teach.program, apply_offset=False)
        cut_moves = [line for line in gcode if line.startswith("G1 X")]
        assert cut_moves == [
            "G1 X0.0000 Y10.0000 F600",
            "G1 X5.0000 Y16.0000 F600",
            "G1 X0.0000 Y30.0000 F600",
        ]

        # Arc-teaching visibility toggle does not crash.
        teach.set_arc_teaching_visible(True)
        teach.set_arc_teaching_visible(False)

        # Simulation: Build draws the path on the camera view, Step advances it.
        sim = win.simulate_panel
        sim.build()
        assert sim._points                       # flattened path points exist
        assert win.camera_view._sim_polylines    # path drawn on the view
        sim.step()
        assert win.camera_view._sim_marker is not None  # tool marker placed
        sim.reset()
        assert win.camera_view._sim_polylines == []     # cleared

        # Notification bar reflects machine readiness (stub = ready).
        assert win.controller.not_ready_reason() is None
        win._notify("hello", "warn")
        assert win.status.text() == "hello"
    finally:
        win.camera.stop()
        win.close()


def test_safe_z_move_and_retract_setting_are_independent(qapp, tmp_path, monkeypatch):
    from camvision.config import ConfigManager
    from camvision.ui.main_window import MainWindow

    config_path = str(tmp_path / "config.json")
    config = ConfigManager(config_path)
    config.set("Gcode_Param", "z_safe", 42.5)
    config.set("Gcode_Param", "retract", 8.0)
    config.set("Gcode_Param", "tool_dia", 0.5)
    config.set("Camera_offset", "camera_to_spindle_x_offset", 10.0)
    config.set("Camera_offset", "camera_to_spindle_y_offset", 5.0)
    config.mm_per_pixel = 0.1
    config.save()

    win = MainWindow(config_path)
    try:
        commands = []
        monkeypatch.setattr(win.controller, "mdi", lambda command: commands.append(command) or True)
        win.btn_go_safe_z.click()
        assert commands == ["G0 Z42.5000"]

        # Tool diameter is typed/selected once and drives config, program,
        # the physical-size crosshair circle, and later the G-code comment.
        win.tool_dia.setValue(1.5)
        assert win.config.gcode_params()["tool_dia"] == 1.5
        assert win.teach_panel.program.tool_dia == 1.5
        assert win.camera_view.center_circle_diameter == 15

        # Check Spindle Position applies the same camera offset as G-code.
        commands.clear()
        monkeypatch.setattr(win.controller, "work_position", lambda: (50.0, 25.0, 0.0))
        win.btn_check_spindle.click()
        assert commands == [
            "G0 Z42.5000",
            "M65 P0",
            "G0 X40.0000 Y20.0000",
        ]

        # Setup changes Retract Z immediately for both config and active G-code.
        win.setup_panel.retract_z.setValue(6.25)
        assert win.config.gcode_params()["retract"] == 6.25
        assert win.teach_panel.program.retract == 6.25

        # One persisted feed controls cutting-segment motion in both dry-run modes.
        win.setup_panel.simulation_feed.setValue(175.0)
        assert win.config.gcode_params()["simulation_feed"] == 175.0

        # Capturing a new Safe Z must not overwrite the independent retract value.
        monkeypatch.setattr(win.controller, "work_position", lambda: (1.0, 2.0, 57.75))
        win._set_safe_z()
        assert win.config.gcode_params()["z_safe"] == 57.75
        assert win.teach_panel.program.z_safe == 57.75
        assert win.config.gcode_params()["retract"] == 6.25
        assert win.teach_panel.program.retract == 6.25
    finally:
        win.camera.stop()
        win.close()


def test_program_metadata_and_dialog_directory_are_remembered(qapp, tmp_path, monkeypatch):
    from PyQt5.QtWidgets import QFileDialog, QMessageBox

    from camvision.ui.main_window import MainWindow

    config_path = str(tmp_path / "config.json")
    initial_dir = tmp_path / "initial"
    saved_dir = tmp_path / "saved"
    initial_dir.mkdir()
    saved_dir.mkdir()
    program_path = saved_dir / "panel-a.cvprog"

    win = MainWindow(config_path)
    try:
        teach = win.teach_panel
        teach.config.set("Program_Settings", "last_directory", str(initial_dir))
        teach.config.save()
        teach.name_edit.setText("Panel A")
        teach.operator_edit.setText("Operator 1")
        win.tool_dia.setValue(2.0)

        save_directories = []

        def choose_save(_parent, _title, directory, _file_filter):
            save_directories.append(directory)
            return str(program_path), ""

        monkeypatch.setattr(QFileDialog, "getSaveFileName", choose_save)
        monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
        teach.save()

        assert save_directories == [str(initial_dir)]
        assert program_path.exists()
        assert teach.config.get("Program_Settings", "last_directory") == str(saved_dir)

        open_directories = []

        def choose_open(_parent, _title, directory, _file_filter):
            open_directories.append(directory)
            return str(program_path), ""

        monkeypatch.setattr(QFileDialog, "getOpenFileName", choose_open)
        win.tool_dia.setValue(1.0)
        teach.load()
        assert open_directories == [str(saved_dir)]
        assert win.tool_dia.value() == 2.0
        assert win.camera_view.center_circle_diameter == round(
            2.0 / win.config.mm_per_pixel
        )
    finally:
        win.camera.stop()
        win.close()

    # A new application window restores the last-used program identity.
    reopened = MainWindow(config_path)
    try:
        assert reopened.teach_panel.name_edit.text() == "Panel A"
        assert reopened.teach_panel.operator_edit.text() == "Operator 1"
        assert reopened.teach_panel._dialog_directory() == str(saved_dir)
    finally:
        reopened.camera.stop()
        reopened.close()
