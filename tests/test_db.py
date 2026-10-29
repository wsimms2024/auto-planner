"""Database tests: setup, saving, editing, and soft delete."""

from datetime import date, datetime, time

import pytest

from autoplan import db
from autoplan.models import Event, Task


@pytest.fixture
def path(tmp_path):
    p = tmp_path / "test.db"
    db.init_db(p)
    return p


def test_init_db_creates_tables(path):
    with db.get_connection(path) as conn:
        tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"events", "tasks", "study_sessions", "preferences"} <= tables


def test_default_preferences(path):
    prefs = db.get_preferences(path)
    assert prefs.work_hours[0] == (time(0, 0), time(23, 59))
    assert len(prefs.work_hours) == 7
    assert (prefs.min_session_minutes, prefs.max_session_minutes, prefs.buffer_minutes) == (30, 90, 15)


def test_init_db_keeps_existing_preferences(path):
    db.set_preference("buffer_minutes", 10, path)
    db.init_db(path)
    assert db.get_preferences(path).buffer_minutes == 10


def test_weekly_event_round_trip(path):
    e = Event(
        name="ISTM",
        start=datetime(2026, 11, 2, 10),
        end=datetime(2026, 11, 2, 11, 15),
        category="class",
        recurrence_days=[2, 0],
        recurrence_until=date(2026, 12, 12),
    )
    new_id = db.add_event(e, path)
    saved = db.list_events(db_path=path)[0]
    assert saved.id == new_id
    assert saved.recurrence_days == [0, 2]
    assert saved.recurrence_until == date(2026, 12, 12)


def test_invalid_event_is_rejected(path):
    bad = Event(name="Backwards", start=datetime(2026, 11, 2, 11), end=datetime(2026, 11, 2, 10))
    with pytest.raises(db.ValidationError):
        db.add_event(bad, path)
    assert db.list_events(db_path=path) == []


def test_weekly_event_needs_days_and_until():
    e = Event(name="X", start=datetime(2026, 11, 2, 9), end=datetime(2026, 11, 2, 10), recurrence_days=[])
    problems = " ".join(e.validate())
    assert "at least one day" in problems and "end date" in problems


def test_update_event(path):
    e = Event(name="Gym", start=datetime(2026, 11, 2, 7), end=datetime(2026, 11, 2, 8), category="gym")
    e.id = db.add_event(e, path)
    e.name = "Leg day"
    db.update_event(e, path)
    assert db.list_events(db_path=path)[0].name == "Leg day"


def test_soft_delete_and_restore_event(path):
    e = Event(name="Gym", start=datetime(2026, 11, 2, 7), end=datetime(2026, 11, 2, 8), category="gym")
    event_id = db.add_event(e, path)
    db.delete_event(event_id, path)
    assert db.list_events(db_path=path) == []
    assert [d.id for d in db.list_deleted_events(path)] == [event_id]
    db.restore_event(event_id, path)
    assert [x.id for x in db.list_events(db_path=path)] == [event_id]


def test_task_status_and_soft_delete(path):
    t = Task(name="Essay", deadline=datetime(2026, 11, 6, 23, 59), estimated_minutes=180, priority="High")
    task_id = db.add_task(t, path)
    db.set_task_status(task_id, "done", path)
    assert db.list_tasks(db_path=path)[0].status == "done"
    db.delete_task(task_id, path)
    assert db.list_tasks(db_path=path) == []
    db.restore_task(task_id, path)
    assert db.list_tasks(db_path=path)[0].name == "Essay"


def test_task_validation():
    t = Task(name=" ", deadline=datetime(2026, 11, 6), estimated_minutes=0, priority="Urgent")
    assert len(t.validate()) == 3


def test_overdue():
    t = Task(name="Quiz", deadline=datetime(2026, 11, 1, 12), estimated_minutes=30)
    assert t.is_overdue(datetime(2026, 11, 2))
    t.status = "done"
    assert not t.is_overdue(datetime(2026, 11, 2))


# ---------- Study sessions and settings ----------

from autoplan.models import StudySession


def test_active_sessions_skip_deleted_and_done_tasks(path):
    now = datetime(2026, 11, 2, 12)
    keep = db.add_task(Task(name="Keep", deadline=datetime(2026, 11, 6), estimated_minutes=60), path)
    gone = db.add_task(Task(name="Gone", deadline=datetime(2026, 11, 6), estimated_minutes=60), path)
    done = db.add_task(Task(name="Done", deadline=datetime(2026, 11, 6), estimated_minutes=60), path)
    for task_id in (keep, gone, done):
        db.add_study_session(StudySession(task_id=task_id, start=datetime(2026, 11, 3, 9), end=datetime(2026, 11, 3, 10), status="accepted"), path)
    db.add_study_session(StudySession(task_id=done, start=datetime(2026, 11, 1, 9), end=datetime(2026, 11, 1, 10), status="accepted"), path)
    db.delete_task(gone, path)
    db.set_task_status(done, "done", path)
    active = db.list_active_sessions(now, path)
    # Keep's future session, plus Done's past session (it happened); Done's future one is dropped
    assert sorted((s.task_id, s.start.day) for s in active) == [(keep, 3), (done, 1)]


def test_session_status_change(path):
    task_id = db.add_task(Task(name="T", deadline=datetime(2026, 11, 6), estimated_minutes=60), path)
    sid = db.add_study_session(StudySession(task_id=task_id, start=datetime(2026, 11, 3, 9), end=datetime(2026, 11, 3, 10), status="accepted"), path)
    db.set_session_status(sid, "rejected", path)
    assert db.list_study_sessions("rejected", path)[0].id == sid


def test_save_preferences_round_trip_with_day_off(path):
    prefs = db.get_preferences(path)
    prefs.work_hours = {d: (time(10), time(22)) for d in range(6)}  # Sunday off
    prefs.max_session_minutes = 60
    prefs.planning_days = 10
    db.save_preferences(prefs, path)
    again = db.get_preferences(path)
    assert 6 not in again.work_hours
    assert again.work_hours[0] == (time(10), time(22))
    assert (again.max_session_minutes, again.planning_days) == (60, 10)


def test_invalid_preferences_rejected(path):
    prefs = db.get_preferences(path)
    prefs.min_session_minutes, prefs.max_session_minutes = 60, 30
    with pytest.raises(db.ValidationError):
        db.save_preferences(prefs, path)
