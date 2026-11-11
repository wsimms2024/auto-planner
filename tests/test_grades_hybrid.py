"""Hybrid grades: Blackboard + corrections + your entries, weights, staleness, bot logging."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from autoplan import config, db, grades, planner_tools
from autoplan.integrations import bb_scraper

NOW = datetime(2026, 11, 11, 12)


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    course = bb_scraper.Course(id="_101_1", code="ISTM 4205", name="Web Applications Development", term="202603", url="",
                               syllabus=[str(tmp_path / "syllabus.txt")])
    (tmp_path / "syllabus.txt").write_text("Grading: Exams 40%, Assignments 40%, Participation 20%. Lowest quiz dropped.", encoding="utf-8")
    db.save_bb_courses([course], NOW)
    db.replace_bb_grades(["_101_1"], [
        {"id": "c1", "course_id": "_101_1", "name": "Assignment 5", "possible": 20, "score": 18, "display": None, "due": NOW - timedelta(days=9), "is_total": False},
        {"id": "c2", "course_id": "_101_1", "name": "Assignment 6", "possible": 20, "score": None, "display": None, "due": NOW + timedelta(days=4), "is_total": False},
        {"id": "c3", "course_id": "_101_1", "name": "Midterm Exam", "possible": 100, "score": None, "display": None, "due": NOW - timedelta(days=20), "is_total": False},
        {"id": "c4", "course_id": "_101_1", "name": "Final Exam", "possible": 100, "score": None, "display": None, "due": NOW + timedelta(days=30), "is_total": False},
        {"id": "c5", "course_id": "_101_1", "name": "Attendance", "possible": 10, "score": 10, "display": None, "due": None, "is_total": False},
        {"id": "tot", "course_id": "_101_1", "name": "Total", "possible": 250, "score": None, "display": "88.5%", "due": None, "is_total": True},
    ])


def test_categories_are_guessed():
    cats = {i["name"]: i["category"] for i in grades.items_for("_101_1")}
    assert cats["Assignment 5"] == "Assignments" and cats["Midterm Exam"] == "Exams" and cats["Attendance"] == "Participation"


def test_stale_gradebook_is_flagged():
    s = grades.standing("_101_1", NOW)
    assert [i["name"] for i in s["stale"]] == ["Midterm Exam"]  # 20 days past due, no score


def test_your_correction_beats_blackboard():
    db.set_grade_override("c3", score=84)
    s = grades.standing("_101_1", NOW)
    mid = next(i for i in s["items"] if i["name"] == "Midterm Exam")
    assert mid["score"] == 84 and mid["source"] == "Blackboard (edited)" and s["stale"] == []


def test_ignore_and_manual_entries():
    db.set_grade_override("c5", ignored=True)
    db.add_grade_entry("_101_1", "Pop quiz", 9, 10)
    s = grades.standing("_101_1", NOW)
    assert s["graded_count"] == 2  # Assignment 5 + Pop quiz (attendance ignored)
    assert s["percent"] == round(100 * 27 / 30, 1)
    pop = next(i for i in s["items"] if i["name"] == "Pop quiz")
    assert pop["source"] == "You" and pop["category"] == "Quizzes"


def test_weighted_standing_and_needed():
    db.set_grade_override("c3", score=80)
    db.set_course_grade_settings("_101_1", weights={"Exams": 40, "Assignments": 40, "Participation": 20})
    s = grades.standing("_101_1", NOW)
    # Exams 80%, Assignments 90%, Participation 100% -> (40*80 + 40*90 + 20*100)/100 = 88
    assert s["percent"] == 88.0 and s["weighted"]
    need = grades.needed_for(s["items"], 90, s["weights"])
    # final(x) = [40*(80+100x)/200 + 40*(18+20x)/40 + 20*1] / 100 = (54 + 40x) / 100 = 0.9  ->  x = 0.9
    assert need["needed_percent"] == 90.0


def test_blackboard_total_mode():
    db.set_course_grade_settings("_101_1", mode="blackboard_total")
    s = grades.standing("_101_1", NOW)
    assert s["shown_percent"] == 88.5 and s["shown_letter"] == "B+"


def test_bot_logs_grades():
    out = planner_tools.run_tool("log_grade", {"course": "istm 4205", "item": "midterm", "score": 86, "possible": 100})
    assert "Midterm Exam: 86/100" in out["added"]  # matched the Blackboard item
    out = planner_tools.run_tool("log_grade", {"course": "ISTM4205", "item": "Quiz 7", "score": 9, "possible": 10})
    assert out["added"] == "ISTM 4205 Quiz 7: 9/10"
    g = planner_tools.run_tool("get_grades", {"course": "4205"})["courses"][0]
    assert g["graded"] == 4 and g["maybe_out_of_date"] == []


def test_syllabus_weight_suggestion():
    class FakeClaude:
        def __init__(self):
            self.messages = self

        def create(self, **kw):
            self.text = kw["messages"][0]["content"]
            return SimpleNamespace(content=[SimpleNamespace(type="tool_use", name="save_weights", id="x", input={
                "weights": [{"category": "Exams", "percent": 40}, {"category": "Assignments", "percent": 40}, {"category": "Participation", "percent": 20}],
                "notes": "Lowest quiz dropped."})])

    fake = FakeClaude()
    result = grades.suggest_weights("_101_1", client=fake)
    assert "Exams 40%" in fake.text
    assert result["weights"] == {"Exams": 40.0, "Assignments": 40.0, "Participation": 20.0}
