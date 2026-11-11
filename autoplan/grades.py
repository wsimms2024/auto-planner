"""
Grade math from your Blackboard gradebook.

Points-based: your earned points over points graded so far. If a class uses
weighted categories (e.g. exams 50%, homework 20%), these numbers are an
estimate; Blackboard's own total (shown alongside) is the official one.
Edit LETTER_CUTOFFS if a syllabus uses a different scale.
"""

LETTER_CUTOFFS = [
    ("A", 93), ("A-", 90), ("B+", 87), ("B", 83), ("B-", 80), ("C+", 77),
    ("C", 73), ("C-", 70), ("D+", 67), ("D", 63), ("D-", 60), ("F", 0),
]


def letter(percent: float | None) -> str | None:
    if percent is None:
        return None
    return next(l for l, cutoff in LETTER_CUTOFFS if percent >= cutoff)


def cutoff(letter_grade: str) -> float:
    return dict(LETTER_CUTOFFS)[letter_grade]


def summary(items: list[dict]) -> dict:
    """Current standing for one course."""
    parts = [i for i in items if not i.get("is_total") and (i.get("possible") or 0) > 0]
    graded = [i for i in parts if i.get("score") is not None]
    remaining = [i for i in parts if i.get("score") is None]
    earned = sum(i["score"] for i in graded)
    graded_possible = sum(i["possible"] for i in graded)
    percent = round(100 * earned / graded_possible, 1) if graded_possible else None
    total = next((i for i in items if i.get("is_total") and (i.get("display") or i.get("score") is not None)), None)
    return {
        "earned": earned,
        "graded_possible": graded_possible,
        "remaining_possible": sum(i["possible"] for i in remaining),
        "percent": percent,
        "letter": letter(percent),
        "graded_count": len(graded),
        "remaining_count": len(remaining),
        "blackboard_total": (total.get("display") or (f"{total['score']:g}" if total and total.get("score") is not None else None)) if total else None,
    }


def needed_for(items: list[dict], target_percent: float) -> dict:
    """
    Average you need on everything not graded yet to finish at target_percent.
    Over 100% means it isn't reachable on points alone.
    """
    s = summary(items)
    if not s["remaining_possible"]:
        return {"needed_percent": None, "note": "Nothing left to grade."}
    total_possible = s["graded_possible"] + s["remaining_possible"]
    need_points = target_percent / 100 * total_possible - s["earned"]
    needed = round(100 * need_points / s["remaining_possible"], 1)
    note = None
    if needed > 100:
        note = "Not reachable on points alone."
    elif needed <= 0:
        note = "Already locked in, even with zeros on the rest."
    return {"needed_percent": needed, "remaining_points": s["remaining_possible"], "note": note}
