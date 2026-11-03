"""Dashboard: today, what needs attention, suggested study sessions, and the week."""

from dataclasses import replace
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

from autoplan import db, scheduler, ui
from autoplan.integrations import sync

now = datetime.now()
today = date.today()

st.title("AutoPlan")
st.caption(f"{today:%A, %B} {today.day}")
ui.show_flash()

# Sync connected accounts if it's been a while (see AUTO_SYNC_MINUTES in config.py)
if sync.due_for_auto_sync(now):
    with st.spinner("Syncing..."):
        failed = [name for name, ok, _ in sync.sync_all(now) if not ok]
    if failed:
        st.warning(f"{', '.join(failed)} didn't sync. Details on the Sync page.")

tasks = db.list_tasks()
task_by_id = {t.id: t for t in tasks}
open_tasks = [t for t in tasks if t.status == "open"]
overdue = [t for t in open_tasks if t.is_overdue(now)]
due_soon = [t for t in open_tasks if not t.is_overdue(now) and t.deadline <= now + timedelta(days=3)]
prefs = db.get_preferences()
accepted = ui.accepted_sessions()
result = ui.get_suggestions(now)
to_review = len(db.list_inbox())

today_blocks = ui.load_blocks(today - timedelta(days=1), today)
today_blocks = [b for b in today_blocks if b.end.date() >= today and b.start.date() <= today]
free_week = scheduler.find_free_slots(db.list_events(), prefs, today, today + timedelta(days=6), accepted, now)
free_hours = sum((e - s).total_seconds() for s, e in free_week) / 3600

c1, c2, c3, c4 = st.columns(4)
c1.metric("Today", len(today_blocks))
c2.metric("Open tasks", len(open_tasks))
c3.metric("Overdue", len(overdue))
c4.metric("Free this week", f"{free_hours:.0f}h")

left, right = st.columns(2, gap="large")

with left:
    st.subheader("Today")
    if not today_blocks:
        st.write("Nothing scheduled.")
    for b in today_blocks:
        when = f"{ui.fmt_time(b.start)}–{ui.fmt_time(b.end)}"
        if b.end < now:
            st.markdown(f":gray[{when} · {b.label}]")
        elif b.start <= now:
            st.markdown(f"**{when} · {b.label}** :blue[now]")
        else:
            st.markdown(f"{when} · {b.label}")

with right:
    st.subheader("Needs attention")
    if to_review:
        st.page_link("views/sync.py", label=f"{to_review} email{'s' if to_review != 1 else ''} to review", icon="📬")
    for t in overdue:
        st.markdown(f":red[{t.name} · {ui.fmt_due(t.deadline, now)}]")
    for t in due_soon:
        st.markdown(f"{t.name} · :gray[{ui.fmt_due(t.deadline, now)}]")
    for task_id, minutes in result.unscheduled.items():
        st.markdown(f":orange[{task_by_id[task_id].name} · {ui.fmt_minutes(minutes)} won't fit]")
    if not (to_review or overdue or due_soon or result.unscheduled):
        st.write("All clear.")

st.divider()
plan_tab, week_tab = st.tabs(["Suggested sessions", "This week"])

# ---------- Suggestions: one compact checklist ----------

with plan_tab:
    suggestions = result.sessions
    if not suggestions:
        st.write("No suggestions right now.")
    else:
        totals = {}
        for s in suggestions:
            totals[s.task_id] = totals.get(s.task_id, 0) + 1
        seen = {}
        rows = []
        for s in suggestions:
            seen[s.task_id] = seen.get(s.task_id, 0) + 1
            part = f" ({seen[s.task_id]}/{totals[s.task_id]})" if totals[s.task_id] > 1 else ""
            t = task_by_id[s.task_id]
            rows.append({
                "Accept": False,
                "Skip": False,
                "Day": f"{s.start:%a %b} {s.start.day}",
                "Time": f"{ui.fmt_time(s.start)}–{ui.fmt_time(s.end)}",
                "Task": t.name + part,
                "Due": f"{t.deadline:%a} {ui.fmt_time(t.deadline)}",
            })
        edited = st.data_editor(
            pd.DataFrame(rows),
            hide_index=True,
            width="stretch",
            disabled=["Day", "Time", "Task", "Due"],
            column_config={
                "Accept": st.column_config.CheckboxColumn(width="small"),
                "Skip": st.column_config.CheckboxColumn(width="small", help="Don't suggest this time again"),
                "Task": st.column_config.TextColumn(width="large"),
            },
            key="suggestions_table",
        )
        c1, c2, _ = st.columns([1, 1, 4])
        if c1.button("Save choices", type="primary"):
            added = skipped = 0
            for i, s in enumerate(suggestions):
                if edited.at[i, "Accept"]:
                    db.add_study_session(replace(s, status="accepted"))
                    added += 1
                elif edited.at[i, "Skip"]:
                    db.add_study_session(replace(s, status="rejected"))
                    skipped += 1
            ui.flash(f"Added {added}, skipped {skipped}." if added or skipped else "Nothing checked.")
            st.rerun()
        if c2.button(f"Accept all {len(suggestions)}"):
            for s in suggestions:
                db.add_study_session(replace(s, status="accepted"))
            ui.flash(f"Added {len(suggestions)} study sessions.")
            st.rerun()

    upcoming = [s for s in accepted if s.end >= now]
    if upcoming:
        with st.expander(f"Planned sessions ({len(upcoming)})"):
            for s in upcoming:
                t = task_by_id.get(s.task_id)
                c1, c2 = st.columns([8, 1])
                c1.markdown(f"{ui.fmt_dt(s.start)}–{ui.fmt_time(s.end)} · {t.name if t else 'task'}")
                if c2.button("Remove", key=f"rm_{s.id}"):
                    db.set_session_status(s.id, "rejected")
                    ui.flash("Removed. It won't be suggested at that time again.")
                    st.rerun()

with week_tab:
    ui.render_week("dash", result.sessions)
