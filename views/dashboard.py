"""Dashboard: today, deadlines, suggested study sessions, and the week at a glance."""

from dataclasses import replace
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

from autoplan import db, scheduler, ui

now = datetime.now()
today = date.today()

st.title("AutoPlan")
st.caption(f"{today:%A, %B} {today.day}")
ui.show_flash()

tasks = db.list_tasks()
task_by_id = {t.id: t for t in tasks}
open_tasks = [t for t in tasks if t.status == "open"]
overdue = [t for t in open_tasks if t.is_overdue(now)]
due_soon = [t for t in open_tasks if not t.is_overdue(now) and t.deadline <= now + timedelta(days=3)]
due_week = [t for t in open_tasks if not t.is_overdue(now) and t.deadline <= now + timedelta(days=7)]
prefs = db.get_preferences()
accepted = ui.accepted_sessions()
result = ui.get_suggestions(now)

today_blocks = ui.load_blocks(today - timedelta(days=1), today)
today_blocks = [b for b in today_blocks if b.end.date() >= today and b.start.date() <= today]
free_week = scheduler.find_free_slots(
    db.list_events(), prefs, today, today + timedelta(days=6), accepted, now
)
free_today = [s for s in free_week if s[0].date() == today]
free_hours = sum((e - s).total_seconds() for s, e in free_week) / 3600

c1, c2, c3, c4 = st.columns(4)
c1.metric("Today", f"{len(today_blocks)} item{'s' if len(today_blocks) != 1 else ''}")
c2.metric("Open tasks", len(open_tasks))
c3.metric("Overdue", len(overdue))
c4.metric("Free time, next 7 days", f"{free_hours:.0f}h")

left, right = st.columns(2)

with left:
    st.subheader("Today")
    if not today_blocks:
        st.write("Nothing scheduled today.")
    for b in today_blocks:
        marker = "✓ " if b.end < now else ("▶ " if b.start <= now < b.end else "")
        st.markdown(f"{marker}**{ui.fmt_time(b.start)} – {ui.fmt_time(b.end)}**  {b.label}  :gray[{b.category}]")
    if free_today:
        st.caption("Still free today: " + ", ".join(f"{ui.fmt_time(s)}-{ui.fmt_time(e)}" for s, e in free_today))

with right:
    st.subheader("Needs attention")
    if not overdue and not due_soon and not result.unscheduled:
        st.write("Nothing overdue, nothing due in the next 3 days.")
    for t in overdue:
        st.markdown(f":red[**{t.name}**, {ui.fmt_due(t.deadline, now)}] · {ui.fmt_minutes(t.estimated_minutes)}")
    for t in due_soon:
        st.markdown(f"**{t.name}**, due {ui.fmt_due(t.deadline, now)} · {ui.fmt_minutes(t.estimated_minutes)} · {t.priority}")
    for task_id, minutes in result.unscheduled.items():
        t = task_by_id[task_id]
        window_end = now + timedelta(days=prefs.planning_days)
        where = "before the deadline" if now < t.deadline <= window_end else f"in the next {prefs.planning_days} days"
        st.warning(
            f"Not enough free time for **{t.name}**: {ui.fmt_minutes(minutes)} won't fit {where}. "
            "Free up time or lower the estimate."
        )

# ---------- Suggestions ----------

st.subheader("Suggested study sessions")
suggestions = result.sessions

if not suggestions:
    st.write("No suggestions right now. Add tasks with time estimates and they'll show up here.")
else:
    totals = {}
    for s in suggestions:
        totals[s.task_id] = totals.get(s.task_id, 0) + 1
    part = {}

    if st.button(f"Accept all {len(suggestions)}", type="primary"):
        for s in suggestions:
            db.add_study_session(replace(s, status="accepted"))
        ui.flash(f"Added {len(suggestions)} study sessions to your schedule.")
        st.rerun()

    current_day = None
    for i, s in enumerate(suggestions):
        if s.start.date() != current_day:
            current_day = s.start.date()
            st.markdown(f"**{current_day:%A, %b} {current_day.day}**")
        t = task_by_id[s.task_id]
        part[s.task_id] = part.get(s.task_id, 0) + 1
        label = f" · part {part[s.task_id]} of {totals[s.task_id]}" if totals[s.task_id] > 1 else ""

        c1, c2, c3 = st.columns([6, 1, 1])
        c1.markdown(
            f"{ui.fmt_time(s.start)} – {ui.fmt_time(s.end)} · **{t.name}**{label}  "
            f":gray[due {ui.fmt_dt(t.deadline)} · {t.priority}]"
        )
        if c2.button("Accept", key=f"acc_{i}"):
            db.add_study_session(replace(s, status="accepted"))
            ui.flash(f"Added {t.name} on {ui.fmt_dt(s.start)}.")
            st.rerun()
        if c3.button("Reject", key=f"rej_{i}"):
            db.add_study_session(replace(s, status="rejected"))
            ui.flash(f"Skipped that slot for {t.name}. Here's the next best option.")
            st.rerun()

# ---------- Planned sessions ----------

upcoming = [s for s in accepted if s.end >= now]
if upcoming:
    with st.expander(f"Planned study sessions ({len(upcoming)})"):
        for s in upcoming:
            t = task_by_id.get(s.task_id)
            c1, c2 = st.columns([6, 1])
            c1.write(f"{ui.fmt_dt(s.start)} – {ui.fmt_time(s.end)} · {t.name if t else 'task'}")
            if c2.button("Remove", key=f"rm_{s.id}"):
                db.set_session_status(s.id, "rejected")
                ui.flash("Removed that session. It won't be suggested at that time again.")
                st.rerun()

# ---------- Deadlines and week ----------

st.subheader("Due this week")
if due_week:
    st.dataframe(
        pd.DataFrame(
            [
                {"Task": t.name, "Due": ui.fmt_dt(t.deadline), "Estimate": ui.fmt_minutes(t.estimated_minutes), "Priority": t.priority}
                for t in due_week
            ]
        ),
        hide_index=True,
        width="stretch",
    )
else:
    st.write("Nothing due in the next 7 days.")

st.subheader("This week")
ui.render_week("dash", suggestions)
