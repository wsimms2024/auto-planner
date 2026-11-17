"""When the Google sign-in expires, say so instead of silently not syncing."""

import os
import time as clock
from datetime import datetime, timedelta

import pytest

from autoplan import config, db, planner_tools
from autoplan.assistant import receipt
from autoplan.integrations import google_auth, sync

NOW = datetime(2026, 11, 17, 12)


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    token = tmp_path / "google_token.json"
    token.write_text("{}")
    old = clock.time() - 3600
    os.utime(token, (old, old))  # signed in an hour ago
    monkeypatch.setattr(google_auth, "TOKEN_PATH", token)
    db.init_db()


def expired(interactive=False):
    raise google_auth.NotSignedIn("Sign in with Google on the Sync page first.")


def test_push_reports_expired_sign_in(monkeypatch):
    monkeypatch.setattr(google_auth, "calendar_service", expired)
    assert not sync.google_needs_login()
    assert sync.push_to_google(NOW) == sync.GOOGLE_EXPIRED
    assert sync.google_needs_login()


def test_push_ok_returns_none(monkeypatch):
    from autoplan.integrations import google_calendar

    monkeypatch.setattr(google_auth, "calendar_service", lambda interactive=False: object())
    monkeypatch.setattr(google_calendar, "push_events", lambda s, n: "ok")
    monkeypatch.setattr(google_calendar, "push_sessions", lambda s, n: "ok")
    assert sync.push_to_google(NOW) is None and not sync.google_needs_login()


def test_scheduled_sync_records_expiry(monkeypatch):
    monkeypatch.setattr(google_auth, "calendar_service", expired)
    ok, _ = sync.sync_google_calendar(NOW)
    assert not ok and sync.google_needs_login()


def test_signing_in_again_clears_it(monkeypatch):
    monkeypatch.setattr(google_auth, "calendar_service", expired)
    sync.push_to_google(NOW)
    google_auth.TOKEN_PATH.write_text("{}")  # new token saved now
    os.utime(google_auth.TOKEN_PATH, (clock.time() + 5, clock.time() + 5))
    assert not sync.google_needs_login()


def test_bot_add_event_says_it_didnt_reach_google(monkeypatch):
    monkeypatch.setattr(google_auth, "calendar_service", expired)
    out = planner_tools.run_tool("add_event", {"name": "Dentist", "start": "2030-11-18 10:00", "end": "2030-11-18 11:00"})
    assert not out.get("error"), out
    assert "Not synced" in out["google_calendar"] and db.list_events()[0].name == "Dentist"
    line = receipt("add_event", out)
    assert line.startswith("✓ Added: Dentist") and "⚠️ Not synced: your Google sign-in expired" in line


def test_not_set_up_is_quiet(monkeypatch):
    google_auth.TOKEN_PATH.unlink()
    assert sync.push_to_google(NOW) is None and not sync.google_needs_login()
