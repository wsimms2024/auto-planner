"""
Planner tools: plain functions that answer questions about your schedule.

The Telegram assistant hands these to Claude as "tools", so Claude looks up
real data instead of guessing. Each function returns a dict that Claude reads.
All reads and writes go through db.py; the scheduling math is scheduler.py.

To give the assistant a new ability, write a function here and add it to TOOLS.
"""

import difflib
from datetime import date, datetime, time, timedelta

from autoplan import config, db, scheduler, task_rules
from autoplan.models import Event, StudySession, Task


# ---------- Helpers ----------

def _fmt(dt: datetime) -> str:
    return dt.strftime("%a %b %d, %I:%M %p").replace(" 0", " ")


def _fmt_time(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def parse_day(value: str | None, now: datetime) -> date:
    """'today', 'tomorrow', a weekday name, or YYYY-MM-DD."""
    v = (value or "today").strip().lower()
    if v == "today":
        return now.date()
    if v == "tomorrow":
        return now.date() + timedelta(days=1)
    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for i, name in enumerate(days):
        if v in (name, name[:3]):
            return now.date() + timedelta(days=(i - now.weekday()) % 7)
    return date.fromisoformat(v)


def find_task(name: str, include_done: bool = False) -> Task | None:
    """Best match for a task name: exact-ish substring first, then fuzzy."""
    tasks = [t for t in db.list_tasks() if include_done or t.status == "open"]
    q = name.lower().strip()
    hits = [t for t in tasks if q in t.name.lower()]
    if hits:
        return min(hits, key=lambda t: t.deadline)
    names = {t.name.lower(): t for t in tasks}
    close = difflib.get_close_matches(q, list(names), n=1, cutoff=0.45)
    return names[close[0]] if close else None


def _blocks_for(day: date) -> list[dict]:
    names = {t.id: t.name for t in db.list_tasks(include_deleted=True)}
    items = [
        {"start": _fmt_time(e.start), "end": _fmt_time(e.end), "what": e.name, "type": e.category}
        for e in scheduler.expand_events(db.list_events(), day, day)
    ]
    for s in db.list_active_sessions():
        if s.status in ("accepted", "completed") and s.start.date() == day:
            items.append({"start": _fmt_time(s.start), "end": _fmt_time(s.end),
                          "what": f"Study: {names.get(s.task_id, 'task')}", "type": "study"})
    return sorted(items, key=lambda i: datetime.strptime(i["start"], "%I:%M %p"))


def _accepted() -> list[StudySession]:
    return [s for s in db.list_active_sessions() if s.status == "accepted"]


# ---------- Tools ----------

def get_schedule(day: str = "today", now: datetime | None = None) -> dict:
    """Everything on one day plus the free gaps."""
    now = now or datetime.now()
    d = parse_day(day, now)
    prefs = db.get_preferences()
    free = scheduler.find_free_slots(db.list_events(), prefs, d, d, _accepted(), now if d == now.date() else None)
    return {
        "date": d.strftime("%A, %B %d"),
        "items": _blocks_for(d),
        "free_time": [f"{_fmt_time(s)}-{_fmt_time(e)}" for s, e in free],
    }


def get_tasks(days_ahead: int = 7, course: str | None = None, include_done: bool = False, now: datetime | None = None) -> dict:
    """Open tasks due in the next N days (and anything overdue), optionally for one course."""
    now = now or datetime.now()
    cutoff = now + timedelta(days=days_ahead)
    sessions = db.list_active_sessions()
    rows = []
    for t in db.list_tasks():
        if t.status == "done" and not include_done:
            continue
        if t.deadline > cutoff:
            continue
        if course and course.lower().replace(" ", "") not in t.name.lower().replace(" ", ""):
            continue
        rows.append({
            "task": t.name,
            "due": _fmt(t.deadline),
            "overdue": t.status == "open" and t.deadline < now,
            "estimate_minutes": t.estimated_minutes,
            "minutes_still_unplanned": scheduler.remaining_minutes(t, sessions) if t.plan_sessions else 0,
            "priority": t.priority,
            "status": t.status,
        })
    note = None
    if course and not rows:
        note = (f"No tasks matched '{course}'. Blackboard task names don't always include the course code, "
                "so try get_tasks without a course filter.")
    return {"tasks": rows, "note": note} if note else {"tasks": rows}


def plan_task(task_name: str, now: datetime | None = None) -> dict:
    """When to work on a task: suggested sessions plus the latest sensible start."""
    now = now or datetime.now()
    task = find_task(task_name)
    if not task:
        return {"error": f"No open task matching '{task_name}'."}
    prefs = db.get_preferences()
    sessions = db.list_active_sessions(now)
    remaining = scheduler.remaining_minutes(task, sessions)
    planned = [s for s in sessions if s.task_id == task.id and s.status == "accepted" and s.end > now]
    # Ignore the Low priority / plan-off filters here: you asked about this task specifically
    forced = Task(**{**task.__dict__, "plan_sessions": True, "priority": "High" if task.priority == "Low" else task.priority})
    result = scheduler.suggest_sessions([forced], db.list_events(), sessions, prefs, now, max(prefs.planning_days, 14))
    latest_start = task.deadline - timedelta(minutes=remaining + prefs.buffer_minutes) if remaining else None
    return {
        "task": task.name,
        "due": _fmt(task.deadline),
        "estimate_minutes": task.estimated_minutes,
        "minutes_left_to_plan": remaining,
        "already_planned": [f"{_fmt(s.start)}-{_fmt_time(s.end)}" for s in planned],
        "suggested_sessions": [{"start": s.start.isoformat(timespec="minutes"), "end": s.end.isoformat(timespec="minutes"),
                                "label": f"{_fmt(s.start)}-{_fmt_time(s.end)}"} for s in result.sessions],
        "wont_fit_minutes": result.unscheduled.get(task.id, 0),
        "start_no_later_than": _fmt(latest_start) if latest_start else None,
    }


def suggest_gym_time(day: str = "today", now: datetime | None = None) -> dict:
    """Best gym slot on a day, using your gym preferences and what's already scheduled."""
    now = now or datetime.now()
    d = parse_day(day, now)
    prefs = db.get_preferences()
    gym_prefs = replace_hours(prefs, d, prefs.gym_earliest, prefs.gym_latest)
    gym_prefs.min_session_minutes = prefs.gym_minutes
    free = scheduler.find_free_slots(db.list_events(), gym_prefs, d, d, _accepted(), now if d == now.date() else None)
    options = [(s, s + timedelta(minutes=prefs.gym_minutes)) for s, e in free]
    already = [b for b in _blocks_for(d) if b["type"] == "gym"]
    return {
        "date": d.strftime("%A, %B %d"),
        "usual_gym_day": d.weekday() in prefs.gym_days,
        "already_scheduled": already,
        "gym_minutes": prefs.gym_minutes,
        "options": [{"start": s.isoformat(timespec="minutes"), "end": e.isoformat(timespec="minutes"),
                     "label": f"{_fmt_time(s)}-{_fmt_time(e)}"} for s, e in options[:4]],
        "note": None if options else "No gap long enough in your gym window. Try a shorter session or another day.",
    }


def replace_hours(prefs, day: date, start: time, end: time):
    """Copy of prefs whose window on `day` is start-end (used for gym)."""
    from copy import deepcopy

    p = deepcopy(prefs)
    p.work_hours = {day.weekday(): (start, end)}
    return p


def add_task(name: str, due: str, minutes: int | None = None, priority: str | None = None) -> dict:
    """Create a task. due is 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DD' (11:59 PM)."""
    deadline = datetime.fromisoformat(due if " " in due or "T" in due else f"{due} 23:59")
    g_priority, g_minutes, plan = task_rules.guess(name)
    task = Task(name=name, deadline=deadline, estimated_minutes=int(minutes or g_minutes),
                priority=priority if priority in config.PRIORITIES else g_priority, plan_sessions=plan)
    task.id = db.add_task(task)
    return {"added": task.name, "due": _fmt(deadline), "estimate_minutes": task.estimated_minutes, "priority": task.priority}


def mark_task_done(task_name: str) -> dict:
    task = find_task(task_name)
    if not task:
        return {"error": f"No open task matching '{task_name}'."}
    db.set_task_status(task.id, "done")
    return {"marked_done": task.name}


def add_event(name: str, start: str, end: str, category: str = "other", allow_overlap: bool = False) -> dict:
    """One-time event, e.g. a gym session. start/end are 'YYYY-MM-DD HH:MM'."""
    event = Event(name=name, start=datetime.fromisoformat(start), end=datetime.fromisoformat(end),
                  category=category if category in config.EVENT_CATEGORIES else "other")
    problems = event.validate()
    if problems:
        return {"error": " ".join(problems)}
    conflicts = scheduler.find_conflicts(event, db.list_events())
    if conflicts and not allow_overlap:
        return {"error": "Overlaps with " + ", ".join(f"{o.name} ({_fmt_time(o.start)}-{_fmt_time(o.end)})" for _, o in conflicts[:3]),
                "hint": "Pick another time, or retry with allow_overlap if Will says so."}
    db.add_event(event)
    return {"added": name, "when": f"{_fmt(event.start)}-{_fmt_time(event.end)}"}


def schedule_study_session(task_name: str, start: str, end: str) -> dict:
    """Put a study session on the calendar (it syncs to Google with a reminder)."""
    task = find_task(task_name)
    if not task:
        return {"error": f"No open task matching '{task_name}'."}
    s, e = datetime.fromisoformat(start), datetime.fromisoformat(end)
    clash = [b for b in scheduler.expand_events(db.list_events(), s.date(), s.date()) if scheduler.overlaps(s, e, b.start, b.end)]
    clash += [x for x in _accepted() if scheduler.overlaps(s, e, x.start, x.end)]
    if clash:
        return {"error": f"That time overlaps with {getattr(clash[0], 'name', 'another study session')}."}
    db.add_study_session(StudySession(task_id=task.id, start=s, end=e, status="accepted"))
    return {"scheduled": task.name, "when": f"{_fmt(s)}-{_fmt_time(e)}"}


# ---------- Tool definitions for Claude ----------

DAY = {"type": "string", "description": "'today', 'tomorrow', a weekday name, or YYYY-MM-DD"}

TOOLS = [
    {"name": "get_schedule", "description": "Events, study sessions and free time for one day.",
     "input_schema": {"type": "object", "properties": {"day": DAY}}},
    {"name": "get_tasks", "description": "Open tasks/assignments due soon, including overdue ones. Filter by course code or name if asked about a class.",
     "input_schema": {"type": "object", "properties": {
         "days_ahead": {"type": "integer", "description": "How many days ahead to look (default 7)"},
         "course": {"type": "string", "description": "Course code or words in the task name, e.g. 'ISTM 4205' or 'quiz'"},
         "include_done": {"type": "boolean"}}}},
    {"name": "plan_task", "description": "When Will should work on a specific task: free slots that fit before the deadline and the latest sensible start.",
     "input_schema": {"type": "object", "properties": {"task_name": {"type": "string"}}, "required": ["task_name"]}},
    {"name": "suggest_gym_time", "description": "Best times to go to the gym on a day, using his gym preferences and schedule.",
     "input_schema": {"type": "object", "properties": {"day": DAY}}},
    {"name": "add_task", "description": "Add a new task/assignment.",
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string"},
         "due": {"type": "string", "description": "'YYYY-MM-DD HH:MM' or 'YYYY-MM-DD'"},
         "minutes": {"type": "integer", "description": "Estimated minutes; leave out to guess"},
         "priority": {"type": "string", "enum": ["High", "Medium", "Low"]}}, "required": ["name", "due"]}},
    {"name": "mark_task_done", "description": "Mark a task as finished.",
     "input_schema": {"type": "object", "properties": {"task_name": {"type": "string"}}, "required": ["task_name"]}},
    {"name": "add_event", "description": "Add a one-time event such as a gym session or appointment. Only when Will asks to put something on the calendar.",
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string"},
         "start": {"type": "string", "description": "YYYY-MM-DD HH:MM"},
         "end": {"type": "string", "description": "YYYY-MM-DD HH:MM"},
         "category": {"type": "string", "enum": config.EVENT_CATEGORIES},
         "allow_overlap": {"type": "boolean"}}, "required": ["name", "start", "end"]}},
    {"name": "schedule_study_session", "description": "Put a study session for a task on the calendar. Only when Will agrees to a time.",
     "input_schema": {"type": "object", "properties": {
         "task_name": {"type": "string"},
         "start": {"type": "string", "description": "YYYY-MM-DD HH:MM"},
         "end": {"type": "string", "description": "YYYY-MM-DD HH:MM"}}, "required": ["task_name", "start", "end"]}},
]

FUNCTIONS = {
    "get_schedule": get_schedule,
    "get_tasks": get_tasks,
    "plan_task": plan_task,
    "suggest_gym_time": suggest_gym_time,
    "add_task": add_task,
    "mark_task_done": mark_task_done,
    "add_event": add_event,
    "schedule_study_session": schedule_study_session,
}


def run_tool(name: str, args: dict) -> dict:
    """Run one tool by name. Errors come back as {'error': ...} so Claude can explain or retry."""
    fn = FUNCTIONS.get(name)
    if fn is None:
        return {"error": f"Unknown tool {name}"}
    try:
        return fn(**(args or {}))
    except (ValueError, TypeError) as e:
        return {"error": f"Bad input: {e}"}
