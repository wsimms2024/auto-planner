"""Events page: classes, work, gym, and other fixed commitments."""

from datetime import date, datetime, time, timedelta

import streamlit as st

from autoplan import config, db, scheduler, ui
from autoplan.integrations import sync
from autoplan.models import Event


st.title("Events")
ui.show_flash()


def event_form(key: str, existing: Event | None = None) -> None:
    """Add form when existing is None, edit form otherwise."""
    is_weekly_default = existing.is_recurring if existing else False
    kind = st.radio(
        "Type", ["One-time", "Weekly"], index=int(is_weekly_default), horizontal=True, key=f"{key}_kind"
    )
    weekly = kind == "Weekly"

    # The form key changes after each successful add, which resets the fields.
    # Fields are NOT cleared on errors, so you don't lose what you typed.
    version = st.session_state.get(f"{key}_version", 0)
    with st.form(f"{key}_form_{version}"):
        name = st.text_input("Name", value=existing.name if existing else "")
        category = st.selectbox(
            "Category",
            config.EVENT_CATEGORIES,
            index=config.EVENT_CATEGORIES.index(existing.category) if existing else 0,
        )

        if weekly:
            c1, c2 = st.columns(2)
            first_day = c1.date_input("Starts on", value=existing.start.date() if existing else date.today())
            until = c2.date_input(
                "Repeat until",
                value=(existing.recurrence_until if existing and existing.recurrence_until else date.today() + timedelta(weeks=12)),
            )
            days = st.multiselect(
                "Repeats on",
                options=list(range(7)),
                default=existing.recurrence_days if existing and existing.recurrence_days else [],
                format_func=lambda d: ui.DAY_NAMES[d],
            )
        else:
            first_day = st.date_input("Date", value=existing.start.date() if existing else date.today())

        c1, c2 = st.columns(2)
        start_t = c1.time_input("Start", value=existing.start.time() if existing else time(9, 0), step=900)
        end_t = c2.time_input("End", value=existing.end.time() if existing else time(10, 0), step=900)
        allow_overlap = st.checkbox("Save even if it overlaps another event")
        submitted = st.form_submit_button("Save changes" if existing else "Add event", type="primary")

    if not submitted:
        return

    start = datetime.combine(first_day, start_t)
    end = datetime.combine(first_day, end_t)
    if not weekly and end_t < start_t:
        # An end time earlier than the start means it runs past midnight
        end += timedelta(days=1)

    event = Event(
        id=existing.id if existing else None,
        name=name,
        start=start,
        end=end,
        category=category,
        recurrence_days=days if weekly else None,
        recurrence_until=until if weekly else None,
    )

    problems = event.validate()
    if problems:
        for p in problems:
            st.error(p)
        return

    if not allow_overlap:
        conflicts = scheduler.find_conflicts(event, db.list_events())
        if conflicts:
            lines = [f"- {o.name}, {ui.fmt_dt(o.start)}-{ui.fmt_time(o.end)}" for _, o in conflicts[:5]]
            more = f"\n- ...and {len(conflicts) - 5} more" if len(conflicts) > 5 else ""
            st.error("This overlaps with:\n" + "\n".join(lines) + more + "\n\nAdjust the time or check the overlap box.")
            return

    if existing:
        db.update_event(event)
        ui.flash(f"Updated **{event.name}**.")
    else:
        db.add_event(event)
        st.session_state[f"{key}_version"] = version + 1
        ui.flash(f"Added **{event.name}**.")
    sync.push_to_google()  # show it on Google Calendar / your phone
    st.rerun()


week_tab, add_tab, edit_tab, deleted_tab = st.tabs(["This week", "Add event", "Edit or delete", "Deleted"])

with week_tab:
    ui.render_week("events")

with add_tab:
    event_form("add")

with edit_tab:
    all_events = db.list_events()
    events = [e for e in all_events if not e.is_imported]
    imported = len(all_events) - len(events)
    if imported:
        st.caption(
            f"{imported} event{'s come' if imported != 1 else ' comes'} from Google Calendar or Blackboard. Edit those at the source; "
            "AutoPlan picks up changes on the next sync."
        )
    if not events:
        st.write("No events added by hand yet.")
    else:
        chosen = st.selectbox("Event", events, format_func=ui.describe_event, key="edit_pick")
        if chosen.is_recurring:
            st.caption("Changes apply to every occurrence of this weekly event.")
        event_form(f"edit_{chosen.id}", chosen)
        if st.button("Delete this event", key=f"del_{chosen.id}"):
            db.delete_event(chosen.id)
            ui.flash(f"Deleted **{chosen.name}**. You can restore it from the Deleted tab.")
            sync.push_to_google()  # show it on Google Calendar / your phone
            st.rerun()

with deleted_tab:
    deleted = [e for e in db.list_deleted_events() if not e.is_imported]
    if not deleted:
        st.write("Nothing deleted.")
    for e in deleted:
        c1, c2 = st.columns([5, 1])
        c1.write(ui.describe_event(e))
        if c2.button("Restore", key=f"restore_{e.id}"):
            db.restore_event(e.id)
            ui.flash(f"Restored **{e.name}**.")
            sync.push_to_google()  # show it on Google Calendar / your phone
            st.rerun()
