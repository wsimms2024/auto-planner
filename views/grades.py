"""
Grades page: Blackboard where it's right, your entries where it isn't.
Edit scores, categories or ignore items right in each class's table.
"""

import pandas as pd
import streamlit as st

from autoplan import db, grades, ui

st.title("Grades")
ui.show_flash()

courses = db.list_bb_courses()
if not courses:
    st.write("No classes yet. Connect Blackboard on the Sync page (Blackboard tab) and they'll show up here.")
    st.stop()

last_run, _ = db.get_sync_state("blackboard_details")
if last_run:
    st.caption(f"Blackboard read {ui.fmt_dt(last_run)}. Your edits and entries always win over Blackboard.")

letters = [l for l, _ in grades.LETTER_CUTOFFS if l != "F"]


def save_table_edits(course_id: str, original: pd.DataFrame, edited: pd.DataFrame, key: str) -> bool:
    """Write changed cells back: corrections for Blackboard items, updates for your own entries, new rows as entries."""
    def num(v):
        return None if v is None or (isinstance(v, float) and pd.isna(v)) or v == "" else float(v)

    before_by_id = {row["_id"]: row for _, row in original.iterrows()}
    changed = False
    for _, row in edited.iterrows():
        row_id = row.get("_id")
        if not isinstance(row_id, str) or not row_id:  # a row you added
            if str(row.get("Item") or "").strip() and row.get("Item") is not None:
                db.add_grade_entry(course_id, str(row["Item"]), num(row["Score"]), num(row["Out of"]),
                                   row["Category"] if isinstance(row.get("Category"), str) else None)
                changed = True
            continue
        before = before_by_id.get(row_id)
        if before is None:
            continue
        same = all((num(row[c]) == num(before[c])) if c in ("Score", "Out of") else (row[c] == before[c])
                   for c in ("Item", "Category", "Score", "Out of", "Ignore"))
        if same:
            continue
        changed = True
        if row_id.startswith("bb:"):
            values = {"category": row["Category"], "ignored": bool(row["Ignore"])}
            if num(row["Score"]) != num(before["Score"]):
                values["score"] = num(row["Score"])
            if num(row["Out of"]) != num(before["Out of"]):
                values["possible"] = num(row["Out of"])
            db.set_grade_override(row_id[3:], **values)
        elif bool(row["Ignore"]):
            db.delete_grade_entry(int(row_id[2:]))
        else:
            db.update_grade_entry(int(row_id[2:]), name=str(row["Item"]), score=num(row["Score"]),
                                  possible=num(row["Out of"]), category=row["Category"])
    if changed:
        ui.reset_editor(key)
    return changed


for c in courses:
    s = grades.standing(c["id"])
    with st.container(border=True):
        left, right = st.columns([3, 2], vertical_alignment="center")
        left.markdown(f"### {c['code']}")
        left.caption(c["name"])
        if s["shown_percent"] is None:
            right.markdown("No grades yet. Add them below or wait for Blackboard.")
        else:
            how = "Blackboard's total" if s["mode"] == "blackboard_total" else ("weighted" if s["weighted"] else "points")
            right.markdown(f"**{s['shown_percent']}%** · {s['shown_letter']}  \n:gray[{how}"
                           + (f" · Blackboard shows {s['blackboard_total']}" if s["blackboard_total"] and s["mode"] != "blackboard_total" else "") + "]")

        if s["stale"]:
            st.warning(f"{len(s['stale'])} past-due item{'s' if len(s['stale']) != 1 else ''} still ungraded on Blackboard "
                       f"({', '.join(i['name'] for i in s['stale'][:3])}). The gradebook may be behind: add the grade yourself below.")

        if s["remaining_count"]:
            target = st.selectbox("Aiming for", letters, index=letters.index("B+"), key=f"target_{c['id']}")
            need = grades.needed_for(s["items"], grades.cutoff(target), s["weights"])
            if need["needed_percent"] is not None:
                st.markdown(f"You need about **{need['needed_percent']}%** on what's left to finish with a {target}." + (f" {need['note']}" if need.get("note") else ""))

        # ---------- items table (editable) ----------
        key = f"grades_{c['id']}"
        rows = [i for i in s["items"] if not i["is_total"]]
        categories = list(dict.fromkeys(grades.CATEGORIES + list(s["weights"])))
        df = pd.DataFrame(
            [{"_id": i["id"], "Item": i["name"], "Category": i["category"], "Score": i["score"], "Out of": i["possible"],
              "Due": ui.fmt_dt(i["due"]) if i["due"] else "", "From": i["source"], "Ignore": i["ignored"]} for i in rows],
            columns=["_id", "Item", "Category", "Score", "Out of", "Due", "From", "Ignore"],
        )
        dupes = sum(1 for i in rows if i.get("duplicate"))
        label = f"{s['graded_count']} graded, {s['remaining_count']} to go · edit or add grades"
        with st.expander(label, expanded=st.session_state.get(f"open_{c['id']}", False)):
            if dupes:
                st.caption(f"{dupes} duplicate Blackboard column{'s are' if dupes != 1 else ' is'} left out (same name and points as another row).")
            # In a form, edits don't reload the page until you save, so nothing gets lost mid-edit
            with st.form(f"gradeform_{c['id']}", border=False):
                edited = st.data_editor(
                    df, key=ui.editor_key(key), num_rows="dynamic", hide_index=True, width="stretch",
                    disabled=["Due", "From"],
                    column_config={
                        "_id": None,  # hidden: links each row to its Blackboard item or your entry
                        "Category": st.column_config.SelectboxColumn(options=categories),
                        "Score": st.column_config.NumberColumn(min_value=0, step=0.5),
                        "Out of": st.column_config.NumberColumn(min_value=0, step=0.5),
                        "Ignore": st.column_config.CheckboxColumn(help="Leave out of the math (deletes your own entries)"),
                    },
                )
                st.caption("Edit as many cells as you like, then Save. Tick Ignore to drop an item, or add a row at the bottom for a grade Blackboard doesn't have.")
                saved = st.form_submit_button("Save changes", type="primary")
            if saved:
                st.session_state[f"open_{c['id']}"] = True  # keep this class open after saving
                if save_table_edits(c["id"], df, edited, key):
                    ui.flash(f"Saved {c['code']} grades.")
                else:
                    ui.flash("No changes to save.", "warning")
                st.rerun()

        # ---------- weights and mode ----------
        with st.expander("Syllabus weights and how to calculate"):
            mode = st.radio("Show", ["Calculate from items", "Use Blackboard's total"], horizontal=True,
                            index=1 if s["mode"] == "blackboard_total" else 0, key=f"mode_{c['id']}")
            suggestion = st.session_state.get(f"suggested_{c['id']}")
            current = suggestion["weights"] if suggestion else s["weights"]
            cats = list(dict.fromkeys(list(current) + grades.CATEGORIES))
            cols = st.columns(3)
            new_weights = {}
            for n, cat in enumerate(cats):
                new_weights[cat] = cols[n % 3].number_input(f"{cat} %", 0.0, 100.0, float(current.get(cat, 0)), step=5.0, key=f"w_{c['id']}_{cat}")
            total = sum(new_weights.values())
            st.caption(f"Total {total:g}%. Leave everything at 0 for points-based. Categories at 0 are left out.")
            if suggestion and suggestion.get("notes"):
                st.info(f"From the syllabus: {suggestion['notes']}")
            a, b = st.columns(2)
            if a.button("Save weights", key=f"saveweights_{c['id']}", type="primary"):
                db.set_course_grade_settings(c["id"], mode="blackboard_total" if mode.startswith("Use") else "calculate", weights=new_weights)
                st.session_state.pop(f"suggested_{c['id']}", None)
                ui.flash(f"Saved {c['code']} settings.")
                st.rerun()
            if c.get("syllabus") and b.button("Suggest from syllabus", key=f"suggest_{c['id']}", help="Claude reads the syllabus from Blackboard; check the numbers before saving"):
                with st.spinner("Reading the syllabus..."):
                    try:
                        result = grades.suggest_weights(c["id"])
                    except Exception as e:
                        result = {"error": f"{type(e).__name__}: {e}"}
                if result.get("error"):
                    ui.flash(result["error"], "error")
                else:
                    st.session_state[f"suggested_{c['id']}"] = result
                    ui.flash("Suggested weights filled in below. Check them, then Save weights.", "warning")
                st.rerun()
