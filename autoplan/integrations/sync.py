"""
Runs the integrations and records how each one went.

Pages call these instead of the integration modules directly. Errors are
caught and saved as the sync message, so one broken connection never
crashes the app or stops the others.
"""

from datetime import datetime, timedelta

from autoplan import config, db
from autoplan.integrations import google_auth


def _run(source: str, fn) -> tuple[bool, str]:
    try:
        message = fn()
        db.set_sync_state(source, message)
        return True, message
    except google_auth.NotSignedIn as e:
        return False, str(e)
    except Exception as e:  # keep the app running; show what went wrong
        message = f"Failed: {type(e).__name__}: {e}"
        db.set_sync_state(source, message)
        return False, message


def selected_calendars() -> dict[str, str]:
    """Calendar id -> category for the calendars you chose to import."""
    return db.get_preference_value("google_calendars") or {}


def sync_google_calendar(now: datetime | None = None, interactive: bool = False) -> tuple[bool, str]:
    from autoplan.integrations import google_calendar

    now = now or datetime.now()

    def work():
        service = google_auth.calendar_service(interactive)
        parts = []
        cals = selected_calendars()
        if cals:
            parts.append(google_calendar.import_events(service, cals, now))
        else:
            parts.append("no calendars chosen to import")
        parts.append(google_calendar.push_sessions(service, now))
        parts.append(google_calendar.push_events(service, now))
        return "; ".join(parts)

    return _run("google", work)


def push_to_google(now: datetime | None = None) -> None:
    """Right after a change in AutoPlan, copy it to Google so it shows on your phone. Silent if not signed in."""
    if not google_auth.is_signed_in():
        return
    from autoplan.integrations import google_calendar

    now = now or datetime.now()
    try:
        service = google_auth.calendar_service()
        google_calendar.push_events(service, now)
        google_calendar.push_sessions(service, now)
    except Exception:
        pass  # the regular sync will retry


def sync_gmail(now: datetime | None = None, interactive: bool = False) -> tuple[bool, str]:
    from autoplan.integrations import gmail

    now = now or datetime.now()
    return _run("gmail", lambda: gmail.scan(google_auth.gmail_service(interactive), now))


def sync_blackboard(now: datetime | None = None) -> tuple[bool, str]:
    from autoplan.integrations import blackboard

    url = db.get_preference_value("blackboard_feed_url")
    if not url:
        return False, "Paste your Blackboard calendar link on the Sync page first."
    return _run("blackboard", lambda: blackboard.sync(url, now or datetime.now()))


def sync_all(now: datetime | None = None) -> list[tuple[str, bool, str]]:
    results = []
    if google_auth.is_signed_in():
        results.append(("Google Calendar", *sync_google_calendar(now)))
        results.append(("Gmail", *sync_gmail(now)))
    if db.get_preference_value("blackboard_feed_url"):
        results.append(("Blackboard", *sync_blackboard(now)))
    if blackboard_details_due(now):
        results.append(("Blackboard details", *sync_blackboard_details(now)))
    from autoplan import exam_mode

    exam_mode.sync_exam_tasks(now)  # exams that just came in from the calendar get study tasks
    return results


def blackboard_details_due(now: datetime | None = None) -> bool:
    """The scraper runs every BLACKBOARD_SCRAPE_HOURS (it's slower), once you've connected Blackboard."""
    from autoplan.integrations import bb_scraper

    if not bb_scraper.has_login():
        return False
    last_run, _ = db.get_sync_state("blackboard_details")
    now = now or datetime.now()
    return last_run is None or now - last_run > timedelta(hours=config.BLACKBOARD_SCRAPE_HOURS)


def sync_blackboard_details(now: datetime | None = None) -> tuple[bool, str]:
    from autoplan.integrations import bb_scraper

    return _run("blackboard_details", lambda: bb_scraper.sync(now or datetime.now()))


def blackboard_needs_login() -> bool:
    _, message = db.get_sync_state("blackboard_details")
    return "NeedsLogin" in message


def due_for_auto_sync(now: datetime | None = None) -> bool:
    """True if anything is connected and hasn't synced in AUTO_SYNC_MINUTES."""
    now = now or datetime.now()
    connected = []
    if google_auth.is_signed_in():
        connected += ["google", "gmail"]
    if db.get_preference_value("blackboard_feed_url"):
        connected.append("blackboard")
    for source in connected:
        last_run, _ = db.get_sync_state(source)
        if last_run is None or now - last_run > timedelta(minutes=config.AUTO_SYNC_MINUTES):
            return True
    return False


def recheck_inbox(now: datetime | None = None) -> tuple[bool, str]:
    from autoplan.integrations import gmail

    return _run("gmail_recheck", lambda: gmail.recheck_pending(google_auth.gmail_service(), now or datetime.now()))
