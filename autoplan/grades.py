"""
Grades: Blackboard where it's right, you where it isn't.

Every course's items are merged from three places:
  1. Blackboard's gradebook (when the professor keeps it up to date)
  2. your corrections to those items (score, out of, category, or ignore)
  3. grades you add yourself (by text to the bot or on the Grades page)

With syllabus weights (Exams 40%, Assignments 30%...) the standing is a
weighted average of category percentages; without them it's points-based.
Each class can instead just show Blackboard's own total.
"""

import json
import os
import re
from datetime import datetime, timedelta

LETTER_CUTOFFS = [
    ("A", 93), ("A-", 90), ("B+", 87), ("B", 83), ("B-", 80), ("C+", 77),
    ("C", 73), ("C-", 70), ("D+", 67), ("D", 63), ("D-", 60), ("F", 0),
]
CATEGORIES = ["Exams", "Quizzes", "Assignments", "Projects", "Participation", "Other"]
STALE_DAYS = 14


def letter(percent: float | None) -> str | None:
    if percent is None:
        return None
    return next(l for l, cutoff in LETTER_CUTOFFS if percent >= cutoff)


def cutoff(letter_grade: str) -> float:
    return dict(LETTER_CUTOFFS)[letter_grade]


def guess_category(name: str) -> str:
    n = name.lower()
    rules = [
        (r"\bexams?\b|midterm|final exam|\btest\b", "Exams"),
        (r"quiz", "Quizzes"),
        (r"project|paper|presentation|essay|report|proposal", "Projects"),
        (r"participation|attendance|discussion|reflection|check-?in|engagement", "Participation"),
        (r"assignment|homework|\bhw\b|lab|\bica\b|ica-?\d|exercise|problem set|case", "Assignments"),
    ]
    return next((cat for pattern, cat in rules if re.search(pattern, n)), "Other")


# ---------- Merging ----------

def items_for(course_id: str) -> list[dict]:
    """All grade items for a course after your corrections, plus your own entries."""
    from autoplan import db

    bb = db.list_bb_grades(course_id)
    overrides = db.get_grade_overrides([i["id"] for i in bb])
    items = []
    for i in bb:
        o = overrides.get(i["id"], {})
        edited = any(o.get(k) is not None for k in ("score", "possible", "category"))
        items.append({
            "id": f"bb:{i['id']}",
            "name": i["name"],
            "category": o.get("category") or guess_category(i["name"]),
            "score": o["score"] if o.get("score") is not None else i["score"],
            "possible": o["possible"] if o.get("possible") is not None else i["possible"],
            "display": i["display"],
            "due": i["due"],
            "source": "Blackboard (edited)" if edited else "Blackboard",
            "ignored": bool(o.get("ignored")),
            "is_total": i["is_total"],
        })
    for e in db.list_grade_entries(course_id):
        items.append({
            "id": f"m:{e['id']}", "name": e["name"], "category": e["category"] or guess_category(e["name"]),
            "score": e["score"], "possible": e["possible"], "display": None, "due": None,
            "source": "You", "ignored": False, "is_total": False,
        })
    return items


def counted(items: list[dict]) -> list[dict]:
    return [i for i in items if not i.get("is_total") and not i.get("ignored") and (i.get("possible") or 0) > 0]


# ---------- Math ----------

def _by_category(items: list[dict], weights: dict | None) -> dict[str, dict]:
    groups: dict[str, dict] = {}
    for i in counted(items):
        cat = i.get("category") or "Other" if weights else "All"
        g = groups.setdefault(cat, {"earned": 0.0, "graded": 0.0, "remaining": 0.0})
        if i.get("score") is not None:
            g["earned"] += i["score"]
            g["graded"] += i["possible"]
        else:
            g["remaining"] += i["possible"]
    return groups


def _weight(cat: str, weights: dict | None) -> float:
    if not weights:
        return 1.0
    return float(weights.get(cat, 0))


def summary(items: list[dict], weights: dict | None = None) -> dict:
    """Current standing. Weighted by category when weights are given, else points-based."""
    weights = weights or None
    groups = _by_category(items, weights)
    graded = {c: g for c, g in groups.items() if g["graded"] > 0 and _weight(c, weights) > 0}
    total_w = sum(_weight(c, weights) for c in graded)
    percent = round(100 * sum(_weight(c, weights) * g["earned"] / g["graded"] for c, g in graded.items()) / total_w, 1) if total_w else None
    unweighted = [c for c in groups if weights and _weight(c, weights) == 0]
    parts = counted(items)
    total = next((i for i in items if i.get("is_total") and (i.get("display") or i.get("score") is not None)), None)
    return {
        "percent": percent,
        "letter": letter(percent),
        "weighted": bool(weights),
        "earned": sum(i["score"] for i in parts if i.get("score") is not None),
        "graded_possible": sum(i["possible"] for i in parts if i.get("score") is not None),
        "remaining_possible": sum(i["possible"] for i in parts if i.get("score") is None),
        "graded_count": sum(1 for i in parts if i.get("score") is not None),
        "remaining_count": sum(1 for i in parts if i.get("score") is None),
        "categories": {c: round(100 * g["earned"] / g["graded"], 1) if g["graded"] else None for c, g in groups.items()},
        "unweighted_categories": unweighted,
        "blackboard_total": (total.get("display") or f"{total['score']:g}") if total else None,
    }


def needed_for(items: list[dict], target_percent: float, weights: dict | None = None) -> dict:
    """
    Average needed on everything not graded yet to finish at target_percent.
    final(x) = sum_c w_c (earned_c + x * remaining_c) / possible_c, over weight sum.
    """
    weights = weights or None
    groups = {c: g for c, g in _by_category(items, weights).items() if _weight(c, weights) > 0 and g["graded"] + g["remaining"] > 0}
    if not any(g["remaining"] for g in groups.values()):
        return {"needed_percent": None, "note": "Nothing left to grade."}
    total_w = sum(_weight(c, weights) for c in groups)
    base = sum(_weight(c, weights) * g["earned"] / (g["graded"] + g["remaining"]) for c, g in groups.items()) / total_w
    slope = sum(_weight(c, weights) * g["remaining"] / (g["graded"] + g["remaining"]) for c, g in groups.items()) / total_w
    needed = round(100 * (target_percent / 100 - base) / slope, 1)
    note = "Not reachable on what's left." if needed > 100 else ("Already locked in, even with zeros on the rest." if needed <= 0 else None)
    return {"needed_percent": needed, "remaining_points": sum(g["remaining"] for g in groups.values()), "note": note}


def stale(items: list[dict], now: datetime | None = None) -> list[dict]:
    """Blackboard items past due by STALE_DAYS+ with no score: the gradebook may be behind."""
    now = now or datetime.now()
    return [i for i in counted(items) if i["source"].startswith("Blackboard") and i.get("score") is None
            and i.get("due") and i["due"] < now - timedelta(days=STALE_DAYS)]


def standing(course_id: str, now: datetime | None = None) -> dict:
    """Everything the Grades page and the bot need for one course."""
    from autoplan import db

    settings = db.get_course_grade_settings(course_id)
    items = items_for(course_id)
    s = summary(items, settings["weights"])
    shown_percent = s["percent"]
    if settings["mode"] == "blackboard_total" and s["blackboard_total"]:
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", s["blackboard_total"])
        shown_percent = float(m.group(1)) if m else shown_percent
    return {**s, "mode": settings["mode"], "weights": settings["weights"], "items": items,
            "shown_percent": shown_percent, "shown_letter": letter(shown_percent), "stale": stale(items, now)}


# ---------- Syllabus weights (Claude reads, you confirm) ----------

WEIGHTS_TOOL = {
    "name": "save_weights",
    "description": "Save the grading breakdown from the syllabus.",
    "input_schema": {
        "type": "object",
        "properties": {
            "weights": {"type": "array", "items": {"type": "object", "properties": {
                "category": {"type": "string", "description": f"Use one of {CATEGORIES} when it fits"},
                "percent": {"type": "number"}}, "required": ["category", "percent"]}},
            "notes": {"type": "string", "description": "Anything unusual: dropped lowest quiz, extra credit, curve, grading scale"},
        },
        "required": ["weights"],
    },
}


def suggest_weights(course_id: str, client=None) -> dict:
    """Read the course syllabus (downloaded from Blackboard) and propose category weights."""
    from autoplan import config, db, prep

    course = next((c for c in db.list_bb_courses() if c["id"] == course_id), None)
    files = (course or {}).get("syllabus") or []
    text = "\n\n".join(prep.extract_text(f) for f in files)[:20000]
    if not text.strip():
        return {"error": "No syllabus found on Blackboard for this class (or it isn't a readable PDF/Word file). Enter the weights yourself."}
    if client is None:
        import anthropic

        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    response = client.messages.create(
        model=config.CLAUDE_MODEL, max_tokens=800,
        system="Extract the course grading breakdown from this syllabus. Only report what the syllabus says. Call save_weights.",
        tools=[WEIGHTS_TOOL], tool_choice={"type": "tool", "name": "save_weights"},
        messages=[{"role": "user", "content": text}],
    )
    block = next(b for b in response.content if getattr(b, "type", None) == "tool_use")
    data = dict(block.input)
    return {"weights": {w["category"]: float(w["percent"]) for w in data.get("weights", [])}, "notes": data.get("notes", "")}
