"""Real-machine simulation dry-runs (kept at safe Z, nothing touches the PCB).

Two modes, matching the operator's workflow for verifying a taught program before
cutting:

* **camera-follow** — the camera is deployed and the machine traces the *taught*
  XY path at the safe Z, so the operator watches the crosshair follow the cut
  line on the actual PCB. This checks the teaching (no offset applied).
* **spindle-path** — the camera is retracted and the machine traces the
  *offset-compensated* path at the safe Z, so the spindle moves over exactly
  where it will cut. This checks the camera-to-spindle offset.

Both run the moves via MDI (so they don't disturb the program AXIS has loaded),
keep Z at the safe height throughout, and can be aborted.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from .program.gcode import dryrun_moves

log = logging.getLogger("camvision.simrun")

CAMERA_FOLLOW = "camera"
SPINDLE_PATH = "spindle"


class SimulationRunner:
    def __init__(self, controller, config, program,
                 pump_events: Optional[Callable[[], None]] = None,
                 status: Optional[Callable[[str], None]] = None):
        self.controller = controller
        self.config = config
        self.program = program
        self.pump_events = pump_events or (lambda: None)
        self.status = status or (lambda msg: None)
        self._abort = False

    def abort(self) -> None:
        self._abort = True
        try:
            self.controller.abort()
        except Exception:
            pass

    def run(self, mode: str) -> Optional[str]:
        """Run one dry-run mode. Returns an error string, or None on success."""
        if not self.program.segments:
            return "Nothing to simulate — teach at least one segment."
        reason = self.controller.not_ready_reason()
        if reason:
            return reason

        self._abort = False
        safe_z = self.config.gcode_params()["z_safe"]
        apply_offset = (mode == SPINDLE_PATH)

        # Camera down to watch the path (follow mode); up to show the spindle path.
        if mode == CAMERA_FOLLOW:
            self.controller.camera_down()
        else:
            self.controller.camera_up()

        moves = dryrun_moves(self.program, self.config.camera_offset, apply_offset, safe_z)
        total = len(moves)
        for i, line in enumerate(moves, 1):
            if self._abort:
                self.status("Simulation aborted.")
                return None
            self.pump_events()
            self.status(f"Simulating ({mode}) {i}/{total}: {line}")
            if not self.controller.mdi(line):
                return "Machine stopped accepting moves (not ready?)."
        self.status(f"Simulation ({mode}) complete — stayed at safe Z {safe_z:.2f} mm.")
        return None
