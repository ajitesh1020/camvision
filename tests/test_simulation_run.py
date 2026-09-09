"""Real-machine simulation program generation (using a fake controller)."""

import os

import pytest

from camvision.config import ConfigManager
from camvision.program.model import Program
from camvision.simulation_run import CAMERA_FOLLOW, SPINDLE_PATH, SimulationRunner


class FakeController:
    def __init__(self):
        self.path = None
        self.lines = []

    def not_ready_reason(self):
        return None

    def run_program_file(self, path):
        self.path = path
        with open(path) as stream:
            self.lines = stream.read().splitlines()
        return True


@pytest.mark.parametrize(
    "mode,camera_mcode,expected_start,expected_end",
    [
        (CAMERA_FOLLOW, "M64 P0", "X10.0000 Y20.0000", "X30.0000 Y20.0000"),
        (SPINDLE_PATH, "M65 P0", "X5.0000 Y18.0000", "X25.0000 Y18.0000"),
    ],
)
def test_both_dryrun_modes_feed_cuts_and_rapid_non_cutting_travel(
        tmp_path, mode, camera_mcode, expected_start, expected_end):
    config = ConfigManager(str(tmp_path / "config.json"))
    config.set("Gcode_Param", "z_safe", 40.0)
    config.set("Gcode_Param", "simulation_feed", 85.0)
    config.set("Camera_offset", "camera_to_spindle_x_offset", 5.0)
    config.set("Camera_offset", "camera_to_spindle_y_offset", 2.0)

    program = Program(z_safe=40.0)
    program.add_line((10.0, 20.0), (30.0, 20.0), z=-2.0)
    controller = FakeController()
    statuses = []
    runner = SimulationRunner(controller, config, program, status=statuses.append)

    try:
        assert runner.run(mode) is None
        assert camera_mcode in controller.lines
        assert f"G0 {expected_start}" in controller.lines
        assert f"G1 {expected_end} F85" in controller.lines
        assert f"G1 {expected_start} F85" not in controller.lines
        assert any("85 mm/min" in line for line in controller.lines)
        assert "85 mm/min" in statuses[-1]
    finally:
        if controller.path and os.path.exists(controller.path):
            os.remove(controller.path)
