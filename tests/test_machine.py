"""MachineController readiness / error / liveness against the stub backend."""

import os

os.environ["CAMVISION_FORCE_STUB"] = "1"

from camvision.machine.linuxcnc_interface import MachineController  # noqa: E402


def test_stub_controller_is_ready_and_alive():
    c = MachineController()
    assert c.simulated is True
    assert c.alive() is True
    assert c.not_ready_reason() is None
    assert c.ok_for_mdi() is True
    assert c.poll_error() is None


def test_not_ready_reasons():
    c = MachineController()
    c.stat.estop = 1
    assert "E-stop" in c.not_ready_reason()
    c.stat.estop = 0
    c.stat.enabled = 0
    assert "power" in c.not_ready_reason().lower()
    c.stat.enabled = 1
    c.stat.homed = [0, 0, 0]
    assert "homed" in c.not_ready_reason().lower()
    c.set_development_mode(True)
    assert c.not_ready_reason() is None
    c.stat.estop = 1
    assert "E-stop" in c.not_ready_reason()


def test_set_camera_and_spindle_zero_keeps_g54_z():
    c = MachineController()
    commands = []
    c.mdi = lambda command: commands.append(command) or True
    c.work_position = lambda: (0.0, 0.0, 57.75)

    assert c.set_camera_and_spindle_zero(112.844, 10.867) is True
    assert commands == [
        "G54",
        "G10 L20 P1 X0 Y0",
        "G10 L20 P2 X112.8440 Y10.8670 Z57.7500",
    ]
