"""
What's happening in each class on a given day, from the syllabus schedule.

Claude turns each syllabus's course schedule into dated class sessions (topic,
readings, what's due) once, and again only when the syllabus changes. Week-based
schedules are converted to dates using the meeting days on your calendar.
Looking up "today" is then instant and free.
"""

import hashlib
import os
from datetime import date, datetime, timedelta

from autoplan import db, scheduler

TOOL = {
    "name": "save_schedule",
    "description": "Save the dated class sessions from the syllabus schedule.",
    "input_schema": {
        "type": "object",
        "properties": {"sessions": {"type": "array", "items": {"type": "object", "properties": {
            "date": {"type": "string", "description": "YYYY-MM-DD of the class meeting"},
            "topic": {"type": "string"},
            "readings": {"type": "string", "description": "Readings or prep for that class, if listed"},
            "due": {"type": "string", "description": "What's due that day, if listed"},
            "notes": {"type": "string", "description": "Anything else: no class, guest speaker, exam, room change"},
        }, "required": ["date", "topic"]}}},
        "required": ["sessions"],
    },
}

SYSTEM = """You turn a course syllabus's schedule into dated class sessions. Today is {today}.
Term: {term}. The class meets: {meetings}.
- Use dates written in the schedule. If it's organized by week or session number, convert to dates
  using the class meeting days and the first class date or term start stated in the syllabus.
- One entry per class meeting that has a topic, reading, deadline or note. Keep text short.
- Don't invent topics. If the syllabus has no schedule, return an empty list."""


def meetings_for(code: str, start: date) -> str:
    """'Mon/Wed 3:45 PM' from the calendar, so week-based schedules can be dated."""
    days, when = set(), None
    for e in scheduler.expand_events(db.list_events(), start, start + timedelta(days=13)):
        if code.replace(" ", "").lower() in e.name.replace(" ", "").lower():
            days.add(e.start.weekday())
            when = when or e.start.strftime("%I:%M %p").lstrip("0")
    if not days:
        return "unknown (not on the calendar)"
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return "/".join(names[d] for d in sorted(days)) + (f" at {when}" if when else "")


def term_name(term_code: str) -> str:
    if len(term_code) == 6:
        return {"01": "Spring", "02": "Summer", "03": "Fall"}.get(term_code[4:], "") + f" {term_code[:4]}"
    return "current term"


def run(now: datetime | None = None, force: bool = False, client=None) -> str:
    from autoplan import llm

    now = now or datetime.now()
    if client is None and not os.environ.get("ANTHROPIC_API_KEY"):
        return "class schedules: skipped (no Claude API key)"
    done = []
    for course in db.list_bb_courses():
        syllabus = "\n\n".join(t["text"] for t in db.list_course_texts(course["id"]) if t["kind"] == "syllabus" and t["text"])
        if not syllabus.strip():
            continue
        meetings = meetings_for(course["code"], now.date())
        key = f"class_schedule_hash:{course['id']}"
        h = hashlib.sha1((syllabus + meetings).encode()).hexdigest()
        if not force and db.get_preference_value(key) == h:
            continue
        try:
            data = llm.structured(
                SYSTEM.format(today=now.strftime("%A, %B %d, %Y"), term=term_name(course.get("term", "")), meetings=meetings),
                f"Course: {course['code']} {course['name']}\n\n{syllabus[:24000]}", TOOL, max_tokens=4000, api=client)
        except Exception:
            continue  # retried next run
        sessions = []
        for s in data.get("sessions", []):
            try:
                date.fromisoformat(s["date"])
                sessions.append(s)
            except (KeyError, ValueError):
                continue
        db.replace_class_sessions(course["id"], sessions)
        db.set_preference(key, h)
        done.append(f"{course['code']} ({len(sessions)})")
    return "class schedules: " + (", ".join(done) if done else "no syllabus changes")


def today_in_class(day: date) -> list[dict]:
    """Per course meeting that day: topic, readings, what's due, plus recent announcements."""
    out = []
    for s in db.class_sessions_on(day):
        recent = [t for t in db.list_course_texts(s["course_id"]) if t["kind"] == "announcement" and t.get("posted_at")
                  and t["posted_at"][:10] >= (day - timedelta(days=7)).isoformat()]
        out.append({**s, "announcements": [{"title": a["title"], "text": a["text"][:400]} for a in recent[-3:]]})
    return out
