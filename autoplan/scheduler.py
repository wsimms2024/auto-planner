"""
The scheduling engine.

Rules of this module:
- No database access and no Streamlit. It takes plain objects in and
  returns plain objects out, so it is easy to test and easy to read.
- Logic is rule-based and explainable, no AI.

Functions are stubs for now. They get implemented in Phase 3.
"""

from datetime import date, datetime

from autoplan.models import Event, Preferences, StudySession, Task

# A free block of time: (start, end)
TimeSlot = tuple[datetime, datetime]


def expand_events(events: list[Event], start: date, end: date) -> list[Event]:
    """
    Turn recurring events into one Event per actual occurrence between
    start and end (inclusive). One-time events in range pass through.
    """
    raise NotImplementedError("Phase 3")


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
