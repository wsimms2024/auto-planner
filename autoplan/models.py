"""
Plain data objects passed between the database, the scheduler, and the UI.

These hold data only. Database code lives in db.py, scheduling rules
live in scheduler.py.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Optional


@dataclass
class Event:
    """A fixed commitment: class, work shift, gym, etc."""

    name: str
    start: datetime
    end: datetime
    category: str = "other"
    # Weekly recurrence: list of weekday numbers (Mon=0 ... Sun=6).
    # None means a one-time event.
    recurrence_days: Optional[list[int]] = None
    # Last date the recurring event happens (inclusive)
    recurrence_until: Optional[date] = None
    id: Optional[int] = None
    deleted_at: Optional[datetime] = None

    @property
    def is_recurring(self) -> bool:
        return bool(self.recurrence_days)


@dataclass
class Task:
    """Something with a deadline that needs time set aside for it."""

    name: str
    deadline: datetime
    estimated_minutes: int
    priority: str = "Medium"  # High / Medium / Low
    status: str = "open"  # open / done
    id: Optional[int] = None
    deleted_at: Optional[datetime] = None


@dataclass
class StudySession:
    """A block of time set aside to work on a task."""

    task_id: int
    start: datetime
    end: datetime
    status: str = "suggested"  # suggested / accepted / rejected
    id: Optional[int] = None


@dataclass
class Preferences:
    """User settings the scheduler needs."""

    # Weekday number (Mon=0) -> (start, end) of the window you're willing to work
    work_hours: dict[int, tuple[time, time]] = field(default_factory=dict)
    min_session_minutes: int = 30
    max_session_minutes: int = 90
    buffer_minutes: int = 15
