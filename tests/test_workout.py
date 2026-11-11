"""Daily Workout Digest: split logic, progression, recovery, fueling, parsing, formatting."""

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from autoplan import config, db, planner_tools
from autoplan.workout import actions, digest, engine, fueling, parser, recovery, settings, store
from autoplan.workout.store import Session, SetLog

TODAY = date(2026, 11, 11)  # Wednesday


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    db.init_db()


def log(text, days_ago):
    return store.add_session(parser.parse_log(text, TODAY - timedelta(days=days_ago)))


# ---------- e1RM ----------

def test_e1rm():
    assert engine.e1rm(240, 4) == pytest.approx(272)
    assert engine.e1rm_rpe(240, 4, 8) == pytest.approx(288)  # 2 reps in reserve
    assert engine.e1rm(300, 1) == 300


# ---------- Split day ----------

@pytest.mark.parametrize("history, expected", [
    ([], "Push"),
    ([("push; bench 240 4x4 @8", 1)], "Pull"),
    ([("legs; squat 345 4x4 @8", 1)], "Rest"),
    ([("legs; squat 345 4x4 @8", 2)], "Upper"),        # rest day already taken
    ([("lower; deadlift 405 3x3 @8", 3)], "Push"),
    ([("pull; tbar 180 3x9", 5)], "Legs"),              # a long gap never skips a workout
])
def test_split_follows_last_workout(history, expected):
    for text, days in history:
        log(text, days)
    assert engine.split_today(TODAY, store.list_sessions())["day"] == expected


def test_split_override_and_logged_today():
    log("push; bench 240 4x4 @8", 1)
    assert engine.split_today(TODAY, store.list_sessions(), "Legs")["day"] == "Legs"
    log("pull; tbar 180 3x9", 0)
    s = engine.split_today(TODAY, store.list_sessions())
    assert s["day"] == "Pull" and s["done_today"]


# ---------- Main-lift progression ----------

def test_pyramid_progresses_after_clean_session():
    log("push; bench 240 4x4 @8", 7)
    p = engine.pyramid("Bench Press", "Push")
    assert (p.sets, p.reps, p.weight, p.status) == ("3", "3", 245, "progress")


def test_pyramid_wraps_to_new_wave():
    log("push; bench 235 5x5 @8", 21)
    log("push; bench 240 4x4 @8", 14)
    log("push; bench 245 3x3 @8", 7)
    log("push; bench 250 2x2 @8.5", 1)
    p = engine.pyramid("Bench Press", "Push")
    assert (p.sets, p.reps, p.weight) == ("5", "5", 240)  # last 5x5 + 5


def test_pyramid_conservative_after_grind():
    log("push; bench 240 4,4,4,4 @8,8.5,9,9.5", 7)
    p = engine.pyramid("Bench Press", "Push")
    assert (p.sets, p.reps, p.weight, p.status) == ("4", "4", 242.5, "conservative")


def test_pyramid_repeats_then_resets_on_misses():
    log("push; bench 245 3,3,2 @9,9.5,10", 7)
    p = engine.pyramid("Bench Press", "Push")
    assert (p.weight, p.status) == (245, "repeat")
    log("push; bench 245 3,3,2 @9,9.5,10", 1)
    p = engine.pyramid("Bench Press", "Push")
    assert p.status == "reset" and p.weight == 235  # ~5% off, rounded to 5


def test_pyramid_uses_seed_without_history():
    p = engine.pyramid("Bench Press", "Push")
    assert p.weight == 245 and "starting numbers" in p.why


def test_strength_and_volume_days_are_tracked_separately():
    log("upper; bench 205 4x8 @8", 2)
    p = engine.pyramid("Bench Press", "Push")
    assert p.weight == 245  # Upper volume work doesn't count as a Push strength session


def test_volume_bench_double_progression():
    first = engine.volume_bench("Upper")
    assert first.weight == 205  # ~72% of the seed's estimated max (288)
    log("upper; bench 205 4x8 @7.5", 3)
    assert engine.volume_bench("Upper").weight == 210


def test_531_week_loads(monkeypatch):
    monkeypatch.setattr(settings, "BENCH_PROGRAM", "531")
    p = engine.main_lift({"name": "Bench Press", "role": "main", "style": "strength"}, "Push")
    assert p.target == "180x5, 205x5, 235x5+"  # 65/75/85% of 275, rounded to 5


def test_accessory_double_progression():
    log("pull; tbar 180 3x10 @8.5", 3)
    assert engine.double_progression("T-Bar Row", "3", (8, 10)).weight == 185
    log("pull; tbar 185 8,7,6 @9,9.5,10", 1)
    p = engine.double_progression("T-Bar Row", "3", (8, 10))
    assert p.weight == 185 and p.status == "repeat"


# ---------- Recovery ----------

def test_one_bad_marker_changes_nothing():
    assert recovery.assess({"sleep_hours": 5.8, "energy": 4})["level"] == "normal"


def test_stacked_markers_reduce():
    r = recovery.assess({"sleep_hours": 5.5, "energy": 2, "low_calories": 1})
    assert r["level"] == "reduce" and r["adjust"]["load_factor"] == 0.93


def test_illness_rules():
    assert recovery.assess({"illness": "systemic"})["adjust"]["rest"]
    assert recovery.assess({"illness": "improving"})["level"] == "conservative"


def test_elbow_pain_changes_pressing():
    plan = engine.plan_for("Upper")
    names = [p.name for p in engine.apply_recovery(engine.plan_for("Upper"), recovery.assess({"elbow_pain": 5})["adjust"])]
    assert "Chest Fly" not in names and "Bench Press" in names
    none = [p.name for p in engine.apply_recovery(plan, recovery.assess({"elbow_pain": 8})["adjust"])]
    assert "Bench Press" not in none and "Chest-Supported Row" in none


def test_recovery_adjusts_main_lift():
    adjust = recovery.assess({"sleep_hours": 5.5, "energy": 2, "low_calories": 1})["adjust"]
    bench = engine.apply_recovery(engine.plan_for("Push"), adjust)[0]
    assert bench.weight == 230 and bench.sets == "2" and bench.rpe == "≤7.5"  # 245 x 0.93, one fewer set


# ---------- Fueling ----------

def test_fueling_rules():
    assert fueling.advise("Pull", {"minutes_to_gym": 60, "food": "eggs + bacon, apple + gatorade"})["status"] == "Enough fuel for Pull"
    assert "heavy" in fueling.advise("Push", {"minutes_to_gym": 30, "food": "bacon cheeseburger"})["status"]
    low = fueling.advise("Legs", {"minutes_to_gym": 75, "food": "chicken and cheese"})
    assert "carbs" in low["tips"][0] and "lower-body" in low["tips"][0]
    assert fueling.advise("Push", None)["status"] == "No pre-workout food logged"


# ---------- Parsing ----------

def test_parse_log_and_errors():
    s = parser.parse_log("bench 245 4,4,4,3 @8,8,8.5,9.5; tbar 180 3x9", TODAY)
    bench = s.sets_for("Bench Press")
    assert [x.reps for x in bench] == [4, 4, 4, 3] and bench[-1].target_reps == 4 and bench[-1].rpe == 9.5
    assert engine.outcome(bench) == "missed"
    with pytest.raises(ValueError):
        parser.parse_log("push; did some bench", TODAY)


def test_parse_recovery_words():
    v = parser.parse_recovery("sleep 6.5 sore high elbow 3 alcohol yes sick fever")
    assert v == {"sleep_hours": 6.5, "soreness": 4, "elbow_pain": 3, "alcohol": 1, "illness": "systemic"}


# ---------- Digest ----------

def test_digest_structure_and_message_size():
    log("push; bench 240 4x4 @8; incline db 70 3x10 @8.5", 2)
    store.save_recovery(TODAY, sleep_hours=7.5, energy=4, soreness=2, bodyweight=165.4, elbow_pain=0)
    d = digest.build(TODAY, datetime(2026, 11, 11, 9), use_llm=False)
    assert d["split_day"] == "Pull" and d["next_split_day"] == "Legs" and d["bodyweight"] == 165.4
    assert d["readiness"]["status"] == "Train normally"
    assert [e["name"] for e in d["exercises"]][:2] == ["Lat Pulldown", "T-Bar Row"]
    assert d["progression"]["Bench Press"]["e1rm_low"] == 272
    messages = digest.format_telegram(d)
    assert all(len(m) <= 4096 for m in messages)
    assert "<b>Pull</b>" in messages[0] and "Stop 1 rep short of failure" in messages[0]


def test_coach_note_uses_llm_only_for_words():
    class FakeClaude:
        def __init__(self):
            self.messages = self

        def create(self, **kw):
            self.sent = kw
            return SimpleNamespace(content=[SimpleNamespace(text="Clean bench triples today.")])

    fake = FakeClaude()
    d = digest.build(TODAY, datetime(2026, 11, 11, 9), llm_client=fake)
    assert d["coach_note"] == "Clean bench triples today."
    assert "245" in fake.sent["messages"][0]["content"]  # numbers come from the engine

    class Broken:
        messages = property(lambda self: (_ for _ in ()).throw(RuntimeError("down")))

    assert digest.build(TODAY, datetime(2026, 11, 11, 9), llm_client=Broken())["coach_note"]


def test_rest_day_and_systemic_illness():
    log("legs; squat 345 4x4 @8", 1)
    d = digest.build(TODAY, datetime(2026, 11, 11, 9), use_llm=False)
    assert d["split_day"] == "Rest" and d["exercises"] == [] and d["next_split_day"] == "Upper"
    store.save_recovery(TODAY, illness="systemic")
    log("upper; bench 205 4x8 @8", 2)
    d = digest.build(TODAY + timedelta(days=0), datetime(2026, 11, 11, 9), use_llm=False)
    assert d["readiness"]["level"] == "rest" and d["exercises"] == []


# ---------- Telegram actions ----------

def test_handle_log_reports_e1rm_and_next():
    reply = actions.handle_log("push; bench 240 4x4 @8", TODAY)
    assert "e1RM ~272–288" in reply and "Next: Pull" in reply


def test_change_split_day():
    actions.set_split("Legs", date.today())
    d = digest.build(use_llm=False)
    assert d["split_day"] == "Legs" and d["exercises"][0]["name"] == "Squat"


def test_start_workout_gives_log_template():
    text = actions.start_workout_text(datetime.combine(date.today(), datetime.min.time()))
    assert "/log push; bench 245 3x3 @8" in text


def test_weekly_summary():
    log("push; bench 240 4x4 @8", 3)
    log("pull; tbar 180 3x9 @8", 2)
    store.save_recovery(TODAY - timedelta(days=9), bodyweight=164.0)
    store.save_recovery(TODAY - timedelta(days=1), bodyweight=165.0)
    w = engine.weekly(TODAY)
    assert w["sets_by_muscle"] == {"Chest": 4, "Back": 3} and w["bodyweight_change"] == 1.0
    assert w["tonnage"] == 240 * 16 + 180 * 27


def test_bot_can_log_from_natural_language_tool():
    out = planner_tools.run_tool("log_workout", {"log_text": "pull; pulldown 210 3x8 @8.5"})
    assert "Logged" in out["saved"]
    assert planner_tools.run_tool("get_workout_plan", {})["split_day"]
