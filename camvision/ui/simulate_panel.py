"""Simulation panel: two real-machine dry-runs plus an on-screen preview.

The real check runs the taught path **on the machine at the safe Z** (nothing
touches the PCB):

* **Run: Camera-follow** — camera down, machine traces the *taught* path so the
  crosshair follows the cut line on the actual PCB (verifies the teaching).
* **Run: Spindle-path** — camera up, machine traces the *offset-compensated* path
  so the spindle moves over exactly where it will cut (verifies the offset).

**Preview** additionally draws the offset-compensated path on the camera view as a
quick sanity check without moving the machine.
"""

from __future__ import annotations

from typing import List, Tuple

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (
    QApplication,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ..program.simulator import bounding_box, flatten_points, simulate
from ..simulation_run import CAMERA_FOLLOW, SPINDLE_PATH, SimulationRunner


class SimulatePanel(QGroupBox):
    """Real dry-runs at safe Z + an on-screen path preview."""

    def __init__(self, teach_panel, camera_view, config, controller, parent=None):
        super().__init__("Simulation", parent)
        self.teach_panel = teach_panel
        self.camera_view = camera_view
        self.config = config
        self.controller = controller
        self._runner = None

        self._points: List[Tuple[float, float]] = []
        self._index = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)

        root = QVBoxLayout(self)
        self.info = QLabel(
            "Verify a program before cutting (all dry-runs stay at the safe Z):\n"
            "• Set Safe Z first (button under Set X/Y Zero).\n"
            "• Simulation feed controls cuts; travel between cuts remains rapid.\n"
            "• Run: Camera-follow — camera down, follows the TAUGHT path so the\n"
            "  crosshair traces the cut line on the PCB (checks teaching).\n"
            "• Run: Spindle-path — camera up, follows the OFFSET-compensated path\n"
            "  so the spindle moves where it will actually cut (checks offset).\n"
            "• Preview just draws the path on screen without moving the machine."
        )
        self.info.setWordWrap(True)
        root.addWidget(self.info)

        # Real machine dry-runs.
        run_row = QHBoxLayout()
        self.btn_cam_follow = QPushButton("Run: Camera-follow")
        self.btn_cam_follow.setToolTip("Camera down; trace the taught path at safe Z so the "
                                       "crosshair follows the cut line at the configured "
                                       "Simulation feed. Verifies teaching.")
        self.btn_spindle = QPushButton("Run: Spindle-path")
        self.btn_spindle.setToolTip("Camera up; trace the offset-compensated path at safe Z so "
                                    "the spindle moves where it will cut at the configured "
                                    "Simulation feed. Verifies the offset.")
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setToolTip("Abort the running dry-run.")
        for b in (self.btn_cam_follow, self.btn_spindle, self.btn_stop):
            run_row.addWidget(b)
        root.addLayout(run_row)
        self.btn_cam_follow.clicked.connect(lambda: self._run(CAMERA_FOLLOW))
        self.btn_spindle.clicked.connect(lambda: self._run(SPINDLE_PATH))
        self.btn_stop.clicked.connect(self._stop)

        # On-screen preview.
        row = QHBoxLayout()
        self.btn_build = QPushButton("Preview")
        self.btn_build.setToolTip("Draw the offset-compensated path on the camera view (no motion).")
        self.btn_play = QPushButton("Play")
        self.btn_play.setToolTip("Animate the tool marker along the previewed path.")
        self.btn_step = QPushButton("Step")
        self.btn_step.setToolTip("Advance the preview marker one point.")
        self.btn_reset = QPushButton("Reset")
        self.btn_reset.setToolTip("Clear the preview overlay.")
        for b in (self.btn_build, self.btn_play, self.btn_step, self.btn_reset):
            row.addWidget(b)
        root.addLayout(row)
        self.btn_build.clicked.connect(self.build)
        self.btn_play.clicked.connect(self.play)
        self.btn_step.clicked.connect(self.step)
        self.btn_reset.clicked.connect(self.reset)

    # -- real dry-runs ----------------------------------------------------
    def _run(self, mode: str) -> None:
        self._runner = SimulationRunner(
            self.controller, self.config, self.teach_panel.program,
            pump_events=QApplication.processEvents, status=self.info.setText,
        )
        err = self._runner.run(mode)
        if err:
            self.info.setText(f"Cannot simulate: {err}")

    def _stop(self) -> None:
        if self._runner is not None:
            self._runner.abort()

    # -- on-screen preview ------------------------------------------------
    def build(self) -> None:
        program = self.teach_panel.program
        if not program.segments:
            self.info.setText("Nothing to preview — teach a segment first.")
            return
        apply_offset = self.config.checkbox("apply_spindle_offsets", True)
        sim = simulate(program, offset=self.config.camera_offset, apply_offset=apply_offset)
        polylines = [s.cut_points for s in sim]
        for s in sim:
            if s.lead_in:
                polylines.append([s.lead_in[0], s.lead_in[1]])
        self.camera_view.set_simulation(polylines, bounding_box(sim))
        self._points = flatten_points(sim)
        self._index = 0
        self.info.setText(
            f"Preview: {len(program.segments)} segment(s), {len(self._points)} points. "
            f"Offset {'applied' if apply_offset else 'off'}, safe Z = {program.z_safe:.2f} mm."
        )

    def play(self) -> None:
        if not self._points:
            self.build()
        if self._points:
            self._timer.start(40)

    def step(self) -> None:
        if not self._points:
            self.build()
        self._advance()

    def reset(self) -> None:
        self._timer.stop()
        self._index = 0
        self.camera_view.clear_simulation()
        self.info.setText("Preview cleared.")

    def _advance(self) -> None:
        if not self._points or self._index >= len(self._points):
            self._timer.stop()
            return
        self.camera_view.set_simulation_marker(self._points[self._index])
        self._index += 1
