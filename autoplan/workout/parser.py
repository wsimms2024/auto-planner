"""
Text formats for logging from Telegram.

/log push; bench 240 4x4 @8; incline db 70 3x10 @9; pullups bw 2x8
    - first part can be the split day (push/pull/legs/upper/lower)
    - each exercise: name, weight (or "bw"), sets x reps (or reps per set "5,5,4"), optional @RPE (or "@8,8,9")
    - with "5,5,4" the first number is taken as the target, so a short set counts as missed

/recovery sleep 7.5 quality 4 energy 4 sore 2 bw 165 elbow 0 joint 0 alcohol no lowcal no sick none
/fuel 60 eggs and bacon, apple + gatorade      (minutes until the gym, then what you ate)
"""

import re
from datetime import date

from autoplan.workout import settings
from autoplan.workout.store import Session, SetLog


def canonical(name: str) -> str:
    key = re.sub(r"\s+", " ", name.strip().lower())
    if key in settings.ALIASES:
        return settings.ALIASES[key]
    for alias, full in sorted(settings.ALIASES.items(), key=lambda kv: -len(kv[0])):
        if key.startswith(alias):
            return full
    return name.strip().title()


ENTRY = re.compile(
    r"^(?P<name>[a-zA-Z][a-zA-Z \-]*?)\s+(?P<weight>bw|\d+(?:\.\d+)?)\s*(?:lbs?)?\s+"
    r"(?:(?P<sets>\d+)\s*x\s*(?P<reps>\d+)|(?P<replist>\d+(?:\s*,\s*\d+)+)|(?P<single>\d+))"
    r"(?:\s*@\s*(?P<rpe>\d+(?:\.\d+)?(?:\s*,\s*\d+(?:\.\d+)?)*))?\s*$",
    re.IGNORECASE,
)


def parse_log(text: str, today: date) -> Session:
    parts = [p.strip() for p in re.split(r"[;\n]", text) if p.strip()]
    if not parts:
        raise ValueError("Nothing to log. Example: /log push; bench 240 4x4 @8; incline db 70 3x10")
    split_day = None
    days = {d.lower(): d for d in settings.SPLIT if d != "Rest"}
    if parts[0].lower() in days:
        split_day = days[parts.pop(0).lower()]
    sets: list[SetLog] = []
    for part in parts:
        m = ENTRY.match(part)
        if not m:
            raise ValueError(f"Couldn't read '{part}'. Format: name weight setsxreps @rpe, e.g. bench 240 4x4 @8")
        name = canonical(m["name"])
        weight = None if m["weight"].lower() == "bw" else float(m["weight"])
        if m["sets"]:
            reps_list = [int(m["reps"])] * int(m["sets"])
        elif m["replist"]:
            reps_list = [int(x) for x in m["replist"].split(",")]
        else:
            reps_list = [int(m["single"])]
        target = reps_list[0]
        rpes = [float(x) for x in m["rpe"].split(",")] if m["rpe"] else []
        for i, reps in enumerate(reps_list):
            rpe = rpes[i] if i < len(rpes) else (rpes[-1] if rpes else None)
            sets.append(SetLog(exercise=name, reps=reps, weight=weight, rpe=rpe, target_reps=target))
    if split_day is None:
        split_day = guess_split(sets)
    return Session(date=today, split_day=split_day, sets=sets)


def guess_split(sets: list[SetLog]) -> str:
    """If you didn't say the split day, pick the template that shares the most exercises."""
    names = {s.exercise for s in sets}
    scores = {}
    for day, t in settings.TEMPLATES.items():
        if day == "Rest":
            continue
        template = {e["name"] if e["name"] != "HEAVY_HINGE" else settings.LOWER_HEAVY_HINGE for e in t["exercises"]}
        scores[day] = len(names & template)
    return max(scores, key=scores.get)


YES = {"yes", "y", "true", "1"}
RECOVERY_KEYS = {
    "sleep": ("sleep_hours", float), "slept": ("sleep_hours", float),
    "quality": ("sleep_quality", int), "q": ("sleep_quality", int),
    "energy": ("energy", int), "e": ("energy", int),
    "sore": ("soreness", int), "soreness": ("soreness", int),
    "bw": ("bodyweight", float), "weight": ("bodyweight", float),
    "elbow": ("elbow_pain", int), "joint": ("joint_pain", int), "pain": ("joint_pain", int),
    "alcohol": ("alcohol", "bool"), "drinks": ("alcohol", "bool"),
    "lowcal": ("low_calories", "bool"), "lowcals": ("low_calories", "bool"),
    "disrupted": ("disrupted", "bool"), "schedule": ("disrupted", "bool"),
    "sick": ("illness", str), "illness": ("illness", str),
}
SORENESS_WORDS = {"none": 1, "low": 2, "moderate": 3, "medium": 3, "high": 4, "very": 5}


def parse_recovery(text: str) -> dict:
    tokens = re.findall(r"[a-zA-Z]+|\d+(?:\.\d+)?", text.lower().replace("=", " "))
    values, i = {}, 0
    while i < len(tokens):
        key = tokens[i]
        if key in RECOVERY_KEYS and i + 1 < len(tokens):
            field_name, kind = RECOVERY_KEYS[key]
            raw = tokens[i + 1]
            if kind == "bool":
                values[field_name] = int(raw in YES)
            elif kind is str:
                values[field_name] = raw if raw in ("none", "mild", "improving", "systemic", "fever") else raw
                if values[field_name] == "fever":
                    values[field_name] = "systemic"
            elif field_name == "soreness" and raw in SORENESS_WORDS:
                values[field_name] = SORENESS_WORDS[raw]
            else:
                values[field_name] = kind(float(raw)) if kind is int else kind(raw)
            i += 2
            continue
        i += 1
    if not values:
        raise ValueError("Example: /recovery sleep 7.5 quality 4 energy 4 sore low bw 165 elbow 0 alcohol no")
    return values


def parse_fuel(text: str) -> tuple[int | None, str]:
    m = re.match(r"^\s*(\d+)\s*(?:min|mins|minutes|m)?\s+(.*)$", text.strip(), re.IGNORECASE)
    if m:
        return int(m.group(1)), m.group(2).strip()
    return None, text.strip()
