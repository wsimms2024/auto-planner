"""Settings page: working hours and study session rules."""

import pandas as pd
import streamlit as st

from autoplan import db

st.set_page_config(page_title="Settings · AutoPlan", page_icon="🗓️", layout="wide")
db.init_db()

st.title("Settings")
st.info("Editing arrives in Phase 4. For now this shows what's saved in the database.")

prefs = db.get_preferences()
day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

st.subheader("Working hours")
st.dataframe(
    pd.DataFrame(
        [
            {"Day": day_names[d], "Start": s.strftime("%I:%M %p"), "End": e.strftime("%I:%M %p")}
            for d, (s, e) in sorted(prefs.work_hours.items())
        ]
    ),
    hide_index=True,
)

st.subheader("Study sessions")
c1, c2, c3 = st.columns(3)
c1.metric("Minimum length", f"{prefs.min_session_minutes} min")
c2.metric("Maximum length", f"{prefs.max_session_minutes} min")
c3.metric("Buffer around events", f"{prefs.buffer_minutes} min")
