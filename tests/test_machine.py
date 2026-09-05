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
