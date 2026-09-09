"""Low-impact operator/program/machine audit logging.

This module deliberately has no LinuxCNC commands and never calls ``stat.poll``.
The GUI supplies already-polled status snapshots; ``AuditLog`` only detects state
changes and puts compact records on a memory queue.  A single background thread
owns SQLite, so machining and the Qt event loop never wait for disk I/O.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


def audit_root() -> Path:
    """Return the per-user audit directory (never a shared/network location)."""
    return Path.home() / ".dePaneling-log"


def _now() -> tuple[str, str]:
    local = datetime.now().astimezone()
    return local.isoformat(timespec="milliseconds"), datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _number_list(value: Any, count: int = 3) -> list[Optional[float]]:
    try:
        return [float(value[i]) for i in range(count)]
    except (TypeError, ValueError, IndexError):
        return [None] * count


class AuditLog:
    """Asynchronous, event-only audit store with daily SQLite databases."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root else audit_root()
        self._events: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=10_000)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._writer, name="camvision-audit", daemon=True)
        self._last_state: tuple[Any, ...] | None = None
        self._last_file = ""
        self._run_active = False
        self._last_paused: bool | None = None
        self._last_line: tuple[str, Any, Any] | None = None
        self._last_wcs: tuple[Any, ...] | None = None
        self._dropped = 0
        self._thread.start()

    def record(self, category: str, action: str, *, severity: str = "info",
               message: str = "", operator: str = "", program_name: str = "",
               program_path: str = "", details: Dict[str, Any] | None = None,
               snapshot: Dict[str, Any] | None = None) -> None:
        """Queue one audit event without waiting for a database operation."""
        local_time, utc_time = _now()
        event = {
            "local_time": local_time, "utc_time": utc_time, "category": category,
            "action": action, "severity": severity, "message": message,
            "operator": operator, "program_name": program_name,
            "program_path": program_path, "details": details or {},
            "snapshot": snapshot or {},
        }
        try:
            self._events.put_nowait(event)
        except queue.Full:
            # Losing a non-critical audit record is preferable to ever delaying
            # the LinuxCNC UI. The next successful record makes this visible.
            self._dropped += 1

    def snapshot(self, stat: Any) -> Dict[str, Any]:
        """Create a JSON-safe status snapshot without polling LinuxCNC."""
        machine = _number_list(getattr(stat, "actual_position", None))
        g5x = _number_list(getattr(stat, "g5x_offset", None))
        g92 = _number_list(getattr(stat, "g92_offset", None))
        work = [
            None if machine[i] is None or g5x[i] is None or g92[i] is None
            else machine[i] - g5x[i] - g92[i]
            for i in range(3)
        ]
        return {
            "machine_xyz": machine, "work_xyz": work,
            "g5x_index": getattr(stat, "g5x_index", None),
            "g5x_offset": g5x, "g92_offset": g92,
            "file": str(getattr(stat, "file", "") or ""),
            "interp_state": getattr(stat, "interp_state", None),
            "task_mode": getattr(stat, "task_mode", None),
            "motion_line": getattr(stat, "motion_line", None),
            "current_line": getattr(stat, "current_line", None),
            "paused": bool(getattr(stat, "paused", False)),
        }

    def observe_status(self, stat: Any, linuxcnc: Any, *, operator: str = "",
                       program_name: str = "") -> None:
        """Record program state transitions from an already-polled status object."""
        snap = self.snapshot(stat)
        interp = snap["interp_state"]
        idle = getattr(linuxcnc, "INTERP_IDLE", 1)
        reading = getattr(linuxcnc, "INTERP_READING", 2)
        paused = getattr(linuxcnc, "INTERP_PAUSED", 3)
        state = (snap["file"], interp, snap["task_mode"])
        previous = self._last_state
        self._last_state = state

        path = snap["file"]
        # LinuxCNC exposes only the active work coordinate system through
        # status. Record it when it changes, not on every 200 ms poll. This
        # captures the actual G54/G55 used while AXIS executes a program.
        wcs = (snap["g5x_index"], *snap["g5x_offset"])
        if wcs != self._last_wcs:
            self._last_wcs = wcs
            g_code = _g5x_name(snap["g5x_index"])
            self.record("machine", "work_offset", message=f"Active work offset: {g_code}.",
                        operator=operator, program_name=program_name, program_path=path,
                        details={"coordinate_system": g_code, "g5x_offset": snap["g5x_offset"]},
                        snapshot=snap)
        if path and path != self._last_file:
            self._last_file = path
            self.record("program", "loaded", message="LinuxCNC loaded program", operator=operator,
                        program_name=program_name, program_path=path,
                        details={"gcode_summary_pending": True}, snapshot=snap)
        # AXIS can enter AUTO after CamVision starts, and some LinuxCNC builds
        # expose execution more reliably via current_line/motion_line than
        # interp_state. Any advancing line means the loaded program is running.
        line = (path, snap["current_line"], snap["motion_line"])
        advancing_line = self._last_line is not None and path == self._last_line[0] and line != self._last_line
        self._last_line = line
        running = interp in (reading, getattr(linuxcnc, "INTERP_WAITING", 4)) or advancing_line
        paused_now = snap["paused"] or interp == paused
        if running and not paused_now and not self._run_active:
            self.record("program", "run", message="Program execution started", operator=operator,
                        program_name=program_name, program_path=path, snapshot=snap)
            self._run_active = True
        if paused_now and self._last_paused is not True and self._run_active:
            self.record("program", "pause", message="Program execution paused", operator=operator,
                        program_name=program_name, program_path=path, snapshot=snap)
        elif self._last_paused is True and not paused_now and self._run_active:
            self.record("program", "resume", message="Program execution resumed", operator=operator,
                        program_name=program_name, program_path=path, snapshot=snap)
        self._last_paused = paused_now
        old_interp = previous[1] if previous is not None else None
        if self._run_active and interp == idle and old_interp != idle:
            self.record("program", "stopped", severity="warning",
                        message="Program returned to idle; completed versus abort cannot be proven from status alone.",
                        operator=operator, program_name=program_name, program_path=path, snapshot=snap)
            self._run_active = False

    def record_error(self, message: str, stat: Any, *, operator: str = "", program_name: str = "") -> None:
        self.record("linuxcnc", "error", severity="error", message=message,
                    operator=operator, program_name=program_name, snapshot=self.snapshot(stat))

    def close(self) -> None:
        self._stop.set()
        try:
            self._events.put_nowait(None)
        except queue.Full:
            pass
        self._thread.join(timeout=2.0)

    def _writer(self) -> None:
        pending: list[dict[str, Any]] = []
        while not self._stop.is_set() or not self._events.empty():
            try:
                item = self._events.get(timeout=0.25)
            except queue.Empty:
                item = None
            if item is not None:
                pending.append(item)
            if pending and (len(pending) >= 25 or item is None):
                try:
                    for event in pending:
                        if event["category"] == "program" and event["action"] == "loaded":
                            event["details"] = _gcode_summary(event["program_path"])
                    self._write_batch(pending)
                    pending.clear()
                except Exception:
                    # The audit writer must never take down or stall machining.
                    pending.clear()

    def _write_batch(self, events: Iterable[dict[str, Any]]) -> None:
        batch = list(events)
        if not batch:
            return
        day = datetime.fromisoformat(batch[0]["local_time"]).date()
        directory = self.root / f"{day:%Y}" / f"{day:%m}"
        directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass
        database = directory / f"{day.isoformat()}.sqlite3"
        with sqlite3.connect(str(database), timeout=2.0) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("""CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, local_time TEXT NOT NULL, utc_time TEXT NOT NULL,
                category TEXT NOT NULL, action TEXT NOT NULL, severity TEXT NOT NULL,
                message TEXT NOT NULL, operator TEXT, program_name TEXT, program_path TEXT,
                details_json TEXT NOT NULL, snapshot_json TEXT NOT NULL)""")
            conn.execute("CREATE INDEX IF NOT EXISTS events_time ON events(local_time)")
            conn.executemany(
                "INSERT INTO events (local_time, utc_time, category, action, severity, message, operator, program_name, program_path, details_json, snapshot_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(e["local_time"], e["utc_time"], e["category"], e["action"], e["severity"],
                  e["message"], e["operator"], e["program_name"], e["program_path"],
                  json.dumps(e["details"], sort_keys=True), json.dumps(e["snapshot"], sort_keys=True))
                 for e in batch],
            )
        try:
            os.chmod(database, 0o600)
        except OSError:
            pass


def _gcode_summary(path: str) -> Dict[str, Any]:
    """Read a loaded program once, outside the status loop, for audit evidence."""
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return {"readable": False}
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    comments = [line.strip()[1:-1].strip() for line in lines if line.strip().startswith("(") and line.strip().endswith(")")]
    z_lines = [line.strip() for line in lines if "Z" in line.upper() and line.strip().upper().startswith(("G0", "G1", "G2", "G3"))]
    return {
        "readable": True, "sha256": hashlib.sha256(raw).hexdigest(),
        "line_count": len(lines), "header": comments[:20], "z_commands": z_lines[:2000],
    }


def _g5x_name(index: Any) -> str:
    """Map LinuxCNC's 1-based G5x index to the operator-facing name."""
    try:
        value = int(index)
    except (TypeError, ValueError):
        return "unknown"
    return {1: "G54", 2: "G55", 3: "G56", 4: "G57", 5: "G58", 6: "G59"}.get(value, f"G5x-{value}")
