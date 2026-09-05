"""On-screen jog buttons with the same short/long-press semantics as click-jog.

Each :class:`JogButton` distinguishes a tap from a hold: a **tap** issues one slow
increment; a **hold** starts a rapid continuous jog that stops on release. This
mirrors the mouse-click jog on the camera view so the two feel identical.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

LONG_PRESS_MS = 250
INCREMENT_MM = 0.5  # slow tap step


class JogButton(QPushButton):
    """A push button that taps-to-increment and holds-to-continuous-jog."""

    def __init__(self, label, axis, direction, controller, speed_getter, parent=None):
        super().__init__(label, parent)
        self.axis = axis
        self.direction = direction
        self.controller = controller
        self._speed_getter = speed_getter
        self._holding = False

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._start_hold)

        self.pressed.connect(self._on_pressed)
        self.released.connect(self._on_released)

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
            self.controller.jog_increment(self.axis, self.direction, INCREMENT_MM, self._speed_getter())


class JogPanel(QGroupBox):
    """XYZ jog cross + a speed slider bound to the config."""

    def __init__(self, controller, config, parent=None):
        super().__init__("Jog", parent)
        self.controller = controller
        self.config = config

        root = QVBoxLayout(self)
        grid = QGridLayout()

        def btn(lbl, axis, d):
            return JogButton(lbl, axis, d, controller, lambda: self.config.jog_speed, self)

        # XY cross
        grid.addWidget(btn("Y+", "Y", 1), 0, 1)
        grid.addWidget(btn("X-", "X", -1), 1, 0)
        grid.addWidget(btn("X+", "X", 1), 1, 2)
        grid.addWidget(btn("Y-", "Y", -1), 2, 1)
        # Z column
        grid.addWidget(btn("Z+", "Z", 1), 0, 3)
        grid.addWidget(btn("Z-", "Z", -1), 2, 3)
        root.addLayout(grid)

        # Speed slider
        root.addWidget(QLabel("Jog speed (mm/min):"))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(1, 5000)
        self.slider.setValue(int(self.config.jog_speed))
        self.speed_label = QLabel(str(int(self.config.jog_speed)))
        self.slider.valueChanged.connect(self._on_speed)
        root.addWidget(self.slider)
        root.addWidget(self.speed_label)

    def _on_speed(self, value: int) -> None:
        self.config.jog_speed = value
        self.speed_label.setText(str(value))
        self.config.save()
