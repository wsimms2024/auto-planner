"""Grades page: where you stand in each class, and what you need to hit a target."""

import pandas as pd
import streamlit as st

from autoplan import db, grades, ui

st.title("Grades")
ui.show_flash()

courses = db.list_bb_courses()
if not courses:
    st.write("No grades yet. Connect Blackboard on the Sync page (Blackboard tab) and they'll show up here.")
    st.stop()

last_run, _ = db.get_sync_state("blackboard_details")
if last_run:
    st.caption(f"From Blackboard, {ui.fmt_dt(last_run)}. Points-based; classes with weighted categories are estimates.")

letters = [l for l, _ in grades.LETTER_CUTOFFS if l != "F"]

for c in courses:
    items = db.list_bb_grades(c["id"])
    s = grades.summary(items)
    with st.container(border=True):
        left, right = st.columns([3, 2], vertical_alignment="center")
        left.markdown(f"### {c['code']}")
        left.caption(c["name"])
        if s["percent"] is None:
            right.markdown("No graded work yet.")
        else:
            standing = f"**{s['percent']}%** · {s['letter']}"
            if s["blackboard_total"]:
                standing += f"  \n:gray[Blackboard total: {s['blackboard_total']}]"
            right.markdown(standing)

        if s["remaining_count"]:
            target = st.selectbox("Aiming for", letters, index=letters.index("B+"), key=f"target_{c['id']}")
            need = grades.needed_for(items, grades.cutoff(target))
            msg = f"You need about **{need['needed_percent']}%** on the remaining {need['remaining_points']:g} points to finish with a {target}."
            if need.get("note"):
                msg += f" {need['note']}"
            st.markdown(msg)

        rows = [i for i in items if not i["is_total"]]
        if rows:
            with st.expander(f"{s['graded_count']} graded, {s['remaining_count']} to go"):
                st.dataframe(
                    pd.DataFrame([{
                        "Item": i["name"],
                        "Score": i["display"] or (f"{i['score']:g} / {i['possible']:g}" if i["score"] is not None and i["possible"] else ""),
                        "Out of": f"{i['possible']:g}" if i["possible"] else "",
                        "Due": ui.fmt_dt(i["due"]) if i["due"] else "",
                    } for i in rows]),
                    hide_index=True, width="stretch",
                )
