"""Thin, well-behaved wrapper around the LinuxCNC command/status channels.

Everything the GUI needs to talk to the machine goes through
:class:`MachineController`: polling status, checking readiness for MDI, issuing
MDI, native jogging (continuous for press-and-hold, incremental for a tap), work
vs machine coordinates, and the pneumatic camera cylinder / safety sensor
digital outputs (M64/M65). It lazy-imports ``linuxcnc``/``hal`` and transparently
falls back to :mod:`camvision.machine.stubs` when they're absent, so the same
code runs on the CNC and on a dev box (``controller.simulated`` says which).

Axis indices: X=0, Y=1, Z=2 (this 6060 is trivkins XYZ).
"""

from __future__ import annotations

import logging
from typing import Tuple

log = logging.getLogger("camvision.machine")

AXIS_INDEX = {"X": 0, "Y": 1, "Z": 2}


def _load_backends():
    """Return ``(linuxcnc, hal, simulated)`` — real modules if present, else stubs."""
    try:
        import linuxcnc  # type: ignore
        import hal  # type: ignore

        return linuxcnc, hal, False
    except Exception:  # pragma: no cover - exercised only off-machine
        from . import stubs

        return stubs, stubs, True


class MachineController:
    """Command + status access to LinuxCNC, safe to construct anywhere."""

    def __init__(self):
        self.linuxcnc, self.hal, self.simulated = _load_backends()
        self.stat = self.linuxcnc.stat()
        self.command = self.linuxcnc.command()
        if self.simulated:
            log.warning("LinuxCNC not available — MachineController running in SIMULATED mode")

    # -- status -----------------------------------------------------------
    def poll(self):
        self.stat.poll()
        return self.stat

    def is_homed(self) -> bool:
        self.stat.poll()
        return self.stat.homed.count(1) >= self.stat.joints

    def ok_for_mdi(self) -> bool:
        """Ready to accept MDI: powered, out of e-stop, homed and interp idle."""
        self.stat.poll()
        return (
            not self.stat.estop
            and self.stat.enabled
            and self.is_homed()
            and self.stat.interp_state == self.linuxcnc.INTERP_IDLE
        )

    # -- coordinates ------------------------------------------------------
    def machine_position(self) -> Tuple[float, float, float]:
        self.stat.poll()
        p = self.stat.actual_position
        return float(p[0]), float(p[1]), float(p[2])

    def work_position(self) -> Tuple[float, float, float]:
        """Current position in the active work coordinate system (G5x + G92)."""
        self.stat.poll()
        pos = self.stat.actual_position
        g5x = self.stat.g5x_offset
        g92 = self.stat.g92_offset
        return tuple(  # type: ignore[return-value]
            float(pos[i] - g5x[i] - g92[i]) for i in range(3)
        )

    # -- MDI --------------------------------------------------------------
    def mdi(self, command: str, wait: bool = True) -> bool:
        """Run one MDI command. Returns False (and logs) if the machine isn't ready."""
        if not self.ok_for_mdi():
            log.error("Machine not ready for MDI: %s", command)
            return False
        self.command.mode(self.linuxcnc.MODE_MDI)
        self.command.wait_complete()
        self.command.mdi(command)
        if wait:
            self.command.wait_complete()
        return True

    # -- jogging (native linuxcnc.command.jog) ----------------------------
    def jog_continuous(self, axis: str, direction: int, speed_mm_min: float) -> None:
        """Start a continuous jog (press-and-hold). ``direction`` is +1 / -1."""
        idx = AXIS_INDEX[axis]
        speed = speed_mm_min / 60.0 * (1 if direction >= 0 else -1)  # mm/s, signed
        self._set_manual()
        self.command.jog(self.linuxcnc.JOG_CONTINUOUS, 0, idx, speed)

    def jog_increment(self, axis: str, direction: int, distance_mm: float,
                      speed_mm_min: float) -> None:
        """Jog a fixed increment (a short tap). ``direction`` is +1 / -1."""
        idx = AXIS_INDEX[axis]
        speed = speed_mm_min / 60.0
        dist = distance_mm * (1 if direction >= 0 else -1)
        self._set_manual()
        self.command.jog(self.linuxcnc.JOG_INCREMENT, 0, idx, speed, dist)

    def jog_stop(self, axis: str) -> None:
        idx = AXIS_INDEX[axis]
        self.command.jog(self.linuxcnc.JOG_STOP, 0, idx)

    def _set_manual(self) -> None:
        self.stat.poll()
        if self.stat.task_mode != self.linuxcnc.MODE_MANUAL:
            self.command.mode(self.linuxcnc.MODE_MANUAL)
            self.command.wait_complete()

    # -- higher-level moves ----------------------------------------------
    def move_work_xy(self, x: float, y: float, feed: float | None = None) -> bool:
        """Move to a work-coordinate XY. Rapid (G0) if ``feed`` is None, else G1."""
        if feed is None:
            return self.mdi(f"G0 X{x:.4f} Y{y:.4f}")
        return self.mdi(f"G1 X{x:.4f} Y{y:.4f} F{feed:.1f}")

    def jog_to_work_xy(self, dx: float, dy: float, feed: float) -> bool:
        """Relative feed move by (dx, dy) mm — used by mouse click 'go-to-point'."""
        return self.mdi(f"G91 G1 X{dx:.4f} Y{dy:.4f} F{feed:.1f}") and self.mdi("G90")

    def set_work_zero_xy(self) -> bool:
        """Set the current XY as G54 work zero (crosshair-to-edge zeroing)."""
        return self.mdi("G10 L20 P0 X0 Y0")

    def apply_rotation(self, degrees: float) -> bool:
        """Rotate the G54 coordinate system (fiducial skew correction)."""
        return self.mdi(f"G10 L2 P0 R{degrees:.4f}")

    # -- pneumatic camera cylinder & safety sensor ------------------------
    def camera_down(self) -> bool:
        """Deploy the camera (cylinder down to inspect) — M64 P0."""
        return self.mdi("M64 P0")

    def camera_up(self) -> bool:
        """Retract the camera (cylinder up for cutting) — M65 P0."""
        return self.mdi("M65 P0")

    def safety_sensor(self, enable: bool) -> bool:
        return self.mdi("M64 P1" if enable else "M65 P1")

    def abort(self) -> None:
        self.command.abort()
