"""Tasks page: assignments and anything with a deadline."""

from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from autoplan import config, db, ui
from autoplan.models import Task


st.title("Tasks")
ui.show_flash()
now = datetime.now()


def task_form(key: str, existing: Task | None = None) -> None:
    """Add form when existing is None, edit form otherwise."""
    # The form key changes after each successful add, which resets the fields.
    # Fields are NOT cleared on errors, so you don't lose what you typed.
    version = st.session_state.get(f"{key}_version", 0)
    with st.form(f"{key}_form_{version}"):
        name = st.text_input("Name", value=existing.name if existing else "")
        c1, c2 = st.columns(2)
        due_day = c1.date_input("Due date", value=existing.deadline.date() if existing else date.today() + timedelta(days=7))
        due_time = c2.time_input("Due time", value=existing.deadline.time() if existing else time(23, 59), step=900)
        c1, c2 = st.columns(2)
        minutes = c1.number_input(
            "Estimated time (minutes)",
            min_value=15,
            max_value=6000,
            step=15,
            value=existing.estimated_minutes if existing else 60,
        )
        priority = c2.selectbox(
            "Priority", config.PRIORITIES, index=config.PRIORITIES.index(existing.priority if existing else "Medium")
        )
        plan = st.checkbox("Plan study time for this", value=existing.plan_sessions if existing else True)
        submitted = st.form_submit_button("Save changes" if existing else "Add task", type="primary")

    if not submitted:
        return

    task = Task(
        id=existing.id if existing else None,
        name=name,
        deadline=datetime.combine(due_day, due_time),
        estimated_minutes=int(minutes),
        priority=priority,
        status=existing.status if existing else "open",
        plan_sessions=plan,
    )
    problems = task.validate()
    if problems:
        for p in problems:
            st.error(p)
        return
    if existing:
        db.update_task(task)
        ui.flash(f"Updated **{task.name}**.")
    else:
        db.add_task(task)
        st.session_state[f"{key}_version"] = version + 1
        ui.flash(f"Added **{task.name}** ({ui.fmt_minutes(task.estimated_minutes)}, due {ui.fmt_dt(task.deadline)}).")
    st.rerun()


planned = {}
for session in ui.accepted_sessions():
    planned[session.task_id] = planned.get(session.task_id, 0) + int((session.end - session.start).total_seconds() // 60)


def status_table(tasks: list[Task], key: str) -> None:
    """Editable table: ticking Done or Plan saves right away."""
    df = pd.DataFrame(
        {
            "Done": [t.status == "done" for t in tasks],
            "Task": [t.name for t in tasks],
            "Due": [ui.fmt_dt(t.deadline) for t in tasks],
            "When": [ui.fmt_due(t.deadline, now) for t in tasks],
            "Estimate": [ui.fmt_minutes(t.estimated_minutes) for t in tasks],
            "Priority": [t.priority for t in tasks],
            "Plan": [t.plan_sessions for t in tasks],
            "Planned": [ui.fmt_minutes(planned[t.id]) if planned.get(t.id) else "" for t in tasks],
            "From": [{"manual": "", "blackboard": "Blackboard", "gmail": "Email"}.get(t.source, t.source) for t in tasks],
        },
        index=[t.id for t in tasks],
    )
    edited = st.data_editor(
        df,
        key=ui.editor_key(key),
        hide_index=True,
        width="stretch",
        disabled=["Task", "Due", "When", "Estimate", "Priority", "Planned", "From"],
        column_config={
            "Done": st.column_config.CheckboxColumn(width="small"),
            "Plan": st.column_config.CheckboxColumn(width="small", help="Suggest study sessions for this task"),
        },
    )
    done_changed = edited.index[edited["Done"] != df["Done"]]
    plan_changed = edited.index[edited["Plan"] != df["Plan"]]
    if len(done_changed) or len(plan_changed):
        for task_id in done_changed:
            db.set_task_status(int(task_id), "done" if edited.at[task_id, "Done"] else "open")
        for task_id in plan_changed:
            db.set_task_planning(int(task_id), bool(edited.at[task_id, "Plan"]))
        ui.reset_editor(key)
        st.rerun()


tasks = db.list_tasks()
open_tasks = [t for t in tasks if t.status == "open"]
overdue = [t for t in open_tasks if t.is_overdue(now)]
upcoming = [t for t in open_tasks if not t.is_overdue(now)]
done = [t for t in tasks if t.status == "done"]

list_tab, details_tab, add_tab, edit_tab, deleted_tab = st.tabs(["Tasks", "Details and prep", "Add task", "Edit or delete", "Deleted"])

with details_tab:
    open_tasks = [t for t in tasks if t.status == "open"]
    if not open_tasks:
        st.write("No open tasks.")
    else:
        chosen = st.selectbox("Task", open_tasks, format_func=lambda t: f"{t.name} · due {ui.fmt_dt(t.deadline)}", key="details_pick")
        details = db.get_bb_assignment_for_task(chosen.id)
        if details:
            meta = [details.get("course_code") or "", f"{details['points']:g} points" if details.get("points") else ""]
            st.caption("  ".join(m for m in meta if m) + f"  [open in Blackboard]({details['url']})")
            with st.expander("Instructions", expanded=not details.get("attachments")):
                st.write(details.get("instructions") or "No instructions posted.")
            for f in details.get("attachments", []):
                path = Path(f["path"])
                if path.exists():
                    st.download_button(f"📎 {f['name']}", path.read_bytes(), file_name=f["name"], key=f"dl_{chosen.id}_{f['name']}")
        else:
            st.caption("No Blackboard details for this task yet. Connect Blackboard on the Sync page, or Prep from the title alone.")

        plan = db.get_prep(chosen.id)
        label = "Prep again" if plan else "Prep this assignment"
        if st.button(label, type="secondary" if plan else "primary", help="Claude reads the instructions and files and plans the work. It doesn't do the assignment."):
            with st.spinner("Reading the assignment..."):
                try:
                    from autoplan import prep

                    plan = prep.run(chosen.id)
                except Exception as e:
                    st.error(f"Prep didn't work: {type(e).__name__}: {e}")
        if plan:
            st.markdown(f"**{plan.get('summary', '')}**")
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Requirements**")
                st.markdown("\n".join(f"- {r}" for r in plan.get("requirements", [])) or "-")
                if plan.get("materials"):
                    st.markdown("**You'll need**")
                    st.markdown("\n".join(f"- {m}" for m in plan["materials"]))
            with c2:
                st.markdown(f"**Steps** · about {ui.fmt_minutes(plan.get('estimated_minutes', 0))}")
                st.markdown("\n".join(f"{i}. {s['step']} :gray[{ui.fmt_minutes(s['minutes'])}]" for i, s in enumerate(plan.get("steps", []), 1)))
                if plan.get("concepts"):
                    st.markdown("**Review first**")
                    st.markdown("\n".join(f"- {c}" for c in plan["concepts"]))
            if plan.get("questions"):
                st.markdown("**Ask the professor**")
                st.markdown("\n".join(f"- {q}" for q in plan["questions"]))
            est = plan.get("estimated_minutes") or 0
            if est and est != chosen.estimated_minutes and st.button(f"Use {ui.fmt_minutes(est)} as the estimate (now {ui.fmt_minutes(chosen.estimated_minutes)})"):
                chosen.estimated_minutes = int(est)
                db.update_task(chosen)
                ui.flash(f"Estimate for {chosen.name} set to {ui.fmt_minutes(est)}.")
                st.rerun()

with list_tab:
    if overdue:
        st.subheader(f":red[Overdue ({len(overdue)})]")
        status_table(overdue, "overdue_table")
    st.subheader(f"Upcoming ({len(upcoming)})")
    if upcoming:
        status_table(upcoming, "upcoming_table")
    else:
        st.write("Nothing due. Add a task in the next tab.")
    if done:
        with st.expander(f"Completed ({len(done)})"):
            status_table(done, "done_table")

with add_tab:
    task_form("add")

with edit_tab:
    if not tasks:
        st.write("No tasks yet.")
    else:
        chosen = st.selectbox(
            "Task", tasks, format_func=lambda t: f"{t.name} · due {ui.fmt_dt(t.deadline)}", key="edit_pick"
        )
        if chosen.source == "blackboard":
            st.caption("From Blackboard. Your estimate, priority and status stay put; name and due date follow Blackboard on each sync.")
        task_form(f"edit_{chosen.id}", chosen)
        if st.button("Delete this task", key=f"del_{chosen.id}"):
            db.delete_task(chosen.id)
            ui.flash(f"Deleted **{chosen.name}**. You can restore it from the Deleted tab.")
            st.rerun()

with deleted_tab:
    deleted = db.list_deleted_tasks()
    if not deleted:
        st.write("Nothing deleted.")
    for t in deleted:
        c1, c2 = st.columns([5, 1])
        c1.write(f"{t.name} · due {ui.fmt_dt(t.deadline)}")
        if c2.button("Restore", key=f"restore_{t.id}"):
            db.restore_task(t.id)
            ui.flash(f"Restored **{t.name}**.")
            st.rerun()
