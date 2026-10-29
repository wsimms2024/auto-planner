"""
The scheduling engine.

Rules of this module:
- No database access and no Streamlit. It takes plain objects in and
  returns plain objects out, so it is easy to test and easy to read.
- Logic is rule-based and explainable, no AI.

expand_events and find_conflicts are live (the weekly view needs them).
The rest are stubs until Phase 3.
"""

from dataclasses import replace
from datetime import date, datetime, timedelta

from autoplan.models import Event, Preferences, StudySession, Task

# A free block of time: (start, end)
TimeSlot = tuple[datetime, datetime]


def expand_events(events: list[Event], start: date, end: date) -> list[Event]:
    """
    Turn recurring events into one Event per actual occurrence between
    start and end (inclusive). One-time events in range pass through.

    Occurrences keep the original event's id and have recurrence cleared,
    so each one is just a plain start/end block. Sorted by start time.
    """
    occurrences = []
    for event in events:
        if not event.is_recurring:
            if start <= event.start.date() <= end:
                occurrences.append(event)
            continue

        first = max(start, event.start.date())
        last = min(end, event.recurrence_until)
        day = first
        while day <= last:
            if day.weekday() in event.recurrence_days:
                occ_start = datetime.combine(day, event.start.time())
                occurrences.append(
                    replace(
                        event,
                        start=occ_start,
                        end=occ_start + event.duration,
                        recurrence_days=None,
                        recurrence_until=None,
                    )
                )
            day += timedelta(days=1)

    return sorted(occurrences, key=lambda e: e.start)


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    """True if two time ranges share any time. Touching end-to-start is not an overlap."""
    return a_start < b_end and b_start < a_end


def find_conflicts(candidate: Event, existing: list[Event]) -> list[tuple[Event, Event]]:
    """
    Check a new or edited event against saved events.

    Returns (candidate occurrence, existing occurrence) pairs that overlap.
    The event being edited (same id) is ignored so it can't clash with itself.
    """
    range_start = candidate.start.date()
    range_end = candidate.recurrence_until if candidate.is_recurring else candidate.end.date()

    mine = expand_events([candidate], range_start, range_end)
    others = expand_events(
        [e for e in existing if e.id is None or e.id != candidate.id],
        range_start - timedelta(days=1),  # catch overnight events from the day before
        range_end,
    )

    conflicts = []
    for m in mine:
        for o in others:
            if overlaps(m.start, m.end, o.start, o.end):
                conflicts.append((m, o))
    return conflicts


def find_free_slots(
    events: list[Event],
    prefs: Preferences,
    start: date,
    end: date,
) -> list[TimeSlot]:
    """
    Return open blocks inside your working hours that don't overlap any
    event (including the buffer around each event). Blocks shorter than
    prefs.min_session_minutes are dropped.
    """
    raise NotImplementedError("Phase 3")


def prioritize_tasks(tasks: list[Task], now: datetime) -> list[Task]:
    """
    Order open tasks by what should be worked on first, using deadline
    urgency and priority. Completed and deleted tasks are skipped.
    """
    raise NotImplementedError("Phase 3")


def suggest_sessions(
    tasks: list[Task],
    events: list[Event],
    existing_sessions: list[StudySession],
    prefs: Preferences,
    now: datetime,
    days_ahead: int = 7,
) -> list[StudySession]:
    """
    Main entry point. Split each task into sessions between
    prefs.min_session_minutes and prefs.max_session_minutes and place them
    in free slots before the task's deadline. Accepted sessions count as
    busy time. Returns sessions with status 'suggested'.
    """
    raise NotImplementedError("Phase 3")
