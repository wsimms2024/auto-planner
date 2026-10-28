"""
AutoPlan dashboard. Run with:  streamlit run app.py

Other pages live in the pages/ folder and show up in the sidebar
automatically.
"""

from datetime import date, datetime

import pandas as pd
import streamlit as st

from autoplan import db

st.set_page_config(page_title="AutoPlan", page_icon="🗓️", layout="wide")
db.init_db()

st.title("AutoPlan")
st.caption(f"Today is {date.today():%A, %B %d}")

events = db.list_events()
tasks = db.list_tasks()
open_tasks = [t for t in tasks if t.status == "open"]
overdue = [t for t in open_tasks if t.deadline < datetime.now()]
accepted = db.list_study_sessions(status="accepted")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Events", len(events))
c2.metric("Open tasks", len(open_tasks))
c3.metric("Overdue", len(overdue))
c4.metric("Planned sessions", len(accepted))

left, right = st.columns(2)

with left:
    st.subheader("Today's schedule")
    st.info("Coming in Phase 2: today's events and study sessions.")

with right:
    st.subheader("Upcoming tasks")
    if open_tasks:
        st.dataframe(
            pd.DataFrame(
                [{"Task": t.name, "Due": t.deadline, "Priority": t.priority} for t in open_tasks[:10]]
            ),
            hide_index=True,
            width="stretch",
        )
    else:
        st.info("No tasks yet. Task entry arrives in Phase 2.")

st.subheader("Suggested study sessions")
st.info("Coming in Phase 3: the scheduler will propose sessions here for you to accept or reject.")
