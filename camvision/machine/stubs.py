"""Importable no-op stand-ins for the ``linuxcnc`` and ``hal`` modules.

On the machine, LinuxCNC ships real ``linuxcnc`` and ``hal`` Python modules. Off
the machine (a laptop, CI, the headless GUI smoke test) they don't exist. These
stubs let every CamVision module import and even let the GUI *run* — jogging and
MDI simply log instead of moving anything — so development and automated tests
need no CNC. :class:`~camvision.machine.linuxcnc_interface.MachineController`
falls back to them automatically and flags itself ``simulated``.
"""

from __future__ import annotations

import logging

log = logging.getLogger("camvision.stub")

# LinuxCNC mode / state / jog constants (values match the real module).
MODE_MANUAL = 1
MODE_AUTO = 2
MODE_MDI = 3
STATE_ESTOP = 1
STATE_ESTOP_RESET = 2
STATE_ON = 4
INTERP_IDLE = 1
INTERP_READING = 2
INTERP_PAUSED = 3
INTERP_WAITING = 4
JOG_STOP = 0
JOG_CONTINUOUS = 1
JOG_INCREMENT = 2
AUTO_RUN = 0
AUTO_STEP = 1
AUTO_PAUSE = 2
AUTO_RESUME = 3


class _Stat:
    """A permissive fake of ``linuxcnc.stat`` reporting a homed, idle machine."""

    def __init__(self):
        self.estop = 0
        self.enabled = 1
        self.homed = [1, 1, 1]
        self.joints = 3
        self.axes = 3
        self.interp_state = INTERP_IDLE
        self.task_mode = MODE_MANUAL
        self.position = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        self.actual_position = self.position
        self.g5x_offset = (0.0,) * 9
        self.g92_offset = (0.0,) * 9
        self.file = ""

    def poll(self):  # noqa: D401 - mirrors linuxcnc API
        return None


class _Command:
    """A fake of ``linuxcnc.command`` that logs instead of commanding motion."""

    def mode(self, m):
        log.debug("stub command.mode(%s)", m)

    def wait_complete(self, timeout=None):
        return 1

    def mdi(self, cmd):
        log.info("stub MDI: %s", cmd)

    def jog(self, *args):
        log.info("stub jog%s", args)

    def abort(self):
        log.info("stub abort")

    def auto(self, *args):
        log.info("stub auto%s", args)

    def program_open(self, path):
        log.info("stub program_open(%s)", path)

    def state(self, s):
        log.debug("stub command.state(%s)", s)

    def teleop_enable(self, v):
        log.debug("stub teleop_enable(%s)", v)


def stat():
    return _Stat()


def command():
    return _Command()


# --- minimal hal stub ----------------------------------------------------- #
class _HalComponent:
    def __init__(self, name):
        self.name = name

    def ready(self):
        pass


def component(name):
    return _HalComponent(name)


def get_value(pin):
    return 0


def set_p(pin, value):
    log.debug("stub hal.set_p(%s, %s)", pin, value)


class _Error:
    """Fake error channel that never has a message."""

    def poll(self):
        return None


def error_channel():
    return _Error()
