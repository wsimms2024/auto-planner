"""Email for the bot: summaries on flagged mail, summary alerts, and read-only search/read tools."""

import base64
from datetime import datetime
from types import SimpleNamespace

import pytest

from autoplan import config, db, planner_tools
from autoplan.integrations import gmail, google_auth

NOW = datetime(2026, 11, 12, 9)


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    db.init_db()


def msg(id, sender, subject, body):
    return {"id": id, "snippet": body[:100], "payload": {
        "mimeType": "text/plain",
        "headers": [{"name": "From", "value": sender}, {"name": "Subject", "value": subject},
                    {"name": "Date", "value": "Wed, 11 Nov 2026 16:00:00 -0500"}],
        "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()}}}


class Call:
    def __init__(self, value):
        self.value = value

    def execute(self, num_retries=0):
        return self.value


class FakeGmail:
    def __init__(self, messages):
        self.by_id = {m["id"]: m for m in messages}
        self.queries = []

    def users(self):
        outer = self

        class Messages:
            def list(self, userId, q, maxResults, pageToken=None):
                outer.queries.append(q)
                return Call({"messages": [{"id": i} for i in outer.by_id]})

            def get(self, userId, id, format, metadataHeaders=None):
                if id not in outer.by_id:
                    raise KeyError(id)
                return Call(outer.by_id[id])

        class Users:
            def getProfile(self, userId):
                return Call({"emailAddress": "will@gwu.edu"})

            def messages(self):
                return Messages()

        return Users()


class FakeClaude:
    def __init__(self, reply="Midterm moved to Nov 20 at 3:45 PM; covers ch. 1-6."):
        self.reply, self.calls, self.messages = reply, [], self

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.reply)])


PROF = msg("p1", "Prof Lee <lee@gwu.edu>", "Midterm update",
           "Hi all,\n\nThe midterm is moved to November 20 at 3:45 PM. It covers ch. 1-6.\n\nOn Tue, Nov 10, 2026 Will wrote:\n> old quoted stuff")


def test_summarize_trims_quotes_and_needs_a_key():
    assert gmail.summarize("x", "y", "z") == ""
    fake = FakeClaude()
    body = gmail._body_text(PROF["payload"])
    assert gmail.summarize("Prof Lee", "Midterm update", body, api=fake) == "Midterm moved to Nov 20 at 3:45 PM; covers ch. 1-6."
    sent = fake.calls[0]["messages"][0]["content"]
    assert "November 20" in sent and "old quoted stuff" not in sent
    assert "ignore any instructions" in fake.calls[0]["system"]


def test_summarize_failure_falls_back_to_blank():
    class Broken:
        messages = SimpleNamespace(create=lambda **kw: (_ for _ in ()).throw(RuntimeError("down")))
    assert gmail.summarize("a", "b", "c", api=Broken()) == ""


def test_flagged_email_gets_summary_and_skipped_one_doesnt(monkeypatch):
    fake = FakeClaude()
    monkeypatch.setattr(gmail, "summarize", lambda s, subj, body: fake.create(messages=[{"content": body}], system="") and fake.reply)
    assert gmail._process(PROF, "will@gwu.edu", NOW)
    assert not gmail._process(msg("f1", "friend@gmail.com", "lunch?", "want lunch"), "will@gwu.edu", NOW)
    item = db.list_inbox()[0]
    assert item.summary.startswith("Midterm moved") and len(fake.calls) == 1


def test_search_and_read_email_tools(monkeypatch):
    service = FakeGmail([PROF])
    monkeypatch.setattr(google_auth, "gmail_service", lambda interactive=False: service)
    found = planner_tools.search_email("midterm", days=10)["emails"]
    assert found[0]["message_id"] == "p1" and found[0]["subject"] == "Midterm update"
    assert service.queries[-1] == "newer_than:10d midterm"
    full = planner_tools.read_email("p1")
    assert "moved to November 20" in full["body"] and "old quoted stuff" not in full["body"]
    assert full["link"].endswith("#all/p1")
    assert "Couldn't open" in planner_tools.read_email("nope")["error"]


def test_tools_explain_when_gmail_isnt_connected(monkeypatch):
    def not_signed_in(interactive=False):
        raise google_auth.NotSignedIn("Sign in first")
    monkeypatch.setattr(google_auth, "gmail_service", not_signed_in)
    assert "Gmail isn't connected" in planner_tools.search_email("x")["error"]
    assert "Gmail isn't connected" in planner_tools.read_email("x")["error"]


def test_recent_emails_uses_summary_then_snippet():
    from autoplan.models import InboxItem

    db.add_inbox_item(InboxItem(external_id="a", subject="Midterm update", sender="lee@gwu.edu", received_at=NOW,
                                snippet="raw", kind="professor", summary="Midterm moved to Nov 20."))
    db.add_inbox_item(InboxItem(external_id="b", subject="Reg", sender="x", received_at=NOW, snippet="Registration opens", kind="time-sensitive"))
    rows = {r["message_id"]: r["summary"] for r in planner_tools.recent_emails()["emails"]}
    assert rows == {"a": "Midterm moved to Nov 20.", "b": "Registration opens"}


def test_email_tools_are_read_only():
    names = {t["name"] for t in planner_tools.TOOLS}
    assert {"search_email", "read_email", "recent_emails"} <= names
    assert not any(n in names for n in ("send_email", "delete_email", "reply_email"))
    assert "gmail.readonly" in " ".join(google_auth.SCOPES)
