"""Teach panel: build an editable Program by capturing machine positions.

Workflow mirrors the legacy start/end capture but generalises it:

* **Capture Start** stores the current work XY.
* **Add Line** appends a LINE from the stored start to the current XY.
* **3-Point Arc** captures start, a mid point and an end; the circumcentre and
  turn direction give a true G2/G3 arc.
* **Add Circle** makes a full circle from a centre (current XY) and a radius.

The panel owns a :class:`Program`; the table is a view of it. Programs save/load
as ``.cvprog`` (editable later) and export to ``.ngc`` through the shared G-code
generator, so a saved teach program and its cut match exactly.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..program.gcode import write_gcode
from ..program.model import (
    ArcDirection,
    Program,
    Segment,
    SegmentType,
    arc_direction_from_three_points,
    center_from_three_points,
)
from ..program.store import load_program, save_program

COLUMNS = ["Type", "X start", "Y start", "X end", "Y end", "Cx", "Cy", "R", "Dir", "Z"]


class TeachPanel(QGroupBox):
    """Editable teaching table backed by a :class:`Program`."""

    program_changed = pyqtSignal()

    def __init__(self, controller, config, parent=None):
        super().__init__("Teach program", parent)
        self.controller = controller
        self.config = config
        self.program = self._new_program()

        self._start: Optional[Tuple[float, float]] = None
        self._last_point: Optional[Tuple[float, float]] = None
        self._insert_start: Optional[Tuple[float, float]] = None
        self._arc_points: List[Tuple[float, float]] = []

        root = QVBoxLayout(self)

        # Metadata
        meta = QHBoxLayout()
        meta.addWidget(QLabel("Program:"))
        self.name_edit = QLineEdit()
        self.name_edit.setToolTip("Program name, stored in the saved .cvprog file.")
        meta.addWidget(self.name_edit)
        meta.addWidget(QLabel("Operator:"))
        self.operator_edit = QLineEdit()
        self.operator_edit.setToolTip("Operator name, stored with the program for traceability.")
        meta.addWidget(self.operator_edit)
        root.addLayout(meta)

        # Depth for the next captured segment
        depth_row = QHBoxLayout()
        depth_row.addWidget(QLabel("Cut Z (mm):"))
        self.depth_spin = QDoubleSpinBox()
        self.depth_spin.setRange(-100.0, 50.0)
        self.depth_spin.setDecimals(3)
        self.depth_spin.setValue(self.config.gcode_params()["depth"])
        self.depth_spin.setToolTip("Cut depth (negative = into the material) applied to the next "
                                   "captured segment.")
        depth_row.addWidget(self.depth_spin)
        self.radius_label = QLabel("Circle R (mm):")
        depth_row.addWidget(self.radius_label)
        self.radius_spin = QDoubleSpinBox()
        self.radius_spin.setRange(0.1, 500.0)
        self.radius_spin.setValue(5.0)
        self.radius_spin.setToolTip("Radius used by 'Add Circle' (centre = current position).")
        depth_row.addWidget(self.radius_spin)
        root.addLayout(depth_row)

        # Capture buttons. A single "Add Point" chains straight cuts: the first
        # click sets the start, each further click adds a line from the previous
        # point — no separate start/line buttons to confuse the order.
        caps = QHBoxLayout()
        self.btn_point = QPushButton("Add Point")
        self.btn_point.setToolTip(
            "Capture one cut: click at the cut START, jog, then click at the cut END. "
            "Each start→end pair is a separate cut; the move to the next cut's start "
            "is a rapid (not a cut)."
        )
        self.btn_arc = QPushButton("3-Point Arc")
        self.btn_arc.setToolTip("Capture three points (start, a point on the arc, end); the arc "
                                "through them is added. Click three times at three positions.")
        self.btn_circle = QPushButton("Add Circle (here)")
        self.btn_circle.setToolTip("Add a full circle centred at the current XY with the radius above.")
        for b in (self.btn_point, self.btn_arc, self.btn_circle):
            caps.addWidget(b)
        root.addLayout(caps)
        self.btn_point.clicked.connect(self.add_point)
        self.btn_arc.clicked.connect(self.capture_arc_point)
        self.btn_circle.clicked.connect(self.add_circle)

        self.arc_status = QLabel("Click Add Point to set the start of the path.")
        self.arc_status.setWordWrap(True)
        root.addWidget(self.arc_status)

        # Table
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setToolTip("Taught segments (5 rows shown; scroll for more). "
                              "Select a row and 'Delete Row' to remove it.")
        # Keep the table compact: show ~5 rows by default and scroll inside it for
        # more, instead of stretching to consume the whole tab.
        self._fit_table_height(5)
        root.addWidget(self.table, 0)

        # Row editing: insert a point after the selected row, jog to a row, delete.
        edit = QHBoxLayout()
        self.btn_insert = QPushButton("Insert Point (here)")
        self.btn_insert.setToolTip(
            "Insert a new line after the selected row. Click once at the START "
            "point, jog, then click again at the END point. The inserted row is "
            "highlighted green."
        )
        self.btn_goto = QPushButton("Move to Selected")
        self.btn_goto.setToolTip("Rapid the machine to the selected row's start point (safe Z first).")
        self.btn_del = QPushButton("Delete Row")
        self.btn_del.setToolTip("Remove the selected segment from the program.")
        for b in (self.btn_insert, self.btn_goto, self.btn_del):
            edit.addWidget(b)
        root.addLayout(edit)
        self.btn_insert.clicked.connect(self.insert_point)
        self.btn_goto.clicked.connect(self.move_to_selected)
        self.btn_del.clicked.connect(self.delete_row)

        # File actions
        files = QHBoxLayout()
        self.btn_new = QPushButton("New")
        self.btn_new.setToolTip("Clear the table and start a new program.")
        self.btn_save = QPushButton("Save .cvprog")
        self.btn_save.setToolTip("Save the editable program (segments + metadata) to a .cvprog file.")
        self.btn_load = QPushButton("Load .cvprog")
        self.btn_load.setToolTip("Load a saved .cvprog program back into the table for editing.")
        self.btn_export = QPushButton("Export G-code")
        self.btn_export.setToolTip("Generate a .ngc file (camera offset compensated) to run in AXIS.")
        for b in (self.btn_new, self.btn_save, self.btn_load, self.btn_export):
            files.addWidget(b)
        root.addLayout(files)
        self.btn_new.clicked.connect(self.new_program)
        self.btn_save.clicked.connect(self.save)
        self.btn_load.clicked.connect(self.load)
        self.btn_export.clicked.connect(self.export_gcode)

        # Apply the arc/circle-teaching visibility from settings.
        self.set_arc_teaching_visible(self.config.checkbox("enable_arc_teaching", False))

    def _fit_table_height(self, rows: int) -> None:
        """Fix the table viewport to ``rows`` rows; extra rows scroll inside it."""
        header = self.table.horizontalHeader().sizeHint().height()
        row_h = self.table.verticalHeader().defaultSectionSize()
        scrollbar = self.table.horizontalScrollBar().sizeHint().height()
        h = header + rows * row_h + 2 * self.table.frameWidth() + scrollbar
        self.table.setMinimumHeight(h)
        self.table.setMaximumHeight(h)

    # -- helpers ----------------------------------------------------------
    def _new_program(self) -> Program:
        g = self.config.gcode_params()
        return Program(
            depth=g["depth"], retract=g["retract"], z_safe=g["z_safe"],
            spindle_rpm=g["spindle_rpm"], tool_dia=g["tool_dia"],
            z_feed=g["z_feed"], xy_feed=g["xy_feed"],
        )

    def _current_xy(self) -> Tuple[float, float]:
        x, y, _z = self.controller.work_position()
        return x, y

    def _sync_meta(self) -> None:
        self.program.program_name = self.name_edit.text()
        self.program.operator = self.operator_edit.text()
        self.program.fiducial_check = self.config.checkbox("enable_fiducial_check")

    def _emit_changed(self) -> None:
        self._sync_meta()
        self.program_changed.emit()

    # -- visibility -------------------------------------------------------
    def set_arc_teaching_visible(self, visible: bool) -> None:
        """Show/hide the arc + circle controls (toggled from Setup)."""
        for w in (self.btn_arc, self.btn_circle, self.radius_label, self.radius_spin):
            w.setVisible(visible)

    # -- capture ----------------------------------------------------------
    def add_point(self) -> None:
        """Capture one cut as a START then an END pair (no chaining between cuts).

        Click 1 records the cut START, click 2 the cut END and adds the line.
        The move from one cut's end to the next cut's start is a rapid, not a cut,
        so only the taught start→end pairs are cutting moves.
        """
        p = self._current_xy()
        if self._last_point is None:
            self._last_point = p
            self.arc_status.setText(
                f"Cut START at {p[0]:.3f}, {p[1]:.3f}. Jog to the cut END and click "
                f"Add Point again."
            )
            return
        self.program.add_line(self._last_point, p, self.depth_spin.value())
        self._last_point = None  # reset — the next cut is a fresh start/end pair
        self._append_row(self.program.segments[-1])
        self.arc_status.setText("Cut added. Click Add Point for the START of the next cut.")
        self._emit_changed()

    # Kept for compatibility / tests: explicit start + end.
    def capture_start(self) -> None:
        self._last_point = self._current_xy()

    def add_line(self) -> None:
        self.add_point()

    def capture_arc_point(self) -> None:
        self._arc_points.append(self._current_xy())
        n = len(self._arc_points)
        self.arc_status.setText(f"Arc point {n}/3 captured.")
        if n == 3:
            p1, p2, p3 = self._arc_points
            try:
                center = center_from_three_points(p1, p2, p3)
            except ValueError as exc:
                QMessageBox.warning(self, "Bad arc", str(exc))
                self._arc_points = []
                return
            direction = arc_direction_from_three_points(p1, p2, p3)
            self.program.add_arc(p1, p3, center, self.depth_spin.value(), direction)
            self._arc_points = []
            self._last_point = None  # each cut is a fresh start/end; no chaining
            self.arc_status.setText("Arc added.")
            self._append_row(self.program.segments[-1])
            self._emit_changed()

    def add_circle(self) -> None:
        center = self._current_xy()
        self.program.add_circle(center, self.radius_spin.value(), self.depth_spin.value())
        self._append_row(self.program.segments[-1])
        self._emit_changed()

    # -- row editing ------------------------------------------------------
    def insert_point(self) -> None:
        """Insert a new LINE (its own start + end) after the selected row.

        Two-step: the first click captures the START, the second the END. The
        inserted row is highlighted so the added segment stands out.
        """
        i = self.table.currentRow()
        if i < 0 or i >= len(self.program.segments):
            QMessageBox.information(self, "Select a row",
                                    "Select the row to insert the new line after.")
            return
        p = self._current_xy()
        if self._insert_start is None:
            self._insert_start = p
            self.arc_status.setText(
                f"Insert: START captured at {p[0]:.3f}, {p[1]:.3f}. Jog to the END "
                f"point and click Insert Point again."
            )
            return
        new_seg = Segment(type=SegmentType.LINE, start=self._insert_start, end=p,
                          z=self.depth_spin.value())
        new_seg._highlight = True  # transient (not saved) — colours the new row
        self.program.segments.insert(i + 1, new_seg)
        self._insert_start = None
        self._rebuild_table()
        self.table.selectRow(i + 1)
        self.arc_status.setText("Inserted new line (highlighted).")
        self._emit_changed()

    def move_to_selected(self) -> None:
        """Rapid the machine to the selected row's start point (safe Z first)."""
        i = self.table.currentRow()
        if i < 0 or i >= len(self.program.segments):
            QMessageBox.information(self, "Select a row", "Select a row to move to.")
            return
        target = self.program.segments[i].start
        if target is None:
            return
        reason = self.controller.not_ready_reason()
        if reason:
            QMessageBox.warning(self, "Machine not ready", reason)
            return
        safe_z = self.config.gcode_params()["z_safe"]
        self.controller.mdi(f"G0 Z{safe_z:.4f}")
        self.controller.move_work_xy(target[0], target[1])

    # -- table ------------------------------------------------------------
    def _append_row(self, seg: Segment) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)

        def cell(col, val):
            self.table.setItem(r, col, QTableWidgetItem("" if val is None else str(val)))

        cell(0, seg.type.value)
        if seg.start:
            cell(1, f"{seg.start[0]:.3f}")
            cell(2, f"{seg.start[1]:.3f}")
        if seg.end:
            cell(3, f"{seg.end[0]:.3f}")
            cell(4, f"{seg.end[1]:.3f}")
        if seg.center:
            cell(5, f"{seg.center[0]:.3f}")
            cell(6, f"{seg.center[1]:.3f}")
        cell(7, "" if seg.radius is None else f"{seg.radius:.3f}")
        cell(8, seg.direction.value)
        cell(9, f"{seg.z:.3f}")

        # Colour inserted rows so the newly added segment stands out.
        if getattr(seg, "_highlight", False):
            colour = QColor(198, 239, 206)  # light green
            for col in range(self.table.columnCount()):
                item = self.table.item(r, col)
                if item is not None:
                    item.setBackground(colour)

    def _rebuild_table(self) -> None:
        self.table.setRowCount(0)
        for seg in self.program.segments:
            self._append_row(seg)

    def delete_row(self) -> None:
        r = self.table.currentRow()
        if 0 <= r < len(self.program.segments):
            del self.program.segments[r]
            self.table.removeRow(r)
            self._emit_changed()

    # -- files ------------------------------------------------------------
    def new_program(self) -> None:
        self.program = self._new_program()
        self.name_edit.clear()
        self.operator_edit.clear()
        self._start = None
        self._last_point = None
        self._insert_start = None
        self._arc_points = []
        self.arc_status.setText("Click Add Point to set the start of the path.")
        self.table.setRowCount(0)
        self._emit_changed()

    def save(self) -> None:
        self._sync_meta()
        try:
            self.program.validate()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid program", str(exc))
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save program", "", "CamVision program (*.cvprog)")
        if not path:
            return
        if not path.endswith(".cvprog"):
            path += ".cvprog"
        save_program(self.program, path)
        QMessageBox.information(self, "Saved", f"Program saved to {path}")

    def load(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load program", "", "CamVision program (*.cvprog)")
        if not path:
            return
        self.program = load_program(path)
        self.name_edit.setText(self.program.program_name)
        self.operator_edit.setText(self.program.operator)
        self.depth_spin.setValue(self.program.depth)
        self._last_point = None  # next Add Point starts a fresh cut
        self._rebuild_table()
        self._emit_changed()

    def export_gcode(self) -> None:
        self._sync_meta()
        try:
            self.program.validate()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid program", str(exc))
            return
        if not self.program.segments:
            QMessageBox.warning(self, "Empty", "Teach at least one segment first.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export G-code", "", "G-code (*.ngc)")
        if not path:
            return
        if not path.endswith(".ngc"):
            path += ".ngc"
        apply_offset = self.config.checkbox("apply_spindle_offsets", True)
        write_gcode(self.program, path, offset=self.config.camera_offset, apply_offset=apply_offset)
        QMessageBox.information(self, "Exported", f"G-code written to {path}")
