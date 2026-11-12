"""
Texts the bot sends on its own: the morning briefing and "did you do it?"
follow-ups after study sessions. Plain rules, no AI, so they're free.
"""

from datetime import date, datetime, timedelta

from autoplan import db, planner_tools, scheduler


def _t(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def morning_text(now: datetime | None = None) -> str:
    now = now or datetime.now()
    today = now.date()
    sched = planner_tools.get_schedule("today", now)
    lines = [f"Morning. Here's {now:%A}:"]

    if sched["items"]:
        lines += [f"{i['start']} {i['what']}" for i in sched["items"]]
    else:
        lines.append("Nothing on the calendar.")

    from autoplan import class_schedule

    in_class = class_schedule.today_in_class(today)
    if in_class:
        lines.append("")
        lines.append("In class:")
        for s in in_class:
            extra = (f" (read: {s['readings']})" if s["readings"] else "") + (f" [due: {s['due']}]" if s["due"] else "")
            lines.append(f"{s['course_code']}: {s['topic'] or 'class'}{extra}")

    tasks = [t for t in db.list_tasks() if t.status == "open"]
    overdue = [t for t in tasks if t.deadline < now]
    soon = [t for t in tasks if now <= t.deadline <= now + timedelta(days=2)]
    if overdue or soon:
        lines.append("")
        lines += [f"Overdue: {t.name}" for t in overdue]
        lines += [f"Due {_due(t.deadline, today)}: {t.name}" for t in soon]

    prefs = db.get_preferences()
    sessions = db.list_active_sessions(now)
    result = scheduler.suggest_sessions(db.list_tasks(), db.list_events(), sessions, prefs, now, 1)
    names = {t.id: t.name for t in tasks}
    todays = [s for s in result.sessions if s.start.date() == today]
    if todays:
        lines.append("")
        lines.append("Good times to study today:")
        lines += [f"{_t(s.start)}-{_t(s.end)} {names.get(s.task_id, '')}" for s in todays[:3]]

    to_confirm = db.list_exam_candidates("pending")
    if to_confirm:
        lines.append("")
        lines.append(f"Found {len(to_confirm)} exam date{'s' if len(to_confirm) != 1 else ''} to confirm: "
                     + ", ".join(f"{c['name']} {date.fromisoformat(c['date']):%b %d}".replace(' 0', ' ') for c in to_confirm[:3])
                     + ". Reply \"confirm them\" or check the Sync page.")

    if today.weekday() in prefs.gym_days:
        gym = planner_tools.suggest_gym_time("today", now)
        if not gym["already_scheduled"] and gym["options"]:
            lines.append("")
            lines.append(f"Gym fits at {gym['options'][0]['label']}.")

    return "\n".join(lines)


def _due(deadline: datetime, today: date) -> str:
    if deadline.date() == today:
        return f"today {_t(deadline)}"
    if deadline.date() == today + timedelta(days=1):
        return f"tomorrow {_t(deadline)}"
    return deadline.strftime("%a ") + _t(deadline)


def followup_question(session_id: int) -> str | None:
    s = db.get_study_session(session_id)
    if not s:
        return None
    names = {t.id: t.name for t in db.list_tasks(include_deleted=True)}
    return f"Did you get your {_t(s.start)}-{_t(s.end)} session done for {names.get(s.task_id, 'your task')}?"


def answer_followup(session_id: int, answer: str, now: datetime | None = None) -> str:
    """
    answer: 'yes' (session happened), 'finished' (task fully done), 'no' (missed it).
    Missed sessions free up their minutes so the scheduler finds a new time.
    """
    now = now or datetime.now()
    s = db.get_study_session(session_id)
    if not s:
        return "I can't find that session anymore."
    names = {t.id: t.name for t in db.list_tasks(include_deleted=True)}
    name = names.get(s.task_id, "that task")
    if answer == "finished":
        db.set_session_status(session_id, "completed")
        db.set_task_status(s.task_id, "done")
        return f"Nice. Marked {name} done."
    if answer == "yes":
        db.set_session_status(session_id, "completed")
        task = next((t for t in db.list_tasks() if t.id == s.task_id), None)
        left = scheduler.remaining_minutes(task, db.list_active_sessions(now)) if task else 0
        return f"Good. {name} still has about {left} minutes left to plan." if left else f"Good. That covers the time you estimated for {name}."
    db.set_session_status(session_id, "missed")
    plan = planner_tools.plan_task(name, now)
    if plan.get("suggested_sessions"):
        nxt = plan["suggested_sessions"][0]["label"]
        return f"No problem. Next open slot for {name}: {nxt}. Reply \"book it\" and I'll add it."
    return f"Got it. There's no free slot for {name} before it's due. Worth freeing up some time."
