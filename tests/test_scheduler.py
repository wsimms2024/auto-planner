"""
Phase 1 smoke tests: confirm the database sets itself up and the
scheduler module imports. Real scheduling tests come in Phase 3.
"""

from datetime import time

from autoplan import db, scheduler


def test_init_db_creates_tables(tmp_path):
    path = tmp_path / "test.db"
    db.init_db(path)
    with db.get_connection(path) as conn:
        tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"events", "tasks", "study_sessions", "preferences"} <= tables


def test_default_preferences(tmp_path):
    path = tmp_path / "test.db"
    db.init_db(path)
    prefs = db.get_preferences(path)
    assert prefs.work_hours[0] == (time(9, 0), time(20, 0))  # Monday
    assert len(prefs.work_hours) == 7
    assert prefs.min_session_minutes == 30
    assert prefs.max_session_minutes == 90
    assert prefs.buffer_minutes == 15


def test_init_db_keeps_existing_preferences(tmp_path):
    path = tmp_path / "test.db"
    db.init_db(path)
    db.set_preference("buffer_minutes", 10, path)
    db.init_db(path)  # running setup again must not reset your settings
    assert db.get_preferences(path).buffer_minutes == 10


def test_scheduler_has_expected_functions():
    for name in ["expand_events", "find_free_slots", "prioritize_tasks", "suggest_sessions"]:
        assert callable(getattr(scheduler, name))
