"""
The scheduling engine.

Rules of this module:
- No database access and no Streamlit. It takes plain objects in and
  returns plain objects out, so it is easy to test and easy to read.
- Logic is rule-based and explainable, no AI.

How suggestions are made (suggest_sessions):
1. Find free time: your working hours for each day, minus every event and
   accepted study session (plus the buffer on both sides), minus anything
   already in the past.
2. Rank open tasks (prioritize_tasks): hours until the deadline divided by
   a priority weight. Lower score goes first, so a High task due in 4 days
   ranks the same as a Medium task due in 2 days. Overdue tasks go first.
3. For each task in that order, place sessions of up to the max length in
   the earliest free time before its deadline. It prefers full-length
   sessions, and tries to put each session on a different day before
   doubling up on one day.
4. Anything that doesn't fit before the deadline is reported as unscheduled.

Exam mode: for exams (see task_rules.is_exam), sessions are spread across
the EXAM_PREP_DAYS before the exam, one per day, weighted toward the last
few days, instead of being packed in as early as possible.

Skipped entirely: tasks with "plan time" turned off, and Low priority tasks
unless Settings says to include them.
"""

from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta

from autoplan import config, task_rules
from autoplan.models import Event, Preferences, StudySession, Task

# A block of time: (start, end)
TimeSlot = tuple[datetime, datetime]

# How much more urgent each priority is treated (see prioritize_tasks)
PRIORITY_WEIGHT = {"High": 2.0, "Medium": 1.0, "Low": 0.5}

# Session start times snap to this many minutes (e.g. 2:00, 2:15, 2:30)
SNAP_MINUTES = 15


@dataclass
class ScheduleResult:
    """What suggest_sessions returns."""

    sessions: list[StudySession] = field(default_factory=list)
    # task id -> minutes that couldn't be fit before the deadline
    unscheduled: dict[int, int] = field(default_factory=dict)


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


# ---------- Time math helpers ----------

def _round_up(dt: datetime, minutes: int = SNAP_MINUTES) -> datetime:
    """Round up to the next multiple of `minutes` (2:07 -> 2:15)."""
    dt = dt.replace(second=0, microsecond=0)
    extra = dt.minute % minutes
    return dt if extra == 0 else dt + timedelta(minutes=minutes - extra)


def _minutes(a: datetime, b: datetime) -> int:
    return int((b - a).total_seconds() // 60)


def subtract(slots: list[TimeSlot], busy: list[TimeSlot]) -> list[TimeSlot]:
    """Remove every busy range from the free slots. Returns what's left, sorted."""
    result = sorted(slots)
    for b_start, b_end in busy:
        next_result = []
        for s_start, s_end in result:
            if not overlaps(s_start, s_end, b_start, b_end):
                next_result.append((s_start, s_end))
                continue
            if s_start < b_start:
                next_result.append((s_start, b_start))
            if b_end < s_end:
                next_result.append((b_end, s_end))
        result = next_result
    return sorted(result)


# ---------- Free time ----------

def find_free_slots(
    events: list[Event],
    prefs: Preferences,
    start: date,
    end: date,
    busy_sessions: list[StudySession] | None = None,
    now: datetime | None = None,
) -> list[TimeSlot]:
    """
    Return open blocks inside your working hours between start and end
    (inclusive) that don't touch any event or busy session, keeping
    prefs.buffer_minutes clear on both sides of each. Anything before `now`
    is dropped. Blocks shorter than prefs.min_session_minutes are dropped.
    Days missing from prefs.work_hours are days off.
    """
    windows = []
    day = start
    while day <= end:
        hours = prefs.work_hours.get(day.weekday())
        if hours:
            w_start = datetime.combine(day, hours[0])
            w_end = datetime.combine(day, hours[1])
            if w_end > w_start:
                windows.append((w_start, w_end))
        day += timedelta(days=1)

    buffer = timedelta(minutes=prefs.buffer_minutes)
    busy = [
        (e.start - buffer, e.end + buffer)
        for e in expand_events(events, start - timedelta(days=1), end)
    ]
    busy += [(s.start - buffer, s.end + buffer) for s in (busy_sessions or [])]
    if now is not None:
        busy.append((datetime.min, _round_up(now)))

    free = subtract(windows, busy)
    # Snap starts to the 15-minute grid so suggestions land on clean times
    free = [(_round_up(s), e) for s, e in free]
    return [(s, e) for s, e in free if _minutes(s, e) >= prefs.min_session_minutes]


# ---------- Prioritizing ----------

def remaining_minutes(task: Task, sessions: list[StudySession]) -> int:
    """Estimated time minus time in accepted or completed sessions for this task."""
    planned = sum(
        _minutes(s.start, s.end) for s in sessions
        if s.task_id == task.id and s.status in ("accepted", "completed")
    )
    return max(task.estimated_minutes - planned, 0)


def priority_score(task: Task, now: datetime) -> float:
    """Lower = more urgent. Hours until deadline divided by the priority weight."""
    hours_left = (task.deadline - now).total_seconds() / 3600
    if hours_left <= 0:
        return hours_left  # overdue: negative, so always first (most overdue first)
    return hours_left / PRIORITY_WEIGHT.get(task.priority, 1.0)


def prioritize_tasks(tasks: list[Task], now: datetime) -> list[Task]:
    """
    Order open tasks by what should be worked on first. Completed and
    deleted tasks are skipped. Ties go to the earlier deadline.
    """
    open_tasks = [t for t in tasks if t.status == "open" and t.deleted_at is None]
    return sorted(open_tasks, key=lambda t: (priority_score(t, now), t.deadline))


# ---------- Suggestions ----------

def suggest_sessions(
    tasks: list[Task],
    events: list[Event],
    existing_sessions: list[StudySession],
    prefs: Preferences,
    now: datetime,
    days_ahead: int = 7,
) -> ScheduleResult:
    """
    Main entry point. Suggest study sessions for the next `days_ahead` days.

    - Accepted sessions count as busy time and as progress on their task.
    - Rejected sessions are never suggested again for that same task and time.
    - Overdue tasks still get scheduled, as soon as possible.
    - Sessions are between prefs.min_session_minutes and
      prefs.max_session_minutes long, with the buffer between them.
    """
    horizon_end = datetime.combine(now.date() + timedelta(days=days_ahead - 1), time.max)
    accepted = [s for s in existing_sessions if s.status == "accepted"]
    free = find_free_slots(events, prefs, now.date(), horizon_end.date(), accepted, now)

    buffer = timedelta(minutes=prefs.buffer_minutes)
    min_len, max_len = prefs.min_session_minutes, prefs.max_session_minutes
    result = ScheduleResult()

    for task in prioritize_tasks(tasks, now):
        if not task.plan_sessions:
            continue  # you said not to plan time for this one
        if task.priority == "Low" and not prefs.suggest_low_priority:
            continue  # Low priority: tracked as a deadline only (see Settings)
        remaining = remaining_minutes(task, existing_sessions)
        if remaining == 0:
            continue

        limit = task.deadline if task.deadline > now else horizon_end
        rejected = [(s.start, s.end) for s in existing_sessions if s.task_id == task.id and s.status in ("rejected", "missed")]
        days_used = {s.start.date() for s in accepted if s.task_id == task.id}

        exam = task_rules.is_exam(task.name) and task.deadline > now
        targets: list[date] = []
        if exam:
            window_start = max(now, task.deadline - timedelta(days=config.EXAM_PREP_DAYS))
            limit = task.deadline - timedelta(hours=config.EXAM_GAP_HOURS)
            rejected = rejected + [(datetime.min, window_start)]  # nothing before the prep window
            open_days = sorted({
                s.date() for s, e in subtract(free, rejected)
                if s < limit and _minutes(s, min(e, limit)) >= min_len
            })
            targets = exam_days(open_days, -(-remaining // max_len), days_used)

        while remaining > 0:
            chunk = max(min(remaining, max_len), min_len)
            view = subtract(free, rejected)
            placed = None
            if targets:
                placed = _place_on_day(view, targets.pop(0), limit, chunk, min_len)
            if placed is None:
                placed = _place(view, limit, chunk, min_len, days_used)
            if placed is None:
                result.unscheduled[task.id] = remaining
                break
            start, end = placed
            result.sessions.append(StudySession(task_id=task.id, start=start, end=end, status="suggested"))
            days_used.add(start.date())
            remaining -= _minutes(start, end)
            free = [
                (s, e) for s, e in subtract(free, [(start - buffer, end + buffer)])
                if _minutes(_round_up(s), e) >= min_len
            ]
            free = [(_round_up(s), e) for s, e in free]

    result.sessions.sort(key=lambda s: s.start)
    return result


def exam_days(open_days: list[date], sessions: int, days_used: set[date]) -> list[date]:
    """
    Pick which days get an exam review session: spread out, ending on the
    last open day before the exam. 4 sessions over 7 open days -> days 2, 4, 5, 7.
    Days that already have a session for this exam are skipped.
    """
    days = [d for d in open_days if d not in days_used]
    if not days or sessions <= 0:
        return []
    if sessions >= len(days):
        return days
    return [days[int((i + 1) * len(days) / sessions + 0.5) - 1] for i in range(sessions)]


def _place_on_day(free: list[TimeSlot], day: date, limit: datetime, chunk: int, min_len: int) -> TimeSlot | None:
    """Earliest spot for one session on a specific day (full length first, then shorter)."""
    for full_length in (True, False):
        for s, e in free:
            if s.date() != day or s >= limit:
                continue
            usable = _minutes(s, min(e, limit))
            usable -= usable % SNAP_MINUTES
            if usable >= chunk:
                return s, s + timedelta(minutes=chunk)
            if not full_length and usable >= min_len:
                return s, s + timedelta(minutes=usable)
    return None


def _place(free: list[TimeSlot], limit: datetime, chunk: int, min_len: int, days_used: set[date]) -> TimeSlot | None:
    """
    Pick the earliest spot for one session, trying in this order:
    1. a full-length session on a day this task doesn't have a session yet
    2. a full-length session on any day
    3. a shorter session (at least min_len) on a new day, then any day
    Shorter sessions are only used when nothing full-length fits before the deadline.
    """
    for full_length in (True, False):
        for fresh_days_only in (True, False):
            for s, e in free:
                if s >= limit:
                    break
                if fresh_days_only and s.date() in days_used:
                    continue
                usable = _minutes(s, min(e, limit))
                usable -= usable % SNAP_MINUTES
                if usable >= chunk:
                    return s, s + timedelta(minutes=chunk)
                if not full_length and usable >= min_len:
                    return s, s + timedelta(minutes=usable)
    return None
