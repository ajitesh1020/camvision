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


def test_mainwindow_builds_and_teaches(qapp, tmp_path):
    from camvision.ui.main_window import MainWindow

    win = MainWindow(str(tmp_path / "config.json"))
    try:
        # Forced stub mode above, so this holds on any box incl. the LinuxCNC PC.
        assert win.controller.simulated is True
        assert win.tabs.count() == 3

        teach = win.teach_panel
        # Add-Point chaining: first call sets the start, next adds a line.
        teach.add_point()              # start at stub (0,0)
        teach.program.add_line((0.0, 0.0), (10.0, 0.0), z=-1.0)
        teach.program.add_line((10.0, 0.0), (10.0, 10.0), z=-1.0)
        teach._rebuild_table()
        assert len(teach.program.segments) == 2

        # Insert a line after the first row: two clicks (start, end), highlighted.
        teach.table.selectRow(0)
        teach.insert_point()           # captures start
        teach.insert_point()           # captures end, inserts
        assert len(teach.program.segments) == 3
        assert getattr(teach.program.segments[1], "_highlight", False) is True

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
