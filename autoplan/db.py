"""
Everything that touches SQLite lives here.

Pages and integrations (Google Calendar, Gmail, Blackboard) read and write
through these functions. Nothing else should run SQL directly.

Deletes are soft: rows get a deleted_at timestamp and are hidden from
normal reads, so nothing is lost by accident.
"""

import json
import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional

from autoplan import config
from autoplan.models import Event, InboxItem, Preferences, StudySession, Task

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

CREATE TABLE IF NOT EXISTS inbox_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id  TEXT    NOT NULL UNIQUE, -- Gmail message id
    subject      TEXT    NOT NULL,
    sender       TEXT    NOT NULL,
    received_at  TEXT    NOT NULL,
    snippet      TEXT    NOT NULL,
    kind         TEXT    NOT NULL,
    detected_due TEXT,
    link         TEXT    NOT NULL DEFAULT '',
    status       TEXT    NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS gmail_seen (
    message_id TEXT PRIMARY KEY          -- emails already checked, so scans never redo them
);

CREATE TABLE IF NOT EXISTS bb_courses (
    id            TEXT PRIMARY KEY,      -- Blackboard's internal course id
    code          TEXT NOT NULL,         -- e.g. ISTM 4205
    name          TEXT NOT NULL,
    term          TEXT NOT NULL DEFAULT '',
    url           TEXT NOT NULL DEFAULT '',
    grade_display TEXT,                  -- overall grade as Blackboard shows it, if any
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bb_assignments (
    id           TEXT PRIMARY KEY,       -- Blackboard content id
    course_id    TEXT NOT NULL,
    title        TEXT NOT NULL,
    due          TEXT,
    instructions TEXT NOT NULL DEFAULT '',
    attachments  TEXT NOT NULL DEFAULT '[]',  -- JSON list of {name, path}
    points       REAL,
    url          TEXT NOT NULL DEFAULT '',
    task_id      INTEGER,                -- matching AutoPlan task, if found
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bb_grade_items (
    id        TEXT PRIMARY KEY,          -- gradebook column id
    course_id TEXT NOT NULL,
    name      TEXT NOT NULL,
    possible  REAL,
    score     REAL,                      -- your score, NULL if not graded yet
    display   TEXT,                      -- e.g. "A-" or "18/20"
    due       TEXT,
    is_total  INTEGER NOT NULL DEFAULT 0 -- Blackboard's overall/total column
);

CREATE TABLE IF NOT EXISTS prep_notes (
    task_id    INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    content    TEXT NOT NULL             -- JSON from the Prep button
);

CREATE TABLE IF NOT EXISTS sync_state (
    source   TEXT PRIMARY KEY,           -- google, gmail, blackboard
    last_run TEXT NOT NULL,
    message  TEXT NOT NULL DEFAULT ''
);
"""

# Columns added after Phase 3. init_db adds any that are missing, so older
# databases upgrade themselves without losing data.
MIGRATIONS = [
    ("events", "source", "TEXT NOT NULL DEFAULT 'manual'"),
    ("events", "external_id", "TEXT"),
    ("tasks", "source", "TEXT NOT NULL DEFAULT 'manual'"),
    ("tasks", "external_id", "TEXT"),
    ("study_sessions", "google_event_id", "TEXT"),
    ("tasks", "plan_sessions", "INTEGER NOT NULL DEFAULT 1"),
    ("study_sessions", "followup_sent", "INTEGER NOT NULL DEFAULT 0"),
    ("events", "skip_dates", "TEXT"),
]

INDEXES = """
CREATE UNIQUE INDEX IF NOT EXISTS events_external ON events(source, external_id) WHERE external_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS tasks_external ON tasks(source, external_id) WHERE external_id IS NOT NULL;
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
        for table, column, ddl in MIGRATIONS:
            existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        conn.executescript(INDEXES)
        _apply_task_rules_once(conn)
        for key, value in config.DEFAULT_PREFERENCES.items():
            conn.execute(
                "INSERT OR IGNORE INTO preferences (key, value) VALUES (?, ?)",
                (key, json.dumps(value)),
            )


def _apply_task_rules_once(conn) -> None:
    """
    One-time cleanup for imported tasks created before task_rules existed:
    any that still have the old defaults (Medium, 60 min) get the guessed
    priority, estimate and plan setting. Tasks you already changed are left alone.
    """
    if conn.execute("SELECT 1 FROM preferences WHERE key = 'task_rules_v1'").fetchone():
        return
    from autoplan import task_rules

    rows = conn.execute(
        "SELECT id, name FROM tasks WHERE source IN ('blackboard', 'gmail') AND priority = 'Medium' AND estimated_minutes = 60"
    ).fetchall()
    for r in rows:
        priority, minutes, plan = task_rules.guess(r["name"])
        conn.execute(
            "UPDATE tasks SET priority = ?, estimated_minutes = ?, plan_sessions = ? WHERE id = ?",
            (priority, minutes, int(plan), r["id"]),
        )
    conn.execute("INSERT INTO preferences (key, value) VALUES ('task_rules_v1', 'true')")


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
        source=row["source"],
        external_id=row["external_id"],
        skip_dates=[date.fromisoformat(d) for d in json.loads(row["skip_dates"] or "[]")],
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
        source=row["source"],
        external_id=row["external_id"],
        plan_sessions=bool(row["plan_sessions"]),
    )


def _row_to_session(row: sqlite3.Row) -> StudySession:
    return StudySession(
        id=row["id"],
        task_id=row["task_id"],
        start=_dt(row["start"]),
        end=_dt(row["end"]),
        status=row["status"],
        google_event_id=row["google_event_id"],
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
        suggest_low_priority=bool(merged["suggest_low_priority"]),
        gym_days=[int(d) for d in merged["gym_days"]],
        gym_minutes=int(merged["gym_minutes"]),
        gym_earliest=time.fromisoformat(merged["gym_earliest"]),
        gym_latest=time.fromisoformat(merged["gym_latest"]),
        briefing_on=bool(merged["briefing_on"]),
        briefing_time=time.fromisoformat(merged["briefing_time"]),
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
    set_preference("suggest_low_priority", prefs.suggest_low_priority, db_path)
    set_preference("gym_days", sorted(prefs.gym_days), db_path)
    set_preference("gym_minutes", prefs.gym_minutes, db_path)
    set_preference("gym_earliest", prefs.gym_earliest.strftime("%H:%M"), db_path)
    set_preference("gym_latest", prefs.gym_latest.strftime("%H:%M"), db_path)
    set_preference("briefing_on", prefs.briefing_on, db_path)
    set_preference("briefing_time", prefs.briefing_time.strftime("%H:%M"), db_path)


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
            'INSERT INTO events (name, start, "end", category, recurrence_days, recurrence_until, source, external_id) '
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (*_event_values(event), event.source, event.external_id),
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


def get_event(event_id: int, db_path: Optional[Path] = None) -> Optional[Event]:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return _row_to_event(row) if row else None


def skip_event_day(event_id: int, day: date, db_path: Optional[Path] = None) -> None:
    """Cancel one day of a weekly event; the rest of the series stays."""
    event = get_event(event_id, db_path)
    days = sorted({*event.skip_dates, day})
    with get_connection(db_path) as conn:
        conn.execute("UPDATE events SET skip_dates = ? WHERE id = ?", (json.dumps([d.isoformat() for d in days]), event_id))


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
    return (task.name.strip(), task.deadline.isoformat(), task.estimated_minutes, task.priority, task.status, int(task.plan_sessions))


def add_task(task: Task, db_path: Optional[Path] = None) -> int:
    _check(task)
    with get_connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO tasks (name, deadline, estimated_minutes, priority, status, plan_sessions, source, external_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (*_task_values(task), task.source, task.external_id),
        )
        return cur.lastrowid


def update_task(task: Task, db_path: Optional[Path] = None) -> None:
    if task.id is None:
        raise ValueError("update_task needs a task with an id.")
    _check(task)
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE tasks SET name = ?, deadline = ?, estimated_minutes = ?, priority = ?, status = ?, plan_sessions = ? WHERE id = ?",
            (*_task_values(task), task.id),
        )


def set_task_status(task_id: int, status: str, db_path: Optional[Path] = None) -> None:
    if status not in config.TASK_STATUSES:
        raise ValidationError(f"Unknown status '{status}'.")
    with get_connection(db_path) as conn:
        conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))


def set_task_planning(task_id: int, plan: bool, db_path: Optional[Path] = None) -> None:
    """Turn study-session suggestions on or off for one task."""
    with get_connection(db_path) as conn:
        conn.execute("UPDATE tasks SET plan_sessions = ? WHERE id = ?", (int(plan), task_id))


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


def sessions_needing_followup(now: datetime, db_path: Optional[Path] = None) -> list[StudySession]:
    """Accepted sessions that ended 10+ minutes ago (within the last day) and haven't been asked about."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT s.* FROM study_sessions s JOIN tasks t ON t.id = s.task_id "
            "WHERE s.status = 'accepted' AND s.followup_sent = 0 AND t.deleted_at IS NULL AND t.status = 'open' "
            'AND s."end" <= ? AND s."end" >= ? ORDER BY s.start',
            ((now - timedelta(minutes=10)).isoformat(), (now - timedelta(days=1)).isoformat()),
        ).fetchall()
        return [_row_to_session(r) for r in rows]


def mark_followup_sent(session_id: int, db_path: Optional[Path] = None) -> None:
    with get_connection(db_path) as conn:
        conn.execute("UPDATE study_sessions SET followup_sent = 1 WHERE id = ?", (session_id,))


def get_study_session(session_id: int, db_path: Optional[Path] = None) -> Optional[StudySession]:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM study_sessions WHERE id = ?", (session_id,)).fetchone()
        return _row_to_session(row) if row else None


def list_active_sessions(now: Optional[datetime] = None, db_path: Optional[Path] = None) -> list[StudySession]:
    """
    Sessions the scheduler should know about (accepted, completed, rejected, missed).
    Sessions for deleted tasks are left out, and so are future sessions
    for tasks you've already marked done (no point studying for those).
    """
    now = now or datetime.now()
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT s.*, t.status AS task_status FROM study_sessions s "
            "JOIN tasks t ON t.id = s.task_id "
            "WHERE t.deleted_at IS NULL AND s.status IN ('accepted', 'rejected', 'completed', 'missed') ORDER BY s.start"
        ).fetchall()
    sessions = []
    for r in rows:
        session = _row_to_session(r)
        if r["task_status"] == "done" and session.start >= now:
            continue
        sessions.append(session)
    return sessions


# ---------- Imported items (Google Calendar, Blackboard) ----------

def _find_external(conn, table: str, source: str, external_id: str):
    return conn.execute(
        f"SELECT * FROM {table} WHERE source = ? AND external_id = ?", (source, external_id)
    ).fetchone()


def upsert_external_event(event: Event, db_path: Optional[Path] = None) -> int:
    """
    Insert or update an event from an outside calendar, matched on
    (source, external_id). Name and times follow the source. Your category
    choice and any delete you did in AutoPlan are kept.
    """
    _check(event)
    with get_connection(db_path) as conn:
        row = _find_external(conn, "events", event.source, event.external_id)
        if row is None:
            cur = conn.execute(
                'INSERT INTO events (name, start, "end", category, source, external_id) VALUES (?, ?, ?, ?, ?, ?)',
                (event.name, event.start.isoformat(), event.end.isoformat(), event.category, event.source, event.external_id),
            )
            return cur.lastrowid
        conn.execute(
            'UPDATE events SET name = ?, start = ?, "end" = ? WHERE id = ?',
            (event.name, event.start.isoformat(), event.end.isoformat(), row["id"]),
        )
        return row["id"]


def remove_missing_external_events(
    source: str, seen_ids: set[str], window_start: datetime, window_end: datetime, db_path: Optional[Path] = None
) -> int:
    """
    Soft-delete imported events in the synced window that the source no
    longer has (cancelled or deleted there). Returns how many were removed.
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT id, external_id FROM events WHERE source = ? AND deleted_at IS NULL AND start >= ? AND start <= ?",
            (source, window_start.isoformat(), window_end.isoformat()),
        ).fetchall()
        gone = [r["id"] for r in rows if r["external_id"] not in seen_ids]
        now = datetime.now().isoformat()
        for event_id in gone:
            conn.execute("UPDATE events SET deleted_at = ? WHERE id = ?", (now, event_id))
        return len(gone)


def upsert_external_task(task: Task, db_path: Optional[Path] = None) -> tuple[int, bool]:
    """
    Insert or update a task from Blackboard, matched on (source, external_id).
    Name and deadline follow the source. Your estimate, priority, status and
    deletes are kept. Returns (task id, True if it was new).
    """
    _check(task)
    with get_connection(db_path) as conn:
        row = _find_external(conn, "tasks", task.source, task.external_id)
        if row is None:
            cur = conn.execute(
                "INSERT INTO tasks (name, deadline, estimated_minutes, priority, status, plan_sessions, source, external_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (*_task_values(task), task.source, task.external_id),
            )
            return cur.lastrowid, True
        conn.execute(
            "UPDATE tasks SET name = ?, deadline = ? WHERE id = ?",
            (task.name.strip(), task.deadline.isoformat(), row["id"]),
        )
        return row["id"], False


def set_session_google_id(session_id: int, google_event_id: Optional[str], db_path: Optional[Path] = None) -> None:
    with get_connection(db_path) as conn:
        conn.execute("UPDATE study_sessions SET google_event_id = ? WHERE id = ?", (google_event_id, session_id))


def list_pushed_sessions(db_path: Optional[Path] = None) -> list[StudySession]:
    """Every session that has a copy on Google Calendar, whatever its status now."""
    with get_connection(db_path) as conn:
        rows = conn.execute("SELECT * FROM study_sessions WHERE google_event_id IS NOT NULL").fetchall()
        return [_row_to_session(r) for r in rows]


# ---------- Email review inbox ----------

def _row_to_inbox(row: sqlite3.Row) -> InboxItem:
    return InboxItem(
        id=row["id"],
        external_id=row["external_id"],
        subject=row["subject"],
        sender=row["sender"],
        received_at=_dt(row["received_at"]),
        snippet=row["snippet"],
        kind=row["kind"],
        detected_due=_dt(row["detected_due"]),
        link=row["link"],
        status=row["status"],
    )


def add_inbox_item(item: InboxItem, db_path: Optional[Path] = None) -> bool:
    """Add an email to the review list. Returns False if it was already there."""
    with get_connection(db_path) as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO inbox_items (external_id, subject, sender, received_at, snippet, kind, detected_due, link) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item.external_id, item.subject, item.sender, item.received_at.isoformat(), item.snippet,
                item.kind, item.detected_due.isoformat() if item.detected_due else None, item.link,
            ),
        )
        return cur.rowcount == 1


def list_inbox(status: str = "pending", db_path: Optional[Path] = None) -> list[InboxItem]:
    with get_connection(db_path) as conn:
        rows = conn.execute("SELECT * FROM inbox_items WHERE status = ? ORDER BY received_at DESC", (status,))
        return [_row_to_inbox(r) for r in rows]


def set_inbox_status(item_id: int, status: str, db_path: Optional[Path] = None) -> None:
    if status not in ("pending", "added", "dismissed"):
        raise ValidationError(f"Unknown inbox status '{status}'.")
    with get_connection(db_path) as conn:
        conn.execute("UPDATE inbox_items SET status = ? WHERE id = ?", (status, item_id))


def filter_unseen_emails(message_ids: list[str], db_path: Optional[Path] = None) -> list[str]:
    """Return only the Gmail message ids that haven't been checked yet, in the same order."""
    if not message_ids:
        return []
    with get_connection(db_path) as conn:
        marks = ",".join("?" * len(message_ids))
        seen = {r[0] for r in conn.execute(f"SELECT message_id FROM gmail_seen WHERE message_id IN ({marks})", message_ids)}
        # Emails already on the review list count as checked too
        seen |= {r[0] for r in conn.execute(f"SELECT external_id FROM inbox_items WHERE external_id IN ({marks})", message_ids)}
    return [m for m in message_ids if m not in seen]


def mark_email_seen(message_id: str, db_path: Optional[Path] = None) -> None:
    with get_connection(db_path) as conn:
        conn.execute("INSERT OR IGNORE INTO gmail_seen (message_id) VALUES (?)", (message_id,))


# ---------- Blackboard details and grades ----------

def save_bb_courses(courses, now: datetime, db_path: Optional[Path] = None) -> None:
    with get_connection(db_path) as conn:
        for c in courses:
            conn.execute(
                "INSERT INTO bb_courses (id, code, name, term, url, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET code = excluded.code, name = excluded.name, term = excluded.term, "
                "url = excluded.url, updated_at = excluded.updated_at",
                (c.id, c.code, c.name, c.term, c.url, now.isoformat()),
            )


def save_bb_assignment(a, task_id: Optional[int], now: datetime, db_path: Optional[Path] = None) -> None:
    with get_connection(db_path) as conn:
        old = conn.execute("SELECT attachments FROM bb_assignments WHERE id = ?", (a.id,)).fetchone()
        attachments = a.attachments or (json.loads(old["attachments"]) if old else [])
        conn.execute(
            "INSERT INTO bb_assignments (id, course_id, title, due, instructions, attachments, points, url, task_id, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET course_id = excluded.course_id, "
            "title = excluded.title, due = excluded.due, instructions = excluded.instructions, "
            "attachments = excluded.attachments, points = excluded.points, url = excluded.url, "
            "task_id = COALESCE(excluded.task_id, bb_assignments.task_id), updated_at = excluded.updated_at",
            (a.id, a.course_id, a.title, a.due.isoformat() if a.due else None, a.instructions,
             json.dumps(attachments), a.points, a.url, task_id, now.isoformat()),
        )


def replace_bb_grades(course_ids: list[str], rows: list[dict], db_path: Optional[Path] = None) -> None:
    with get_connection(db_path) as conn:
        for cid in course_ids:
            conn.execute("DELETE FROM bb_grade_items WHERE course_id = ?", (cid,))
        for r in rows:
            conn.execute(
                "INSERT OR REPLACE INTO bb_grade_items (id, course_id, name, possible, score, display, due, is_total) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (r["id"], r["course_id"], r["name"], r["possible"], r["score"], r["display"],
                 r["due"].isoformat() if r["due"] else None, int(r["is_total"])),
            )


def list_bb_courses(db_path: Optional[Path] = None) -> list[dict]:
    with get_connection(db_path) as conn:
        latest = conn.execute("SELECT MAX(term) FROM bb_courses").fetchone()[0] or ""
        rows = conn.execute("SELECT * FROM bb_courses WHERE term = ? ORDER BY code", (latest,)).fetchall()
        return [dict(r) for r in rows]


def list_bb_grades(course_id: str, db_path: Optional[Path] = None) -> list[dict]:
    with get_connection(db_path) as conn:
        rows = conn.execute("SELECT * FROM bb_grade_items WHERE course_id = ? ORDER BY due IS NULL, due, name", (course_id,)).fetchall()
    return [{**dict(r), "due": _dt(r["due"]), "is_total": bool(r["is_total"])} for r in rows]


def get_bb_assignment_for_task(task_id: int, db_path: Optional[Path] = None) -> Optional[dict]:
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT a.*, c.code AS course_code, c.name AS course_name FROM bb_assignments a "
            "LEFT JOIN bb_courses c ON c.id = a.course_id WHERE a.task_id = ? ORDER BY a.updated_at DESC",
            (task_id,),
        ).fetchone()
    if not row:
        return None
    return {**dict(row), "due": _dt(row["due"]), "attachments": json.loads(row["attachments"])}


def save_prep(task_id: int, content: dict, db_path: Optional[Path] = None) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO prep_notes (task_id, created_at, content) VALUES (?, ?, ?)",
            (task_id, datetime.now().isoformat(), json.dumps(content)),
        )


def get_prep(task_id: int, db_path: Optional[Path] = None) -> Optional[dict]:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT content, created_at FROM prep_notes WHERE task_id = ?", (task_id,)).fetchone()
    return {**json.loads(row["content"]), "_created_at": row["created_at"]} if row else None


# ---------- Sync bookkeeping ----------

def get_sync_state(source: str, db_path: Optional[Path] = None) -> tuple[Optional[datetime], str]:
    """(last time this source synced, status message)."""
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT last_run, message FROM sync_state WHERE source = ?", (source,)).fetchone()
    return (_dt(row["last_run"]), row["message"]) if row else (None, "")


def set_sync_state(source: str, message: str, db_path: Optional[Path] = None) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO sync_state (source, last_run, message) VALUES (?, ?, ?) "
            "ON CONFLICT(source) DO UPDATE SET last_run = excluded.last_run, message = excluded.message",
            (source, datetime.now().isoformat(), message),
        )


# ---------- Preference writes ----------

def get_preference_value(key: str, db_path: Optional[Path] = None):
    """Read one stored preference (any JSON value), or None if it isn't set."""
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT value FROM preferences WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else None


def set_preference(key: str, value, db_path: Optional[Path] = None) -> None:
    """Save one preference. value can be any JSON-friendly Python object."""
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO preferences (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )
