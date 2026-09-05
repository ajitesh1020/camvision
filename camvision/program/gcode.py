"""G-code generation from a :class:`~camvision.program.model.Program`.

Faithfully reproduces the legacy program skeleton (see
``cam_align/cam_jog.py::generate_gcode``) and extends it with arc (G2/G3) and
full-circle support. The camera-to-spindle offset is applied here so the spindle
cuts exactly where the camera taught the line; Z rides at the safe height between
cuts and plunges per segment.

Pure function of the model + offset — no Qt, no LinuxCNC — so it is unit-tested
directly (``tests/test_gcode.py``).
"""

from __future__ import annotations

from typing import List, Tuple

from ..vision.geometry import CameraOffset
from .model import ArcDirection, Program, Segment, SegmentType


def _fmt_xy(x: float, y: float) -> str:
    return f"X{x:.4f} Y{y:.4f}"


def _arc_ij(start: Tuple[float, float], center: Tuple[float, float]) -> str:
    """Center-format I/J offsets (relative to the segment start), per RS274NGC."""
    i = center[0] - start[0]
    j = center[1] - start[1]
    return f"I{i:.4f} J{j:.4f}"


def generate_gcode(
    program: Program,
    offset: CameraOffset | None = None,
    apply_offset: bool = True,
) -> List[str]:
    """Return the G-code for ``program`` as a list of lines.

    Parameters
    ----------
    program:
        The taught program (validated before emission).
    offset:
        Camera-to-spindle offset. When ``apply_offset`` is true every XY is
        shifted by ``-offset`` so the spindle follows the taught line.
    apply_offset:
        Mirrors the legacy "apply spindle offsets" checkbox.
    """
    program.validate()
    off = offset or CameraOffset()

    def comp(pt: Tuple[float, float]) -> Tuple[float, float]:
        return off.compensate(*pt) if apply_offset else pt

    g: List[str] = []
    g.append("G54")           # work offset
    g.append("G21")           # millimetres
    g.append("G90")           # absolute
    g.append("G17")           # XY plane
    g.append("G64 P0.01")     # path blend tolerance
    if program.fiducial_check:
        g.append("M101")      # in-GUI fiducial cycle gate (kept for compatibility)
    g.append(f"G0 Z{program.z_safe:.4f}")
    g.append(f"M3 S{program.spindle_rpm:.0f}")
    g.append("G04 P3")        # dwell for spindle to reach speed

    for seg in program.segments:
        g.extend(_segment_lines(seg, program, comp))

    g.append(f"G0 Z{program.z_safe:.4f}")
    g.append("G28")           # return to home
    if program.fiducial_check:
        g.append("M102")      # fiducial reset
    g.append("M5")            # spindle off
    g.append("M30")           # program end
    return g


def _segment_lines(seg: Segment, program: Program, comp) -> List[str]:
    """Emit the moves for one segment: rapid to start, plunge, cut, retract."""
    lines: List[str] = []
    start = comp(seg.start)
    lines.append(f"G0 {_fmt_xy(*start)}")                       # rapid to start (safe Z)
    lines.append(f"G0 Z{program.retract:.4f}")                  # drop to retract height
    lines.append(f"G1 Z{seg.z:.4f} F{program.z_feed:.0f}")      # plunge to cut depth

    if seg.type == SegmentType.LINE:
        end = comp(seg.end)
        lines.append(f"G1 {_fmt_xy(*end)} F{program.xy_feed:.0f}")
    elif seg.type == SegmentType.ARC:
        end = comp(seg.end)
        center = comp(seg.center)
        code = "G2" if seg.direction == ArcDirection.CW else "G3"
        lines.append(f"{code} {_fmt_xy(*end)} {_arc_ij(start, center)} F{program.xy_feed:.0f}")
    elif seg.type == SegmentType.CIRCLE:
        center = comp(seg.center)
        code = "G2" if seg.direction == ArcDirection.CW else "G3"
        # Full circle: end == start, I/J point from start to center.
        lines.append(f"{code} {_fmt_xy(*start)} {_arc_ij(start, center)} F{program.xy_feed:.0f}")

    lines.append(f"G0 Z{program.retract:.4f}")                  # retract before next segment
    return lines


def write_gcode(program: Program, path: str, offset: CameraOffset | None = None,
                apply_offset: bool = True) -> None:
    """Generate and write the program to ``path`` (adds a trailing newline)."""
    lines = generate_gcode(program, offset=offset, apply_offset=apply_offset)
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
