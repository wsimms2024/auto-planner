"""Today in class (syllabus schedule), course file search, and sending files over Telegram."""

import asyncio
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

import pytest

from autoplan import briefing, class_schedule, config, db, planner_tools
from autoplan.assistant import Assistant
from autoplan.integrations import bb_scraper
from autoplan.models import Event, Task

NOW = datetime(2026, 11, 12, 8)  # Thursday


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    db.init_db()
    db.save_bb_courses([bb_scraper.Course(id="_101_1", code="ISTM 4205", name="Web Apps", term="202603", url=""),
                        bb_scraper.Course(id="_202_1", code="STAT 2118", name="Regression", term="202603", url="")], NOW)
    db.save_course_texts([
        {"id": "syl:s.pdf", "course_id": "_101_1", "kind": "syllabus", "title": "syllabus.pdf",
         "text": "Week 12 (Nov 12): REST APIs. Read ch. 9. Lab 5 due.", "posted_at": None},
        {"id": "ann:1", "course_id": "_101_1", "kind": "announcement", "title": "Bring laptops",
         "text": "Bring your laptop Thursday.", "posted_at": "2026-11-10T15:00:00Z"},
        {"id": "ann:old", "course_id": "_101_1", "kind": "announcement", "title": "Old news",
         "text": "Ancient.", "posted_at": "2026-09-01T15:00:00Z"},
        {"id": "content:c1", "course_id": "_101_1", "kind": "content", "title": "Week 12 REST APIs slides",
         "text": "", "posted_at": None, "handler": "resource/x-bb-file", "folder": "Lectures / Week 12"},
        {"id": "content:c2", "course_id": "_101_1", "kind": "content", "title": "Lab 5 instructions",
         "text": "Build a REST endpoint", "posted_at": None, "handler": "resource/x-bb-document", "folder": "Labs"},
        {"id": "content:c3", "course_id": "_202_1", "kind": "content", "title": "Residual plots",
         "text": "regression diagnostics", "posted_at": None, "handler": "resource/x-bb-file", "folder": "Week 10"},
    ])
    db.add_event(Event(name="ISTM 4205", start=datetime(2026, 11, 5, 15, 45), end=datetime(2026, 11, 5, 17), category="class", recurrence_days=[3], recurrence_until=date(2026, 12, 10)))


class FakeClaude:
    def __init__(self, sessions):
        self.sessions, self.calls, self.messages = sessions, 0, self

    def create(self, **kw):
        self.calls += 1
        self.system = kw["system"]
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", name="save_schedule", id="x", input={"sessions": self.sessions})])


SESSIONS = [{"date": "2026-11-12", "topic": "REST APIs", "readings": "Ch. 9", "due": "Lab 5"},
            {"date": "not a date", "topic": "junk"},
            {"date": "2026-11-17", "topic": "Auth"}]


def test_run_saves_dated_sessions_and_skips_unchanged():
    fake = FakeClaude(SESSIONS)
    assert "ISTM 4205 (2)" in class_schedule.run(NOW, client=fake)
    assert "Thu at 3:45 PM" in fake.system and "Fall 2026" in fake.system
    assert class_schedule.run(NOW, client=fake) == "class schedules: no syllabus changes" and fake.calls == 1
    class_schedule.run(NOW, force=True, client=fake)
    assert fake.calls == 2


def test_run_needs_a_key():
    assert "skipped" in class_schedule.run(NOW)


def test_today_in_class_has_recent_announcements_only():
    class_schedule.run(NOW, client=FakeClaude(SESSIONS))
    rows = class_schedule.today_in_class(NOW.date())
    assert len(rows) == 1 and rows[0]["course_code"] == "ISTM 4205" and rows[0]["topic"] == "REST APIs"
    assert [a["title"] for a in rows[0]["announcements"]] == ["Bring laptops"]
    assert class_schedule.today_in_class(date(2026, 11, 13)) == []


def test_class_today_tool_includes_due_soon_and_filters_course():
    class_schedule.run(NOW, client=FakeClaude(SESSIONS))
    out = planner_tools.class_today("today", now=NOW)
    assert out["classes"][0]["topic"] == "REST APIs" and out["classes"][0]["readings"] == "Ch. 9"
    assert planner_tools.class_today("today", course="stat", now=NOW)["classes"] == []
    assert "note" in planner_tools.class_today("tomorrow", now=NOW)


def test_briefing_mentions_class():
    class_schedule.run(NOW, client=FakeClaude(SESSIONS))
    text = briefing.morning_text(NOW)
    assert "In class:" in text and "ISTM 4205: REST APIs" in text


def test_search_ranks_title_matches_and_filters_course():
    rows = db.search_course_files("rest")
    assert [r["title"] for r in rows][:2] == ["Week 12 REST APIs slides", "Lab 5 instructions"]
    assert db.search_course_files("week", "_202_1")[0]["title"] == "Residual plots"
    assert db.search_course_files("quantum") == []


def test_find_course_files_tool():
    files = planner_tools.find_course_files("slides", course="4205")["files"]
    assert files == [{"content_id": "c1", "course": "ISTM 4205", "title": "Week 12 REST APIs slides",
                      "folder": "Lectures / Week 12", "type": "file"}]
    assert "note" in planner_tools.find_course_files("nothing here")


def test_get_course_file_downloads_and_handles_no_files(monkeypatch, tmp_path):
    f = tmp_path / "slides.pdf"
    f.write_bytes(b"%PDF")
    monkeypatch.setattr(bb_scraper, "download_content", lambda cid, content: [str(f)] if content == "c1" else [])
    out = planner_tools.get_course_file("c1")
    assert out["send_files"] == [str(f)] and out["sent_files"] == ["slides.pdf"]
    none = planner_tools.get_course_file("c2")
    assert "no attached files" in none["error"] and none["text"] == "Build a REST endpoint"
    assert "find_course_files" in planner_tools.get_course_file("zzz")["error"]


def test_assistant_queues_files_without_leaking_paths(monkeypatch, tmp_path):
    f = tmp_path / "slides.pdf"
    f.write_bytes(b"%PDF")
    monkeypatch.setattr(bb_scraper, "download_content", lambda cid, content: [str(f)])
    scripted = [SimpleNamespace(content=[SimpleNamespace(type="tool_use", name="get_course_file", input={"content_id": "c1"}, id="t1")]),
                SimpleNamespace(content=[SimpleNamespace(type="text", text="Here are the slides.")])]
    fake = SimpleNamespace(calls=[])
    fake.messages = SimpleNamespace(create=lambda **kw: (fake.calls.append(kw), scripted.pop(0))[1])
    bot = Assistant(client=fake, model="m")
    answer = bot.reply("42", "send me the rest slides", now=NOW)
    assert bot.outbox["42"] == [str(f)]
    assert "✓ Sent files from: Week 12 REST APIs slides" in answer
    assert str(tmp_path) not in str(fake.calls[-1]["messages"][-1]["content"])


def test_bot_sends_queued_documents(monkeypatch, tmp_path):
    import telegram_bot

    f = tmp_path / "slides.pdf"
    f.write_bytes(b"%PDF")
    sent = []

    class FakeAssistant:
        outbox = {"42": [str(f)]}

        def reply(self, chat_id, text):
            return "Here."

    async def reply_text(t, **kw):
        sent.append(("text", t))

    async def reply_document(doc, filename=None, **kw):
        sent.append(("doc", filename))

    monkeypatch.setattr(telegram_bot, "get_assistant", lambda: FakeAssistant())
    monkeypatch.setattr(telegram_bot, "is_owner", lambda update: True)
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=42),
                             message=SimpleNamespace(text="slides", reply_text=reply_text, reply_document=reply_document,
                                                     chat=SimpleNamespace(send_action=None)))
    async def send_chat_action(*a, **kw):
        pass

    context = SimpleNamespace(bot=SimpleNamespace(send_chat_action=send_chat_action))
    asyncio.run(telegram_bot.chat(update, context))
    assert ("doc", "slides.pdf") in sent and FakeAssistant.outbox == {}
