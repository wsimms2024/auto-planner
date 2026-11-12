"""
"Only tell me if something important changed."

After each scheduled sync, the current state is compared with what the bot
last told you about (saved in the database), and only meaningful differences
become a Telegram message:
  - new assignments or tasks due in the next 2 weeks, and moved due dates
  - grades posted on Blackboard
  - exam dates found, or changed
  - important new emails, each with a 1-2 sentence summary (professors and Blackboard first;
    beyond the first few, the rest are counted)
  - imported calendar events in the next 48 hours that were added or cancelled
Nothing else (routine re-syncs, your own edits in the app) triggers a message.
"""

from datetime import datetime, timedelta

from autoplan import db, scheduler

SNAPSHOT_KEY = "alert_snapshot"
TASK_WINDOW_DAYS = 14
EVENT_WINDOW_HOURS = 48
MAX_LINES = 12
MAX_EMAILS = 4


def _fmt(dt: datetime) -> str:
    return f"{dt:%a %b} {dt.day}, {dt:%I:%M %p}".replace(" 0", " ")


def snapshot(now: datetime) -> dict:
    tasks = {str(t.id): [t.name, t.deadline.isoformat(), t.status, t.source] for t in db.list_tasks()}
    grades = {}
    for c in db.list_bb_courses():
        for g in db.list_bb_grades(c["id"]):
            if not g["is_total"]:
                grades[g["id"]] = [c["code"], g["name"], g["score"], g["possible"]]
    exams = {str(e["id"]): [e["name"], e["date"], e.get("start_time"), e["status"]] for e in db.list_exam_candidates()}
    inbox = {i.external_id: [i.subject, i.kind, i.summary or i.snippet[:200]] for i in db.list_inbox()}
    imported = [e for e in db.list_events() if e.source in ("google", "blackboard")]
    soon = {
        f"{e.id}:{e.start.isoformat()}": [e.name, e.start.isoformat()]
        for e in scheduler.expand_events(imported, now.date(), (now + timedelta(hours=EVENT_WINDOW_HOURS)).date())
        if now <= e.start <= now + timedelta(hours=EVENT_WINDOW_HOURS)
    }
    return {"tasks": tasks, "grades": grades, "exams": exams, "inbox": inbox, "soon": soon}


def diff(before: dict, after: dict, now: datetime) -> list[str]:
    lines = []
    horizon = now + timedelta(days=TASK_WINDOW_DAYS)

    for tid, (name, due, status, source) in after["tasks"].items():
        due_dt = datetime.fromisoformat(due)
        old = before["tasks"].get(tid)
        if old is None and status == "open" and due_dt <= horizon and source != "manual":
            lines.append(f"📌 New: {name}, due {_fmt(due_dt)}")
        elif old and status == "open" and old[1] != due and due_dt >= now - timedelta(days=1):
            lines.append(f"📅 {name} moved to {_fmt(due_dt)} (was {_fmt(datetime.fromisoformat(old[1]))})")

    for gid, (code, name, score, possible) in after["grades"].items():
        old = before["grades"].get(gid)
        if score is not None and (old is None or old[2] is None):
            out_of = f"/{possible:g}" if possible else ""
            lines.append(f"🎓 Grade posted: {code} {name}: {score:g}{out_of}")

    for eid, (name, day, start, status) in after["exams"].items():
        old = before["exams"].get(eid)
        when = day + (f" {start}" if start else "")
        if status == "dismissed":
            continue
        if old is None:
            lines.append(f"📝 Exam date found: {name}, {when}. Confirm it on the Sync page or reply \"confirm it\".")
        elif (old[1], old[2]) != (day, start):
            lines.append(f"⚠️ {name} changed to {when} (was {old[1]}{' ' + old[2] if old[2] else ''})")

    new_mail = [v for k, v in after["inbox"].items() if k not in before["inbox"]]
    rank = {"professor": 0, "blackboard": 1, "time-sensitive": 2}
    new_mail.sort(key=lambda v: rank.get(v[1], 3))
    shown = new_mail[:MAX_EMAILS]
    for subject, _kind, *rest in shown:
        summary = rest[0] if rest else ""
        lines.append(f"📬 {subject}" + (f": {summary}" if summary else ""))
    others = len(new_mail) - len(shown)
    if others > 0:
        lines.append(f"📬 {others} more email{'s' if others != 1 else ''} to review")

    for key, (name, start) in before["soon"].items():
        start_dt = datetime.fromisoformat(start)
        if key not in after["soon"] and start_dt > now:
            lines.append(f"❌ Removed from your calendar: {name}, {_fmt(start_dt)}")
    for key, (name, start) in after["soon"].items():
        if key not in before["soon"]:
            lines.append(f"➕ New on your calendar: {name}, {_fmt(datetime.fromisoformat(start))}")

    return lines[:MAX_LINES] + ([f"…and {len(lines) - MAX_LINES} more in the app"] if len(lines) > MAX_LINES else [])


def check(now: datetime | None = None) -> list[str]:
    """Compare with the last snapshot the bot reported, save the new one, return what's worth texting."""
    now = now or datetime.now()
    after = snapshot(now)
    before = db.get_preference_value(SNAPSHOT_KEY)
    db.set_preference(SNAPSHOT_KEY, after)
    if before is None:
        return []  # first run: just remember the current state, no flood of "new" alerts
    # Events that were outside the 48 h window last time aren't "new", they just came into range
    before_soon_window_end = now - timedelta(hours=2) + timedelta(hours=EVENT_WINDOW_HOURS)
    after = {**after, "soon": {k: v for k, v in after["soon"].items() if datetime.fromisoformat(v[1]) <= before_soon_window_end or k in before["soon"]}}
    return diff(before, after, now)
