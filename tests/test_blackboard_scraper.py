"""
Blackboard scraper, grade math and the Prep button.
Uses sample data shaped like Blackboard Learn's REST API and a fake client;
nothing here talks to Blackboard or Claude.
"""

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoplan import config, db, grades, planner_tools, prep
from autoplan.integrations import bb_scraper, sync
from autoplan.models import Task

NOW = datetime(2026, 11, 11, 9, 0)


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(bb_scraper, "FILES_DIR", tmp_path / "bb_files")
    monkeypatch.setattr(bb_scraper, "PROFILE_DIR", tmp_path / "bb_profile")
    monkeypatch.setattr(bb_scraper, "STATE_PATH", tmp_path / "bb_session.json")
    db.init_db()


MEMBERSHIPS = [
    {"course": {"id": "_101_1", "courseId": "54792_202603", "name": "202603_Web Applications Development_ISTM_4205_10",
                "externalAccessUrl": "https://blackboard.gwu.edu/ultra/courses/_101_1/outline", "availability": {"available": "Yes"}}},
    {"course": {"id": "_102_1", "courseId": "56945_202603", "name": "202603_Intro to Arts of Africa_CAH_2193_10",
                "availability": {"available": "Yes"}}},
    {"course": {"id": "_050_1", "courseId": "46188_202601", "name": "202601_Introduction to Financial Accounting_ACCY_2001_15",
                "availability": {"available": "Yes"}}},  # last term
    {"course": {"id": "_103_1", "courseId": "99999_202603", "name": "202603_Hidden_XXX_1000_1", "availability": {"available": "No"}}},
]

COLUMNS = [
    {"id": "c1", "name": "Assignment 5 - JavaScript", "contentId": "a5", "score": {"possible": 20}, "grading": {"due": "2026-11-02T04:59:00Z"}},
    {"id": "c2", "name": "Assignment 6 - React", "contentId": "a6", "score": {"possible": 20}, "grading": {"due": "2026-11-16T04:59:00Z"}},
    {"id": "c3", "name": "Final Project", "contentId": "fp", "score": {"possible": 100}, "grading": {"due": "2026-12-07T04:59:00Z"}},
    {"id": "c4", "name": "Quiz 7", "score": {"possible": 10}},
    {"id": "tot", "name": "Total", "externalGrade": True, "score": {"possible": 150}},
]
MY_GRADES = [
    {"columnId": "c1", "score": 18, "displayGrade": {"text": "18.00"}},
    {"columnId": "c4", "score": 9},
    {"columnId": "tot", "score": 27, "displayGrade": {"text": "90%"}},
]
CONTENTS = [
    {"id": "folder1", "title": "Week 11", "contentHandler": {"id": "resource/x-bb-folder"}, "children": [
        {"id": "a6", "title": "Assignment 6 - React", "body": "<p>Build a <b>to-do app</b> in React.</p><ul><li>Use hooks</li><li>Submit a zip</li></ul>",
         "contentHandler": {"id": "resource/x-bb-assignment"}},
        {"id": "doc1", "title": "Lecture slides", "contentHandler": {"id": "resource/x-bb-file"}},
    ]},
    {"id": "fp", "title": "Final Project", "body": "Team web app.", "contentHandler": {"id": "resource/x-bb-assignment", "dueDate": "2026-12-07T04:59:00Z"}},
]


# ---------- Parsing ----------

def test_parse_courses_keeps_current_term_only():
    courses = bb_scraper.parse_courses(MEMBERSHIPS)
    assert [c.code for c in courses] == ["ISTM 4205", "CAH 2193"]
    assert courses[0].name == "Web Applications Development"
    assert {c.term for c in courses} == {"202603"}


def test_parse_assignments_reads_instructions_due_and_points():
    course = bb_scraper.parse_courses(MEMBERSHIPS)[0]
    found = bb_scraper.parse_assignments(course, bb_scraper.walk_contents(CONTENTS), COLUMNS)
    by_id = {a.id: a for a in found}
    assert set(by_id) == {"a6", "fp"}  # slides and folders skipped
    a6 = by_id["a6"]
    assert a6.due == datetime(2026, 11, 15, 23, 59) and a6.points == 20
    assert "to-do app" in a6.instructions and "• Use hooks" in a6.instructions


def test_parse_grades_marks_total_and_ungraded():
    course = bb_scraper.parse_courses(MEMBERSHIPS)[0]
    rows = {r["id"]: r for r in bb_scraper.parse_grades(course, COLUMNS, MY_GRADES)}
    assert rows["c1"]["score"] == 18 and rows["c2"]["score"] is None
    assert rows["tot"]["is_total"] and rows["tot"]["display"] == "90%"


# ---------- Full fetch with a fake client ----------

class FakeBB:
    def __init__(self):
        self.downloads = []

    def get(self, path, **params):
        if path.endswith("/users/me"):
            return {"id": "_u1", "userName": "wsimms"}
        return None

    def get_all(self, path, **params):
        if "/users/_u1/courses" in path:
            # split across two pages to exercise paging callers don't see
            return MEMBERSHIPS
        if path.endswith("gradebook/columns"):
            return COLUMNS if "_101_1" in path else []
        if "gradebook/users" in path:
            return MY_GRADES if "_101_1" in path else []
        if path.endswith("/contents"):
            return CONTENTS if "_101_1" in path else []
        if path.endswith("/a6/attachments"):
            return [{"id": "f1", "fileName": "a6_instructions.pdf"}]
        return []

    def try_get(self, path, **params):
        """Like GW: the all-grades endpoint is refused for students; per-column grades work."""
        if path.endswith("gradebook/columns"):
            return (200, {"results": COLUMNS if "_101_1" in path else []})
        if "/v2/courses/" in path and path.endswith("/gradebook/users/_u1"):
            return (403, None)
        if "/v1/courses/_101_1/gradebook/columns/" in path and path.endswith("/users/_u1"):
            col = path.split("/columns/")[1].split("/")[0]
            g = next((g for g in MY_GRADES if g["columnId"] == col), None)
            return (200, {"columnId": col, "score": g["score"], "text": (g.get("displayGrade") or {}).get("text")}) if g else (404, None)
        return (404, None)

    def download(self, path, dest):
        self.downloads.append(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"%PDF fake")
        return True


def test_fetch_and_save_links_assignments_to_tasks():
    task_id = db.add_task(Task(name="Assignment 6 - React", deadline=datetime(2026, 11, 15, 23, 59), estimated_minutes=60,
                               source="blackboard", external_id="ics-a6"))
    fake = FakeBB()
    data = bb_scraper.fetch(fake, NOW)
    msg = bb_scraper.save(data, NOW)
    assert "2 courses" in msg and "1 matched" in msg
    details = db.get_bb_assignment_for_task(task_id)
    assert details["course_code"] == "ISTM 4205" and details["points"] == 20
    assert details["attachments"][0]["name"] == "a6_instructions.pdf"
    assert fake.downloads == ["/learn/api/public/v1/courses/_101_1/contents/a6/attachments/f1/download"]  # final project is >21 days out
    assert [c["code"] for c in db.list_bb_courses()] == ["CAH 2193", "ISTM 4205"]
    assert len(db.list_bb_grades("_101_1")) == 5


def test_resave_keeps_files_and_link():
    task_id = db.add_task(Task(name="Assignment 6 - React", deadline=datetime(2026, 11, 15, 23, 59), estimated_minutes=60))
    bb_scraper.save(bb_scraper.fetch(FakeBB(), NOW), NOW)
    later = NOW + timedelta(days=30)  # now outside the download window
    data = bb_scraper.fetch(FakeBB(), later)
    bb_scraper.save(data, later)
    assert db.get_bb_assignment_for_task(task_id)["attachments"]


def test_sync_reports_missing_login():
    ok, message = sync.sync_blackboard_details(NOW)
    assert not ok and "NeedsLogin" in message
    assert sync.blackboard_needs_login()
    assert not sync.blackboard_details_due(NOW)  # not connected, so no auto runs


# ---------- Grade math ----------

ITEMS = [
    {"name": "A5", "possible": 20, "score": 18, "is_total": False},
    {"name": "Quiz 7", "possible": 10, "score": 9, "is_total": False},
    {"name": "A6", "possible": 20, "score": None, "is_total": False},
    {"name": "Final", "possible": 100, "score": None, "is_total": False},
    {"name": "Total", "possible": 150, "score": 27, "display": "90%", "is_total": True},
]


def test_summary_and_letter():
    s = grades.summary(ITEMS)
    assert (s["earned"], s["graded_possible"], s["remaining_possible"]) == (27, 30, 120)
    assert s["percent"] == 90.0 and s["letter"] == "A-" and s["blackboard_total"] == "90%"


def test_needed_for_target():
    need = grades.needed_for(ITEMS, grades.cutoff("B+"))
    # 87% of 150 = 130.5; minus 27 earned = 103.5 of 120 remaining
    assert need["needed_percent"] == 86.2
    assert grades.needed_for(ITEMS, 100)["needed_percent"] == 102.5
    assert "Not reachable" in grades.needed_for(ITEMS, 100)["note"]


# ---------- Prep with a fake Claude ----------

class FakeClaude:
    def __init__(self):
        self.sent = None
        self.messages = self

    def create(self, **kwargs):
        self.sent = kwargs
        plan = {"summary": "Build a React to-do app.", "requirements": ["Use hooks", "Submit a zip"],
                "materials": ["Node.js"], "steps": [{"step": "Set up project", "minutes": 30}, {"step": "Build features", "minutes": 120}],
                "concepts": ["useState"], "questions": []}
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", name="save_prep", input=plan, id="x")])


def test_prep_uses_instructions_and_files(tmp_path):
    task_id = db.add_task(Task(name="Assignment 6 - React", deadline=datetime(2026, 11, 15, 23, 59), estimated_minutes=60))
    bb_scraper.save(bb_scraper.fetch(FakeBB(), NOW), NOW)
    notes = tmp_path / "bb_files" / "ISTM_4205" / "a6" / "notes.txt"
    notes.write_text("Rubric: 10 pts for components", encoding="utf-8")
    with db.get_connection() as conn:  # add a readable text file to the attachments
        conn.execute("UPDATE bb_assignments SET attachments = ? WHERE id = 'a6'",
                     ('[{"name": "notes.txt", "path": "%s"}]' % str(notes).replace("\\", "\\\\"),))
    fake = FakeClaude()
    plan = prep.run(task_id, client=fake)
    prompt = fake.sent["messages"][0]["content"]
    assert "to-do app" in prompt and "Rubric: 10 pts" in prompt and "ISTM 4205" in prompt
    assert fake.sent["tool_choice"] == {"type": "tool", "name": "save_prep"}
    assert plan["estimated_minutes"] == 150 and plan["had_blackboard_details"]
    assert db.get_prep(task_id)["summary"] == "Build a React to-do app."


def test_extract_text_docx(tmp_path):
    import docx

    path = tmp_path / "brief.docx"
    d = docx.Document()
    d.add_paragraph("Write 1500 words, APA format.")
    d.save(path)
    assert "1500 words" in prep.extract_text(str(path))
    assert prep.extract_text(str(tmp_path / "image.png")) == ""


# ---------- Bot tools ----------

def test_bot_grade_tools():
    bb_scraper.save(bb_scraper.fetch(FakeBB(), NOW), NOW)
    g = planner_tools.get_grades("istm 4205")["courses"][0]
    assert g["percent"] == 90.0 and g["blackboard_total"] == "90%"
    need = planner_tools.grade_needed("ISTM4205", "A")
    assert need["course"] == "ISTM 4205" and need["needed_percent"] > 90
    assert "error" in planner_tools.grade_needed("CHEM", "A")


def test_bot_assignment_details():
    db.add_task(Task(name="Assignment 6 - React", deadline=datetime(2026, 11, 15, 23, 59), estimated_minutes=60))
    bb_scraper.save(bb_scraper.fetch(FakeBB(), NOW), NOW)
    d = planner_tools.get_assignment_details("assignment 6")
    assert d["course"] == "ISTM 4205" and "to-do app" in d["instructions"] and d["files"] == ["a6_instructions.pdf"]
