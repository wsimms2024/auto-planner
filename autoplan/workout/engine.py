"""
The training math. Everything numeric in the digest comes from here, never from the LLM.

Main-lift progression (comparing the most recent comparable session, same split day):
  all prescribed reps done, last set at or under RPE 8.5 -> progress normally
  done, but last set RPE 9+                              -> progress conservatively (small jump, same scheme)
  reps missed                                            -> repeat; missed twice at that weight -> drop ~5%
Accessories use double progression: hit the top of the rep range on every set, then add weight.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

from autoplan.workout import settings
from autoplan.workout.store import Session, SetLog, sessions_with


# ---------- Small helpers ----------

def e1rm(weight: float, reps: int) -> float:
    """Epley: weight x (1 + reps/30)."""
    return weight * (1 + reps / 30) if reps > 1 else float(weight)


def e1rm_rpe(weight: float, reps: int, rpe: float | None) -> float:
    """Epley counting reps left in the tank (RPE 8 = 2 more reps)."""
    rir = max(0.0, 10 - rpe) if rpe else 0.0
    return e1rm(weight, reps + rir)


def round_to(x: float, inc: float) -> float:
    return round(round(x / inc) * inc, 1)


def increment(lift: str) -> float:
    if lift in settings.DUMBBELL:
        return settings.INCREMENT["dumbbell"]
    return settings.INCREMENT.get(lift, settings.INCREMENT["default"])


def working_sets(session: Session, exercise: str) -> list[SetLog]:
    """Sets at the session's top weight (warm-ups excluded)."""
    sets = session.sets_for(exercise)
    weighted = [s for s in sets if s.weight]
    if not weighted:
        return sets
    top = max(s.weight for s in weighted)
    return [s for s in weighted if s.weight == top]


def outcome(sets: list[SetLog], planned_reps: int | None = None) -> str:
    """'missed', 'near_failure', or 'normal'."""
    for s in sets:
        target = s.target_reps or planned_reps
        if target and s.reps < target:
            return "missed"
    last_rpe = sets[-1].rpe if sets else None
    if last_rpe is not None and last_rpe >= 9:
        return "near_failure"
    return "normal"


def describe(sets: list[SetLog]) -> str:
    """'240 x 4 x 4 @ RPE 8' style summary."""
    if not sets:
        return ""
    w = f"{sets[0].weight:g}" if sets[0].weight else "BW"
    reps = [s.reps for s in sets]
    body = f"{w} x {len(sets)} x {reps[0]}" if len(set(reps)) == 1 else f"{w} x " + ",".join(map(str, reps))
    rpes = [s.rpe for s in sets if s.rpe is not None]
    return body + (f" @ RPE {rpes[-1]:g}" if rpes else "")


def avg_rpe(sessions: list[Session], exercise: str) -> float | None:
    vals = [s.rpe for sess in sessions for s in working_sets(sess, exercise) if s.rpe is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


# ---------- Which split day is today ----------

def split_today(today: date, sessions: list[Session], override: str | None = None) -> dict:
    """
    From the most recently completed workout, not the weekday. Workouts are never
    skipped after a gap; a Rest slot counts as taken once a day has gone by.
    """
    split = settings.SPLIT
    if override:
        return {"day": override, "reason": "set by you today", "done_today": False}
    workouts = [s for s in sessions if s.split_day != "Rest"]
    if not workouts:
        return {"day": split[0], "reason": "no workouts logged yet, starting the split", "done_today": False}
    last = workouts[0]
    if last.date == today:
        return {"day": last.split_day, "reason": "already logged today", "done_today": True, "last": last}
    idx = split.index(last.split_day)
    gap = (today - last.date).days
    nxt = split[(idx + 1) % len(split)]
    if nxt == "Rest" and gap >= 2:
        nxt = split[(idx + 2) % len(split)]
        reason = f"last workout was {last.split_day} on {last.date:%a}; rest day already taken"
    else:
        reason = f"last workout was {last.split_day} on {last.date:%a}"
    return {"day": nxt, "reason": reason, "done_today": False, "last": last}


def next_split_day(day: str) -> str:
    split = settings.SPLIT
    return split[(split.index(day) + 1) % len(split)]


# ---------- Main lifts ----------

@dataclass
class Prescription:
    name: str
    sets: str
    reps: str
    weight: float | None = None
    target: str = ""
    rpe: str = ""
    why: str = ""
    status: str = ""
    main: bool = False
    notes: list[str] = field(default_factory=list)


def _history(lift: str, split_day: str) -> list[Session]:
    return sessions_with(lift, split_day=split_day, limit=4)


def _seed_session(lift: str, split_day: str) -> Session | None:
    seed = settings.SEEDS.get(lift)
    if not seed:
        return None
    w, n, r, rpe = seed
    return Session(date=date.min, split_day=split_day, sets=[SetLog(lift, r, w, rpe, r) for _ in range(n)])


def pyramid(lift: str, split_day: str) -> Prescription:
    inc = increment(lift)
    steps = settings.PYRAMID_STEPS
    history = _history(lift, split_day)
    seeded = not history
    if seeded:
        seed = _seed_session(lift, split_day)
        if not seed:
            return Prescription(lift, "?", "?", why=f"No {lift} history yet. Log your last session with /log so I can program it.", main=True)
        history = [seed]
    last = working_sets(history[0], lift)
    weight, n, reps = last[0].weight, len(last), (last[0].target_reps or last[0].reps)
    i = min(range(len(steps)), key=lambda k: abs(steps[k][1] - reps) + abs(steps[k][0] - n) * 0.1)
    result = outcome(last)
    src = "your starting numbers" if seeded else f"last {split_day} ({history[0].date:%b %d})"

    if result == "normal":
        j = (i + 1) % len(steps)
        if j == 0:
            prev5 = next((working_sets(s, lift)[0].weight for s in history[1:]
                          if len(working_sets(s, lift)) == steps[0][0] and (working_sets(s, lift)[0].target_reps or working_sets(s, lift)[0].reps) == steps[0][1]), None)
            new = prev5 + inc if prev5 else weight - 2 * inc
            why = f"Finished the wave ({describe(last)}). New wave starts at 5x5, {inc:g} lb over the last one."
        else:
            new = weight + inc
            why = f"{describe(last)} from {src}, all reps within target RPE → next step."
        status = "progress"
    elif result == "near_failure":
        j = i
        new = weight + settings.MICRO_INCREMENT
        why = f"{describe(last)} from {src}: done, but the last set was a grind → same scheme, small jump."
        status = "conservative"
    else:
        j = i
        prev = working_sets(history[1], lift) if len(history) > 1 else []
        if prev and prev[0].weight == weight and outcome(prev) == "missed":
            new = round_to(weight * 0.95, inc)
            why = f"Missed reps at {weight:g} two sessions running → back off ~5% and rebuild."
            status = "reset"
        else:
            new = weight
            why = f"Missed reps last time ({describe(last)}) → repeat {weight:g} before adding weight."
            status = "repeat"
    s, r = steps[j]
    lo, hi = settings.MAIN_LIFT_RPE
    return Prescription(lift, str(s), str(r), new, f"{new:g} lb", f"{lo:g}–{hi:g}", why, status, main=True)


def five_three_one(lift: str, split_day: str) -> Prescription:
    weeks = [[(.65, 5), (.75, 5), (.85, 5)], [(.70, 3), (.80, 3), (.90, 3)], [(.75, 5), (.85, 3), (.95, 1)], [(.40, 5), (.50, 5), (.60, 5)]]
    history = sessions_with(lift, split_day=split_day, limit=200)
    week, cycles = len(history) % 4, len(history) // 4
    tm = settings.TRAINING_MAX[lift] + increment(lift) * cycles
    scheme = weeks[week]
    loads = [(round_to(tm * pct, 5), reps) for pct, reps in scheme]
    sets_txt = ", ".join(f"{w:g}x{r}" for w, r in loads[:-1]) + f", {loads[-1][0]:g}x{loads[-1][1]}+"
    label = ["5s week", "3s week", "5/3/1 week", "deload"][week]
    return Prescription(lift, "3", "5/3/1", loads[-1][0], sets_txt, "top set to ~8.5, no grinding",
                        f"5/3/1 {label}, training max {tm:g} lb (cycle {cycles + 1}).", "531", main=True)


def double_progression(lift: str, sets: str, rep_range: tuple[int, int], split_day: str | None = None,
                       rpe: tuple[float, float] = settings.ACCESSORY_RPE, main: bool = False) -> Prescription:
    lo, hi = rep_range
    history = sessions_with(lift, split_day=split_day, limit=4) if split_day else sessions_with(lift, limit=4)
    inc = increment(lift)
    rpe_txt = f"{rpe[0]:g}–{rpe[1]:g}"
    if not history:
        return Prescription(lift, sets, f"{lo}–{hi}", None, f"a weight for ~{hi} reps", rpe_txt,
                            "No history yet: pick a load and log it.", "new", main=main)
    ws = working_sets(history[0], lift)
    weight = ws[0].weight
    if weight is None:
        return Prescription(lift, sets, f"{lo}–{hi}", None, "bodyweight", rpe_txt, f"Last: {describe(ws)}. Beat it by a rep.", "progress", main=main)
    all_top = all(s.reps >= hi for s in ws)
    last_rpe = ws[-1].rpe
    if all_top and (last_rpe is None or last_rpe <= rpe[1]):
        return Prescription(lift, sets, f"{lo}–{hi}", weight + inc, f"{weight + inc:g} lb", rpe_txt,
                            f"Hit {hi}+ on every set at {weight:g} → add weight.", "progress", main=main)
    if any(s.reps < lo for s in ws):
        return Prescription(lift, sets, f"{lo}–{hi}", weight, f"{weight:g} lb", rpe_txt,
                            f"Fell under {lo} reps last time ({describe(ws)}) → same weight.", "repeat", main=main)
    return Prescription(lift, sets, f"{lo}–{hi}", weight, f"{weight:g} lb", rpe_txt,
                        f"Last: {describe(ws)}. Add reps before load.", "progress", main=main)


def best_e1rm(lift: str) -> tuple[float, float, Session] | None:
    """(Epley, RPE-adjusted, session) from the best of the last 4 sessions with this lift."""
    best = None
    for sess in sessions_with(lift, limit=4):
        for s in working_sets(sess, lift):
            if s.weight and s.reps:
                plain, adj = e1rm(s.weight, s.reps), e1rm_rpe(s.weight, s.reps, s.rpe)
                if best is None or adj > best[1]:
                    best = (plain, adj, sess)
    if best is None:
        seed = settings.SEEDS.get(lift)
        if seed:
            w, n, r, rpe = seed
            return e1rm(w, r), e1rm_rpe(w, r, rpe), None
    return best


def volume_bench(split_day: str) -> Prescription:
    p = double_progression("Bench Press", "4", (6, 8), split_day=split_day, rpe=(7.5, 8.0), main=True)
    if p.status == "new":
        est = best_e1rm("Bench Press")
        if est:
            w = round_to(0.72 * est[1], 5)
            p.weight, p.target = w, f"{w:g} lb"
            p.why = f"First volume day: ~72% of your estimated max ({est[1]:.0f})."
    return p


def moderate_squat() -> Prescription:
    est = best_e1rm("Squat")
    if not est:
        return Prescription("Squat", "3", "5", None, "moderate", "7–8", "No squat history yet.", "new", main=True)
    w = round_to(0.78 * est[1], 5)
    return Prescription("Squat", "3", "5", w, f"{w:g} lb", "7–8",
                        f"Lighter exposure: ~78% of estimated max ({est[1]:.0f}). Keep reps crisp.", "moderate", main=True)


def main_lift(entry: dict, split_day: str) -> Prescription:
    name, style = entry["name"], entry.get("style")
    if name == "HEAVY_HINGE":
        name = settings.LOWER_HEAVY_HINGE
        if name == "Deadlift":
            return pyramid("Deadlift", split_day)
        return double_progression("Romanian Deadlift", "3", (5, 8), split_day=split_day, rpe=settings.MAIN_LIFT_RPE, main=True)
    if name == "Bench Press":
        if style == "volume":
            return volume_bench(split_day)
        return five_three_one(name, split_day) if settings.BENCH_PROGRAM == "531" else pyramid(name, split_day)
    if name == "Squat":
        return moderate_squat() if style == "moderate" else pyramid("Squat", split_day)
    if name == "Romanian Deadlift":
        return double_progression(name, "3", (5, 8), split_day=split_day, rpe=(7.5, 8.0), main=True)
    return pyramid(name, split_day)


# ---------- Recovery adjustments ----------

PRESSING = {"Bench Press", "Incline DB Press", "Shoulder Press", "Chest Fly", "Triceps Pushdown"}


def apply_recovery(plan: list[Prescription], adjust: dict) -> list[Prescription]:
    out = []
    for p in plan:
        if adjust.get("avoid_pressing") and p.name in PRESSING:
            continue
        if p.main and p.weight:
            factor = adjust.get("load_factor", 1.0)
            if adjust.get("reduce_pressing") and p.name in PRESSING:
                factor = min(factor, 0.92)
            if factor < 1:
                inc = increment(p.name)
                p.weight = round_to(p.weight * factor, inc)
                p.target = f"{p.weight:g} lb"
                p.notes.append("lighter today for recovery")
        drop = adjust.get("drop_sets", 0) + (1 if adjust.get("reduce_pressing") and p.name in PRESSING else 0)
        if drop and p.main and p.sets.isdigit() and int(p.sets) > 2:
            p.sets = str(int(p.sets) - min(drop, 1))
        if adjust.get("rpe_cap") and p.main:
            p.rpe = f"≤{adjust['rpe_cap']:g}"
        if adjust.get("reduce_pressing") and p.name == "Chest Fly":
            continue
        out.append(p)
    return out


# ---------- Today's plan ----------

def plan_for(split_day: str) -> list[Prescription]:
    template = settings.TEMPLATES[split_day]
    plan = []
    for entry in template["exercises"]:
        if entry.get("role") == "main":
            plan.append(main_lift(entry, split_day))
        else:
            if entry.get("reps"):
                p = double_progression(entry["name"], entry["sets"], entry["reps"], rpe=settings.ACCESSORY_RPE)
            else:
                p = Prescription(entry["name"], entry["sets"], "")
            if entry.get("note"):
                p.notes.append(entry["note"])
            if entry.get("optional"):
                p.notes.append("optional")
            plan.append(p)
    return plan


# ---------- Progress and weekly numbers ----------

def progress() -> dict:
    out = {}
    for lift, goal in settings.GOALS.items():
        hist = sessions_with(lift, limit=4)
        best = best_e1rm(lift)
        if not best:
            out[lift] = {"goal": goal, "note": "no data yet"}
            continue
        plain, adj, sess = best
        last = working_sets(hist[0], lift) if hist else []
        seed = _seed_session(lift, "")
        last_text = describe(last) if last else (f"starting numbers: {describe(seed.sets)}" if seed else "")
        out[lift] = {
            "goal": goal,
            "last": last_text,
            "last_date": hist[0].date.isoformat() if hist else None,
            "e1rm_low": round(plain), "e1rm_high": round(adj),
            "goal_pct": round(100 * plain / goal),  # conservative: plain Epley
            "avg_rpe": avg_rpe(hist, lift),
            "sessions": len(hist),
        }
    return out


def weekly(today: date) -> dict:
    from autoplan.workout import store

    week = [s for s in store.list_sessions(since=today - timedelta(days=6))]
    sets_by_muscle: dict[str, int] = {}
    tonnage, rpes = 0.0, []
    for sess in week:
        for s in sess.sets:
            muscle = settings.MUSCLES.get(s.exercise, "Other")
            sets_by_muscle[muscle] = sets_by_muscle.get(muscle, 0) + 1
            tonnage += (s.weight or 0) * s.reps
            if s.rpe is not None:
                rpes.append(s.rpe)
    bw = store.bodyweights(today - timedelta(days=13))
    this_week = [w for d, w in bw if d >= today - timedelta(days=6)]
    last_week = [w for d, w in bw if d < today - timedelta(days=6)]
    return {
        "sessions": [(s.date.isoformat(), s.split_day) for s in reversed(week)],
        "sets_by_muscle": dict(sorted(sets_by_muscle.items(), key=lambda kv: -kv[1])),
        "tonnage": round(tonnage),
        "avg_rpe": round(sum(rpes) / len(rpes), 1) if rpes else None,
        "bodyweight_avg": round(sum(this_week) / len(this_week), 1) if this_week else None,
        "bodyweight_change": round(sum(this_week) / len(this_week) - sum(last_week) / len(last_week), 1) if this_week and last_week else None,
    }
