"""
Small Streamlit helpers shared by the pages: formatting, the week picker,
the weekly grid, and one-time success messages.
"""

from datetime import date, datetime, timedelta

import streamlit as st

from autoplan import db, scheduler
from autoplan.week_view import Block, build_week_grid, style_week_grid, week_start_for

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# ---------- Formatting ----------

def fmt_time(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def fmt_dt(dt: datetime) -> str:
    return f"{dt:%a %b} {dt.day}, {fmt_time(dt)}"


def fmt_minutes(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h}h {m}m"
    return f"{h}h" if h else f"{m}m"


def fmt_due(deadline: datetime, now: datetime) -> str:
    """Human-friendly 'due' text, e.g. 'in 2 days' or '3 hours overdue'."""
    delta = deadline - now
    hours = abs(delta.total_seconds()) / 3600
    if hours < 24:
        n, unit = int(hours), "hour"
    else:
        n, unit = round(hours / 24), "day"
    amount = f"{n} {unit}{'s' if n != 1 else ''}"
    return f"in {amount}" if delta.total_seconds() >= 0 else f"{amount} overdue"


def describe_event(event) -> str:
    """One-line summary used in dropdowns and lists."""
    times = f"{fmt_time(event.start)}-{fmt_time(event.end)}"
    if event.is_recurring:
        days = "/".join(DAY_SHORT[d] for d in event.recurrence_days)
        return f"{event.name} · {days} {times} until {event.recurrence_until:%b} {event.recurrence_until.day}"
    return f"{event.name} · {event.start:%a %b} {event.start.day} {times}"


# ---------- Flash messages (survive a rerun) ----------

def flash(message: str) -> None:
    st.session_state["_flash"] = message


def show_flash() -> None:
    message = st.session_state.pop("_flash", None)
    if message:
        st.success(message)


# ---------- Calendar data ----------

def task_names() -> dict[int, str]:
    return {t.id: t.name for t in db.list_tasks(include_deleted=True)}


def get_suggestions(now: datetime | None = None) -> scheduler.ScheduleResult:
    """Run the scheduler on everything saved. Fast enough to run on every page load."""
    now = now or datetime.now()
    prefs = db.get_preferences()
    return scheduler.suggest_sessions(
        db.list_tasks(), db.list_events(), db.list_active_sessions(now), prefs, now, prefs.planning_days
    )


def accepted_sessions() -> list:
    return [s for s in db.list_active_sessions() if s.status == "accepted"]


def load_blocks(start: date, end: date, suggestions: list | None = None) -> list[Block]:
    """Events, accepted study sessions, and optional suggestions between two dates."""
    blocks = [
        Block(e.name, e.start, e.end, e.category)
        for e in scheduler.expand_events(db.list_events(), start, end)
    ]
    names = task_names()
    for s in accepted_sessions():
        if start <= s.start.date() <= end:
            blocks.append(Block(f"Study: {names.get(s.task_id, 'task')}", s.start, s.end, "study"))
    for s in suggestions or []:
        if start <= s.start.date() <= end:
            blocks.append(Block(f"Suggested: {names.get(s.task_id, 'task')}", s.start, s.end, "suggested"))
    return sorted(blocks, key=lambda b: b.start)


# ---------- Week grid ----------

def week_picker(key: str) -> date:
    """Prev / This week / Next buttons. Returns the Monday being viewed."""
    state_key = f"{key}_week"
    if state_key not in st.session_state:
        st.session_state[state_key] = week_start_for(date.today())

    c1, c2, c3, c4 = st.columns([1, 1, 1, 4])
    if c1.button("← Prev", key=f"{key}_prev"):
        st.session_state[state_key] -= timedelta(weeks=1)
    if c2.button("This week", key=f"{key}_this"):
        st.session_state[state_key] = week_start_for(date.today())
    if c3.button("Next →", key=f"{key}_next"):
        st.session_state[state_key] += timedelta(weeks=1)

    monday = st.session_state[state_key]
    sunday = monday + timedelta(days=6)
    c4.markdown(f"**{monday:%b} {monday.day} – {sunday:%b} {sunday.day}, {sunday.year}**")
    return monday


def render_week(key: str, suggestions: list | None = None) -> None:
    """Week picker plus the color-coded grid."""
    monday = week_picker(key)
    blocks = load_blocks(monday - timedelta(days=1), monday + timedelta(days=6), suggestions)
    labels, cats = build_week_grid(blocks, monday, db.get_preferences())
    st.dataframe(
        style_week_grid(labels, cats),
        width="stretch",
        height=min(36 * (len(labels) + 1) + 3, 900),
    )
    st.caption(
        "Blue class · Orange work · Green gym · Purple personal · Gray other · "
        "Teal study · Pale teal suggested · Red overlap"
    )
