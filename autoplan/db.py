"""
Everything that touches SQLite lives here.

Pages and future integrations (Google Calendar, Blackboard) read and write
through these functions. Nothing else should run SQL directly.

Deletes are soft: rows get a deleted_at timestamp and are hidden from
normal reads, so nothing is lost by accident.
"""

import json
import sqlite3
from datetime import date, datetime, time
from pathlib import Path
from typing import Optional

from autoplan import config
from autoplan.models import Event, Preferences, StudySession, Task

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT    NOT NULL,
    start            TEXT    NOT NULL,   -- ISO datetime
    "end"            TEXT    NOT NULL,   -- ISO datetime
    category         TEXT    NOT NULL DEFAULT 'other',
    recurrence_days  TEXT,               -- JSON list of weekday numbers, NULL = one-time
    recurrence_until TEXT,               -- ISO date, NULL = one-time
    deleted_at       TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL,
    deadline          TEXT    NOT NULL,  -- ISO datetime
    estimated_minutes INTEGER NOT NULL,
    priority          TEXT    NOT NULL DEFAULT 'Medium',
    status            TEXT    NOT NULL DEFAULT 'open',
    deleted_at        TEXT
);

CREATE TABLE IF NOT EXISTS study_sessions (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    start   TEXT    NOT NULL,
    "end"   TEXT    NOT NULL,
    status  TEXT    NOT NULL DEFAULT 'suggested'
);

CREATE TABLE IF NOT EXISTS preferences (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL                  -- JSON-encoded
);
"""


# ---------- Connection and setup ----------

def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Open the database. Pass db_path in tests to use a temporary file."""
    path = Path(db_path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Optional[Path] = None) -> None:
    """Create tables if missing and fill in any default preferences."""
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA)
        for key, value in config.DEFAULT_PREFERENCES.items():
            conn.execute(
                "INSERT OR IGNORE INTO preferences (key, value) VALUES (?, ?)",
                (key, json.dumps(value)),
            )


# ---------- Small conversion helpers ----------

def _dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


def _d(value: Optional[str]) -> Optional[date]:
    return date.fromisoformat(value) if value else None


def _row_to_event(row: sqlite3.Row) -> Event:
    return Event(
        id=row["id"],
        name=row["name"],
        start=_dt(row["start"]),
        end=_dt(row["end"]),
        category=row["category"],
        recurrence_days=json.loads(row["recurrence_days"]) if row["recurrence_days"] else None,
        recurrence_until=_d(row["recurrence_until"]),
        deleted_at=_dt(row["deleted_at"]),
    )


def _row_to_task(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        name=row["name"],
        deadline=_dt(row["deadline"]),
        estimated_minutes=row["estimated_minutes"],
        priority=row["priority"],
        status=row["status"],
        deleted_at=_dt(row["deleted_at"]),
    )


def _row_to_session(row: sqlite3.Row) -> StudySession:
    return StudySession(
        id=row["id"],
        task_id=row["task_id"],
        start=_dt(row["start"]),
        end=_dt(row["end"]),
        status=row["status"],
    )


# ---------- Reads ----------

def list_events(include_deleted: bool = False, db_path: Optional[Path] = None) -> list[Event]:
    sql = "SELECT * FROM events"
    if not include_deleted:
        sql += " WHERE deleted_at IS NULL"
    sql += " ORDER BY start"
    with get_connection(db_path) as conn:
        return [_row_to_event(r) for r in conn.execute(sql)]


def list_tasks(include_deleted: bool = False, db_path: Optional[Path] = None) -> list[Task]:
    sql = "SELECT * FROM tasks"
    if not include_deleted:
        sql += " WHERE deleted_at IS NULL"
    sql += " ORDER BY deadline"
    with get_connection(db_path) as conn:
        return [_row_to_task(r) for r in conn.execute(sql)]


def list_study_sessions(status: Optional[str] = None, db_path: Optional[Path] = None) -> list[StudySession]:
    sql = "SELECT * FROM study_sessions"
    params: tuple = ()
    if status:
        sql += " WHERE status = ?"
        params = (status,)
    sql += " ORDER BY start"
    with get_connection(db_path) as conn:
        return [_row_to_session(r) for r in conn.execute(sql, params)]


def get_preferences(db_path: Optional[Path] = None) -> Preferences:
    """Load preferences, converting stored JSON into a Preferences object."""
    with get_connection(db_path) as conn:
        raw = {r["key"]: json.loads(r["value"]) for r in conn.execute("SELECT key, value FROM preferences")}

    # Fall back to defaults for anything missing
    merged = {**config.DEFAULT_PREFERENCES, **raw}
    work_hours = {
        config.WEEKDAYS.index(day): (time.fromisoformat(start), time.fromisoformat(end))
        for day, (start, end) in merged["work_hours"].items()
    }
    return Preferences(
        work_hours=work_hours,
        min_session_minutes=int(merged["min_session_minutes"]),
        max_session_minutes=int(merged["max_session_minutes"]),
        buffer_minutes=int(merged["buffer_minutes"]),
    )


# ---------- Writes ----------
# Add/edit/soft-delete for events and tasks arrive in Phase 2.

def set_preference(key: str, value, db_path: Optional[Path] = None) -> None:
    """Save one preference. value can be any JSON-friendly Python object."""
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO preferences (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )
