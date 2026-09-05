"""Central configuration: one loader/saver replacing the legacy config sprawl.

The legacy code opened and rewrote ``config.json`` in ~15 places. Here a single
:class:`ConfigManager` owns the file: it loads on construction (merging over a
schema of sane defaults so missing keys never crash), exposes typed accessors for
the values the rest of the app needs, and writes atomically. The on-disk schema
is kept compatible with the legacy ``config.json`` so an existing calibration and
the measured camera offset carry straight over.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from typing import Any, Dict, Tuple

from .vision.fiducial import HoughParams
from .vision.geometry import CameraOffset

# Defaults mirror the legacy config.json structure and the 6060's measured values.
DEFAULT_CONFIG: Dict[str, Any] = {
    "Camera_Settings": {
        "flip_x": False,
        "flip_y": False,
        "rotation_angle": 0,
        "video_input_port": 0,
        # Stable device handle (recommended): a /dev/v4l/by-id/... symlink, a
        # device path, or a name/serial fragment. When non-empty it takes
        # precedence over the volatile integer video_input_port, so the kernel
        # renumbering /dev/videoN does not break the feed.
        "device": "",
        "cam_on": True,
    },
    "Fiducials_Settings": {
        "param1": "200",
        "param2": "15",
        "minDist": "100000",
        "minRadius": "5",
        "maxRadius": "10",
    },
    "ROI_Settings": {"roi": [310, 140, 442, 252]},
    "Camera_offset": {
        "camera_to_spindle_x_offset": 109.448,
        "camera_to_spindle_y_offset": 9.91,
    },
    "Camera_Detact_Z_Pos": {"Machine_Z_Pos": 78.0},
    "Fiducial_Positions": {
        "fiducial1_mm": [0.0, 0.0],
        "fiducial2_mm": [0.0, 0.0],
    },
    "Center_Pixel": {"center_pixel_X": 320, "center_pixel_Y": 240},
    "Gcode_Param": {
        "depth": "-2.0",
        "retract": "10.0",
        "z_safe": "25.0",
        "spindle_RPM": "24000",
        "tool_dia": "0.5",
        "z_feed": "800",
        "xy_feed": "600",
    },
    "Pixel_to_mm_Data": {
        "pixels_per_mm": 0.08192319205190404,  # legacy key name; value is mm/pixel
        "actual_distance_mm": "10.0",
        "multiplying_factor": 0.0016230761274373367,
    },
    "PCB_Thickness": {"pcb_thickness": "3.0"},
    "Fixture_Thickness": {"fixture_thickness": "10"},
    "Base_Plate_Thickness": {"baseplate_thickness": "0"},
    "Current_Jog_Speed": {"jog_speed": 1000},
    "Checkbox_States": {
        "enable_autodetection": False,
        "enable_crosshair": True,
        "enable_roi": True,
        "apply_spindle_offsets": True,
        "enable_fiducial_check": False,
    },
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge ``override`` onto a copy of ``base``."""
    out = copy.deepcopy(base)
    for key, val in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(val, dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = copy.deepcopy(val)
    return out


class ConfigManager:
    """Load, access and atomically persist the CamVision configuration."""

    def __init__(self, path: str):
        self.path = path
        self.data: Dict[str, Any] = copy.deepcopy(DEFAULT_CONFIG)
        self.load()

    # -- load / save ------------------------------------------------------
    def load(self) -> None:
        try:
            with open(self.path, "r") as f:
                loaded = json.load(f)
            self.data = _deep_merge(DEFAULT_CONFIG, loaded)
        except FileNotFoundError:
            self.data = copy.deepcopy(DEFAULT_CONFIG)

    def save(self) -> None:
        """Atomically write the config (temp file + ``os.replace``)."""
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self.data, f, indent=4)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    # -- generic access ---------------------------------------------------
    def get(self, section: str, key: str, default: Any = None) -> Any:
        return self.data.get(section, {}).get(key, default)

    def set(self, section: str, key: str, value: Any) -> None:
        self.data.setdefault(section, {})[key] = value

    # -- typed accessors --------------------------------------------------
    @property
    def camera_device_spec(self):
        """The device to open: the stable ``device`` handle if set, else the int port.

        Returns a string (path / by-id symlink / name fragment) when ``device``
        is configured, otherwise the integer ``video_input_port`` for backward
        compatibility.
        """
        device = self.data["Camera_Settings"].get("device", "")
        if isinstance(device, str) and device.strip():
            return device.strip()
        return int(self.data["Camera_Settings"].get("video_input_port", 0))

    @property
    def camera_offset(self) -> CameraOffset:
        return CameraOffset.from_dict(self.data["Camera_offset"])

    @property
    def mm_per_pixel(self) -> float:
        return float(self.data["Pixel_to_mm_Data"]["pixels_per_mm"])

    @mm_per_pixel.setter
    def mm_per_pixel(self, value: float) -> None:
        self.data["Pixel_to_mm_Data"]["pixels_per_mm"] = float(value)

    @property
    def roi(self) -> Tuple[int, int, int, int]:
        return tuple(int(v) for v in self.data["ROI_Settings"]["roi"])  # type: ignore[return-value]

    @roi.setter
    def roi(self, value) -> None:
        self.data["ROI_Settings"]["roi"] = [int(v) for v in value]

    @property
    def hough_params(self) -> HoughParams:
        f = self.data["Fiducials_Settings"]
        return HoughParams(
            param1=int(float(f["param1"])),
            param2=int(float(f["param2"])),
            min_dist=int(float(f["minDist"])),
            min_radius=int(float(f["minRadius"])),
            max_radius=int(float(f["maxRadius"])),
        )

    @property
    def inspect_z(self) -> float:
        """Machine Z the camera drops to for inspection (before fixture/baseplate)."""
        return float(self.data["Camera_Detact_Z_Pos"]["Machine_Z_Pos"])

    @property
    def fixture_thickness(self) -> float:
        return float(self.data["Fixture_Thickness"]["fixture_thickness"])

    @property
    def baseplate_thickness(self) -> float:
        return float(self.data["Base_Plate_Thickness"]["baseplate_thickness"])

    @property
    def jog_speed(self) -> float:
        return float(self.data["Current_Jog_Speed"]["jog_speed"])

    @jog_speed.setter
    def jog_speed(self, value: float) -> None:
        self.data["Current_Jog_Speed"]["jog_speed"] = float(value)

    def gcode_params(self) -> Dict[str, float]:
        """Numeric G-code parameters (legacy stored these as strings)."""
        g = self.data["Gcode_Param"]
        return {
            "depth": float(g["depth"]),
            "retract": float(g["retract"]),
            "z_safe": float(g["z_safe"]),
            "spindle_rpm": float(g["spindle_RPM"]),
            "tool_dia": float(g["tool_dia"]),
            "z_feed": float(g["z_feed"]),
            "xy_feed": float(g["xy_feed"]),
        }

    def checkbox(self, name: str, default: bool = False) -> bool:
        return bool(self.data["Checkbox_States"].get(name, default))

    def set_checkbox(self, name: str, value: bool) -> None:
        self.data["Checkbox_States"][name] = bool(value)


def import_legacy_config(legacy_path: str, new_path: str) -> ConfigManager:
    """Create a CamVision config seeded from a legacy ``config.json``.

    Unknown legacy keys are merged over the defaults, so calibration, ROI and the
    measured camera offset carry over unchanged. Returns a saved ConfigManager.
    """
    with open(legacy_path, "r") as f:
        legacy = json.load(f)
    cm = ConfigManager.__new__(ConfigManager)
    cm.path = new_path
    cm.data = _deep_merge(DEFAULT_CONFIG, legacy)
    cm.save()
    return cm
