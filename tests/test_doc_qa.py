"""Questions about syllabi and course files, with the PDF sent to Claude as a document."""

import base64
from datetime import datetime
from types import SimpleNamespace

import pytest

from autoplan import config, db, doc_qa, planner_tools
from autoplan.integrations import bb_scraper

NOW = datetime(2026, 11, 17, 13)


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    db.init_db()


class FakeClaude:
    def __init__(self, answer="Late work loses 10% per day, up to 3 days (page 4).", fail_first=None):
        self.answer, self.fail_first, self.calls = answer, fail_first, []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        if self.fail_first and len(self.calls) == 1:
            raise RuntimeError(self.fail_first)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.answer)])


def pdf(path, pages=1):
    from pypdf import PdfWriter

    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(612, 792)
    with open(path, "wb") as f:
        w.write(f)
    return str(path)


def course(tmp_path, syllabus):
    db.save_bb_courses([bb_scraper.Course(id="_1_1", code="ISTM 4205", name="Web Apps", term="202603", url="", syllabus=syllabus)], NOW)


def test_pdf_goes_as_document_with_cache(tmp_path):
    path = pdf(tmp_path / "syllabus.pdf")
    fake = FakeClaude()
    assert doc_qa.ask([path], "late policy?", "ISTM 4205", api=fake).startswith("Late work")
    content = fake.calls[0]["messages"][0]["content"]
    assert content[0]["type"] == "document" and content[0]["source"]["media_type"] == "application/pdf"
    assert base64.b64decode(content[0]["source"]["data"]).startswith(b"%PDF")
    assert content[0]["cache_control"] == {"type": "ephemeral"}
    assert content[-1]["text"] == "Course: ISTM 4205\nQuestion: late policy?"


def test_docx_and_text_go_as_text_with_tables(tmp_path):
    import docx

    d = docx.Document()
    d.add_paragraph("Grading")
    t = d.add_table(rows=1, cols=2)
    t.rows[0].cells[0].text, t.rows[0].cells[1].text = "Final exam", "30%"
    d.save(tmp_path / "syl.docx")
    fake = FakeClaude()
    doc_qa.ask([str(tmp_path / "syl.docx")], "final weight?", api=fake)
    text = fake.calls[0]["messages"][0]["content"][0]["text"]
    assert "Grading" in text and "Final exam | 30%" in text


def test_pdf_rejected_falls_back_to_text(tmp_path):
    path = pdf(tmp_path / "huge.pdf")
    fake = FakeClaude(fail_first="A maximum of 100 PDF pages may be provided")
    doc_qa.ask([path], "q", api=fake)
    assert len(fake.calls) == 2 and fake.calls[1]["messages"][0]["content"][0]["type"] == "text"


def test_ask_syllabus_tool(tmp_path, monkeypatch):
    course(tmp_path, [pdf(tmp_path / "syllabus.pdf")])
    fake = FakeClaude()
    monkeypatch.setattr("autoplan.llm.client", lambda: fake)
    out = planner_tools.ask_syllabus("4205", "what's the late policy")
    assert out == {"course": "ISTM 4205", "files": ["syllabus.pdf"], "answer": fake.answer}
    assert "No course matching" in planner_tools.ask_syllabus("chem", "x")["error"]


def test_syllabus_found_in_course_content_when_not_downloaded(tmp_path, monkeypatch):
    course(tmp_path, [])
    db.save_course_texts([{"id": "content:c9", "course_id": "_1_1", "kind": "content", "title": "Course Syllabus Fall 2026",
                           "text": "", "posted_at": None, "handler": "resource/x-bb-file", "folder": "Start Here"}])
    path = pdf(tmp_path / "s.pdf")
    monkeypatch.setattr(bb_scraper, "download_content", lambda cid, content: [path] if content == "c9" else [])
    monkeypatch.setattr("autoplan.llm.client", lambda: FakeClaude())
    assert planner_tools.ask_syllabus("web apps", "office hours?")["files"] == ["s.pdf"]


def test_no_syllabus_and_no_key(tmp_path, monkeypatch):
    course(tmp_path, [])
    assert "No syllabus file" in planner_tools.ask_syllabus("4205", "x")["error"]
    course(tmp_path, [pdf(tmp_path / "s.pdf")])
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert "ANTHROPIC_API_KEY" in planner_tools.ask_syllabus("4205", "x")["error"]


def test_ask_course_file_tool(tmp_path, monkeypatch):
    course(tmp_path, [])
    db.save_course_texts([{"id": "content:c2", "course_id": "_1_1", "kind": "content", "title": "Lab 5 instructions",
                           "text": "", "posted_at": None, "handler": "resource/x-bb-file", "folder": "Labs"}])
    path = pdf(tmp_path / "lab5.pdf")
    monkeypatch.setattr(bb_scraper, "download_content", lambda cid, content: [path])
    fake = FakeClaude("Submit a zip of the project folder on Blackboard.")
    monkeypatch.setattr("autoplan.llm.client", lambda: fake)
    assert planner_tools.ask_course_file("c2", "how do I submit")["answer"].startswith("Submit a zip")
    assert "find_course_files" in planner_tools.ask_course_file("nope", "x")["error"]
