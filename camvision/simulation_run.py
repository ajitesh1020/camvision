"""Real-machine simulation dry-runs (kept at safe Z, nothing touches the PCB).

Two modes, matching the operator's workflow for verifying a taught program before
cutting:

* **camera-follow** — the camera is deployed and the machine traces the *taught*
  XY path at the safe Z, so the operator watches the crosshair follow the cut
  line on the actual PCB. This checks the teaching (no offset applied).
* **spindle-path** — the camera is retracted and the machine traces the
  *offset-compensated* path at the safe Z, so the spindle moves over exactly
  where it will cut. This checks the camera-to-spindle offset.

The dry-run is written to a temporary ``.ngc`` and executed in **AUTO** mode, so
LinuxCNC runs the whole path continuously — the GUI stays responsive and the
camera keeps streaming, and it doesn't stop half-way (the earlier line-by-line
MDI approach could stall on a transient not-ready). Z stays at the safe height
throughout. Note: this loads a temporary program, replacing whatever AXIS had
loaded — reload your file in AXIS afterwards.
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Callable, List, Optional

from .program.gcode import dryrun_moves

log = logging.getLogger("camvision.simrun")

CAMERA_FOLLOW = "camera"
SPINDLE_PATH = "spindle"

# Pause (seconds) at the end of each cut so the operator can view the point.
VIEW_DWELL_S = 2.0


class SimulationRunner:
    def __init__(self, controller, config, program,
                 pump_events: Optional[Callable[[], None]] = None,
                 status: Optional[Callable[[str], None]] = None):
        self.controller = controller
        self.config = config
        self.program = program
        self.pump_events = pump_events or (lambda: None)
        self.status = status or (lambda msg: None)

    def abort(self) -> None:
        try:
            self.controller.abort()
        except Exception:
            pass
        self.status("Simulation aborted.")

    def _write_program(self, mode: str, safe_z: float, simulation_feed: float) -> str:
        apply_offset = (mode == SPINDLE_PATH)
        # Camera down to watch the path (follow); up to show the spindle path.
        cam = "M64 P0" if mode == CAMERA_FOLLOW else "M65 P0"
        # Pause at each point in camera-follow so the operator can view every cut.
        dwell = VIEW_DWELL_S if mode == CAMERA_FOLLOW else 0.0
        moves = dryrun_moves(self.program, self.config.camera_offset, apply_offset,
                             safe_z, dwell_s=dwell,
                             simulation_feed=simulation_feed)
        lines: List[str] = [
            f"( CamVision dry-run: {mode} — safe Z {safe_z:.3f} — "
            f"XY feed {simulation_feed:.0f} mm/min )",
            cam,
        ]
        lines += moves
        lines.append("M2")
        fd, path = tempfile.mkstemp(prefix="camvision_sim_", suffix=".ngc")
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(lines) + "\n")
        return path

    def run(self, mode: str) -> Optional[str]:
        """Start one dry-run mode in AUTO. Returns an error string, or None."""
        if not self.program.segments:
            return "Nothing to simulate — teach at least one segment."
        reason = self.controller.not_ready_reason()
        if reason:
            return reason

        params = self.config.gcode_params()
        safe_z = params["z_safe"]
        simulation_feed = params["simulation_feed"]
        path = self._write_program(mode, safe_z, simulation_feed)
        if not self.controller.run_program_file(path):
            return "Machine did not start the dry-run (not ready?)."
        self.status(f"Dry-run ({mode}) running at {simulation_feed:.0f} mm/min — "
                    f"stays at safe Z {safe_z:.2f} mm. "
                    f"Reload your program in AXIS afterwards.")
        return None
