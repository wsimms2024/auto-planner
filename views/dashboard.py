"""Dashboard: today, deadlines, and the week at a glance."""

from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

from autoplan import db, ui


now = datetime.now()
today = date.today()

st.title("AutoPlan")
st.caption(f"{today:%A, %B} {today.day}")

tasks = db.list_tasks()
open_tasks = [t for t in tasks if t.status == "open"]
overdue = [t for t in open_tasks if t.is_overdue(now)]
due_soon = [t for t in open_tasks if not t.is_overdue(now) and t.deadline <= now + timedelta(days=3)]
due_week = [t for t in open_tasks if not t.is_overdue(now) and t.deadline <= now + timedelta(days=7)]
today_blocks = ui.load_blocks(today - timedelta(days=1), today)
today_blocks = [b for b in today_blocks if b.end.date() >= today and b.start.date() <= today]

c1, c2, c3, c4 = st.columns(4)
c1.metric("Today", f"{len(today_blocks)} item{'s' if len(today_blocks) != 1 else ''}")
c2.metric("Open tasks", len(open_tasks))
c3.metric("Due in 3 days", len(due_soon))
c4.metric("Overdue", len(overdue))

left, right = st.columns(2)

with left:
    st.subheader("Today")
    if not today_blocks:
        st.write("Nothing scheduled today.")
    for b in today_blocks:
        marker = "✓ " if b.end < now else ("▶ " if b.start <= now < b.end else "")
        st.markdown(f"{marker}**{ui.fmt_time(b.start)} – {ui.fmt_time(b.end)}**  {b.label}  :gray[{b.category}]")

with right:
    st.subheader("Needs attention")
    if not overdue and not due_soon:
        st.write("Nothing overdue or due in the next 3 days.")
    for t in overdue:
        st.markdown(f":red[**{t.name}**, {ui.fmt_due(t.deadline, now)}] · {ui.fmt_minutes(t.estimated_minutes)}")
    for t in due_soon:
        st.markdown(f"**{t.name}**, due {ui.fmt_due(t.deadline, now)} · {ui.fmt_minutes(t.estimated_minutes)} · {t.priority}")

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
ui.render_week("dash")

st.subheader("Suggested study sessions")
st.info("Coming in Phase 3: the scheduler will propose sessions here for you to accept or reject.")
