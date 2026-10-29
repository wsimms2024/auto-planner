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
    # A day saved as an empty list is a day off (no study sessions)
    work_hours = {
        config.WEEKDAYS.index(day): (time.fromisoformat(hours[0]), time.fromisoformat(hours[1]))
        for day, hours in merged["work_hours"].items()
        if hours
    }
    return Preferences(
        work_hours=work_hours,
        min_session_minutes=int(merged["min_session_minutes"]),
        max_session_minutes=int(merged["max_session_minutes"]),
        buffer_minutes=int(merged["buffer_minutes"]),
        planning_days=int(merged["planning_days"]),
    )


def save_preferences(prefs: Preferences, db_path: Optional[Path] = None) -> None:
    """Save every preference at once. Raises ValidationError if invalid."""
    _check(prefs)
    work_hours = {
        day: [prefs.work_hours[i][0].strftime("%H:%M"), prefs.work_hours[i][1].strftime("%H:%M")]
        if i in prefs.work_hours else []
        for i, day in enumerate(config.WEEKDAYS)
    }
    set_preference("work_hours", work_hours, db_path)
    set_preference("min_session_minutes", prefs.min_session_minutes, db_path)
    set_preference("max_session_minutes", prefs.max_session_minutes, db_path)
    set_preference("buffer_minutes", prefs.buffer_minutes, db_path)
    set_preference("planning_days", prefs.planning_days, db_path)


# ---------- Event writes ----------

class ValidationError(ValueError):
    """Raised when an Event or Task fails validation. str() lists the problems."""


def _check(obj) -> None:
    problems = obj.validate()
    if problems:
        raise ValidationError(" ".join(problems))


def _event_values(event: Event) -> tuple:
    return (
        event.name.strip(),
        event.start.isoformat(),
        event.end.isoformat(),
        event.category,
        json.dumps(sorted(event.recurrence_days)) if event.recurrence_days else None,
        event.recurrence_until.isoformat() if event.recurrence_days and event.recurrence_until else None,
    )


def add_event(event: Event, db_path: Optional[Path] = None) -> int:
    """Save a new event and return its id. Raises ValidationError if invalid."""
    _check(event)
    with get_connection(db_path) as conn:
        cur = conn.execute(
            'INSERT INTO events (name, start, "end", category, recurrence_days, recurrence_until) '
            "VALUES (?, ?, ?, ?, ?, ?)",
            _event_values(event),
        )
        return cur.lastrowid


def update_event(event: Event, db_path: Optional[Path] = None) -> None:
    """Overwrite an existing event (matched by event.id)."""
    if event.id is None:
        raise ValueError("update_event needs an event with an id.")
    _check(event)
    with get_connection(db_path) as conn:
        conn.execute(
            'UPDATE events SET name = ?, start = ?, "end" = ?, category = ?, '
            "recurrence_days = ?, recurrence_until = ? WHERE id = ?",
            (*_event_values(event), event.id),
        )


def delete_event(event_id: int, db_path: Optional[Path] = None) -> None:
    """Soft delete: hide the event but keep the row so it can be restored."""
    with get_connection(db_path) as conn:
        conn.execute("UPDATE events SET deleted_at = ? WHERE id = ?", (datetime.now().isoformat(), event_id))


def restore_event(event_id: int, db_path: Optional[Path] = None) -> None:
    with get_connection(db_path) as conn:
        conn.execute("UPDATE events SET deleted_at = NULL WHERE id = ?", (event_id,))


def list_deleted_events(db_path: Optional[Path] = None) -> list[Event]:
    with get_connection(db_path) as conn:
        rows = conn.execute("SELECT * FROM events WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC")
        return [_row_to_event(r) for r in rows]


# ---------- Task writes ----------

def _task_values(task: Task) -> tuple:
    return (task.name.strip(), task.deadline.isoformat(), task.estimated_minutes, task.priority, task.status)


def add_task(task: Task, db_path: Optional[Path] = None) -> int:
    _check(task)
    with get_connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO tasks (name, deadline, estimated_minutes, priority, status) VALUES (?, ?, ?, ?, ?)",
            _task_values(task),
        )
        return cur.lastrowid


def update_task(task: Task, db_path: Optional[Path] = None) -> None:
    if task.id is None:
        raise ValueError("update_task needs a task with an id.")
    _check(task)
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE tasks SET name = ?, deadline = ?, estimated_minutes = ?, priority = ?, status = ? WHERE id = ?",
            (*_task_values(task), task.id),
        )


def set_task_status(task_id: int, status: str, db_path: Optional[Path] = None) -> None:
    if status not in config.TASK_STATUSES:
        raise ValidationError(f"Unknown status '{status}'.")
    with get_connection(db_path) as conn:
        conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))


def delete_task(task_id: int, db_path: Optional[Path] = None) -> None:
    """Soft delete: hide the task but keep the row so it can be restored."""
    with get_connection(db_path) as conn:
        conn.execute("UPDATE tasks SET deleted_at = ? WHERE id = ?", (datetime.now().isoformat(), task_id))


def restore_task(task_id: int, db_path: Optional[Path] = None) -> None:
    with get_connection(db_path) as conn:
        conn.execute("UPDATE tasks SET deleted_at = NULL WHERE id = ?", (task_id,))


def list_deleted_tasks(db_path: Optional[Path] = None) -> list[Task]:
    with get_connection(db_path) as conn:
        rows = conn.execute("SELECT * FROM tasks WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC")
        return [_row_to_task(r) for r in rows]


# ---------- Study session writes ----------

def add_study_session(session: StudySession, db_path: Optional[Path] = None) -> int:
    """Save a session (usually one you accepted, or rejected so it isn't suggested again)."""
    if session.end <= session.start:
        raise ValidationError("Session end must be after its start.")
    if session.status not in config.SESSION_STATUSES:
        raise ValidationError(f"Unknown session status '{session.status}'.")
    with get_connection(db_path) as conn:
        cur = conn.execute(
            'INSERT INTO study_sessions (task_id, start, "end", status) VALUES (?, ?, ?, ?)',
            (session.task_id, session.start.isoformat(), session.end.isoformat(), session.status),
        )
        return cur.lastrowid


def set_session_status(session_id: int, status: str, db_path: Optional[Path] = None) -> None:
    """Change a session's status, e.g. remove an accepted session by marking it rejected."""
    if status not in config.SESSION_STATUSES:
        raise ValidationError(f"Unknown session status '{status}'.")
    with get_connection(db_path) as conn:
        conn.execute("UPDATE study_sessions SET status = ? WHERE id = ?", (status, session_id))


def list_active_sessions(now: Optional[datetime] = None, db_path: Optional[Path] = None) -> list[StudySession]:
    """
    Accepted and rejected sessions the scheduler should know about.
    Sessions for deleted tasks are left out, and so are future sessions
    for tasks you've already marked done (no point studying for those).
    """
    now = now or datetime.now()
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT s.*, t.status AS task_status FROM study_sessions s "
            "JOIN tasks t ON t.id = s.task_id "
            "WHERE t.deleted_at IS NULL AND s.status IN ('accepted', 'rejected') ORDER BY s.start"
        ).fetchall()
    sessions = []
    for r in rows:
        session = _row_to_session(r)
        if r["task_status"] == "done" and session.start >= now:
            continue
        sessions.append(session)
    return sessions


# ---------- Preference writes ----------

def set_preference(key: str, value, db_path: Optional[Path] = None) -> None:
    """Save one preference. value can be any JSON-friendly Python object."""
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO preferences (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )
