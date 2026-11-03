"""
Integration tests with fake Google services and a sample Blackboard feed.
Nothing here touches the internet or your real accounts.
"""

import base64
from datetime import datetime, timedelta

import pytest

from autoplan import config, db
from autoplan.integrations import blackboard, gmail, google_calendar, sync
from autoplan.models import StudySession, Task


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    db.init_db()


# ---------- A tiny fake of the Google API client ----------

class Call:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result() if callable(self.result) else self.result


class FakeEvents:
    def __init__(self, items_by_cal):
        self.items_by_cal = items_by_cal
        self.inserted, self.deleted = [], []

    def list(self, calendarId, pageToken=None, **kw):
        return Call({"items": self.items_by_cal.get(calendarId, [])})

    def insert(self, calendarId, body):
        self.inserted.append((calendarId, body))
        return Call({"id": f"g{len(self.inserted)}"})

    def delete(self, calendarId, eventId):
        self.deleted.append(eventId)
        return Call({})


class FakeCalendarService:
    def __init__(self, items_by_cal=None):
        self._events = FakeEvents(items_by_cal or {})
        self.created_calendars = []

    def events(self):
        return self._events

    def calendarList(self):
        outer = self

        class CL:
            def list(self, pageToken=None):
                cals = [{"id": "primary@gwu.edu", "summary": "Will", "primary": True}]
                cals += [{"id": c, "summary": "AutoPlan"} for c in outer.created_calendars]
                return Call({"items": cals})

        return CL()

    def calendars(self):
        outer = self

        class C:
            def insert(self, body):
                outer.created_calendars.append("autoplan-cal")
                return Call({"id": "autoplan-cal"})

            def get(self, calendarId):
                return Call({"id": calendarId})

        return C()


def gevent(id, summary, start, end, **extra):
    return {"id": id, "summary": summary, "status": "confirmed",
            "start": {"dateTime": start}, "end": {"dateTime": end}, **extra}


# ---------- Google Calendar import ----------

def test_to_event_converts_utc_to_eastern():
    e = google_calendar.to_event(gevent("a", "Class", "2026-11-04T15:00:00Z", "2026-11-04T16:15:00Z"), "cal", "class")
    assert e.start == datetime(2026, 11, 4, 10, 0) and e.end == datetime(2026, 11, 4, 11, 15)
    assert e.external_id == "cal:a" and e.source == "google" and e.category == "class"


@pytest.mark.parametrize("item", [
    {"id": "x", "status": "confirmed", "start": {"date": "2026-11-04"}, "end": {"date": "2026-11-05"}},
    gevent("x", "Free", "2026-11-04T10:00:00-05:00", "2026-11-04T11:00:00-05:00", transparency="transparent"),
    gevent("x", "Nope", "2026-11-04T10:00:00-05:00", "2026-11-04T11:00:00-05:00",
           attendees=[{"self": True, "responseStatus": "declined"}]),
    gevent("x", "Gone", "2026-11-04T10:00:00-05:00", "2026-11-04T11:00:00-05:00", status="cancelled"),
])
def test_to_event_skips_all_day_free_declined_cancelled(item):
    assert google_calendar.to_event(item, "cal", "other") is None


def test_import_updates_and_removes():
    now = datetime(2026, 11, 3, 12)
    items = {"primary@gwu.edu": [
        gevent("a", "ISTM 4121", "2026-11-04T10:00:00-05:00", "2026-11-04T11:15:00-05:00"),
        gevent("b", "Advising", "2026-11-05T13:00:00-05:00", "2026-11-05T13:30:00-05:00"),
    ]}
    service = FakeCalendarService(items)
    google_calendar.import_events(service, {"primary@gwu.edu": "class"}, now)
    assert sorted(e.name for e in db.list_events()) == ["Advising", "ISTM 4121"]

    # Renamed in Google, and Advising cancelled
    items["primary@gwu.edu"] = [gevent("a", "ISTM 4121 (Room 202)", "2026-11-04T10:00:00-05:00", "2026-11-04T11:15:00-05:00")]
    msg = google_calendar.import_events(service, {"primary@gwu.edu": "class"}, now)
    assert [e.name for e in db.list_events()] == ["ISTM 4121 (Room 202)"]
    assert "1 removed" in msg


# ---------- Pushing study sessions ----------

def make_session(task_name="Essay", start=datetime(2026, 11, 4, 14), minutes=90):
    task_id = db.add_task(Task(name=task_name, deadline=datetime(2026, 11, 8, 23, 59), estimated_minutes=180))
    sid = db.add_study_session(StudySession(task_id=task_id, start=start, end=start + timedelta(minutes=minutes), status="accepted"))
    return task_id, sid


def test_push_creates_calendar_and_event_with_reminder():
    now = datetime(2026, 11, 3, 12)
    make_session()
    service = FakeCalendarService()
    google_calendar.push_sessions(service, now)
    cal_id, body = service.events().inserted[0]
    assert cal_id == "autoplan-cal" and body["summary"] == "Study: Essay"
    assert body["reminders"]["overrides"] == [{"method": "popup", "minutes": config.SESSION_REMINDER_MINUTES}]
    assert db.list_pushed_sessions()[0].google_event_id == "g1"

    # Second run doesn't duplicate
    google_calendar.push_sessions(service, now)
    assert len(service.events().inserted) == 1


def test_removed_session_and_done_task_are_deleted_from_google():
    now = datetime(2026, 11, 3, 12)
    _, sid = make_session("Essay")
    done_task, _ = make_session("Reading", start=datetime(2026, 11, 5, 10))
    service = FakeCalendarService()
    google_calendar.push_sessions(service, now)
    assert len(service.events().inserted) == 2

    db.set_session_status(sid, "rejected")
    db.set_task_status(done_task, "done")
    google_calendar.push_sessions(service, now)
    assert sorted(service.events().deleted) == ["g1", "g2"]
    assert db.list_pushed_sessions() == []


def test_past_sessions_are_not_pushed():
    make_session(start=datetime(2026, 11, 1, 9))
    service = FakeCalendarService()
    google_calendar.push_sessions(service, datetime(2026, 11, 3, 12))
    assert service.events().inserted == []


# ---------- Gmail: dates ----------

RECEIVED = datetime(2026, 11, 3, 9, 30)  # a Tuesday


@pytest.mark.parametrize("text, expected", [
    ("Homework 5 is due Nov 5 at 11:59pm", datetime(2026, 11, 5, 23, 59)),
    ("Reminder: the paper is due Friday", datetime(2026, 11, 6, 23, 59)),
    ("Quiz moved to 11/10 at 2:30 PM", datetime(2026, 11, 10, 14, 30)),
    ("Submit the form by tomorrow at noon", datetime(2026, 11, 4, 12, 0)),
    ("Final exam on December 14th, 2026 at 9am", datetime(2026, 12, 14, 9, 0)),
    ("Midterm grades are posted", None),
])
def test_extract_due(text, expected):
    assert gmail.extract_due(text, RECEIVED) == expected


def test_extract_due_prefers_date_after_deadline_word_and_skips_past():
    text = "We met on Oct 20. Office hours Nov 4. The project is due Nov 12."
    assert gmail.extract_due(text, RECEIVED) == datetime(2026, 11, 12, 23, 59)


# ---------- Gmail: what counts ----------

@pytest.mark.parametrize("sender, subject, body, expected", [
    ("Blackboard <do-not-reply@blackboard.com>", "New announcement in ISTM 4121", "", "blackboard"),
    ("Prof Smith <psmith@gwu.edu>", "Exam room change", "The exam is now in Room 101.", "professor"),
    ("Prof Smith <psmith@gwu.edu>", "Great discussion today", "Thanks all.", None),
    ("GW Today <gwtoday@gwu.edu>", "This week at GW", "Events this week", None),
    ("Nike <news@nike.com>", "Sale ends Friday", "Shop now, 30% off. Unsubscribe", None),
    ("Deloitte Recruiting <recruiting@deloitte.com>", "Interview confirmation", "Your interview is Nov 10 at 2pm.", "time-sensitive"),
    ("Random <a@b.com>", "Hello", "Just checking in, no rush.", None),
])
def test_classify(sender, subject, body, expected):
    due = gmail.extract_due(f"{subject}\n{body}", RECEIVED)
    assert gmail.classify(sender, subject, body, due) == expected


# ---------- Gmail: scan with a fake service ----------

def b64(text):
    return base64.urlsafe_b64encode(text.encode()).decode()


class FakeGmail:
    def __init__(self, messages):
        self.messages_by_id = {m["id"]: m for m in messages}

    def users(self):
        outer = self

        class Messages:
            def list(self, userId, q, maxResults, pageToken=None):
                outer.last_query = q
                return Call({"messages": [{"id": i} for i in outer.messages_by_id]})

            def get(self, userId, id, format):
                return Call(outer.messages_by_id[id])

        class Users:
            def getProfile(self, userId):
                return Call({"emailAddress": "will@gwu.edu"})

            def messages(self):
                return Messages()

        return Users()


def gmail_msg(id, sender, subject, body, date="Tue, 03 Nov 2026 09:30:00 -0500"):
    return {"id": id, "snippet": body[:100], "payload": {
        "mimeType": "text/plain",
        "headers": [{"name": "From", "value": sender}, {"name": "Subject", "value": subject}, {"name": "Date", "value": date}],
        "body": {"data": b64(body)},
    }}


def test_scan_adds_only_time_sensitive_and_skips_duplicates():
    service = FakeGmail([
        gmail_msg("m1", "psmith@gwu.edu", "HW 6", "HW 6 is due Thursday at 5pm."),
        gmail_msg("m2", "friend@gmail.com", "lunch?", "want to get lunch"),
        gmail_msg("m3", "Blackboard <do-not-reply@blackboard.com>", "Blackboard: new announcement", "Read the update."),
        gmail_msg("m4", "do-not-reply@blackboard.com", "Submission received", "We got your submission."),
    ])
    msg = gmail.scan(service, now=datetime(2026, 11, 3, 12))
    items = db.list_inbox()
    assert sorted(i.external_id for i in items) == ["m1", "m3"]
    hw = next(i for i in items if i.external_id == "m1")
    assert hw.kind == "professor" and hw.detected_due == datetime(2026, 11, 5, 17, 0)
    assert "2 new" in msg
    assert "newer_than:14d" in service.last_query

    gmail.scan(service, now=datetime(2026, 11, 3, 13), since=datetime(2026, 11, 3, 12))
    assert len(db.list_inbox()) == 2
    assert "after:" in service.last_query


# ---------- Blackboard feed ----------

FEED = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Blackboard//EN
BEGIN:VEVENT
UID:bb-1
SUMMARY:ISTM 4121: Homework 4
DTSTART:20261106T045900Z
DTEND:20261106T045900Z
END:VEVENT
BEGIN:VEVENT
UID:bb-2
SUMMARY:STAT 2118: Discussion post
DTSTART;VALUE=DATE:20261108
END:VEVENT
BEGIN:VEVENT
UID:bb-3
SUMMARY:STAT 2118 Review Session
DTSTART:20261109T190000Z
DTEND:20261109T200000Z
END:VEVENT
END:VCALENDAR
"""


def test_parse_feed_splits_tasks_and_events():
    tasks, events = blackboard.parse_feed(FEED)
    assert [(t.name, t.deadline) for t in tasks] == [
        ("ISTM 4121: Homework 4", datetime(2026, 11, 5, 23, 59)),
        ("STAT 2118: Discussion post", datetime(2026, 11, 8, 23, 59)),
    ]
    assert [(e.name, e.start) for e in events] == [("STAT 2118 Review Session", datetime(2026, 11, 9, 14, 0))]


def test_blackboard_sync_keeps_your_edits(monkeypatch):
    monkeypatch.setattr(blackboard, "fetch_feed", lambda url: FEED)
    now = datetime(2026, 11, 3, 12)
    assert "2 new assignments" in blackboard.sync("https://example/feed.ics", now)

    hw = next(t for t in db.list_tasks() if "Homework 4" in t.name)
    hw.estimated_minutes, hw.priority = 150, "High"
    db.update_task(hw)
    msg = blackboard.sync("https://example/feed.ics", now)
    hw = next(t for t in db.list_tasks() if "Homework 4" in t.name)
    assert (hw.estimated_minutes, hw.priority) == (150, "High")
    assert "0 new" in msg and len(db.list_tasks()) == 2


# ---------- Sync orchestration ----------

def test_failed_sync_is_recorded_not_raised(monkeypatch):
    db.set_preference("blackboard_feed_url", "https://example/feed.ics")
    monkeypatch.setattr(blackboard, "fetch_feed", lambda url: (_ for _ in ()).throw(OSError("network down")))
    ok, message = sync.sync_blackboard()
    assert not ok and "network down" in message
    assert db.get_sync_state("blackboard")[1].startswith("Failed")


def test_due_for_auto_sync(monkeypatch):
    monkeypatch.setattr(sync.google_auth, "is_signed_in", lambda: False)
    assert not sync.due_for_auto_sync()  # nothing connected
    db.set_preference("blackboard_feed_url", "https://example/feed.ics")
    assert sync.due_for_auto_sync()
    db.set_sync_state("blackboard", "ok")
    assert not sync.due_for_auto_sync()
    assert sync.due_for_auto_sync(datetime.now() + timedelta(minutes=config.AUTO_SYNC_MINUTES + 1))



# ---------- Real GWU Blackboard formats (names made up) ----------

BB = "Jane Doe - jdoe@gwu.edu <do-not-reply@blackboard.com>"


def test_clean_subject():
    assert gmail.clean_subject(
        "54792_202603 (202603_Web Applications Development_ISTM_4205_10): ISTM 4205 Assignment 4 and 5"
    ) == "ISTM 4205: ISTM 4205 Assignment 4 and 5"
    assert gmail.clean_subject(
        "54792_202603 (202603_Web Applications Development_ISTM_4205_10): Announcement: Class Canceled"
    ) == "ISTM 4205: Class Canceled"
    assert gmail.clean_subject("52586-56781_202603 (Ops-Mgmt-Fall-2026-Merged): Exam 1 prep") == "Ops-Mgmt-Fall-2026-Merged: Exam 1 prep"
    assert gmail.clean_subject("Submission received") == "Submission received"


def test_blackboard_receipts_are_skipped():
    assert gmail.classify("do-not-reply@blackboard.com", "Submission received", "", None) is None
    assert gmail.classify(BB, "54792_202603 (202603_X_ISTM_4205_10): Assignment 4", "", None) == "blackboard"


def test_blackboard_announcement_date_format():
    body = ("Just a reminder that Assignment 4 is due tonight, Oct. 26 2026 at 11:59 pm. "
            "Please note that Assignment 5 is due this upcoming Sunday, Nov. 1 2026 at 11:59 pm.")
    assert gmail.extract_due(body, datetime(2026, 10, 26, 16, 56)) == datetime(2026, 10, 26, 23, 59)


def test_exam_announcement_date_format():
    body = "Prepare for your first upcoming exam which will take place on Thursday, October 1st in Duques 152."
    assert gmail.extract_due(body, datetime(2026, 9, 28, 13, 28)) == datetime(2026, 10, 1, 23, 59)
