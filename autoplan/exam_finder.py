"""
Finds exam dates in everything Blackboard has for a class: the syllabus,
announcements, course pages, the course calendar and the gradebook.

Claude reads each class's text and lists the exams it can actually point to
(with a short quote as evidence). Newer announcements beat the syllabus.
A class is only re-read when its text changed, so this costs little.
Without an API key, a simpler keyword-and-date scan is used instead.

Found exams wait on the Sync page (Exam dates) or the bot until you confirm;
confirming puts them on your calendar, and exam mode plans review sessions.
"""

import hashlib
import json
import os
import re
from datetime import date, datetime, time, timedelta

from autoplan import config, db, task_rules

MAX_CHARS = 30000
EXAM_WORDS = re.compile(r"\b(exams?|midterms?|final exam|finals?|tests?|prelim)\b", re.IGNORECASE)

TOOL = {
    "name": "save_exams",
    "description": "Save the exams found for this course.",
    "input_schema": {
        "type": "object",
        "properties": {"exams": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Course code + exam, e.g. 'ISTM 4205 Midterm' or 'ACCY 2001 Exam 2'"},
            "date": {"type": "string", "description": "YYYY-MM-DD"},
            "start_time": {"type": "string", "description": "HH:MM 24-hour, only if stated"},
            "end_time": {"type": "string", "description": "HH:MM 24-hour, only if stated"},
            "location": {"type": "string"},
            "source": {"type": "string", "enum": ["syllabus", "announcement", "content", "calendar", "gradebook"]},
            "quote": {"type": "string", "description": "Under 20 words from the text that shows the date"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        }, "required": ["name", "date", "source", "quote", "confidence"]}}},
        "required": ["exams"],
    },
}

SYSTEM = """You find exam dates for a college student's course. Today is {today}.
Read the course text (syllabus, announcements, pages, calendar, gradebook) and call save_exams with every
upcoming exam, midterm, final exam, or in-class test that has a date you can point to.
Rules:
- Only dates actually stated or directly computable from a dated schedule in the text. Never guess.
- When an announcement changes a date from the syllabus, use the announcement (it's newer).
- Skip exams before today. Skip regular quizzes unless called a test or exam.
- confidence: high = date and time stated in an announcement or calendar; medium = stated date, no time,
  or only in the syllabus; low = inferred from a weekly schedule.
- If nothing qualifies, return an empty list."""


def course_document(course_id: str) -> str:
    """The course text, most useful first, trimmed to MAX_CHARS."""
    parts = []
    for t in db.list_course_texts(course_id):
        text = (t["text"] or "").strip()
        if t["kind"] == "content" and not (EXAM_WORDS.search(t["title"] + " " + text) or re.search(r"\b\d{1,2}/\d{1,2}\b|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? \d", text, re.I)):
            continue  # ordinary pages with no dates or exam words
        limit = 14000 if t["kind"] == "syllabus" else 3000
        posted = f" | posted {t['posted_at'][:10]}" if t.get("posted_at") else ""
        parts.append(f"[{t['kind']} | {t['title']}{posted}]\n{text[:limit]}")
    return "\n\n".join(parts)[:MAX_CHARS]


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


def extract_with_claude(code: str, document: str, today: date, client=None) -> list[dict]:
    from autoplan import llm

    data = llm.structured(SYSTEM.format(today=today.strftime("%A, %B %d, %Y")), f"Course: {code}\n\n{document}",
                          TOOL, max_tokens=1500, api=client)
    return list(data.get("exams", []))


def extract_with_rules(code: str, course_id: str, today: date) -> list[dict]:
    """No API key: sentences with an exam word and a date."""
    from autoplan.integrations import gmail

    found = {}
    for t in db.list_course_texts(course_id):
        received = datetime.fromisoformat(t["posted_at"][:19].replace("Z", "")) if t.get("posted_at") else datetime.combine(today, time())
        for sentence in re.split(r"(?<=[.!?\n])\s+", f"{t['title']}. {t['text']}"):
            m = EXAM_WORDS.search(sentence)
            if not m:
                continue
            due = gmail.extract_due(sentence, received)
            if not due or due.date() < today:
                continue
            label = re.search(r"(midterm(?: exam)?|final exam|exam\s*\d+|test\s*\d+|exam|test)", sentence, re.I).group(1).title()
            name = f"{code} {label}"
            has_time = due.time() != time(23, 59)
            found[name.lower()] = {
                "name": name, "date": due.date().isoformat(), "start_time": due.strftime("%H:%M") if has_time else None,
                "source": t["kind"] if t["kind"] in TOOL["input_schema"]["properties"]["exams"]["items"]["properties"]["source"]["enum"] else "content",
                "quote": sentence.strip()[:120], "confidence": "medium" if t["kind"] in ("announcement", "calendar") else "low",
            }
    return list(found.values())


def run(now: datetime | None = None, force: bool = False, client=None) -> str:
    """Look for exam dates in every current course whose text changed since last time."""
    now = now or datetime.now()
    new = changed = checked = 0
    failed: list[str] = []
    use_claude = client is not None or bool(os.environ.get("ANTHROPIC_API_KEY"))
    for course in db.list_bb_courses():
        document = course_document(course["id"])
        if not document.strip():
            continue
        key = f"exam_finder_hash:{course['id']}"
        h = _hash(document)
        if not force and db.get_preference_value(key) == h:
            continue
        checked += 1
        try:
            exams = extract_with_claude(course["code"], document, now.date(), client) if use_claude else extract_with_rules(course["code"], course["id"], now.date())
        except Exception as e:  # one class failing shouldn't stop the rest; it's retried next run
            failed.append(f"{course['code']} ({type(e).__name__})")
            continue
        for e in exams:
            try:
                if date.fromisoformat(e["date"]) < now.date():
                    continue
            except (KeyError, ValueError):
                continue
            result = db.upsert_exam_candidate({**e, "course_id": course["id"]})
            new += result == "new"
            changed += result == "changed"
        db.set_preference(key, h)
    if not checked:
        return "exam dates: no course changes"
    message = f"exam dates: read {checked} course{'s' if checked != 1 else ''}, {new} new, {changed} changed"
    return message + (f"; couldn't read {', '.join(failed)}" if failed else "")


# ---------- Confirming ----------

def _class_time(course_code: str, day: date) -> tuple[time, time] | None:
    """If the class meets that day, assume the exam is in class."""
    from autoplan import scheduler

    for e in scheduler.expand_events(db.list_events(), day, day):
        if course_code.replace(" ", "").lower() in e.name.replace(" ", "").lower():
            return e.start.time(), e.end.time()
    return None


def confirm(candidate_id: int, day: date | None = None, start: time | None = None, end: time | None = None,
            location: str | None = None) -> dict:
    """Put a found exam on the calendar (or move it, if its date changed). Exam mode then plans review."""
    from autoplan import exam_mode
    from autoplan.integrations import sync
    from autoplan.models import Event

    c = next((x for x in db.list_exam_candidates() if x["id"] == candidate_id), None)
    if not c:
        return {"error": f"No exam candidate {candidate_id}."}
    day = day or date.fromisoformat(c["date"])
    note = ""
    if start is None and c.get("start_time"):
        start = time.fromisoformat(c["start_time"])
    if end is None and c.get("end_time"):
        end = time.fromisoformat(c["end_time"])
    if start is None:
        meeting = _class_time(c.get("course_code") or "", day)
        if meeting:
            start, end = meeting
            note = "time taken from your class meeting"
        else:
            start, note = time(9, 0), "time not stated: set to 9:00 AM, edit if needed"
    start_dt = datetime.combine(day, start)
    end_dt = datetime.combine(day, end) if end else start_dt + timedelta(minutes=75)
    name = c["name"] if task_rules.is_exam(c["name"]) else f"{c['name']} Exam"
    if c.get("location") or location:
        name = f"{name} ({location or c['location']})"

    existing = db.get_event(c["event_id"]) if c.get("event_id") else None
    if existing and not existing.deleted_at:
        existing.name, existing.start, existing.end = name, start_dt, end_dt
        db.update_event(existing)
        event_id = existing.id
    else:
        event_id = db.add_event(Event(name=name, start=start_dt, end=end_dt, category="class"))
    db.set_exam_status(candidate_id, "added", event_id)
    exam_mode.sync_exam_tasks()
    sync.push_to_google()
    return {"added": name, "when": f"{start_dt:%a %b %d, %I:%M %p}".replace(" 0", " "), "note": note}


def dismiss(candidate_id: int) -> None:
    db.set_exam_status(candidate_id, "dismissed")
