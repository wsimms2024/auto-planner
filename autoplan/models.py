"""
Plain data objects passed between the database, the scheduler, and the UI.

These hold data plus simple validation. Database code lives in db.py,
scheduling rules live in scheduler.py.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Optional

from autoplan import config


@dataclass
class Event:
    """
    A fixed commitment: class, work shift, gym, etc.

    For a one-time event, start/end are the actual date and times.
    For a weekly event, start/end hold the first date it can happen plus the
    times; it repeats on recurrence_days until recurrence_until.
    """

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

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    def validate(self) -> list[str]:
        """Return a list of problems. Empty list means the event is OK."""
        problems = []
        if not self.name.strip():
            problems.append("Name is required.")
        if self.end <= self.start:
            problems.append("End time must be after start time.")
        if self.category not in config.EVENT_CATEGORIES:
            problems.append(f"Unknown category '{self.category}'.")
        if self.recurrence_days is not None:
            if not self.recurrence_days:
                problems.append("Pick at least one day for a weekly event.")
            if any(d not in range(7) for d in self.recurrence_days):
                problems.append("Weekdays must be numbers 0 (Mon) to 6 (Sun).")
            if self.recurrence_until is None:
                problems.append("A weekly event needs an end date.")
            elif self.recurrence_until < self.start.date():
                problems.append("Repeat-until date can't be before the start date.")
        return problems


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

    def is_overdue(self, now: datetime) -> bool:
        return self.status == "open" and self.deadline < now

    def validate(self) -> list[str]:
        problems = []
        if not self.name.strip():
            problems.append("Name is required.")
        if self.estimated_minutes <= 0:
            problems.append("Estimated time must be more than 0 minutes.")
        if self.priority not in config.PRIORITIES:
            problems.append(f"Unknown priority '{self.priority}'.")
        if self.status not in config.TASK_STATUSES:
            problems.append(f"Unknown status '{self.status}'.")
        return problems


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
