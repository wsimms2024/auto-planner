"""
Best guesses for tasks that come in from Blackboard or email, based on the name.

Blackboard doesn't say how big something is, so these rules pick a starting
priority, time estimate, and whether AutoPlan should plan study time for it.
You can change any of them per task on the Tasks page. Edit the lists below
to change the defaults. First match wins, top to bottom.
"""

import re

# (pattern, priority, minutes, plan study time?)
RULES = [
    # Admin stuff: still tracked as a deadline, but no study sessions
    (r"training|refresher|title ix|safety|survey|orientation|acknowledg|attendance|participation|"
     r"check-?in|evaluation|feedback|registration|sign[- ]?up|form\b", "Low", 30, False),
    # Smaller pieces of bigger work
    (r"outline|proposal|draft|peer review|reflection", "Medium", 90, True),
    # Big work
    (r"exam|midterm|final|project|paper|essay|presentation|thesis|report", "High", 180, True),
    # Regular coursework
    (r"quiz", "Medium", 45, True),
    (r"assignment|homework|\bhw\b|lab|problem set|\bica\b|ica-?\d|exercise|discussion|case|chapter", "Medium", 60, True),
]

DEFAULT = ("Medium", 60, True)


def guess(name: str) -> tuple[str, int, bool]:
    """(priority, estimated minutes, plan study time) for a task name."""
    for pattern, priority, minutes, plan in RULES:
        if re.search(pattern, name, re.IGNORECASE):
            return priority, minutes, plan
    return DEFAULT
