"""Guessed priority/estimate for imported tasks."""

from datetime import datetime

import pytest

from autoplan import config, db, task_rules
from autoplan.models import Task


@pytest.mark.parametrize("name, expected", [
    ("Safety Awareness Training", ("Low", 30, False)),
    ("Equal Opportunity and Title IX Refresher for Returning Students", ("Low", 30, False)),
    ("Art History Event Participation", ("Low", 30, False)),
    ("Submit the Project Outline (by group)", ("Medium", 90, True)),
    ("WebAppDev-Final Project", ("High", 180, True)),
    ("Quiz Eight", ("Medium", 45, True)),
    ("ICA-8", ("Medium", 60, True)),
    ("Assignment 5 - JavaScript", ("Medium", 60, True)),
    ("Something else", ("Medium", 60, True)),
])
def test_guess(name, expected):
    assert task_rules.guess(name) == expected


def test_old_imported_tasks_get_guesses_once(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    # Simulate tasks imported before the rules existed
    with db.get_connection() as conn:
        conn.execute("DELETE FROM preferences WHERE key = 'task_rules_v1'")
    untouched = db.add_task(Task(name="Safety Awareness Training", deadline=datetime(2026, 11, 9), estimated_minutes=60, source="blackboard", external_id="a"))
    edited = db.add_task(Task(name="Final Project", deadline=datetime(2026, 11, 9), estimated_minutes=240, source="blackboard", external_id="b"))
    manual = db.add_task(Task(name="Safety training", deadline=datetime(2026, 11, 9), estimated_minutes=60))
    db.init_db()
    tasks = {t.id: t for t in db.list_tasks()}
    assert (tasks[untouched].priority, tasks[untouched].estimated_minutes, tasks[untouched].plan_sessions) == ("Low", 30, False)
    assert (tasks[edited].priority, tasks[edited].estimated_minutes) == ("Medium", 240)  # you changed it: left alone
    assert tasks[manual].plan_sessions  # typed in by hand: left alone

    # Runs only once: later edits back to Medium/60 aren't overwritten
    db.set_task_planning(untouched, True)
    db.init_db()
    assert {t.id: t for t in db.list_tasks()}[untouched].plan_sessions


@pytest.mark.parametrize("name, expected", [
    ("ACCY 2001 Midterm", True),
    ("Exam 1", True),
    ("Final Exam", True),
    ("WebAppDev-Final Project", False),
    ("Quiz Eight", False),
])
def test_is_exam(name, expected):
    assert task_rules.is_exam(name) == expected


def test_exam_events_become_study_tasks(tmp_path, monkeypatch):
    from datetime import timedelta
    from autoplan import exam_mode
    from autoplan.models import Event

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    now = datetime(2026, 11, 11, 9)
    db.add_event(Event(name="ACCY 2001 Midterm", start=datetime(2026, 11, 18, 10), end=datetime(2026, 11, 18, 11, 15), category="class"))
    db.add_event(Event(name="BADM Exam 2", start=datetime(2026, 11, 20, 12), end=datetime(2026, 11, 20, 13), category="class"))
    db.add_task(Task(name="BADM Exam 2", deadline=datetime(2026, 11, 20, 12), estimated_minutes=300))  # already tracked
    assert exam_mode.sync_exam_tasks(now) == 1
    assert exam_mode.sync_exam_tasks(now) == 0  # no duplicates on re-run
    study = next(t for t in db.list_tasks() if t.source == "exam")
    assert study.name == "Study for ACCY 2001 Midterm" and study.priority == "High"
    assert study.deadline == datetime(2026, 11, 18, 10) and study.estimated_minutes == config.EXAM_STUDY_MINUTES
