"""
Blackboard assignments -> AutoPlan tasks.

Uses Blackboard's private calendar feed (Calendar -> gear/"Get external
calendar link"). It lists every due date across your courses, needs no
login or Duo, and updates on its own. Paste the link on the Sync page; it's
saved only in your local database.

Each item that looks like a due date becomes a task (source "blackboard")
with a default time estimate you can change. Re-syncing updates the name
and due date but keeps your estimate, priority, and done/deleted status.
Items that look like scheduled course events (class meetings, office hours)
become events instead.
"""

import re
import urllib.request
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from autoplan import config, db
from autoplan.models import Event, Task

SOURCE = "blackboard"
DUE_WORDS = re.compile(r"\b(due|assignment|quiz|test|exam|homework|hw|discussion|submission|project|paper|essay|lab)\b", re.I)


def fetch_feed(url: str) -> bytes:
    url = url.strip().replace("webcal://", "https://", 1)
    req = urllib.request.Request(url, headers={"User-Agent": "AutoPlan/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def _local(value) -> datetime:
    """icalendar gives date, naive datetime, or aware datetime -> naive local."""
    if isinstance(value, datetime):
        if value.tzinfo:
            return value.astimezone(ZoneInfo(config.TIMEZONE)).replace(tzinfo=None)
        return value
    return datetime.combine(value, time(23, 59))  # all-day item: due end of day


def parse_feed(data: bytes) -> tuple[list[Task], list[Event]]:
    """Split the feed into tasks (things due) and events (things that happen)."""
    from icalendar import Calendar

    cal = Calendar.from_ical(data)
    tasks, events = [], []
    for item in cal.walk("VEVENT"):
        uid = str(item.get("UID", "")).strip()
        title = str(item.get("SUMMARY", "")).strip() or "Blackboard item"
        description = str(item.get("DESCRIPTION", ""))
        if not uid or item.get("DTSTART") is None:
            continue
        start = _local(item.decoded("DTSTART"))
        end = _local(item.decoded("DTEND")) if item.get("DTEND") is not None else start
        is_all_day = not isinstance(item.decoded("DTSTART"), datetime)
        looks_due = is_all_day or end <= start or DUE_WORDS.search(f"{title} {description}") is not None

        if looks_due:
            tasks.append(Task(
                name=title,
                deadline=end if end > start and not is_all_day else start,
                estimated_minutes=config.BLACKBOARD_DEFAULT_MINUTES,
                priority="Medium",
                source=SOURCE,
                external_id=uid,
            ))
        else:
            events.append(Event(name=title, start=start, end=end, category="class", source=SOURCE, external_id=uid))
    return tasks, events


def sync(url: str, now: datetime) -> str:
    """Fetch the feed and save new or changed items. Only upcoming items (and the last week) are kept."""
    tasks, events = parse_feed(fetch_feed(url))
    cutoff = now - timedelta(days=7)
    new = updated = 0
    for t in tasks:
        if t.deadline < cutoff:
            continue
        _, is_new = db.upsert_external_task(t)
        new += is_new
        updated += not is_new
    seen = set()
    for e in events:
        if e.start >= cutoff:
            db.upsert_external_event(e)
            seen.add(e.external_id)
    if events:
        db.remove_missing_external_events(SOURCE, seen, cutoff, now + timedelta(days=365))
    plural = lambda n, word: f"{n} {word}{'s' if n != 1 else ''}"
    return f"{plural(new, 'new assignment')}, {updated} updated" + (f", {plural(len(seen), 'course event')}" if seen else "")
