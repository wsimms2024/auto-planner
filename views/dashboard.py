"""Dashboard: today, what needs attention, suggested study sessions, and the week."""

from dataclasses import replace
from datetime import date, datetime, timedelta
from html import escape

import pandas as pd
import streamlit as st

from autoplan import db, exam_mode, scheduler, task_rules, ui
from autoplan.integrations import sync
from autoplan.week_view import CATEGORY_ACCENTS

now = datetime.now()
today = date.today()

st.title("AutoPlan")
st.caption(f"{today:%A, %B} {today.day}")
ui.show_flash()

# Sync connected accounts if it's been a while (see AUTO_SYNC_MINUTES in config.py).
# The Telegram bot also syncs every 2 hours and texts you about important changes.
if sync.due_for_auto_sync(now):
    with st.spinner("Syncing..."):
        failed = [name for name, ok, _ in sync.sync_all(now) if not ok]
    if failed:
        st.warning(f"{', '.join(failed)} didn't sync. Details on the Sync page.")

exam_mode.sync_exam_tasks(now)
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

def plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n != 1 else ''}"


late = f'<span class="late"><b>{len(overdue)}</b> overdue</span>' if overdue else "nothing overdue"
st.markdown(
    f'<p class="ap-summary"><b>{len(today_blocks)}</b> {"thing" if len(today_blocks) == 1 else "things"} on today, '
    f'<b>{len(open_tasks)}</b> open {"task" if len(open_tasks) == 1 else "tasks"}, {late}, '
    f'and <b>{free_hours:.0f}</b> free hours this week.</p>',
    unsafe_allow_html=True,
)

left, right = st.columns([1.1, 1], gap="large")

with left:
    st.subheader("Today")
    if not today_blocks:
        st.markdown('<p class="ap-empty">Nothing on the calendar.</p>', unsafe_allow_html=True)
    else:
        rows = []
        for b in today_blocks:
            state = "past" if b.end < now else "now" if b.start <= now else ""
            pill = '<span class="pill">Now</span>' if state == "now" else ""
            color = CATEGORY_ACCENTS.get(b.category, "#8C99A6")
            rows.append(
                f'<li class="{state}"><span class="t">{ui.fmt_time(b.start)}</span>'
                f'<span class="what" style="--cat:{color}">{escape(b.label)}{pill}'
                f"<small>until {ui.fmt_time(b.end)}</small></span></li>"
            )
        st.markdown(f'<ul class="ap-timeline">{"".join(rows)}</ul>', unsafe_allow_html=True)

    from autoplan import class_schedule

    in_class = class_schedule.today_in_class(today)
    if in_class:
        st.markdown("**In class today**")
        for s in in_class:
            extra = f" · read: {s['readings']}" if s["readings"] else ""
            extra += f" · due: {s['due']}" if s["due"] else ""
            st.markdown(f"{s['course_code']}: {s['topic'] or 'class'}<span style='color:var(--muted)'>{escape(extra)}</span>", unsafe_allow_html=True)

with right:
    st.subheader("Needs attention")
    if to_review:
        st.page_link("views/sync.py", label=f"{plural(to_review, 'email')} to review", icon="📬")
    exams_to_confirm = len(db.list_exam_candidates("pending"))
    if exams_to_confirm:
        st.page_link("views/sync.py", label=f"{plural(exams_to_confirm, 'exam date')} to confirm", icon="📝")
    items = [("late", t.name, ui.fmt_due(t.deadline, now)) for t in overdue]
    items += [("", t.name, ui.fmt_due(t.deadline, now)) for t in due_soon]
    items += [("short", task_by_id[i].name, f"{ui.fmt_minutes(m)} won't fit") for i, m in result.unscheduled.items()]
    if items:
        st.markdown(
            '<ul class="ap-attn">'
            + "".join(f'<li class="{cls}"><span>{escape(name)}</span><span class="when">{when}</span></li>' for cls, name, when in items)
            + "</ul>",
            unsafe_allow_html=True,
        )
    elif not to_review:
        st.markdown('<p class="ap-empty">Nothing due in the next 3 days.</p>', unsafe_allow_html=True)

st.write("")
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
            if task_rules.is_exam(t.name):
                part += " · exam prep"
            rows.append({
                "Accept": False,
                "Done": False,
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
                "Accept": st.column_config.CheckboxColumn(width="small", help="Put this session on your calendar"),
                "Done": st.column_config.CheckboxColumn(width="small", help="Already finished: mark the task done"),
                "Skip": st.column_config.CheckboxColumn(width="small", help="Stop suggesting time for this task"),
                "Task": st.column_config.TextColumn(width="large"),
            },
            key=ui.editor_key("suggestions"),
        )
        c1, c2, _ = st.columns([1, 1, 4])
        if c1.button("Save choices", type="primary"):
            done_ids = {suggestions[i].task_id for i in edited.index[edited["Done"]]}
            skip_ids = {suggestions[i].task_id for i in edited.index[edited["Skip"]]} - done_ids
            for task_id in done_ids:
                db.set_task_status(task_id, "done")
            for task_id in skip_ids:
                db.set_task_planning(task_id, False)
            added = 0
            for i in edited.index[edited["Accept"]]:
                s = suggestions[i]
                if s.task_id not in done_ids | skip_ids:
                    db.add_study_session(replace(s, status="accepted"))
                    added += 1
            parts = [f"{added} added"] if added else []
            parts += [f"{len(done_ids)} marked done"] if done_ids else []
            parts += [f"{len(skip_ids)} won't be suggested again (turn back on under Tasks)"] if skip_ids else []
            ui.flash(", ".join(parts).capitalize() + "." if parts else "Nothing checked.")
            ui.reset_editor("suggestions")
            st.rerun()
        if c2.button(f"Accept all {len(suggestions)}"):
            for s in suggestions:
                db.add_study_session(replace(s, status="accepted"))
            ui.flash(f"Added {len(suggestions)} study sessions.")
            ui.reset_editor("suggestions")
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
