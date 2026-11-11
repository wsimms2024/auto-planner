"""
Two-way Google Calendar sync.

In:  events from the calendars you pick become fixed events in AutoPlan
     (source "google"). They're read-only here; edit them in Google.
Out: accepted study sessions are copied to a separate "AutoPlan" calendar
     with a reminder, so your phone alerts you. Removing a session, or
     finishing/deleting its task, removes the copy.

Skipped on import: all-day events, events marked "free", events you
declined, and anything on the AutoPlan calendar itself.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from autoplan import config, db
from autoplan.models import Event

SOURCE = "google"
AUTOPLAN_CALENDAR_NAME = "AutoPlan"


# ---------- Small helpers ----------

def to_local(value: str) -> datetime:
    """Google's '2026-11-04T10:00:00-05:00' -> naive local time used everywhere in AutoPlan."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.astimezone(ZoneInfo(config.TIMEZONE)).replace(tzinfo=None)


def to_google(dt: datetime) -> str:
    """Naive local time -> RFC 3339 string with the right offset."""
    return dt.replace(tzinfo=ZoneInfo(config.TIMEZONE)).isoformat()


def _all_pages(request_fn, **kwargs) -> list[dict]:
    items, token = [], None
    while True:
        resp = request_fn(pageToken=token, **kwargs).execute()
        items += resp.get("items", [])
        token = resp.get("nextPageToken")
        if not token:
            return items


# ---------- Calendars ----------

def list_calendars(service) -> list[dict]:
    """Your calendars as [{id, name, primary}], minus the AutoPlan one."""
    autoplan_id = _autoplan_calendar_id()
    return [
        {"id": c["id"], "name": c.get("summaryOverride") or c.get("summary", c["id"]), "primary": c.get("primary", False)}
        for c in _all_pages(service.calendarList().list)
        if c["id"] != autoplan_id and c.get("summary") != AUTOPLAN_CALENDAR_NAME
    ]


def _autoplan_calendar_id() -> str | None:
    return db.get_preference_value("google_autoplan_calendar_id")


def ensure_autoplan_calendar(service) -> str:
    """Find or create the AutoPlan calendar and remember its id."""
    cal_id = _autoplan_calendar_id()
    if cal_id:
        try:
            service.calendars().get(calendarId=cal_id).execute()
            return cal_id
        except Exception:
            pass  # deleted in Google; make a new one
    for c in _all_pages(service.calendarList().list):
        if c.get("summary") == AUTOPLAN_CALENDAR_NAME:
            cal_id = c["id"]
            break
    else:
        created = service.calendars().insert(
            body={"summary": AUTOPLAN_CALENDAR_NAME, "timeZone": config.TIMEZONE,
                  "description": "Study sessions accepted in AutoPlan"}
        ).execute()
        cal_id = created["id"]
    db.set_preference("google_autoplan_calendar_id", cal_id)
    return cal_id


# ---------- Import ----------

def to_event(item: dict, calendar_id: str, category: str) -> Event | None:
    """Turn one Google event into an AutoPlan Event, or None if it should be skipped."""
    if item.get("status") == "cancelled":
        return None
    if "dateTime" not in item.get("start", {}) or "dateTime" not in item.get("end", {}):
        return None  # all-day event
    if item.get("transparency") == "transparent":
        return None  # marked "free"
    for person in item.get("attendees", []):
        if person.get("self") and person.get("responseStatus") == "declined":
            return None
    start, end = to_local(item["start"]["dateTime"]), to_local(item["end"]["dateTime"])
    if end <= start:
        return None
    return Event(
        name=(item.get("summary") or "(busy)").strip() or "(busy)",
        start=start,
        end=end,
        category=category,
        source=SOURCE,
        external_id=f"{calendar_id}:{item['id']}",
    )


def import_events(service, calendars: dict[str, str], now: datetime) -> str:
    """
    Pull events from each calendar (id -> category) for the sync window and
    save them. Events that disappeared from Google are removed here too.
    """
    window_start = now - timedelta(days=config.GOOGLE_SYNC_DAYS_BACK)
    window_end = now + timedelta(weeks=config.GOOGLE_SYNC_WEEKS_AHEAD)
    seen, added = set(), 0
    for cal_id, category in calendars.items():
        items = _all_pages(
            service.events().list,
            calendarId=cal_id,
            timeMin=to_google(window_start),
            timeMax=to_google(window_end),
            singleEvents=True,
            orderBy="startTime",
            maxResults=250,
        )
        for item in items:
            event = to_event(item, cal_id, category)
            if event:
                db.upsert_external_event(event)
                seen.add(event.external_id)
                added += 1
    removed = db.remove_missing_external_events(SOURCE, seen, window_start, window_end)
    return f"{added} event{'s' if added != 1 else ''} imported" + (f", {removed} removed" if removed else "")


# ---------- Edit or delete an imported event in Google ----------

def _split_id(external_id: str) -> tuple[str, str]:
    cal_id, _, event_id = external_id.rpartition(":")
    return cal_id, event_id


def update_google_event(service, external_id: str, name: str, start: datetime, end: datetime) -> None:
    """Change an event's title/time in Google so the next import doesn't undo it."""
    cal_id, event_id = _split_id(external_id)
    service.events().patch(calendarId=cal_id, eventId=event_id, body={
        "summary": name,
        "start": {"dateTime": to_google(start), "timeZone": config.TIMEZONE},
        "end": {"dateTime": to_google(end), "timeZone": config.TIMEZONE},
    }).execute()


def delete_google_event(service, external_id: str) -> None:
    """Delete one event in Google. For a repeating event this removes only that occurrence."""
    cal_id, event_id = _split_id(external_id)
    service.events().delete(calendarId=cal_id, eventId=event_id).execute()


# ---------- Push study sessions ----------

def push_sessions(service, now: datetime) -> str:
    """
    Make the AutoPlan calendar match your accepted, upcoming study sessions:
    add new ones, remove ones you removed or whose task is done/deleted.
    """
    tasks = {t.id: t for t in db.list_tasks()}
    active = {s.id for s in db.list_active_sessions(now) if s.status == "accepted"}
    wanted = [
        s for s in db.list_study_sessions(status="accepted")
        if s.id in active and s.end > now and s.task_id in tasks
    ]
    pushed = db.list_pushed_sessions()
    wanted_ids = {s.id for s in wanted}
    if not wanted and not pushed:
        return "no study sessions to push"

    cal_id = ensure_autoplan_calendar(service)
    created = removed = 0

    for s in pushed:
        if s.id not in wanted_ids and s.end > now:
            try:
                service.events().delete(calendarId=cal_id, eventId=s.google_event_id).execute()
            except Exception:
                pass  # already gone in Google
            db.set_session_google_id(s.id, None)
            removed += 1

    for s in wanted:
        if s.google_event_id:
            continue
        task = tasks[s.task_id]
        body = {
            "summary": f"Study: {task.name}",
            "description": f"Due {task.deadline:%a %b %d, %I:%M %p}. Added by AutoPlan.",
            "start": {"dateTime": to_google(s.start), "timeZone": config.TIMEZONE},
            "end": {"dateTime": to_google(s.end), "timeZone": config.TIMEZONE},
            "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": config.SESSION_REMINDER_MINUTES}]},
        }
        result = service.events().insert(calendarId=cal_id, body=body).execute()
        db.set_session_google_id(s.id, result["id"])
        created += 1

    return f"{created} sessions added to Google, {removed} removed"
