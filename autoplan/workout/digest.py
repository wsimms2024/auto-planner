"""
Builds the Daily Workout Digest as a structured object, then formats it for Telegram.

build() does all the deciding (via engine/recovery/fueling). The only LLM
step is an optional two-sentence coach note written from that object; if
there's no API key it falls back to a rule-based note.
"""

import html
import json
import os
from datetime import date, datetime

from autoplan import config
from autoplan.workout import engine, fueling, recovery, settings, store

TELEGRAM_LIMIT = 4000
FOCUS_ICONS = {"Push": "💪", "Pull": "🧲", "Legs": "🦵", "Upper": "💪", "Lower": "🦵", "Rest": "😴"}


def build(today: date | None = None, now: datetime | None = None, llm_client=None, use_llm: bool = True) -> dict:
    now = now or datetime.now()
    today = today or now.date()
    sessions = store.list_sessions(limit=30)
    override = store.get_split_override(today)
    from_calendar = None if override else calendar_split(today)
    split = engine.split_today(today, sessions, override or (from_calendar[0] if from_calendar else None))
    if from_calendar and not split["done_today"]:
        split["reason"] = f"from your calendar ({from_calendar[1]})"
    day = split["day"]

    check = recovery.assess(store.get_recovery(today))
    if check["adjust"]["rest"]:
        day_plan, focus = [], ["Rest and recover"]
    else:
        day_plan = engine.apply_recovery(engine.plan_for(day), check["adjust"]) if day != "Rest" else []
        focus = list(settings.TEMPLATES[day]["focus"])
        if day != "Rest":
            focus.append(f"Main lifts at RPE {settings.MAIN_LIFT_RPE[0]:g}–{settings.MAIN_LIFT_RPE[1]:g}; accessories can go closer to failure")

    bw = (check["log"] or {}).get("bodyweight") or store.latest_bodyweight() or settings.BODYWEIGHT["current"]
    digest = {
        "date": today.isoformat(),
        "split_day": day,
        "split_reason": split["reason"],
        "done_today": split["done_today"],
        "bodyweight": bw,
        "main_goal": f"{settings.GOALS[settings.MAIN_GOAL]} {settings.MAIN_GOAL.split()[0].lower()}",
        "readiness": {k: check[k] for k in ("status", "level", "reasons")} | {"log": check["log"]},
        "focus": focus,
        "exercises": [p.__dict__ for p in day_plan],
        "progression": engine.progress(),
        "fueling": fueling.advise(day if not check["adjust"]["rest"] else "Rest", store.latest_fueling(now)),
        "next_split_day": engine.next_split_day(day),
    }
    digest["coach_note"] = coach_note(digest, llm_client if use_llm else False)
    return digest


def calendar_split(day: date) -> tuple[str, str] | None:
    """A gym event today named after a split day ('Legs', 'push day') decides today's workout."""
    import re

    from autoplan import db, scheduler

    names = {d.lower(): d for d in settings.SPLIT if d != "Rest"}
    for e in scheduler.expand_events(db.list_events(), day, day):
        m = re.search(r"\b(" + "|".join(names) + r")\b", e.name.lower())
        if m:
            return names[m.group(1)], f"{e.name} at {e.start:%I:%M %p}".replace(" 0", " ")
    return None


# ---------- Coach note ----------

def rule_note(d: dict) -> str:
    level = d["readiness"]["level"]
    if d["split_day"] == "Rest":
        return "Rest day. Walk, stretch, eat your protein. Strength shows up on the next session."
    if level == "rest":
        return "Sit this one out. Training through a systemic illness costs more than one missed day."
    statuses = {e["status"] for e in d["exercises"] if e.get("main")}
    if "reset" in statuses:
        return "Two rough sessions at that weight: the back-off is planned, not a step backward. Own the lighter sets."
    if "repeat" in statuses:
        return "Repeating last session's weight. One weaker day isn't a trend; look at the last 2–4 sessions before changing anything."
    if level in ("trim", "reduce", "conservative"):
        return "Recovery is a little down, so the plan is trimmed. Hit the numbers cleanly and leave a rep in the tank."
    return "Keep compound sets controlled and don't chase failure on the main lifts. Push accessories harder."


def coach_note(d: dict, client=None) -> str:
    if client is False or not os.environ.get("ANTHROPIC_API_KEY") and client is None:
        return rule_note(d)
    try:
        if client is None:
            import anthropic

            client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        facts = {k: d[k] for k in ("split_day", "readiness", "progression", "next_split_day")}
        facts["exercises"] = [{k: e[k] for k in ("name", "sets", "reps", "target", "why", "status")} for e in d["exercises"]]
        response = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=160,
            system=("You are a concise strength coach. Write a 1–2 sentence coach note for today using ONLY the facts given. "
                    "Do not invent numbers or history. Plain text, no emojis, no lists."),
            messages=[{"role": "user", "content": json.dumps(facts, default=str)}],
        )
        text = "".join(getattr(b, "text", "") for b in response.content).strip()
        return text or rule_note(d)
    except Exception:
        return rule_note(d)


# ---------- Telegram formatting (HTML) ----------

def _e(x) -> str:
    return html.escape(str(x), quote=False)


def format_telegram(d: dict) -> list[str]:
    """One message for the plan, a second for progress/recovery/fueling if it would run long."""
    day = d["split_day"]
    when = date.fromisoformat(d["date"])
    head = [
        "🏋️ <b>DAILY WORKOUT DIGEST</b>",
        f"📅 {when:%A, %b} {when.day}",
        f"⚖️ BW: ~{d['bodyweight']:g} lb",
        f"🔄 Split: <b>{_e(day)}</b> {FOCUS_ICONS.get(day, '')}",
        f"🎯 Main goal: {_e(d['main_goal'])}",
    ]
    if d["done_today"]:
        head.append("✔️ Already logged today. Here's what was planned.")

    r = d["readiness"]
    log = r.get("log") or {}
    ready = ["", "✅ <b>READINESS</b>"]
    if log:
        if "sleep_hours" in log:
            ready.append(f"Sleep: {log['sleep_hours']:g} h" + (f" (quality {log['sleep_quality']}/5)" if "sleep_quality" in log else ""))
        if "energy" in log:
            ready.append(f"Energy: {log['energy']}/5")
        if "soreness" in log:
            ready.append(f"Soreness: {['', 'None', 'Low', 'Moderate', 'High', 'Very high'][int(log['soreness'])]}")
        if "elbow_pain" in log or "joint_pain" in log:
            ready.append(f"Pain: elbow {log.get('elbow_pain', 0)}/10" + (f", joints {log['joint_pain']}/10" if log.get("joint_pain") else ""))
    ready.append(f"Status: <b>{_e(r['status'])}</b>")
    if r["reasons"]:
        ready.append(f"<i>{_e(', '.join(r['reasons']))}</i>")

    plan = ["", "🎯 <b>TODAY'S FOCUS</b>"] + [f"• {_e(f)}" for f in d["focus"]]
    if d["exercises"]:
        plan += ["", "📋 <b>TODAY'S WORKOUT</b>"]
        for i, e in enumerate(d["exercises"], 1):
            title = f"{i}. <b>{_e(e['name'])}</b>" + (" ⭐" if e.get("main") else "")
            lines = [title]
            scheme = f"{e['sets']} x {e['reps']}" if e["reps"] else f"{e['sets']} sets"
            lines.append(f"   {_e(scheme)}")
            if e.get("target"):
                lines.append(f"   Target: {_e(e['target'])}")
            if e.get("rpe"):
                lines.append(f"   RPE: {_e(e['rpe'])}")
            if e.get("why") and (e.get("main") or e.get("status") in ("progress", "repeat")):
                lines.append(f"   <i>Why: {_e(e['why'])}</i>")
            for n in e.get("notes", []):
                lines.append(f"   <i>{_e(n)}</i>")
            plan += lines

    extra = ["", "📈 <b>PROGRESSION</b>"]
    for lift, p in d["progression"].items():
        if "e1rm_low" not in p:
            extra.append(f"{_e(lift)}: no data yet")
            continue
        extra.append(f"<b>{_e(lift)}</b>: {_e(p['last'])}")
        extra.append(f"   e1RM ~{p['e1rm_low']}–{p['e1rm_high']} lb · {p['goal_pct']}% of {p['goal']}")
    extra.append("<i>Multi-set work at RPE 8 usually means more strength than a one-set calculator shows; the upper number counts reps left in the tank.</i>")

    f = d["fueling"]
    extra += ["", "🍎 <b>FUELING</b>"]
    if f.get("food"):
        extra.append(f"Ate: {_e(f['food'])}" + (f" ({f['minutes']} min before gym)" if f.get("minutes") is not None else ""))
    extra.append(f"Status: {_e(f['status'])}")
    extra += [f"• {_e(t)}" for t in f.get("tips", [])]
    extra.append(f"<i>{_e(f['daily'])}</i>")

    extra += ["", "💡 <b>COACH NOTE</b>", _e(d["coach_note"]), "", f"➡️ <b>NEXT</b>: {_e(d['next_split_day'])}"]

    first = "\n".join(head + ready + plan)
    second = "\n".join(extra).strip()
    if len(first) + len(second) + 2 <= TELEGRAM_LIMIT:
        return [first + "\n" + "\n".join(extra)]
    return [first[:TELEGRAM_LIMIT], second[:TELEGRAM_LIMIT]]


def format_progress(prog: dict) -> str:
    lines = ["📈 <b>PROGRESS</b>"]
    for lift, p in prog.items():
        if "e1rm_low" not in p:
            lines.append(f"<b>{_e(lift)}</b>: no data yet")
            continue
        bar = "▓" * (p["goal_pct"] // 10) + "░" * max(0, 10 - p["goal_pct"] // 10)
        lines += [f"<b>{_e(lift)}</b> → {p['goal']}", f"   {_e(p['last'])}", f"   e1RM ~{p['e1rm_low']}–{p['e1rm_high']} lb",
                  f"   {bar} {p['goal_pct']}%" + (f" · avg RPE {p['avg_rpe']:g}" if p.get("avg_rpe") else "")]
    return "\n".join(lines)


def format_weekly(w: dict) -> str:
    lines = ["🗓️ <b>LAST 7 DAYS</b>"]
    lines.append("Sessions: " + (", ".join(f"{date.fromisoformat(d):%a} {s}" for d, s in w["sessions"]) or "none logged"))
    if w["sets_by_muscle"]:
        lines.append("Sets: " + ", ".join(f"{m} {n}" for m, n in w["sets_by_muscle"].items()))
    lines.append(f"Volume: {w['tonnage']:,} lb" + (f" · avg RPE {w['avg_rpe']:g}" if w["avg_rpe"] else ""))
    if w["bodyweight_avg"]:
        change = f" ({w['bodyweight_change']:+g} vs prior week)" if w["bodyweight_change"] is not None else ""
        lines.append(f"Bodyweight avg: {w['bodyweight_avg']:g} lb{change}")
    goal = settings.BODYWEIGHT
    lines.append(f"<i>Goal: {goal['goal_low']}–{goal['goal_high']} lb, about 0.25–0.5 lb a week</i>")
    return "\n".join(lines)
