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


@pytest.fixture(scope="module")
def qapp():
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def test_mainwindow_builds_and_teaches(qapp, tmp_path):
    from camvision.ui.main_window import MainWindow

    win = MainWindow(str(tmp_path / "config.json"))
    try:
        assert win.controller.simulated is True  # no LinuxCNC on the test box
        assert win.tabs.count() == 3

        teach = win.teach_panel
        teach.capture_start()          # stub work position (0,0)
        teach.controller  # noqa: B018 - ensure attribute exists
        teach.add_line()               # line from (0,0) to (0,0)+current
        assert len(teach.program.segments) == 1

        # Simulation builds a polyline without raising.
        win.simulate_panel.build()
        assert win.simulate_panel._points  # flattened points exist
    finally:
        win.camera.stop()
        win.close()
