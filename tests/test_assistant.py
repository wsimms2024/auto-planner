"""Planner tools, the Claude tool loop (with a fake Claude), and the bot's own texts."""

from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

import pytest

from autoplan import briefing, config, db, planner_tools
from autoplan.assistant import Assistant
from autoplan.models import Event, StudySession, Task

NOW = datetime(2026, 11, 11, 9, 0)  # a Wednesday


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    prefs = db.get_preferences()
    prefs.work_hours = {d: (time(9), time(21)) for d in range(7)}
    prefs.gym_days, prefs.gym_minutes, prefs.gym_earliest, prefs.gym_latest = [2], 60, time(15), time(20)
    db.save_preferences(prefs)


def task(name, due, minutes=60, priority="Medium"):
    return db.add_task(Task(name=name, deadline=due, estimated_minutes=minutes, priority=priority))


# ---------- Planner tools ----------

def test_get_tasks_filters_by_course_and_flags_overdue():
    task("ISTM 4205 Assignment 6", NOW + timedelta(days=1))
    task("STAT 2118 HW 8", NOW + timedelta(days=2))
    task("Old quiz", NOW - timedelta(hours=3))
    rows = planner_tools.get_tasks(7, course="istm4205", now=NOW)["tasks"]
    assert [r["task"] for r in rows] == ["ISTM 4205 Assignment 6"]
    every = planner_tools.get_tasks(7, now=NOW)["tasks"]
    assert next(r for r in every if r["task"] == "Old quiz")["overdue"]
    assert "note" in planner_tools.get_tasks(7, course="CHEM", now=NOW)


def test_plan_task_gives_sessions_and_latest_start():
    task("WebAppDev Final Project", datetime(2026, 11, 13, 23, 59), 180, "High")
    plan = planner_tools.plan_task("final project", now=NOW)
    assert plan["task"] == "WebAppDev Final Project"
    assert sum((datetime.fromisoformat(s["end"]) - datetime.fromisoformat(s["start"])).seconds // 60
               for s in plan["suggested_sessions"]) == 180
    assert plan["start_no_later_than"].startswith("Fri Nov 13")


def test_plan_task_still_works_for_low_priority():
    task("Safety Awareness Training", datetime(2026, 11, 12, 23, 59), 30, "Low")
    assert planner_tools.plan_task("safety", now=NOW)["suggested_sessions"]


def test_suggest_gym_time_respects_window_and_events():
    db.add_event(Event(name="Shift", start=datetime(2026, 11, 11, 15), end=datetime(2026, 11, 11, 17), category="work"))
    gym = planner_tools.suggest_gym_time("today", now=NOW)
    assert gym["usual_gym_day"]
    first = gym["options"][0]
    assert first["start"] == "2026-11-11T17:15" and first["end"] == "2026-11-11T18:15"


def test_add_event_refuses_overlap_unless_allowed():
    db.add_event(Event(name="ISTM 4205", start=datetime(2026, 11, 11, 15, 45), end=datetime(2026, 11, 11, 17), category="class"))
    out = planner_tools.add_event("Gym", "2026-11-11 16:00", "2026-11-11 17:00", "gym")
    assert "Overlaps" in out["error"]
    assert "added" in planner_tools.add_event("Gym", "2026-11-11 17:30", "2026-11-11 18:30", "gym")


def test_add_task_guesses_defaults_and_mark_done():
    out = planner_tools.add_task("Stats midterm", "2026-11-20")
    assert out["priority"] == "High" and out["estimate_minutes"] == 360  # exam: 6h of review
    assert planner_tools.mark_task_done("stats mid")["marked_done"] == "Stats midterm"
    assert "error" in planner_tools.mark_task_done("nonexistent thing xyz")


def test_schedule_study_session_blocks_clashes():
    task("Quiz Nine", datetime(2026, 11, 12, 23, 59), 45)
    assert "scheduled" in planner_tools.schedule_study_session("quiz nine", "2026-11-11 13:00", "2026-11-11 13:45")
    assert "error" in planner_tools.schedule_study_session("quiz nine", "2026-11-11 13:30", "2026-11-11 14:15")


def test_run_tool_reports_bad_input():
    assert "error" in planner_tools.run_tool("get_schedule", {"day": "not a day"})
    assert "error" in planner_tools.run_tool("nope", {})


# ---------- Assistant tool loop with a fake Claude ----------

class FakeClaude:
    """Replays scripted responses and records what it was sent."""

    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})  # snapshot: the list keeps growing
        return self.scripted.pop(0)


def tool_use(name, args, id="t1"):
    return SimpleNamespace(content=[SimpleNamespace(type="tool_use", name=name, input=args, id=id)])


def text(t):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=t)])


def test_assistant_runs_tools_then_answers():
    task("Quiz Nine", datetime(2026, 11, 12, 23, 59), 45)
    fake = FakeClaude([tool_use("get_tasks", {"days_ahead": 2}), text("Quiz Nine is due tomorrow at 11:59 PM.")])
    bot = Assistant(client=fake, model="test-model")
    answer = bot.reply("me", "anything due tomorrow?", now=NOW)
    assert answer == "Quiz Nine is due tomorrow at 11:59 PM."
    tool_result = fake.calls[1]["messages"][-1]["content"][0]
    assert tool_result["type"] == "tool_result" and "Quiz Nine" in tool_result["content"]
    assert "Wednesday, November 11, 2026" in fake.calls[0]["system"]


def test_assistant_remembers_then_forgets():
    fake = FakeClaude([text("first"), text("second"), text("third")])
    bot = Assistant(client=fake, model="m")
    bot.reply("me", "hi", now=NOW)
    bot.reply("me", "and?", now=NOW + timedelta(minutes=5))
    assert len(fake.calls[1]["messages"]) == 3  # hi, first, and?
    bot.reply("me", "later", now=NOW + timedelta(hours=3))
    assert len(fake.calls[2]["messages"]) == 1


# ---------- Morning text and follow-ups ----------

def test_morning_text_lists_day_deadlines_and_gym():
    db.add_event(Event(name="ISTM 4205", start=datetime(2026, 11, 11, 15, 45), end=datetime(2026, 11, 11, 17), category="class"))
    task("Quiz Nine", datetime(2026, 11, 12, 23, 59), 45)
    msg = briefing.morning_text(NOW)
    assert "3:45 PM ISTM 4205" in msg
    assert "Due tomorrow 11:59 PM: Quiz Nine" in msg
    assert "Good times to study today" in msg
    assert "Gym fits at 5:15 PM" in msg


def test_followups_once_and_answers():
    tid = task("Essay", datetime(2026, 11, 14, 23, 59), 180)
    sid = db.add_study_session(StudySession(task_id=tid, start=datetime(2026, 11, 11, 7), end=datetime(2026, 11, 11, 8, 30), status="accepted"))
    due = db.sessions_needing_followup(NOW)
    assert [s.id for s in due] == [sid]
    db.mark_followup_sent(sid)
    assert db.sessions_needing_followup(NOW) == []

    assert "90 minutes left" in briefing.answer_followup(sid, "yes", NOW)
    assert db.get_study_session(sid).status == "completed"


def test_missed_session_offers_new_time():
    tid = task("Essay", datetime(2026, 11, 14, 23, 59), 90)
    sid = db.add_study_session(StudySession(task_id=tid, start=datetime(2026, 11, 11, 7), end=datetime(2026, 11, 11, 8, 30), status="accepted"))
    reply = briefing.answer_followup(sid, "no", NOW)
    assert db.get_study_session(sid).status == "missed"
    assert "Next open slot for Essay" in reply


def test_finished_marks_task_done():
    tid = task("Essay", datetime(2026, 11, 14, 23, 59), 90)
    sid = db.add_study_session(StudySession(task_id=tid, start=datetime(2026, 11, 11, 7), end=datetime(2026, 11, 11, 8, 30), status="accepted"))
    briefing.answer_followup(sid, "finished", NOW)
    assert db.list_tasks()[0].status == "done"
