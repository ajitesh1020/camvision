"""Config load/save round-trip and legacy import."""

import json

from camvision.config import ConfigManager, import_legacy_config

LEGACY = {
    "Camera_offset": {"camera_to_spindle_x_offset": 109.448, "camera_to_spindle_y_offset": 9.91},
    "Pixel_to_mm_Data": {"pixels_per_mm": 0.08192319205190404, "actual_distance_mm": "10.0"},
    "ROI_Settings": {"roi": [310, 140, 442, 252]},
    "Gcode_Param": {"depth": "-2.0", "retract": "10.0", "z_safe": "25.0",
                    "spindle_RPM": "24000", "tool_dia": "0.5", "z_feed": "800", "xy_feed": "600"},
    "Checkbox_States": {"apply_spindle_offsets": True},
}


def test_defaults_and_typed_accessors(tmp_path):
    cm = ConfigManager(str(tmp_path / "config.json"))
    assert cm.camera_offset.x == 109.448
    assert cm.camera_offset.y == 9.91
    assert cm.roi == (310, 140, 442, 252)
    assert cm.gcode_params()["spindle_rpm"] == 24000.0
    assert cm.gcode_params()["retract"] == 10.0
    assert cm.gcode_params()["z_safe"] == 25.0
    assert cm.gcode_params()["simulation_feed"] == 200.0
    assert cm.get("Program_Settings", "program_name") == ""
    assert cm.get("Program_Settings", "operator") == ""
    assert cm.get("Program_Settings", "last_directory") == ""


def test_save_and_reload_roundtrip(tmp_path):
    path = str(tmp_path / "config.json")
    cm = ConfigManager(path)
    cm.jog_speed = 1234
    cm.roi = (1, 2, 3, 4)
    cm.set_checkbox("enable_fiducial_check", True)
    cm.set("Gcode_Param", "retract", 7.5)
    cm.set("Gcode_Param", "z_safe", 55.0)
    cm.set("Gcode_Param", "tool_dia", 1.5)
    cm.set("Gcode_Param", "simulation_feed", 175.0)
    cm.set("Program_Settings", "program_name", "Panel A")
    cm.set("Program_Settings", "operator", "Operator 1")
    cm.set("Program_Settings", "last_directory", "/programs")
    cm.save()

    cm2 = ConfigManager(path)
    assert cm2.jog_speed == 1234
    assert cm2.roi == (1, 2, 3, 4)
    assert cm2.checkbox("enable_fiducial_check") is True
    assert cm2.gcode_params()["retract"] == 7.5
    assert cm2.gcode_params()["z_safe"] == 55.0
    assert cm2.gcode_params()["tool_dia"] == 1.5
    assert cm2.gcode_params()["simulation_feed"] == 175.0
    assert cm2.get("Program_Settings", "program_name") == "Panel A"
    assert cm2.get("Program_Settings", "operator") == "Operator 1"
    assert cm2.get("Program_Settings", "last_directory") == "/programs"


def test_legacy_import_preserves_values(tmp_path):
    legacy_path = tmp_path / "legacy.json"
    legacy_path.write_text(json.dumps(LEGACY))
    new_path = str(tmp_path / "config.json")

    cm = import_legacy_config(str(legacy_path), new_path)
    assert cm.camera_offset.x == 109.448
    assert abs(cm.mm_per_pixel - 0.08192319205190404) < 1e-12
    assert cm.gcode_params()["depth"] == -2.0
    assert cm.gcode_params()["simulation_feed"] == 200.0
    # Missing legacy keys are filled from defaults, not lost.
    assert "Fiducials_Settings" in cm.data
