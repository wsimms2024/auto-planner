"""Exam finder: reading course text for exam dates, review, confirm, and changes."""

from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

import pytest

from autoplan import config, db, exam_finder, planner_tools, scheduler
from autoplan.integrations import bb_scraper
from autoplan.models import Event

NOW = datetime(2026, 11, 11, 9)


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("autoplan.integrations.sync.push_to_google", lambda now=None: None)
    db.init_db()
    db.save_bb_courses([bb_scraper.Course(id="_101_1", code="ISTM 4205", name="Web Apps", term="202603", url="")], NOW)
    db.save_course_texts([
        {"id": "syl:s.pdf", "course_id": "_101_1", "kind": "syllabus", "title": "syllabus.pdf",
         "text": "Course schedule. Midterm Exam on November 18 in class. Final Exam December 14 at 9am.", "posted_at": None},
        {"id": "content:x", "course_id": "_101_1", "kind": "content", "title": "Week 3 slides", "text": "Lecture notes", "posted_at": None},
    ])


class FakeClaude:
    def __init__(self, exams):
        self.exams, self.calls, self.messages = exams, 0, self

    def create(self, **kw):
        self.calls += 1
        self.prompt = kw["messages"][0]["content"]
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", name="save_exams", id="x", input={"exams": self.exams})])


MIDTERM = {"name": "ISTM 4205 Midterm", "date": "2026-11-18", "source": "syllabus", "quote": "Midterm Exam on November 18 in class", "confidence": "medium"}
FINAL = {"name": "ISTM 4205 Final Exam", "date": "2026-12-14", "start_time": "09:00", "source": "syllabus", "quote": "Final Exam December 14 at 9am", "confidence": "medium"}


def test_document_keeps_useful_text_only():
    doc = exam_finder.course_document("_101_1")
    assert "Midterm Exam on November 18" in doc and "Lecture notes" not in doc


def test_claude_finds_exams_and_skips_unchanged_courses():
    fake = FakeClaude([MIDTERM, FINAL])
    assert "2 new" in exam_finder.run(NOW, client=fake)
    assert "Midterm Exam on November 18" in fake.prompt
    assert [c["name"] for c in db.list_exam_candidates("pending")] == ["ISTM 4205 Midterm", "ISTM 4205 Final Exam"]
    assert exam_finder.run(NOW, client=fake) == "exam dates: no course changes" and fake.calls == 1


def test_announcement_change_is_flagged():
    exam_finder.run(NOW, client=FakeClaude([MIDTERM]))
    db.save_course_texts([{"id": "ann:1", "course_id": "_101_1", "kind": "announcement", "title": "Midterm moved",
                           "text": "The midterm is moved to November 20 at 3:45 PM.", "posted_at": "2026-11-10T12:00:00Z"}])
    moved = {**MIDTERM, "date": "2026-11-20", "start_time": "15:45", "source": "announcement", "confidence": "high"}
    assert "1 changed" in exam_finder.run(NOW, client=FakeClaude([moved]))
    c = db.list_exam_candidates("pending")[0]
    assert c["date"] == "2026-11-20" and c["previous"] == "2026-11-18"


def test_rules_fallback_without_api_key():
    exam_finder.run(NOW)
    found = {c["name"]: c for c in db.list_exam_candidates("pending")}
    assert found["ISTM 4205 Midterm Exam"]["date"] == "2026-11-18"
    assert found["ISTM 4205 Final Exam"]["start_time"] == "09:00"


def test_confirm_uses_class_time_and_creates_study_task():
    db.add_event(Event(name="ISTM 4205", start=datetime(2026, 11, 2, 15, 45), end=datetime(2026, 11, 2, 17), category="class",
                       recurrence_days=[0, 2], recurrence_until=date(2026, 12, 9)))
    exam_finder.run(NOW, client=FakeClaude([MIDTERM]))
    cid = db.list_exam_candidates("pending")[0]["id"]
    out = exam_finder.confirm(cid)
    assert out["added"] == "ISTM 4205 Midterm" and "class meeting" in out["note"]
    ev = next(e for e in db.list_events() if e.name == "ISTM 4205 Midterm")
    assert (ev.start, ev.end) == (datetime(2026, 11, 18, 15, 45), datetime(2026, 11, 18, 17))
    assert any(t.name == "Study for ISTM 4205 Midterm" for t in db.list_tasks())  # exam mode kicks in


def test_confirming_a_changed_exam_moves_the_event():
    exam_finder.run(NOW, client=FakeClaude([FINAL]))
    cid = db.list_exam_candidates("pending")[0]["id"]
    exam_finder.confirm(cid)
    db.save_course_texts([{"id": "ann:2", "course_id": "_101_1", "kind": "announcement", "title": "Final room",
                           "text": "Final moved to Dec 15, 1pm", "posted_at": "2026-11-11T08:00:00Z"}])
    exam_finder.run(NOW, client=FakeClaude([{**FINAL, "date": "2026-12-15", "start_time": "13:00"}]))
    exam_finder.confirm(cid)
    finals = [e for e in db.list_events() if "Final" in e.name]
    assert len(finals) == 1 and finals[0].start == datetime(2026, 12, 15, 13)


def test_bot_exam_tools():
    exam_finder.run(NOW, client=FakeClaude([MIDTERM]))
    found = planner_tools.run_tool("get_exam_dates", {})
    eid = found["to_confirm"][0]["id"]
    out = planner_tools.run_tool("confirm_exam", {"exam_id": eid, "start_time": "15:45"})
    assert "added" in out and planner_tools.run_tool("get_exam_dates", {})["on_calendar"][0]["name"] == "ISTM 4205 Midterm"


def test_past_exams_are_ignored():
    past = {**MIDTERM, "date": "2026-10-01"}
    exam_finder.run(NOW, client=FakeClaude([past]))
    assert db.list_exam_candidates() == []
