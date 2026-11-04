"""Sync page: connect Google and Blackboard, run syncs, review emails."""

from datetime import datetime, time, timedelta

import streamlit as st

from autoplan import config, db, task_rules, ui
from autoplan.integrations import gmail, google_auth, sync
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


def status_badge(source: str, label: str, connected: bool) -> str:
    if not connected:
        return f'<span class="off">{label}: not connected</span>'
    last_run, message = db.get_sync_state(source)
    if last_run is None:
        return f'<span class="off">{label}: not synced yet</span>'
    cls = "bad" if message.startswith("Failed") else "ok"
    return f'<span class="{cls}">{label}<small>{ui.fmt_time(last_run)}</small></span>'


signed_in = google_auth.is_signed_in()
has_bb = bool(db.get_preference_value("blackboard_feed_url"))
c1, c2 = st.columns([3, 1], vertical_alignment="center")
c1.markdown(
    '<div class="ap-status">'
    + status_badge("google", "Calendar", signed_in)
    + status_badge("gmail", "Gmail", signed_in)
    + status_badge("blackboard", "Blackboard", has_bb)
    + "</div>",
    unsafe_allow_html=True,
)
if c2.button("Sync now", type="primary", width="stretch"):
    with st.spinner("Syncing..."):
        results = sync.sync_all()
    if not results:
        ui.flash("Nothing is connected yet. Set up Google or Blackboard below.", "warning")
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
        st.subheader("Calendars to import")
        st.caption("Imported as fixed events. Accepted study sessions go to a separate AutoPlan calendar with a reminder.")
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
    st.caption("Blackboard → Calendar → gear → Get external calendar link. Saved only on this computer.")
    saved_url = db.get_preference_value("blackboard_feed_url") or ""
    with st.form("blackboard"):
        url = st.text_input("Blackboard calendar link", value=saved_url, type="password")
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

def add_from_email(item, name, due, minutes, priority) -> None:
    plan = task_rules.guess(name)[2]
    task = Task(name=name, deadline=due, estimated_minutes=int(minutes), priority=priority,
                plan_sessions=plan, source="gmail", external_id=item.external_id)
    problems = task.validate()
    if problems:
        st.error(" ".join(problems))
        return
    db.add_task(task)
    db.set_inbox_status(item.id, "added")
    ui.flash(f"Added **{task.name}**.")
    st.rerun()


with review_tab:
    items = db.list_inbox()
    if not items:
        st.write("Nothing to review.")
    else:
        c1, c2, _ = st.columns([1.2, 1.2, 3])
        if signed_in and c1.button("Re-check with current rules", help="Re-reads these emails and dismisses ones that no longer qualify"):
            with st.spinner("Re-checking..."):
                ok, msg = sync.recheck_inbox()
            ui.flash(f"Re-check: {msg}", "success" if ok else "error")
            st.rerun()
        if c2.button("Dismiss all"):
            for item in items:
                db.set_inbox_status(item.id, "dismissed")
            ui.flash(f"Dismissed {len(items)} emails.")
            st.rerun()

    labels = {"blackboard": "Blackboard", "professor": "GW", "time-sensitive": "Deadline"}
    for item in items:
        default_due = item.detected_due or datetime.combine(item.received_at.date() + timedelta(days=3), time(23, 59))
        guess_priority, guess_minutes, _ = task_rules.guess(item.subject)
        sender_name = item.sender.split("<")[0].split(" - ")[0].strip().strip('"') or item.sender
        due_text = f"due {ui.fmt_dt(item.detected_due)}" if item.detected_due else "no date found"

        with st.container(border=True):
            c1, c2, c3, c4, c5 = st.columns([7, 1, 1, 0.7, 0.7], vertical_alignment="center")
            c1.markdown(
                f"**{item.subject}**  \n"
                f":gray[{labels.get(item.kind, item.kind)} · {sender_name} · {due_text} · [open]({item.link})]"
            )
            if c2.button("Add", key=f"add_{item.id}", help=f"Add as a task, {due_text}, {ui.fmt_minutes(guess_minutes)}"):
                add_from_email(item, item.subject[:120], default_due, guess_minutes, guess_priority)
            with c3.popover("Edit"):
                st.caption(item.snippet)
                with st.form(f"inbox_{item.id}", border=False):
                    name = st.text_input("Task", value=item.subject[:120])
                    a, b = st.columns(2)
                    due_day = a.date_input("Due", value=default_due.date())
                    due_time = b.time_input("Time", value=default_due.time(), step=900)
                    a, b = st.columns(2)
                    est = a.number_input("Minutes", 15, 6000, guess_minutes, step=15)
                    priority = b.selectbox("Priority", config.PRIORITIES, index=config.PRIORITIES.index(guess_priority))
                    if st.form_submit_button("Add task", type="primary"):
                        add_from_email(item, name, datetime.combine(due_day, due_time), est, priority)
            if c4.button("✕", key=f"dis_{item.id}", help="Dismiss"):
                db.set_inbox_status(item.id, "dismissed")
                st.rerun()
            # No mute for Blackboard: every course shares one sender address
            if item.kind != "blackboard" and c5.button("🔇", key=f"mute_{item.id}", help=f"Hide everything from {sender_name}"):
                address = gmail.sender_address(item.sender)
                muted = sorted(gmail.muted_senders() | {address})
                db.set_preference("gmail_muted_senders", muted)
                hidden = [i for i in items if gmail.sender_address(i.sender) == address]
                for i in hidden:
                    db.set_inbox_status(i.id, "dismissed")
                ui.flash(f"Muted {sender_name}. Hid {len(hidden)} email{'s' if len(hidden) != 1 else ''}.")
                st.rerun()

    muted = sorted(gmail.muted_senders())
    if muted:
        with st.expander(f"Muted senders ({len(muted)})"):
            for address in muted:
                a, b = st.columns([6, 1])
                a.write(address)
                if b.button("Unmute", key=f"unmute_{address}"):
                    db.set_preference("gmail_muted_senders", [m for m in muted if m != address])
                    st.rerun()
