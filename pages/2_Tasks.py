"""Tasks page: assignments and anything with a deadline."""

import pandas as pd
import streamlit as st

from autoplan import db

st.set_page_config(page_title="Tasks · AutoPlan", page_icon="🗓️", layout="wide")
db.init_db()

st.title("Tasks")
st.info("Phase 2 adds forms to create, edit, complete, and delete tasks.")

tasks = db.list_tasks()
if tasks:
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Task": t.name,
                    "Due": t.deadline,
                    "Est. minutes": t.estimated_minutes,
                    "Priority": t.priority,
                    "Status": t.status,
                }
                for t in tasks
            ]
        ),
        hide_index=True,
        width="stretch",
    )
else:
    st.write("No tasks saved yet.")
