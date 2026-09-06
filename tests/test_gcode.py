"""G-code generation: linear, arc and offset compensation."""

from camvision.program.gcode import dryrun_moves, generate_gcode
from camvision.program.model import ArcDirection, Program
from camvision.vision.geometry import CameraOffset


def _program():
    p = Program(program_name="t", operator="op", depth=-2.0, retract=10.0, z_safe=25.0,
                spindle_rpm=24000, z_feed=800, xy_feed=600)
    return p


def test_linear_program_structure_and_offset():
    p = _program()
    p.add_line((10.0, 20.0), (30.0, 20.0), z=-2.0)
    offset = CameraOffset(x=100.0, y=5.0)
    g = generate_gcode(p, offset=offset, apply_offset=True)

    # Header comments, then preamble/postamble
    assert any(ln.startswith("( CamVision program:") for ln in g)
    assert any(ln.startswith("( Operator:") for ln in g)
    assert any(ln.startswith("( Segments:") for ln in g)
    assert "G54" in g
    assert "G21" in g and "G90" in g and "G17" in g
    assert g[-1] == "M30"
    assert "M5" in g
    assert any(line.startswith("M3 S24000") for line in g)

    # Start point compensated by the camera offset (10-100, 20-5)
    assert "G0 X-90.0000 Y15.0000" in g
    # Cut to compensated end (30-100, 20-5) at xy feed
    assert "G1 X-70.0000 Y15.0000 F600" in g
    # Plunge at z feed
    assert "G1 Z-2.0000 F800" in g


def test_no_offset_when_disabled():
    p = _program()
    p.add_line((10.0, 20.0), (30.0, 20.0), z=-1.5)
    g = generate_gcode(p, offset=CameraOffset(100, 5), apply_offset=False)
    assert "G0 X10.0000 Y20.0000" in g
    assert "G1 X30.0000 Y20.0000 F600" in g


def test_arc_emits_g2_g3_with_ij():
    p = _program()
    # Quarter arc from (10,0) to (0,10) about origin, CCW => G3
    p.add_arc((10.0, 0.0), (0.0, 10.0), (0.0, 0.0), z=-2.0, direction=ArcDirection.CCW)
    g = generate_gcode(p, apply_offset=False)
    arc_line = [ln for ln in g if ln.startswith("G3")]
    assert arc_line, "expected a G3 arc line"
    # I/J are relative to the start point (0-10, 0-0) => I-10 J0
    assert "I-10.0000 J0.0000" in arc_line[0]
    assert "X0.0000 Y10.0000" in arc_line[0]


def test_circle_is_full_and_closes():
    p = _program()
    p.add_circle((5.0, 5.0), 3.0, z=-1.0)
    g = generate_gcode(p, apply_offset=False)
    circ = [ln for ln in g if ln.startswith("G2 ")]
    assert circ
    # start == end for a full circle: start is (center_x + r, center_y) = (8,5)
    assert "X8.0000 Y5.0000" in circ[0]
    assert "I-3.0000 J0.0000" in circ[0]


def test_fiducial_check_inserts_mcodes():
    p = _program()
    p.fiducial_check = True
    p.add_line((0, 0), (1, 0), z=-1)
    g = generate_gcode(p, apply_offset=False)
    assert "M101" in g and "M102" in g


def test_dryrun_stays_at_safe_z_no_plunge():
    p = _program()
    p.add_line((10.0, 20.0), (30.0, 20.0), z=-2.0)
    moves = dryrun_moves(p, CameraOffset(100, 5), apply_offset=True, safe_z=25.0)
    # No spindle, no plunge (no Z below safe), no M-codes.
    assert all("M3" not in m and "M5" not in m for m in moves)
    zmoves = [m for m in moves if "Z" in m]
    assert zmoves and all("Z25.0000" in m for m in zmoves)
    # Offset applied to XY (camera_mark convention: subtract offset).
    assert "G0 X-90.0000 Y15.0000" in moves
    assert "G1 X-70.0000 Y15.0000 F600" in moves


def test_retract_to_safe_after_every_cut():
    p = _program()  # z_safe=25, retract=10
    p.add_line((0.0, 0.0), (0.0, 10.0), z=-2.0)
    p.add_line((50.0, 0.0), (50.0, 10.0), z=-2.0)
    g = generate_gcode(p, apply_offset=False)

    # Camera is retracted before cutting.
    assert "M65 P0" in g
    # Each of the two cutting moves is immediately followed by a retract to safe Z.
    cut_idxs = [i for i, ln in enumerate(g) if ln.startswith("G1 X")]
    assert len(cut_idxs) == 2
    for i in cut_idxs:
        assert g[i + 1] == "G0 Z25.0000"
    # The rapid to a start is preceded by the previous cut's retract to safe Z.
    idx = g.index("G0 X50.0000 Y0.0000")
    assert g[idx - 1] == "G0 Z25.0000"
    # Approach descends to the retract clearance before each plunge.
    assert g.count("G0 Z10.0000") == 2


def test_dryrun_camera_follow_has_no_offset():
    p = _program()
    p.add_line((10.0, 20.0), (30.0, 20.0), z=-2.0)
    moves = dryrun_moves(p, CameraOffset(100, 5), apply_offset=False, safe_z=30.0)
    assert "G0 X10.0000 Y20.0000" in moves
    assert "G1 X30.0000 Y20.0000 F600" in moves
