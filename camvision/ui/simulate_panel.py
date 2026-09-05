"""Simulation panel: animate the offset-compensated cut path on the camera view.

Uses the same :mod:`camvision.program.simulator` flattening that the G-code
generator's geometry agrees with, so the animated tool traces exactly where the
spindle will cut — camera-to-spindle offset applied, Z riding at the safe height.
Play/Step/Reset drive a marker along the flattened path.
"""

from __future__ import annotations

from typing import List, Tuple

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ..program.simulator import bounding_box, flatten_points, simulate


class SimulatePanel(QGroupBox):
    """Drives an animated tool marker over the taught path on the camera view."""

    def __init__(self, teach_panel, camera_view, config, parent=None):
        super().__init__("Simulation", parent)
        self.teach_panel = teach_panel
        self.camera_view = camera_view
        self.config = config

        self._points: List[Tuple[float, float]] = []
        self._index = 0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)

        root = QVBoxLayout(self)
        self.info = QLabel("Teach a program, then Build to preview the cut path.")
        root.addWidget(self.info)

        row = QHBoxLayout()
        self.btn_build = QPushButton("Build")
        self.btn_play = QPushButton("Play")
        self.btn_step = QPushButton("Step")
        self.btn_reset = QPushButton("Reset")
        for b in (self.btn_build, self.btn_play, self.btn_step, self.btn_reset):
            row.addWidget(b)
        root.addLayout(row)

        self.btn_build.clicked.connect(self.build)
        self.btn_play.clicked.connect(self.play)
        self.btn_step.clicked.connect(self.step)
        self.btn_reset.clicked.connect(self.reset)

    def build(self) -> None:
        program = self.teach_panel.program
        if not program.segments:
            self.info.setText("Nothing to simulate — teach a segment first.")
            return
        apply_offset = self.config.checkbox("apply_spindle_offsets", True)
        sim = simulate(program, offset=self.config.camera_offset, apply_offset=apply_offset)
        polylines = [s.cut_points for s in sim]
        # include lead-ins as their own light polylines
        for s in sim:
            if s.lead_in:
                polylines.append([s.lead_in[0], s.lead_in[1]])
        bbox = bounding_box(sim)
        self.camera_view.set_simulation(polylines, bbox)
        self._points = flatten_points(sim)
        self._index = 0
        self.info.setText(
            f"{len(program.segments)} segment(s), {len(self._points)} path points. "
            f"Offset {'applied' if apply_offset else 'off'}, Z safe = {program.z_safe:.2f} mm."
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
        self.info.setText("Simulation reset.")

    def _advance(self) -> None:
        if not self._points or self._index >= len(self._points):
            self._timer.stop()
            return
        self.camera_view.set_simulation_marker(self._points[self._index])
        self._index += 1
