"""Settings page: when study sessions can be scheduled and how long they are."""

from datetime import time

import streamlit as st

from autoplan import db, ui
from autoplan.models import Preferences

st.title("Settings")
ui.show_flash()
st.caption("These only control suggested study sessions. Events can be at any time.")

prefs = db.get_preferences()

with st.form("settings"):
    st.subheader("Study hours")
    st.caption("Uncheck a day to keep it free of study sessions.")
    hours = {}
    for d, name in enumerate(ui.DAY_NAMES):
        start, end = prefs.work_hours.get(d, (time(9), time(20)))
        c1, c2, c3 = st.columns([1, 2, 2])
        on = c1.checkbox(name, value=d in prefs.work_hours, key=f"on_{d}")
        s = c2.time_input("From", value=start, step=900, key=f"start_{d}", label_visibility="collapsed")
        e = c3.time_input("To", value=end, step=900, key=f"end_{d}", label_visibility="collapsed")
        if on:
            hours[d] = (s, e)

    st.subheader("Study sessions")
    c1, c2 = st.columns(2)
    min_len = c1.number_input("Shortest session (minutes)", 15, 240, prefs.min_session_minutes, step=15)
    max_len = c2.number_input("Longest session (minutes)", 15, 480, prefs.max_session_minutes, step=15)
    c1, c2 = st.columns(2)
    buffer = c1.number_input("Buffer around events (minutes)", 0, 120, prefs.buffer_minutes, step=5)
    days = c2.number_input("Plan this many days ahead", 1, 28, prefs.planning_days)
    low = st.checkbox("Suggest study time for Low priority tasks too", value=prefs.suggest_low_priority)

    st.subheader("Gym")
    st.caption("Used when you ask the Telegram bot when to go, and in the morning text.")
    gym_days = st.multiselect(
        "Gym days", list(range(7)), default=prefs.gym_days, format_func=lambda d: ui.DAY_NAMES[d]
    )
    c1, c2, c3 = st.columns(3)
    gym_minutes = c1.number_input("Session length (minutes)", 15, 300, prefs.gym_minutes, step=15)
    gym_earliest = c2.time_input("Earliest start", value=prefs.gym_earliest, step=900)
    gym_latest = c3.time_input("Done by", value=prefs.gym_latest, step=900)

    st.subheader("Morning text")
    c1, c2 = st.columns(2)
    briefing_on = c1.checkbox("Send a morning briefing on Telegram", value=prefs.briefing_on)
    briefing_time = c2.time_input("Send at", value=prefs.briefing_time, step=900)

    saved = st.form_submit_button("Save settings", type="primary")

if saved:
    new_prefs = Preferences(
        work_hours=hours,
        min_session_minutes=int(min_len),
        max_session_minutes=int(max_len),
        buffer_minutes=int(buffer),
        planning_days=int(days),
        suggest_low_priority=low,
        gym_days=gym_days,
        gym_minutes=int(gym_minutes),
        gym_earliest=gym_earliest,
        gym_latest=gym_latest,
        briefing_on=briefing_on,
        briefing_time=briefing_time,
    )
    problems = new_prefs.validate()
    if problems:
        for p in problems:
            st.error(p)
    else:
        db.save_preferences(new_prefs)
        ui.flash("Settings saved. Suggestions now use the new rules.")
        st.rerun()
