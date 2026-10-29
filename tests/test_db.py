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
    assert prefs.work_hours[0] == (time(9, 0), time(20, 0))
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
