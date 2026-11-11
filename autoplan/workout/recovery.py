"""
Readiness from your daily check-in. Deterministic and deliberately calm:
one mediocre marker doesn't change the plan.

Points: sleep under 6 h (1, under 5 h: 2), sleep quality 1-2/5 (1), energy 1-2/5 (1),
soreness 4-5/5 (1), alcohol (1), low calories (1), schedule disruption (0.5).
  under 2 points  -> train normally
  2 to 2.5        -> trim: one fewer set on main lifts, RPE cap 8
  3 or more       -> reduce: ~7% lighter main lifts, one fewer set, RPE cap 7.5
Illness: fever/chest/systemic -> rest. Mild or improving -> conservative.
Elbow pain 4+/10 -> lighter pressing; 7+/10 -> skip pressing today.
"""


def assess(log: dict | None) -> dict:
    adj = {"load_factor": 1.0, "drop_sets": 0, "rpe_cap": None, "reduce_pressing": False, "avoid_pressing": False, "rest": False}
    if not log:
        return {"status": "No check-in yet: planning a normal day", "level": "unknown", "reasons": [], "adjust": adj, "log": {}}

    reasons, points = [], 0.0
    sleep = log.get("sleep_hours")
    if sleep is not None and sleep < 6:
        points += 2 if sleep < 5 else 1
        reasons.append(f"short sleep ({sleep:g} h)")
    if (log.get("sleep_quality") or 5) <= 2:
        points += 1
        reasons.append("poor sleep quality")
    if (log.get("energy") or 5) <= 2:
        points += 1
        reasons.append("low energy")
    if (log.get("soreness") or 1) >= 4:
        points += 1
        reasons.append("very sore")
    if log.get("alcohol"):
        points += 1
        reasons.append("alcohol last night")
    if log.get("low_calories"):
        points += 1
        reasons.append("low calories yesterday")
    if log.get("disrupted"):
        points += 0.5
        reasons.append("schedule disrupted")

    illness = (log.get("illness") or "none").lower()
    if illness in ("systemic", "fever", "chest"):
        adj["rest"] = True
        return {"status": "Rest today: systemic illness. Train once it clears.", "level": "rest",
                "reasons": reasons + ["fever/chest symptoms"], "adjust": adj, "log": log}

    level, status = "normal", "Train normally"
    if points >= 3:
        level, status = "reduce", "Reduce: ~7% lighter main lifts, one fewer set"
        adj.update(load_factor=0.93, drop_sets=1, rpe_cap=7.5)
    elif points >= 2:
        level, status = "trim", "Trim: one fewer set on main lifts, cap RPE 8"
        adj.update(drop_sets=1, rpe_cap=8.0)

    if illness in ("mild", "improving"):
        reasons.append(f"{illness} illness")
        if level == "normal":
            level, status = "conservative", "Train conservatively: mild illness"
            adj.update(drop_sets=1, rpe_cap=7.5)

    elbow = log.get("elbow_pain") or 0
    if elbow >= 7:
        adj["avoid_pressing"] = True
        reasons.append(f"elbow pain {elbow}/10")
        status += ". Skip pressing today"
    elif elbow >= 4:
        adj["reduce_pressing"] = True
        reasons.append(f"elbow pain {elbow}/10")
        status += ". Lighter pressing"

    if (log.get("joint_pain") or 0) >= 5:
        reasons.append(f"joint pain {log['joint_pain']}/10: work around anything that aggravates it")

    return {"status": status, "level": level, "reasons": reasons, "adjust": adj, "log": log}
