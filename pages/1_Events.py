"""Events page: classes, work, gym, and other fixed commitments."""

import pandas as pd
import streamlit as st

from autoplan import db

st.set_page_config(page_title="Events · AutoPlan", page_icon="🗓️", layout="wide")
db.init_db()

st.title("Events")
st.info("Phase 2 adds forms to create, edit, and delete events, plus the weekly grid.")

events = db.list_events()
if events:
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Name": e.name,
                    "Start": e.start,
                    "End": e.end,
                    "Category": e.category,
                    "Repeats": "Weekly" if e.is_recurring else "Once",
                }
                for e in events
            ]
        ),
        hide_index=True,
        width="stretch",
    )
else:
    st.write("No events saved yet.")
