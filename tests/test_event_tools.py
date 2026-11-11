"""Editing and deleting events, tasks and sessions through the planner tools."""

from datetime import date, datetime, time, timedelta

import pytest

from autoplan import config, db, planner_tools, scheduler
from autoplan.models import Event, StudySession, Task

NOW = datetime(2026, 11, 11, 9, 0)  # Wednesday


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    db.init_db()


def weekly_gym():
    return db.add_event(Event(name="Gym", start=datetime(2026, 11, 2, 17), end=datetime(2026, 11, 2, 18, 30),
                              category="gym", recurrence_days=[0, 2, 4], recurrence_until=date(2026, 12, 18)))


def occurrences(day):
    return scheduler.expand_events(db.list_events(), day, day)


def test_find_events_by_name_and_day():
    gid = weekly_gym()
    found = planner_tools.find_events("gym", "today", now=NOW)["events"]
    assert found[0]["event_id"] == gid and found[0]["repeats_weekly"] and found[0]["date"] == "2026-11-11"
    assert planner_tools.find_events("dentist", now=NOW)["events"] == []


def test_cancel_one_day_of_weekly_event():
    gid = weekly_gym()
    out = planner_tools.delete_event(gid, date="2026-11-11")
    assert "cancelled_one_day" in out
    assert occurrences(date(2026, 11, 11)) == []
    assert len(occurrences(date(2026, 11, 13))) == 1  # Friday still on


def test_weekly_delete_needs_date_or_series():
    gid = weekly_gym()
    assert "error" in planner_tools.delete_event(gid)
    assert "deleted" in planner_tools.delete_event(gid, whole_series=True)
    assert db.list_events() == []
    assert [e.id for e in db.list_deleted_events()] == [gid]  # restorable


def test_move_one_day_of_weekly_event():
    gid = weekly_gym()
    out = planner_tools.edit_event(gid, start="19:00", date="2026-11-11")
    assert "moved_one_day" in out
    today = occurrences(date(2026, 11, 11))
    assert [(e.name, e.start.time(), e.end.time()) for e in today] == [("Gym", time(19), time(20, 30))]
    assert occurrences(date(2026, 11, 13))[0].start.time() == time(17)


def test_edit_whole_series_and_one_time_event():
    gid = weekly_gym()
    planner_tools.edit_event(gid, start="06:30", end="07:45", whole_series=True)
    assert occurrences(date(2026, 11, 13))[0].start.time() == time(6, 30)
    eid = db.add_event(Event(name="Dentist", start=datetime(2026, 11, 12, 14), end=datetime(2026, 11, 12, 15)))
    out = planner_tools.edit_event(eid, name="Dentist (moved)", start="2026-11-13 10:00")
    e = db.get_event(eid)
    assert e.name == "Dentist (moved)" and e.start == datetime(2026, 11, 13, 10) and e.end == datetime(2026, 11, 13, 11)


def test_blackboard_events_are_read_only():
    eid = db.add_event(Event(name="Review Session", start=datetime(2026, 11, 12, 14), end=datetime(2026, 11, 12, 15),
                             category="class", source="blackboard", external_id="bb-1"))
    assert "error" in planner_tools.delete_event(eid)
    assert "error" in planner_tools.edit_event(eid, start="15:00")


class FakeGoogle:
    def __init__(self):
        self.patched, self.deleted = [], []

    def events(self):
        outer = self

        class E:
            def patch(self, calendarId, eventId, body):
                outer.patched.append((calendarId, eventId, body))
                return type("C", (), {"execute": lambda self: {}})()

            def delete(self, calendarId, eventId):
                outer.deleted.append((calendarId, eventId))
                return type("C", (), {"execute": lambda self: {}})()

        return E()


def test_google_events_change_in_google(monkeypatch):
    fake = FakeGoogle()
    monkeypatch.setattr(planner_tools, "_google_service", lambda: fake)
    eid = db.upsert_external_event(Event(name="Advising", start=datetime(2026, 11, 12, 13), end=datetime(2026, 11, 12, 13, 30),
                                         category="other", source="google", external_id="w.simms@gwmail.gwu.edu:abc123"))
    out = planner_tools.edit_event(eid, start="14:00")
    assert "updated_in_google" in out
    cal, ev, body = fake.patched[0]
    assert (cal, ev) == ("w.simms@gwmail.gwu.edu", "abc123") and body["start"]["dateTime"].startswith("2026-11-12T14:00")
    assert db.get_event(eid).start == datetime(2026, 11, 12, 14)

    assert "deleted_in_google" in planner_tools.delete_event(eid)
    assert fake.deleted == [("w.simms@gwmail.gwu.edu", "abc123")]
    assert db.list_events() == []


def test_update_and_delete_task():
    db.add_task(Task(name="Quiz Nine", deadline=datetime(2026, 11, 12, 23, 59), estimated_minutes=45))
    out = planner_tools.update_task("quiz nine", due="2026-11-14", minutes=60, priority="High")
    t = db.list_tasks()[0]
    assert (t.deadline, t.estimated_minutes, t.priority) == (datetime(2026, 11, 14, 23, 59), 60, "High")
    assert "deleted" in planner_tools.delete_task("quiz nine")
    assert db.list_tasks() == []


def test_cancel_study_session():
    tid = db.add_task(Task(name="Essay", deadline=datetime(2026, 12, 14, 23, 59), estimated_minutes=180))
    future = datetime.now() + timedelta(days=1)
    sid = db.add_study_session(StudySession(task_id=tid, start=future, end=future + timedelta(hours=1), status="accepted"))
    assert "cancelled" in planner_tools.cancel_study_session("essay")
    assert db.get_study_session(sid).status == "rejected"
