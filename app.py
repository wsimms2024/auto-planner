"""
AutoPlan entry point. Run with:  streamlit run app.py

This file sets up the app and the sidebar. Each page lives in views/.
To add a page, create a file in views/ and add a line below.
"""

import streamlit as st

from autoplan import db

st.set_page_config(page_title="AutoPlan", page_icon="🗓️", layout="wide")
db.init_db()

page = st.navigation(
    [
        st.Page("views/dashboard.py", title="Dashboard", icon="🏠", default=True),
        st.Page("views/events.py", title="Events", icon="🗓️"),
        st.Page("views/tasks.py", title="Tasks", icon="✅"),
        st.Page("views/sync.py", title="Sync", icon="🔄"),
        st.Page("views/settings.py", title="Settings", icon="⚙️"),
    ]
)
page.run()
