"""Every-2-hours sync: text only when something important changed."""

from datetime import date, datetime, timedelta

import pytest

from autoplan import config, db
from autoplan.integrations import bb_scraper, changes, sync
from autoplan.models import Event, InboxItem, Task

NOW = datetime(2026, 11, 11, 14, 0)


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    db.save_bb_courses([bb_scraper.Course(id="_1", code="ISTM 4205", name="Web", term="202603", url="")], NOW)


def test_first_check_is_silent():
    db.upsert_external_task(Task(name="HW 9", deadline=NOW + timedelta(days=2), estimated_minutes=60, source="blackboard", external_id="a"))
    assert changes.check(NOW) == []


def test_new_assignment_and_moved_deadline():
    changes.check(NOW)
    db.upsert_external_task(Task(name="HW 9", deadline=NOW + timedelta(days=2), estimated_minutes=60, source="blackboard", external_id="a"))
    db.add_task(Task(name="Call mom", deadline=NOW + timedelta(days=1), estimated_minutes=15))  # typed in by you: no alert
    db.upsert_external_task(Task(name="Final Project", deadline=NOW + timedelta(days=40), estimated_minutes=180, source="blackboard", external_id="b"))  # far off
    lines = changes.check(NOW)
    assert lines == ["📌 New: HW 9, due Fri Nov 13, 2:00 PM"]
    db.upsert_external_task(Task(name="HW 9", deadline=NOW + timedelta(days=3), estimated_minutes=60, source="blackboard", external_id="a"))
    assert changes.check(NOW) == ["📅 HW 9 moved to Sat Nov 14, 2:00 PM (was Fri Nov 13, 2:00 PM)"]
    assert changes.check(NOW) == []  # nothing new since


def test_grade_posted_and_exam_found_and_changed():
    db.replace_bb_grades(["_1"], [{"id": "c1", "course_id": "_1", "name": "Quiz 7", "possible": 10, "score": None, "display": None, "due": None, "is_total": False}])
    changes.check(NOW)
    db.replace_bb_grades(["_1"], [{"id": "c1", "course_id": "_1", "name": "Quiz 7", "possible": 10, "score": 9, "display": None, "due": None, "is_total": False}])
    db.upsert_exam_candidate({"course_id": "_1", "name": "ISTM 4205 Midterm", "date": "2026-11-18", "source": "syllabus", "quote": "q", "confidence": "medium"})
    lines = changes.check(NOW)
    assert "🎓 Grade posted: ISTM 4205 Quiz 7: 9/10" in lines
    assert any(l.startswith("📝 Exam date found: ISTM 4205 Midterm, 2026-11-18") for l in lines)
    db.upsert_exam_candidate({"course_id": "_1", "name": "ISTM 4205 Midterm", "date": "2026-11-20", "start_time": "15:45", "source": "announcement", "quote": "q", "confidence": "high"})
    assert changes.check(NOW) == ["⚠️ ISTM 4205 Midterm changed to 2026-11-20 15:45 (was 2026-11-18)"]


def test_only_important_email_is_named():
    changes.check(NOW)
    for i, (subject, kind) in enumerate([("ISTM 4205: Class cancelled Friday", "blackboard"), ("Registration opens", "time-sensitive")]):
        db.add_inbox_item(InboxItem(external_id=f"m{i}", subject=subject, sender="x", received_at=NOW, snippet="", kind=kind))
    assert changes.check(NOW) == ["📬 ISTM 4205: Class cancelled Friday", "📬 1 more email to review"]


def test_cancelled_calendar_event_in_next_48h():
    eid = db.upsert_external_event(Event(name="ISTM 4205 lecture", start=NOW + timedelta(hours=20), end=NOW + timedelta(hours=21),
                                         category="class", source="google", external_id="cal:1"))
    changes.check(NOW)
    db.delete_event(eid)  # what the Google import does when the event is cancelled
    assert changes.check(NOW) == ["❌ Removed from your calendar: ISTM 4205 lecture, Thu Nov 12, 10:00 AM"]


def test_events_coming_into_range_are_not_news():
    db.upsert_external_event(Event(name="Advising", start=NOW + timedelta(hours=49), end=NOW + timedelta(hours=50),
                                   category="other", source="google", external_id="cal:2"))
    changes.check(NOW)
    assert changes.check(NOW + timedelta(hours=2)) == []  # same event, just now inside 48 h


def test_schedule_and_quiet_hours(monkeypatch):
    monkeypatch.setattr(sync, "sync_all", lambda now=None: [("Gmail", True, "ok")])
    assert sync.scheduled_sync_due(NOW)
    results, alerts = sync.run_scheduled(NOW)
    assert results == [("Gmail", True, "ok")] and not sync.scheduled_sync_due(NOW + timedelta(minutes=30))
    assert sync.scheduled_sync_due(NOW + timedelta(hours=2))
    late = datetime(2026, 11, 11, 23, 30)
    db.upsert_external_task(Task(name="HW 10", deadline=late + timedelta(days=1), estimated_minutes=60, source="blackboard", external_id="z"))
    assert sync.run_scheduled(late)[1] == []  # held overnight
    morning = datetime(2026, 11, 12, 7, 10)
    assert sync.run_scheduled(morning)[1] == ["📌 New: HW 10, due Thu Nov 12, 11:30 PM"]
