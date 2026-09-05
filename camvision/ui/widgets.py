"""Small shared UI helpers."""

from __future__ import annotations

from PyQt5.QtCore import QEvent, QObject
from PyQt5.QtWidgets import QAbstractSpinBox, QComboBox


class WheelBlocker(QObject):
    """Application event filter that stops the mouse wheel changing values.

    Scrolling the wheel over a spin box or combo box normally increments/decrements
    it, which is dangerous on a CNC panel (an accidental scroll changes a feed,
    depth or step). Installed on the QApplication, this consumes wheel events
    delivered to any spin box / combo box so their value never changes on scroll.
    """

    def eventFilter(self, obj, event):  # noqa: N802 (Qt naming)
        if event.type() == QEvent.Wheel:
            w = obj
            for _ in range(3):  # the target may be an internal child (e.g. line edit)
                if isinstance(w, (QAbstractSpinBox, QComboBox)):
                    return True  # swallow — do not change the value
                w = w.parent() if isinstance(w, QObject) else None
                if w is None:
                    break
        return False
