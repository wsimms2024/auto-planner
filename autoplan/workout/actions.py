"""
What each Telegram command/button does, as plain functions returning text.
Kept out of telegram_bot.py so it's easy to test and reuse.
"""

import html
from datetime import date, datetime

from autoplan.workout import digest, engine, fueling, parser, recovery, settings, store


def _e(x) -> str:
    return html.escape(str(x), quote=False)


def digest_messages(now: datetime | None = None) -> list[str]:
    return digest.format_telegram(digest.build(now=now))


def start_workout_text(now: datetime | None = None) -> str:
    """Compact checklist plus a ready-to-edit /log line."""
    now = now or datetime.now()
    d = digest.build(now=now, use_llm=False)
    if not d["exercises"]:
        return "😴 Rest day. Nothing to start. /digest shows what's next."
    lines = [f"▶️ <b>{_e(d['split_day'])}: let's go</b>"]
    log_parts = [d["split_day"].lower()]
    for e in d["exercises"]:
        target = f" @ {e['target']}" if e.get("target") and e["target"][0].isdigit() else ""
        lines.append(f"☐ {_e(e['name'])}: {_e(e['sets'])} x {_e(e['reps'] or '?')}{_e(target)}")
        w = f"{e['weight']:g}" if e.get("weight") else "WEIGHT"
        reps = e["reps"].split("–")[0] if e.get("reps") else "REPS"
        sets = e["sets"].split("-")[0] if e["sets"].split("-")[0].isdigit() else "3"
        short = next((a for a, full in settings.ALIASES.items() if full == e["name"] and len(a) <= 12), e["name"].lower())
        log_parts.append(f"{short} {w} {sets}x{reps} @8")
    lines += ["", "When you're done, send (edit the numbers to what you actually did):",
              f"<code>/log {_e('; '.join(log_parts))}</code>"]
    return "\n".join(lines)


def recovery_prompt() -> str:
    return ("😴 <b>Recovery check</b>\nSend one line, any order, skip what doesn't apply:\n"
            "<code>/recovery sleep 7.5 quality 4 energy 4 sore low bw 165 elbow 0 joint 0 alcohol no lowcal no sick none</code>\n"
            "<i>sore: none/low/moderate/high · sick: none/mild/improving/systemic</i>")


def handle_recovery(args: str, today: date | None = None) -> str:
    today = today or date.today()
    values = parser.parse_recovery(args)
    saved = store.save_recovery(today, **values)
    r = recovery.assess(saved)
    reasons = f"\n<i>{_e(', '.join(r['reasons']))}</i>" if r["reasons"] else ""
    return f"✅ Saved. Readiness: <b>{_e(r['status'])}</b>{reasons}\n/digest for the adjusted plan."


def handle_log(args: str, today: date | None = None) -> str:
    today = today or date.today()
    session = parser.parse_log(args, today)
    store.add_session(session)
    lines = [f"📝 Logged <b>{_e(session.split_day)}</b>: {len(session.sets)} sets"]
    for lift in settings.GOALS:
        ws = [s for s in session.sets if s.exercise == lift]
        if ws:
            top = engine.working_sets(session, lift)
            best = max(top, key=lambda s: engine.e1rm_rpe(s.weight or 0, s.reps, s.rpe))
            lo, hi = engine.e1rm(best.weight or 0, best.reps), engine.e1rm_rpe(best.weight or 0, best.reps, best.rpe)
            result = {"missed": "missed reps, will repeat", "near_failure": "done, last set was a grind", "normal": "all reps done"}[engine.outcome(top)]
            lines.append(f"• {_e(lift)}: {_e(engine.describe(top))} → e1RM ~{lo:.0f}–{hi:.0f} ({result})")
    lines.append(f"➡️ Next: {_e(engine.next_split_day(session.split_day))}")
    return "\n".join(lines)


def handle_fuel(args: str, now: datetime | None = None) -> str:
    now = now or datetime.now()
    minutes, food = parser.parse_fuel(args)
    if not food:
        return "🍎 Send: <code>/fuel 60 eggs and toast, banana</code> (minutes until the gym, then what you ate)"
    store.add_fueling(minutes, food, at=now)
    split = engine.split_today(now.date(), store.list_sessions(limit=10), store.get_split_override(now.date()))["day"]
    f = fueling.advise(split, {"minutes_to_gym": minutes, "food": food})
    return "\n".join([f"🍎 <b>{_e(f['status'])}</b>"] + [f"• {_e(t)}" for t in f["tips"]] + [f"<i>{_e(f['daily'])}</i>"])


def progress_text() -> str:
    return digest.format_progress(engine.progress())


def weekly_text(today: date | None = None) -> str:
    return digest.format_weekly(engine.weekly(today or date.today()))


def settings_text() -> str:
    tm = ", ".join(f"{k} {v}" for k, v in settings.TRAINING_MAX.items())
    goals = ", ".join(f"{k.split()[0]} {v}" for k, v in settings.GOALS.items())
    return "\n".join([
        "⚙️ <b>Training settings</b>",
        f"Split: {' → '.join(settings.SPLIT)}",
        f"Goals: {goals}",
        f"Bench program: {settings.BENCH_PROGRAM}" + (f" (TMs: {tm})" if settings.BENCH_PROGRAM == "531" else " (5x5 → 4x4 → 3x3 → 2x2)"),
        f"Lower heavy hinge: {settings.LOWER_HEAVY_HINGE}",
        f"Main-lift RPE: {settings.MAIN_LIFT_RPE[0]:g}–{settings.MAIN_LIFT_RPE[1]:g}",
        f"Nutrition: {settings.NUTRITION['calories'][0]}–{settings.NUTRITION['calories'][1]} kcal, {settings.NUTRITION['protein'][0]}–{settings.NUTRITION['protein'][1]} g protein",
        "<i>Change these in autoplan/workout/settings.py</i>",
    ])


def set_split(day_name: str, today: date | None = None) -> str:
    today = today or date.today()
    if day_name not in settings.SPLIT:
        return f"Unknown split day {day_name}."
    store.set_split_override(today, day_name)
    return f"🔄 Today is now <b>{_e(day_name)}</b>. Sending the updated digest."
