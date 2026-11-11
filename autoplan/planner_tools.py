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


# ---------- Changing and removing things ----------

def find_events(query: str = "", day: str | None = None, now: datetime | None = None) -> dict:
    """Events matching a name, optionally on one day. Returns ids to use with edit_event / delete_event."""
    now = now or datetime.now()
    q = query.lower().strip()
    if day:
        d = parse_day(day, now)
        start, end = d, d
    else:
        start, end = now.date(), now.date() + timedelta(days=14)
    seen, rows = set(), []
    for e in scheduler.expand_events(db.list_events(), start, end):
        if q and q not in e.name.lower():
            continue
        if not day and e.id in seen:
            continue  # without a day, list each event once (next occurrence)
        seen.add(e.id)
        rows.append({
            "event_id": e.id,
            "name": e.name,
            "date": e.start.date().isoformat(),
            "when": f"{_fmt(e.start)}-{_fmt_time(e.end)}",
            "repeats_weekly": bool(db.get_event(e.id).recurrence_days),
            "from": e.source,
        })
    return {"events": rows[:15]} if rows else {"events": [], "note": f"No events matching '{query}'."}


def _google_service():
    from autoplan.integrations import google_auth

    return google_auth.calendar_service()


def edit_event(event_id: int, name: str | None = None, start: str | None = None, end: str | None = None,
               date: str | None = None, whole_series: bool = False) -> dict:
    """
    Rename or move an event. start/end are 'YYYY-MM-DD HH:MM' (or just 'HH:MM' to keep the day).
    For a weekly event, pass the date of the occurrence; whole_series=True changes every week instead.
    """
    event = db.get_event(event_id)
    if not event or event.deleted_at:
        return {"error": f"No event with id {event_id}. Use find_events first."}
    if event.source == "blackboard":
        return {"error": "Blackboard events can't be changed from here; they come from Blackboard."}

    day = datetime.fromisoformat(date).date() if date else (datetime.fromisoformat(start).date() if start and len(start) > 5 else (datetime.now().date() if event.recurrence_days else event.start.date()))

    def resolve(value: str | None, original: datetime) -> datetime:
        if not value:
            return datetime.combine(day, original.time())
        if len(value) <= 5:  # just a time
            return datetime.combine(day, datetime.strptime(value, "%H:%M").time())
        return datetime.fromisoformat(value)

    new_start = resolve(start, event.start)
    new_end = resolve(end, event.end) if end else new_start + event.duration
    new_name = name or event.name
    if new_end <= new_start:
        return {"error": "End has to be after start."}

    if event.source == "google":
        _google_change = __import__("autoplan.integrations.google_calendar", fromlist=["x"])
        _google_change.update_google_event(_google_service(), event.external_id, new_name, new_start, new_end)
        event.name, event.start, event.end = new_name, new_start, new_end
        db.upsert_external_event(event)
        return {"updated_in_google": new_name, "when": f"{_fmt(new_start)}-{_fmt_time(new_end)}"}

    if event.recurrence_days and not whole_series:
        # Move just this week's occurrence: skip it in the series, add a one-time copy
        db.skip_event_day(event.id, day)
        db.add_event(Event(name=new_name, start=new_start, end=new_end, category=event.category))
        return {"moved_one_day": new_name, "when": f"{_fmt(new_start)}-{_fmt_time(new_end)}",
                "note": "Other weeks are unchanged."}

    if event.recurrence_days:  # whole series: keep the series' first date, change times/name
        event.start = datetime.combine(event.start.date(), new_start.time())
        event.end = event.start + (new_end - new_start)
    else:
        event.start, event.end = new_start, new_end
    event.name = new_name
    problems = event.validate()
    if problems:
        return {"error": " ".join(problems)}
    db.update_event(event)
    return {"updated": event.name, "when": f"{_fmt_time(event.start)}-{_fmt_time(event.end)}" + (" every week" if event.recurrence_days else f" on {event.start:%a %b %d}")}


def delete_event(event_id: int, date: str | None = None, whole_series: bool = False) -> dict:
    """
    Remove an event. For a weekly event, pass the date to cancel just that day,
    or whole_series=True to remove every week. Deleted events can be restored on the Events page.
    """
    event = db.get_event(event_id)
    if not event or event.deleted_at:
        return {"error": f"No event with id {event_id}. Use find_events first."}
    if event.source == "blackboard":
        return {"error": "Blackboard events can't be deleted from here; they come from Blackboard."}

    if event.source == "google":
        _google_change = __import__("autoplan.integrations.google_calendar", fromlist=["x"])
        _google_change.delete_google_event(_google_service(), event.external_id)
        db.delete_event(event.id)
        return {"deleted_in_google": event.name, "when": _fmt(event.start)}

    if event.recurrence_days and not whole_series:
        if not date:
            return {"error": "This repeats weekly. Give the date to cancel one day, or whole_series=true to remove it entirely."}
        day = datetime.fromisoformat(date).date()
        db.skip_event_day(event.id, day)
        return {"cancelled_one_day": event.name, "date": day.strftime("%a %b %d"), "note": "Other weeks are unchanged."}

    db.delete_event(event.id)
    return {"deleted": event.name, "note": "Restorable from the Deleted tab on the Events page."}


def update_task(task_name: str, new_name: str | None = None, due: str | None = None,
                minutes: int | None = None, priority: str | None = None, plan_time: bool | None = None) -> dict:
    task = find_task(task_name)
    if not task:
        return {"error": f"No open task matching '{task_name}'."}
    if new_name:
        task.name = new_name
    if due:
        task.deadline = datetime.fromisoformat(due if " " in due or "T" in due else f"{due} 23:59")
    if minutes:
        task.estimated_minutes = int(minutes)
    if priority in config.PRIORITIES:
        task.priority = priority
    if plan_time is not None:
        task.plan_sessions = bool(plan_time)
    problems = task.validate()
    if problems:
        return {"error": " ".join(problems)}
    db.update_task(task)
    return {"updated": task.name, "due": _fmt(task.deadline), "estimate_minutes": task.estimated_minutes,
            "priority": task.priority, "plan_time": task.plan_sessions}


def delete_task(task_name: str) -> dict:
    task = find_task(task_name, include_done=True)
    if not task:
        return {"error": f"No task matching '{task_name}'."}
    db.delete_task(task.id)
    return {"deleted": task.name, "note": "Restorable from the Deleted tab on the Tasks page."}


def cancel_study_session(task_name: str, date: str | None = None) -> dict:
    """Remove an upcoming planned study session (the next one, or the one on a given date)."""
    task = find_task(task_name)
    if not task:
        return {"error": f"No open task matching '{task_name}'."}
    upcoming = [s for s in _accepted() if s.task_id == task.id and s.end > datetime.now()]
    if date:
        d = datetime.fromisoformat(date).date()
        upcoming = [s for s in upcoming if s.start.date() == d]
    if not upcoming:
        return {"error": f"No upcoming study session for {task.name}" + (f" on {date}" if date else "") + "."}
    s = min(upcoming, key=lambda x: x.start)
    db.set_session_status(s.id, "rejected")
    return {"cancelled": f"{task.name} {_fmt(s.start)}-{_fmt_time(s.end)}",
            "note": "It's also removed from Google Calendar on the next sync."}


# ---------- Blackboard details, prep and grades ----------

def get_assignment_details(task_name: str) -> dict:
    """Instructions, files and points from Blackboard, plus the saved prep plan if there is one."""
    task = find_task(task_name, include_done=True)
    if not task:
        return {"error": f"No task matching '{task_name}'."}
    details = db.get_bb_assignment_for_task(task.id)
    out = {"task": task.name, "due": _fmt(task.deadline)}
    if details:
        out.update({
            "course": details.get("course_code"),
            "points": details.get("points"),
            "instructions": (details.get("instructions") or "")[:3000],
            "files": [f["name"] for f in details.get("attachments", [])],
        })
    else:
        out["note"] = "No Blackboard details for this task (not matched, or Blackboard isn't connected)."
    plan = db.get_prep(task.id)
    if plan:
        out["prep_plan"] = {k: plan[k] for k in ("summary", "requirements", "materials", "steps", "estimated_minutes") if k in plan}
    return out


def prep_assignment(task_name: str) -> dict:
    """Run the Prep button for a task (Claude reads instructions/files and plans the work)."""
    from autoplan import prep

    task = find_task(task_name)
    if not task:
        return {"error": f"No open task matching '{task_name}'."}
    plan = prep.run(task.id)
    return {"task": task.name, **{k: plan[k] for k in ("summary", "requirements", "materials", "steps", "estimated_minutes") if k in plan}}


def _course_match(course: str) -> dict | None:
    key = course.lower().replace(" ", "")
    return next((c for c in db.list_bb_courses() if key in (c["code"] + c["name"]).lower().replace(" ", "")), None)


def get_grades(course: str | None = None) -> dict:
    """Current standing per course: Blackboard plus Will's own entries and corrections, weighted if set up."""
    from autoplan import grades

    match = _course_match(course) if course else None
    if course and not match:
        return {"courses": [], "note": f"No course matching '{course}'."}
    rows = []
    for c in db.list_bb_courses():
        if match and c["id"] != match["id"]:
            continue
        s = grades.standing(c["id"])
        recent = [i for i in s["items"] if not i["is_total"] and not i["ignored"] and i["score"] is not None][-3:]
        rows.append({
            "course": c["code"], "name": c["name"], "percent": s["shown_percent"], "letter": s["shown_letter"],
            "how": "Blackboard total" if s["mode"] == "blackboard_total" else ("weighted" if s["weighted"] else "points"),
            "blackboard_total": s["blackboard_total"], "graded": s["graded_count"], "remaining": s["remaining_count"],
            "maybe_out_of_date": [i["name"] for i in s["stale"]],
            "recent": [f"{i['name']}: {i['score']:g}/{i['possible']:g}" for i in recent],
        })
    if not rows:
        return {"courses": [], "note": "No classes found. Blackboard may not be connected yet."}
    return {"courses": rows}


def grade_needed(course: str, target_letter: str = "B+") -> dict:
    """What average he needs on the remaining work to finish a course with a target letter."""
    from autoplan import grades

    c = _course_match(course)
    if not c:
        return {"error": f"No course matching '{course}'."}
    if target_letter not in dict(grades.LETTER_CUTOFFS):
        return {"error": f"Use a letter like {', '.join(l for l, _ in grades.LETTER_CUTOFFS[:6])}."}
    s = grades.standing(c["id"])
    need = grades.needed_for(s["items"], grades.cutoff(target_letter), s["weights"])
    return {"course": c["code"], "target": target_letter, "weighted": s["weighted"], **need}


def log_grade(course: str, item: str, score: float, possible: float, category: str | None = None) -> dict:
    """
    Record a grade Will tells you about. If it matches a Blackboard item in that class,
    it corrects that item; otherwise it's added as his own entry.
    """
    from autoplan import grades

    c = _course_match(course)
    if not c:
        return {"error": f"No course matching '{course}'."}
    bb = db.list_bb_grades(c["id"])
    names = {i["name"].lower(): i for i in bb if not i["is_total"]}
    q = item.lower().strip()
    hit = next((i for n, i in names.items() if q == n or q in n or n in q), None)
    if hit is None:
        close = difflib.get_close_matches(q, list(names), n=1, cutoff=0.75)
        hit = names[close[0]] if close else None
    if hit:
        db.set_grade_override(hit["id"], score=float(score), possible=float(possible), **({"category": category} if category else {}))
        return {"added": f"{c['code']} {hit['name']}: {score:g}/{possible:g}", "note": "Corrected the Blackboard item."}
    db.add_grade_entry(c["id"], item, float(score), float(possible), category or grades.guess_category(item))
    return {"added": f"{c['code']} {item}: {score:g}/{possible:g}"}


# ---------- Workouts ----------

def log_workout(log_text: str) -> dict:
    """Save a workout given in /log format: 'push; bench 240 4x4 @8; incline db 70 3x10 @9'."""
    from autoplan.workout import actions

    return {"saved": actions.handle_log(log_text)}


def log_recovery(text: str) -> dict:
    """Save today's recovery check-in, e.g. 'sleep 7 energy 3 sore low bw 165 elbow 2'."""
    from autoplan.workout import actions

    return {"saved": actions.handle_recovery(text)}


def get_workout_plan(split_day: str | None = None) -> dict:
    """
    Today's workout as structured data. Pass split_day to preview another day's plan
    without changing anything (use set_workout_day to actually switch today).
    """
    from autoplan.workout import digest, engine, recovery, settings, store

    if split_day:
        day = _split_name(split_day)
        if not day:
            return {"error": f"Split days are {', '.join(dict.fromkeys(settings.SPLIT))}."}
        check = recovery.assess(store.get_recovery(datetime.now().date()))
        plan = engine.apply_recovery(engine.plan_for(day), check["adjust"]) if day != "Rest" else []
        return {"preview_of": day, "exercises": [p.__dict__ for p in plan], "readiness": check["status"]}
    d = digest.build(use_llm=False)
    return {k: d[k] for k in ("split_day", "split_reason", "readiness", "exercises", "progression", "next_split_day")}


def _split_name(value: str) -> str | None:
    from autoplan.workout import settings

    days = {d.lower(): d for d in settings.SPLIT}
    return days.get(value.strip().lower())


def set_workout_day(split_day: str) -> dict:
    """Make today a given split day (e.g. when Will says 'today is legs'). Returns today's plan for it."""
    from autoplan.workout import store

    day = _split_name(split_day)
    if not day:
        return {"error": f"Unknown split day '{split_day}'."}
    store.set_split_override(datetime.now().date(), day)
    plan = get_workout_plan()
    return {"workout_day": day, "exercises": plan["exercises"], "readiness": plan["readiness"]["status"]}


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
    {"name": "find_events", "description": "Look up events by name and/or day to get their event_id before editing or deleting.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "Words in the event name; empty for all"},
         "day": DAY}}},
    {"name": "edit_event", "description": "Rename or move an event (needs event_id from find_events). For a weekly event, pass the occurrence date; set whole_series only if Will wants every week changed. Google Calendar events are changed in Google too.",
     "input_schema": {"type": "object", "properties": {
         "event_id": {"type": "integer"},
         "name": {"type": "string"},
         "start": {"type": "string", "description": "'YYYY-MM-DD HH:MM', or 'HH:MM' to keep the day"},
         "end": {"type": "string", "description": "'YYYY-MM-DD HH:MM' or 'HH:MM'; leave out to keep the same length"},
         "date": {"type": "string", "description": "YYYY-MM-DD of the occurrence, for weekly events"},
         "whole_series": {"type": "boolean"}}, "required": ["event_id"]}},
    {"name": "delete_event", "description": "Delete or cancel an event (needs event_id from find_events). For a weekly event, pass the date to cancel just that day; whole_series removes every week. Google Calendar events are deleted in Google too.",
     "input_schema": {"type": "object", "properties": {
         "event_id": {"type": "integer"},
         "date": {"type": "string", "description": "YYYY-MM-DD, for cancelling one day of a weekly event"},
         "whole_series": {"type": "boolean"}}, "required": ["event_id"]}},
    {"name": "update_task", "description": "Change a task's name, due date, estimate, priority, or whether to plan study time for it.",
     "input_schema": {"type": "object", "properties": {
         "task_name": {"type": "string"},
         "new_name": {"type": "string"},
         "due": {"type": "string", "description": "'YYYY-MM-DD HH:MM' or 'YYYY-MM-DD'"},
         "minutes": {"type": "integer"},
         "priority": {"type": "string", "enum": ["High", "Medium", "Low"]},
         "plan_time": {"type": "boolean"}}, "required": ["task_name"]}},
    {"name": "delete_task", "description": "Delete a task (restorable in the app).",
     "input_schema": {"type": "object", "properties": {"task_name": {"type": "string"}}, "required": ["task_name"]}},
    {"name": "cancel_study_session", "description": "Remove a planned study session for a task (the next one, or the one on a date).",
     "input_schema": {"type": "object", "properties": {
         "task_name": {"type": "string"},
         "date": {"type": "string", "description": "YYYY-MM-DD"}}, "required": ["task_name"]}},
    {"name": "get_assignment_details", "description": "Blackboard instructions, files and points for a task, plus its saved prep plan.",
     "input_schema": {"type": "object", "properties": {"task_name": {"type": "string"}}, "required": ["task_name"]}},
    {"name": "prep_assignment", "description": "Have Claude read an assignment's instructions and files and make a plan (requirements, materials, steps, time). Use when Will asks what he needs or how to approach an assignment. Never write the assignment for him.",
     "input_schema": {"type": "object", "properties": {"task_name": {"type": "string"}}, "required": ["task_name"]}},
    {"name": "log_grade", "description": "Record a grade Will got (e.g. 'got 18/20 on quiz 7 in ISTM 4205'). Corrects the matching Blackboard item or adds it.",
     "input_schema": {"type": "object", "properties": {
         "course": {"type": "string"}, "item": {"type": "string"},
         "score": {"type": "number"}, "possible": {"type": "number"},
         "category": {"type": "string", "enum": ["Exams", "Quizzes", "Assignments", "Projects", "Participation", "Other"]}},
      "required": ["course", "item", "score", "possible"]}},
    {"name": "get_grades", "description": "Current grade per course (Blackboard plus Will's own entries, weighted if he set syllabus weights). Mention any items flagged as maybe out of date.",
     "input_schema": {"type": "object", "properties": {"course": {"type": "string"}}}},
    {"name": "grade_needed", "description": "What average Will needs on the remaining work to finish a course with a target letter grade.",
     "input_schema": {"type": "object", "properties": {
         "course": {"type": "string"}, "target_letter": {"type": "string", "description": "e.g. A, A-, B+"}}, "required": ["course"]}},
    {"name": "log_workout", "description": "Log a workout Will describes. Convert it to this format: 'push; bench 240 4x4 @8; incline db 70 3x10 @9; pullups bw 2x8'. Split day first (push/pull/legs/upper/lower), then exercise weight setsxreps @rpe, separated by semicolons. Reps per set like '4,4,4,3' also work.",
     "input_schema": {"type": "object", "properties": {"log_text": {"type": "string"}}, "required": ["log_text"]}},
    {"name": "log_recovery", "description": "Save today's recovery check-in. Format: 'sleep 7.5 quality 4 energy 4 sore low bw 165 elbow 0 joint 0 alcohol no lowcal no sick none' (include only what he said).",
     "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "get_workout_plan", "description": "Today's workout plan: split day, readiness, exercises with sets/reps/target loads/RPE, progress toward goals. Pass split_day to preview another day without changing anything. Never invent numbers.",
     "input_schema": {"type": "object", "properties": {"split_day": {"type": "string", "description": "Push, Pull, Legs, Upper, Lower or Rest"}}}},
    {"name": "set_workout_day", "description": "Set today's split day when Will says what he's training (e.g. 'today is legs'). Returns the plan for that day.",
     "input_schema": {"type": "object", "properties": {"split_day": {"type": "string"}}, "required": ["split_day"]}},
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
    "find_events": find_events,
    "edit_event": edit_event,
    "delete_event": delete_event,
    "update_task": update_task,
    "delete_task": delete_task,
    "cancel_study_session": cancel_study_session,
    "get_assignment_details": get_assignment_details,
    "prep_assignment": prep_assignment,
    "get_grades": get_grades,
    "grade_needed": grade_needed,
    "log_grade": log_grade,
    "log_workout": log_workout,
    "log_recovery": log_recovery,
    "get_workout_plan": get_workout_plan,
    "set_workout_day": set_workout_day,
}


# Changes that should show up on Google Calendar (and your phone) right away
CALENDAR_WRITES = {"add_event", "edit_event", "delete_event", "schedule_study_session", "cancel_study_session"}


def run_tool(name: str, args: dict) -> dict:
    """Run one tool by name. Errors come back as {'error': ...} so Claude can explain or retry."""
    fn = FUNCTIONS.get(name)
    if fn is None:
        return {"error": f"Unknown tool {name}"}
    try:
        result = fn(**(args or {}))
        if name in CALENDAR_WRITES and isinstance(result, dict) and not result.get("error"):
            from autoplan.integrations import sync

            sync.push_to_google()
        return result
    except (ValueError, TypeError) as e:
        return {"error": f"Bad input: {e}"}
    except Exception as e:  # e.g. Google rejected the change
        return {"error": f"{type(e).__name__}: {e}"}
