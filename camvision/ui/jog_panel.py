"""On-screen jog controls: XYZ cross, a step selector and a speed slider.

The **step** chosen here drives both the on-screen jog taps *and* the camera
click-jog, so a single click / tap always moves a known, fixed distance. A
**tap** issues one step increment; a **hold** starts a rapid continuous jog that
stops on release. The step combo and speed slider persist to the config.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
)

LONG_PRESS_MS = 250
STEP_CHOICES = [0.05, 0.1, 0.5, 1.0, 5.0, 10.0]  # mm


class JogButton(QPushButton):
    """A push button that taps-to-increment and holds-to-continuous-jog."""

    def __init__(self, label, axis, direction, controller, speed_getter, step_getter, parent=None):
        super().__init__(label, parent)
        self.axis = axis
        self.direction = direction
        self.controller = controller
        self._speed_getter = speed_getter
        self._step_getter = step_getter
        self._holding = False

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._start_hold)

        self.pressed.connect(self._on_pressed)
        self.released.connect(self._on_released)

        sign = "+" if direction > 0 else "−"
        self.setToolTip(
            f"Jog {axis}{sign}.\nTap = one step (the Step value); "
            f"press and hold = continuous rapid jog until released."
        )

    def _on_pressed(self):
        self._holding = False
        self._timer.start(LONG_PRESS_MS)

    def _start_hold(self):
        self._holding = True
        self.controller.jog_continuous(self.axis, self.direction, self._speed_getter())

    def _on_released(self):
        if self._holding:
            self.controller.jog_stop(self.axis)
            self._holding = False
        elif self._timer.isActive():
            self._timer.stop()
            self.controller.jog_increment(
                self.axis, self.direction, self._step_getter(), self._speed_getter()
            )


class JogPanel(QGroupBox):
    """XYZ jog cross + step selector + speed slider, bound to the config."""

    def __init__(self, controller, config, parent=None):
        super().__init__("Jog", parent)
        self.controller = controller
        self.config = config

        root = QVBoxLayout(self)

        # Step selector (shared with camera click-jog).
        step_row = QHBoxLayout()
        step_lbl = QLabel("Step (mm):")
        step_lbl.setToolTip("Distance moved by one jog tap or one camera click.")
        step_row.addWidget(step_lbl)
        self.step_combo = QComboBox()
        self.step_combo.setToolTip(
            "Fixed jog increment. A single camera click, or a jog-button tap, moves "
            "this many mm on each axis toward the clicked/pressed direction."
        )
        for s in STEP_CHOICES:
            self.step_combo.addItem(f"{s:g}", s)
        self._select_step(self.config.jog_step)
        self.step_combo.currentIndexChanged.connect(self._on_step)
        step_row.addWidget(self.step_combo)
        root.addLayout(step_row)

        grid = QGridLayout()

        def btn(lbl, axis, d):
            return JogButton(lbl, axis, d, controller,
                             lambda: self.config.jog_speed, self.current_step, self)

        # XY cross
        grid.addWidget(btn("Y+", "Y", 1), 0, 1)
        grid.addWidget(btn("X-", "X", -1), 1, 0)
        grid.addWidget(btn("X+", "X", 1), 1, 2)
        grid.addWidget(btn("Y-", "Y", -1), 2, 1)
        # Z column
        grid.addWidget(btn("Z+", "Z", 1), 0, 3)
        grid.addWidget(btn("Z-", "Z", -1), 2, 3)
        root.addLayout(grid)

        # Speed slider (continuous / hold speed)
        speed_lbl = QLabel("Jog speed (mm/min):")
        speed_lbl.setToolTip("Feed rate used for press-and-hold continuous jogging.")
        root.addWidget(speed_lbl)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setToolTip("Continuous (hold) jog speed in mm/min.")
        self.slider.setRange(1, 5000)
        self.slider.setValue(int(self.config.jog_speed))
        self.speed_label = QLabel(str(int(self.config.jog_speed)))
        self.slider.valueChanged.connect(self._on_speed)
        root.addWidget(self.slider)
        root.addWidget(self.speed_label)
        root.addStretch(1)

    # -- step -------------------------------------------------------------
    def current_step(self) -> float:
        data = self.step_combo.currentData()
        return float(data) if data is not None else 1.0

    def _select_step(self, value: float) -> None:
        for i in range(self.step_combo.count()):
            if abs(float(self.step_combo.itemData(i)) - value) < 1e-9:
                self.step_combo.setCurrentIndex(i)
                return
        self.step_combo.setCurrentIndex(STEP_CHOICES.index(1.0))

    def _on_step(self) -> None:
        self.config.jog_step = self.current_step()
        self.config.save()

    # -- speed ------------------------------------------------------------
    def _on_speed(self, value: int) -> None:
        self.config.jog_speed = value
        self.speed_label.setText(str(value))
        self.config.save()
