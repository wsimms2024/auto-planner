"""The bot reports only real changes, and follows Will's lead on the workout day."""

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from autoplan import config, db, planner_tools
from autoplan.assistant import Assistant, receipt
from autoplan.models import Event
from autoplan.workout import digest, store


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    db.init_db()


class FakeClaude:
    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.messages = self

    def create(self, **kwargs):
        return self.scripted.pop(0)


def tool(name, args, id="t1"):
    return SimpleNamespace(content=[SimpleNamespace(type="tool_use", name=name, input=args, id=id)])


def say(text):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


def test_real_delete_gets_a_receipt():
    today = date.today()
    eid = db.add_event(Event(name="Gym", start=datetime.combine(today, datetime.min.time()) + timedelta(hours=18),
                             end=datetime.combine(today, datetime.min.time()) + timedelta(hours=19), category="gym"))
    fake = FakeClaude([tool("delete_event", {"event_id": eid}), say("Done, the 6 PM gym session is gone.")])
    answer = Assistant(client=fake, model="m").reply("me", "delete my 6pm gym")
    assert "✓ Deleted: Gym" in answer
    assert db.list_events() == []


def test_claimed_change_without_a_tool_is_flagged():
    fake = FakeClaude([say("Done, I deleted both of tonight's gym blocks.")])
    answer = Assistant(client=fake, model="m").reply("me", "delete tonight's gym")
    assert "Nothing was actually changed" in answer


def test_plain_answers_are_left_alone():
    fake = FakeClaude([say("Your class was moved to Room 202 by the professor.")])
    assert "Nothing was actually changed" not in Assistant(client=fake, model="m").reply("me", "where's class?")


def test_failed_change_is_reported():
    fake = FakeClaude([tool("delete_event", {"event_id": 999}), say("Couldn't find it.")])
    answer = Assistant(client=fake, model="m").reply("me", "delete it")
    assert "✗ delete event: No event with id 999" in answer


def test_receipt_wording():
    assert receipt("add_event", {"added": "Legs", "when": "Wed Nov 11, 1:45 PM-3:00 PM"}) == "✓ Added: Legs (Wed Nov 11, 1:45 PM-3:00 PM)"
    assert receipt("set_workout_day", {"workout_day": "Legs"}) == "✓ Today's workout set to: Legs"


def test_today_is_legs_switches_the_plan():
    out = planner_tools.run_tool("set_workout_day", {"split_day": "legs"})
    assert out["workout_day"] == "Legs" and out["exercises"][0]["name"] == "Squat"
    assert out["exercises"][0]["weight"] == 355  # seed 345 4x4 -> next wave step 3x3 +10
    assert digest.build(use_llm=False)["split_day"] == "Legs"


def test_preview_doesnt_change_today():
    preview = planner_tools.run_tool("get_workout_plan", {"split_day": "Upper"})
    assert preview["preview_of"] == "Upper"
    assert digest.build(use_llm=False)["split_day"] == "Push"


def test_calendar_gym_event_sets_the_day():
    today = date.today()
    start = datetime.combine(today, datetime.min.time()) + timedelta(hours=13, minutes=45)
    db.add_event(Event(name="Legs", start=start, end=start + timedelta(minutes=75), category="gym"))
    d = digest.build(use_llm=False)
    assert d["split_day"] == "Legs" and "from your calendar (Legs at 1:45 PM)" in d["split_reason"]


def test_explicit_choice_beats_calendar():
    today = date.today()
    start = datetime.combine(today, datetime.min.time()) + timedelta(hours=13)
    db.add_event(Event(name="push day", start=start, end=start + timedelta(hours=1), category="gym"))
    store.set_split_override(today, "Pull")
    assert digest.build(use_llm=False)["split_day"] == "Pull"
