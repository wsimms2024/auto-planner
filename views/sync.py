"""Sync page: connect Google and Blackboard, run syncs, review emails."""

from datetime import datetime, time, timedelta

import streamlit as st

from autoplan import config, db, ui
from autoplan.integrations import google_auth, sync
from autoplan.models import Task

st.title("Sync")
ui.show_flash()
now = datetime.now()


def status_line(source: str) -> None:
    last_run, message = db.get_sync_state(source)
    if last_run is None:
        st.caption("Never synced.")
    elif message.startswith("Failed"):
        st.caption(f":red[Last try {ui.fmt_dt(last_run)}: {message}]")
    else:
        st.caption(f"Last synced {ui.fmt_dt(last_run)}: {message}")


def show_result(name: str, ok: bool, message: str) -> None:
    ui.flash(f"{name}: {message}", "success" if ok else "error")


if st.button("Sync everything now", type="primary"):
    with st.spinner("Syncing..."):
        results = sync.sync_all()
    if not results:
        st.info("Nothing is connected yet. Set up Google or Blackboard below.")
    else:
        all_ok = all(ok for _, ok, _ in results)
        ui.flash("  \n".join(f"{name}: {msg}" for name, _, msg in results), "success" if all_ok else "warning")
        st.rerun()

review_tab, google_tab, bb_tab = st.tabs([f"Email to review ({len(db.list_inbox())})", "Google", "Blackboard"])

# ---------- Google ----------

with google_tab:
    if not google_auth.has_credentials_file():
        st.warning(
            "**Google isn't set up yet.** Create a Google Cloud project, enable the Calendar and Gmail APIs, "
            "make a Desktop OAuth client, and save the downloaded file as `credentials.json` in "
            f"`{config.PROJECT_ROOT}`. Then refresh this page."
        )
    elif not google_auth.is_signed_in():
        st.write("Sign in once and AutoPlan can read your calendar and email. A browser tab will open.")
        if st.button("Sign in with Google"):
            try:
                google_auth.get_credentials(interactive=True)
                ui.flash("Signed in with Google. Pick which calendars to import below.")
                st.rerun()
            except Exception as e:
                st.error(f"Sign-in didn't work: {e}")
    else:
        st.markdown("**Signed in with Google.**")

        st.subheader("Calendars to import")
        st.caption(
            "Events from these calendars become fixed commitments. Accepted study sessions are copied to a "
            f"separate **AutoPlan** calendar with a {config.SESSION_REMINDER_MINUTES}-minute reminder."
        )
        if "google_calendar_list" not in st.session_state or st.button("Refresh calendar list"):
            try:
                from autoplan.integrations import google_calendar

                st.session_state["google_calendar_list"] = google_calendar.list_calendars(google_auth.calendar_service())
            except Exception as e:
                st.session_state["google_calendar_list"] = []
                st.error(f"Couldn't load your calendars: {e}")

        cals = st.session_state["google_calendar_list"]
        chosen = sync.selected_calendars()
        if cals:
            with st.form("calendars"):
                picks = {}
                for c in cals:
                    c1, c2 = st.columns([3, 2])
                    on = c1.checkbox(c["name"] + (" (main)" if c["primary"] else ""), value=c["id"] in chosen, key=f"cal_{c['id']}")
                    current = chosen.get(c["id"], "class" if c["primary"] else "other")
                    cat = c2.selectbox(
                        "Category", config.EVENT_CATEGORIES, index=config.EVENT_CATEGORIES.index(current),
                        key=f"calcat_{c['id']}", label_visibility="collapsed",
                    )
                    if on:
                        picks[c["id"]] = cat
                if st.form_submit_button("Save calendars"):
                    db.set_preference("google_calendars", picks)
                    ui.flash(f"Importing {len(picks)} calendar{'s' if len(picks) != 1 else ''}. Click Sync calendar now to pull them in.")
                    st.rerun()

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Calendar**")
            status_line("google")
            if st.button("Sync calendar now"):
                with st.spinner("Syncing calendar..."):
                    show_result("Calendar", *sync.sync_google_calendar())
                st.rerun()
        with c2:
            st.markdown("**Gmail**")
            status_line("gmail")
            if st.button("Scan email now"):
                with st.spinner("Scanning email..."):
                    show_result("Gmail", *sync.sync_gmail())
                st.rerun()

        st.divider()
        if st.button("Sign out of Google"):
            google_auth.sign_out()
            ui.flash("Signed out. Imported events stay until you delete them.")
            st.rerun()

# ---------- Blackboard ----------

with bb_tab:
    st.write(
        "In Blackboard, open **Calendar**, then the gear or **Get external calendar link** option, and copy the link. "
        "Paste it here. It's saved only in your local database."
    )
    saved_url = db.get_preference_value("blackboard_feed_url") or ""
    with st.form("blackboard"):
        url = st.text_input("Blackboard calendar link", value=saved_url, type="password")
        st.caption(f"New assignments start with a {config.BLACKBOARD_DEFAULT_MINUTES}-minute estimate. Change it per task on the Tasks page.")
        if st.form_submit_button("Save link"):
            if url and not url.strip().lower().startswith(("https://", "webcal://")):
                st.error("That doesn't look like a calendar link. It should start with https:// or webcal://.")
            else:
                db.set_preference("blackboard_feed_url", url.strip())
                ui.flash("Blackboard link saved." if url else "Blackboard link removed.")
                st.rerun()
    status_line("blackboard")
    if saved_url and st.button("Sync Blackboard now"):
        with st.spinner("Pulling assignments from Blackboard..."):
            show_result("Blackboard", *sync.sync_blackboard())
        st.rerun()

# ---------- Email review ----------

with review_tab:
    items = db.list_inbox()
    if not items:
        st.write("Nothing to review. Time-sensitive emails show up here after a Gmail scan.")
    labels = {"blackboard": "Blackboard", "professor": "Professor", "time-sensitive": "Time-sensitive"}
    for item in items:
        with st.container(border=True):
            sender_name = item.sender.split("<")[0].strip().strip('"') or item.sender
            st.markdown(f"**{item.subject}**  \n:gray[{labels.get(item.kind, item.kind)} · {sender_name} · {ui.fmt_dt(item.received_at)}]")
            st.write(item.snippet)
            st.markdown(f"[Open in Gmail]({item.link})")

            default_due = item.detected_due or datetime.combine(item.received_at.date() + timedelta(days=3), time(23, 59))
            with st.form(f"inbox_{item.id}"):
                name = st.text_input("Task name", value=item.subject[:120])
                c1, c2, c3, c4 = st.columns(4)
                due_day = c1.date_input("Due", value=default_due.date())
                due_time = c2.time_input("Time", value=default_due.time(), step=900)
                est = c3.number_input("Minutes", 15, 6000, 60, step=15)
                priority = c4.selectbox("Priority", config.PRIORITIES, index=1)
                if item.detected_due:
                    st.caption(f"Due date found in the email: {ui.fmt_dt(item.detected_due)}. Double-check it.")
                b1, b2 = st.columns([1, 1])
                add = b1.form_submit_button("Add as task", type="primary")
                dismiss = b2.form_submit_button("Dismiss")
            if add:
                task = Task(name=name, deadline=datetime.combine(due_day, due_time), estimated_minutes=int(est),
                            priority=priority, source="gmail", external_id=item.external_id)
                problems = task.validate()
                if problems:
                    for p in problems:
                        st.error(p)
                else:
                    db.add_task(task)
                    db.set_inbox_status(item.id, "added")
                    ui.flash(f"Added **{task.name}** to your tasks.")
                    st.rerun()
            if dismiss:
                db.set_inbox_status(item.id, "dismissed")
                st.rerun()
