"""
The Prep button: Claude reads an assignment's instructions, rubric and files
and returns a plan for doing it yourself: requirements checklist, what you
need, steps with time estimates, and concepts to review.

It is set up as a helper, not a ghostwriter: it won't write the deliverable
or answer graded questions, which keeps you inside GW's academic integrity rules.
"""

import json
import os
from pathlib import Path

from autoplan import config, db

MAX_FILE_CHARS = 12000

SYSTEM = """You help a college student (Will, GWU) get ready to do an assignment himself.
Read the instructions, rubric and attached files, then plan the work.

Rules:
- Do not write the deliverable, solve graded problems, or draft answers he could submit.
- Do explain what's being asked, list every requirement (formatting, length, file type, citations),
  name the materials and tools he needs, break the work into steps with realistic minutes,
  and point out concepts worth reviewing.
- Be specific to this assignment. If something is unclear, list it as a question for the professor.
- Always answer by calling the save_prep tool."""

PREP_TOOL = {
    "name": "save_prep",
    "description": "Save the assignment prep plan.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "One or two sentences: what the assignment actually is"},
            "requirements": {"type": "array", "items": {"type": "string"}, "description": "Checklist of everything graded or required"},
            "materials": {"type": "array", "items": {"type": "string"}, "description": "Readings, data, tools, files needed"},
            "steps": {"type": "array", "items": {"type": "object", "properties": {
                "step": {"type": "string"}, "minutes": {"type": "integer"}}, "required": ["step", "minutes"]}},
            "concepts": {"type": "array", "items": {"type": "string"}, "description": "Concepts to review first"},
            "questions": {"type": "array", "items": {"type": "string"}, "description": "Unclear points to ask the professor"},
        },
        "required": ["summary", "requirements", "materials", "steps"],
    },
}


def extract_text(path: str) -> str:
    """Readable text from a PDF, Word file, or plain text file. Other types are skipped."""
    p = Path(path)
    suffix = p.suffix.lower()
    try:
        if suffix == ".pdf":
            from pypdf import PdfReader

            return "\n".join(page.extract_text() or "" for page in PdfReader(str(p)).pages)[:MAX_FILE_CHARS]
        if suffix == ".docx":
            import docx

            return "\n".join(par.text for par in docx.Document(str(p)).paragraphs)[:MAX_FILE_CHARS]
        if suffix in (".txt", ".md", ".csv", ".py", ".sql", ".html", ".json"):
            return p.read_text(encoding="utf-8", errors="replace")[:MAX_FILE_CHARS]
    except Exception as e:
        return f"(couldn't read {p.name}: {type(e).__name__})"
    return ""


def build_prompt(task, details: dict | None) -> str:
    parts = [f"Assignment: {task.name}", f"Due: {task.deadline:%A %B %d, %I:%M %p}"]
    if details:
        if details.get("course_code"):
            parts.append(f"Course: {details['course_code']} {details.get('course_name') or ''}".strip())
        if details.get("points"):
            parts.append(f"Points: {details['points']:g}")
        parts.append("Instructions from Blackboard:\n" + (details.get("instructions") or "(none posted)"))
        for f in details.get("attachments", []):
            text = extract_text(f["path"])
            if text:
                parts.append(f"Attached file {f['name']}:\n{text}")
            else:
                parts.append(f"Attached file {f['name']} (not readable as text)")
    else:
        parts.append("No Blackboard details found; plan from the title and say what's missing.")
    return "\n\n".join(parts)


def run(task_id: int, client=None) -> dict:
    task = next((t for t in db.list_tasks(include_deleted=True) if t.id == task_id), None)
    if task is None:
        raise ValueError(f"No task {task_id}")
    details = db.get_bb_assignment_for_task(task_id)
    from autoplan import llm

    plan = llm.structured(SYSTEM, build_prompt(task, details), PREP_TOOL, max_tokens=2000, api=client)
    plan["estimated_minutes"] = sum(int(s.get("minutes", 0)) for s in plan.get("steps", []))
    plan["had_blackboard_details"] = details is not None
    db.save_prep(task_id, plan)
    return plan
