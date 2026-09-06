"""G-code generation from a :class:`~camvision.program.model.Program`.

Faithfully reproduces the legacy program skeleton (see
``cam_align/cam_jog.py::generate_gcode``) and extends it with arc (G2/G3) and
full-circle support. The camera-to-spindle offset is applied here so the spindle
cuts exactly where the camera taught the line; Z rides at the safe height between
independent cutting segments and plunges for every segment. A header comment
block records the operator, tool diameter, creation time, and the
segment/point counts and the program extents.

Pure function of the model + offset — no Qt, no LinuxCNC — so it is unit-tested
directly (``tests/test_gcode.py``).
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Tuple

from ..vision.geometry import CameraOffset
from .model import ArcDirection, Program, Segment, SegmentType
from .simulator import bounding_box, flatten_points, simulate


def _fmt_xy(x: float, y: float) -> str:
    return f"X{x:.4f} Y{y:.4f}"


def _arc_ij(start: Tuple[float, float], center: Tuple[float, float]) -> str:
    """Center-format I/J offsets (relative to the segment start), per RS274NGC."""
    i = center[0] - start[0]
    j = center[1] - start[1]
    return f"I{i:.4f} J{j:.4f}"


def _header(program: Program, off: CameraOffset, apply_offset: bool) -> List[str]:
    """Comment lines describing the program (operator, time, size, point count)."""
    sim = simulate(program, offset=off, apply_offset=apply_offset)
    pts = flatten_points(sim)
    min_x, min_y, max_x, max_y = bounding_box(sim)
    return [
        f"( CamVision program: {program.program_name or 'unnamed'} )",
        f"( Operator: {program.operator or 'n/a'} )",
        f"( Created: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} )",
        f"( Segments: {len(program.segments)}   Cut points: {len(pts)} )",
        f"( Extents mm: X {min_x:.3f}..{max_x:.3f}  Y {min_y:.3f}..{max_y:.3f} )",
        f"( Size mm: {max_x - min_x:.3f} x {max_y - min_y:.3f} )",
        f"( Camera->spindle offset applied: {'yes' if apply_offset else 'no'}"
        f"  X{off.x:.3f} Y{off.y:.3f} )",
        f"( Tool diameter: {program.tool_dia:.3f} mm )",
    ]


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

    g: List[str] = list(_header(program, off, apply_offset))
    g.append("G54")           # work offset
    g.append("G21")           # millimetres
    g.append("G90")           # absolute
    g.append("G17")           # XY plane
    g.append("G64 P0.01")     # path blend tolerance
    g.append("M65 P0")        # camera cylinder UP (retract) for cutting
    if program.fiducial_check:
        g.append("M101")      # in-GUI fiducial cycle gate (kept for compatibility)
    g.append(f"G0 Z{program.z_safe:.4f}")
    g.append(f"M3 S{program.spindle_rpm:.0f}")
    g.append("G04 P3")        # dwell for spindle to reach speed

    # Every stored segment is an intentional cut. Any gap between one segment's
    # end and the next segment's start is implicit non-cutting travel: the first
    # cut retracts to Safe Z and the next segment begins with an XY rapid.
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
    """Moves for one segment: rapid to start → approach → plunge → cut → retract.

    Matching the reference program: every rapid travels at the **safe Z**, the
    tool descends to the retract clearance only over the start point, feeds the
    plunge, cuts, then **retracts back to the safe Z** so the next rapid clears
    the fixture. (The XY rapid to the start happens first, at the safe Z left by
    the previous retract, so travel is always high.)
    """
    lines: List[str] = []
    start = comp(seg.start)
    lines.append(f"G0 {_fmt_xy(*start)}")                       # rapid to start (at safe Z)
    lines.append(f"G0 Z{program.retract:.4f}")                  # descend to retract clearance
    lines.append(f"G1 Z{seg.z:.4f} F{program.z_feed:.0f}")      # feed plunge to cut depth
    lines.extend(_cut_move(seg, program, comp, start))
    lines.append(f"G0 Z{program.z_safe:.4f}")                   # retract to SAFE Z after the cut
    return lines


def _cut_move(seg: Segment, program: Program, comp, start,
              feed: float | None = None) -> List[str]:
    """The single cutting move (line / arc / circle) for a segment."""
    xy_feed = program.xy_feed if feed is None else feed
    if seg.type == SegmentType.LINE:
        end = comp(seg.end)
        return [f"G1 {_fmt_xy(*end)} F{xy_feed:.0f}"]
    code = "G2" if seg.direction == ArcDirection.CW else "G3"
    if seg.type == SegmentType.ARC:
        end = comp(seg.end)
        center = comp(seg.center)
        return [f"{code} {_fmt_xy(*end)} {_arc_ij(start, center)} F{xy_feed:.0f}"]
    # CIRCLE: end == start, I/J from start to centre.
    center = comp(seg.center)
    return [f"{code} {_fmt_xy(*start)} {_arc_ij(start, center)} F{xy_feed:.0f}"]


def dryrun_moves(
    program: Program,
    offset: CameraOffset | None,
    apply_offset: bool,
    safe_z: float,
    dwell_s: float = 0.0,
    simulation_feed: float | None = None,
) -> List[str]:
    """Moves that trace the path at a fixed safe Z (no plunge, no spindle).

    Used by the two simulation dry-runs:

    * camera-follow: ``apply_offset=False`` so the camera traces the taught line;
    * spindle-path:  ``apply_offset=True``  so the spindle traces the real cut.

    The tool never leaves ``safe_z``, so nothing touches the PCB. Every XY move,
    including travel to the next segment, is a controlled feed move at
    ``simulation_feed`` so the operator can see it. When ``dwell_s`` is set, the
    tool pauses that many seconds at the end of each cut. If no simulation feed
    is supplied, the program XY feed is used for backward compatibility.
    """
    program.validate()
    off = offset or CameraOffset()
    trace_feed = program.xy_feed if simulation_feed is None else float(simulation_feed)
    if trace_feed <= 0:
        raise ValueError("Simulation feed must be greater than zero")

    def comp(pt):
        return off.compensate(*pt) if apply_offset else pt

    moves: List[str] = ["G21", "G90", "G17", f"G0 Z{safe_z:.4f}"]
    for seg in program.segments:
        start = comp(seg.start)
        moves.append(f"G1 {_fmt_xy(*start)} F{trace_feed:.0f}")
        moves.extend(_cut_move(seg, program, comp, start, feed=trace_feed))
        if dwell_s > 0:
            moves.append(f"G4 P{dwell_s:g}")          # pause to view this cut
    moves.append(f"G0 Z{safe_z:.4f}")
    return moves


def write_gcode(program: Program, path: str, offset: CameraOffset | None = None,
                apply_offset: bool = True) -> None:
    """Generate and write the program to ``path`` (adds a trailing newline)."""
    lines = generate_gcode(program, offset=offset, apply_offset=apply_offset)
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
