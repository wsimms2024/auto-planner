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
