"""Audit logging stays asynchronous and records only state transitions."""

from pathlib import Path
import sqlite3

from camvision.audit import AuditLog
from camvision.machine import stubs


def test_audit_writes_daily_event_and_status_transitions(tmp_path):
    audit = AuditLog(tmp_path / "audit")
    stat = stubs.stat()
    audit.record("program", "exported", operator="Asha", program_name="panel")
    audit.observe_status(stat, stubs, operator="Asha", program_name="panel")
    stat.file = "/missing/panel.ngc"
    stat.interp_state = stubs.INTERP_READING
    audit.observe_status(stat, stubs, operator="Asha", program_name="panel")
    stat.interp_state = stubs.INTERP_PAUSED
    audit.observe_status(stat, stubs, operator="Asha", program_name="panel")
    stat.interp_state = stubs.INTERP_IDLE
    audit.observe_status(stat, stubs, operator="Asha", program_name="panel")
    audit.close()

    databases = list((tmp_path / "audit").glob("*/*/*.sqlite3"))
    assert len(databases) == 1
    with sqlite3.connect(databases[0]) as conn:
        actions = [row[0] for row in conn.execute("SELECT action FROM events ORDER BY id")]
    assert actions == ["exported", "loaded", "run", "pause", "stopped"]


def test_abort_notifies_optional_audit_observer():
    from camvision.machine.linuxcnc_interface import MachineController

    controller = MachineController()
    events = []
    controller.audit_callback = events.append
    controller.abort()
    assert events == ["abort"]
