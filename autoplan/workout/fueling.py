"""
Pre-workout fueling guidance from what you ate and how soon you're training.
Food is sorted by keywords; it's a rough read, not calorie counting.
"""

import re

from autoplan.workout import settings

CARB_WORDS = r"apple|banana|gatorade|powerade|juice|rice|oat|oatmeal|bagel|toast|bread|cereal|pasta|potato|honey|fruit|granola|pretzel|crackers?|candy|gummies|sports drink|waffle|pancake|wrap|sandwich|berries|dates"
PROTEIN_WORDS = r"eggs?|chicken|whey|shake|protein|yogurt|turkey|beef|steak|tuna|salmon|fish|milk|cottage|jerky|sandwich|wrap"
FAT_WORDS = r"bacon|peanut butter|pb|avocado|cheese|nuts|almonds|sausage|butter|fries|pizza|burger"
BIG_WORDS = r"big|large|huge|full meal|burrito|plate|feast"


def read_food(food: str) -> dict:
    f = food.lower()
    return {
        "carbs": bool(re.search(rf"\b({CARB_WORDS})", f)),
        "protein": bool(re.search(rf"\b({PROTEIN_WORDS})", f)),
        "fat": bool(re.search(rf"\b({FAT_WORDS})", f)),
        "big": bool(re.search(rf"\b({BIG_WORDS})", f)),
    }


def advise(split_day: str, entry: dict | None) -> dict:
    """entry: {'minutes_to_gym', 'food'} or None. Returns status + one or two tips."""
    lower_day = split_day in ("Legs", "Lower")
    targets = settings.NUTRITION
    daily = f"Today: {targets['calories'][0]}–{targets['calories'][1]} kcal, {targets['protein'][0]}–{targets['protein'][1]} g protein"
    if split_day == "Rest":
        return {"status": "Rest day: hit protein, carbs can come down a bit", "tips": [], "daily": daily}
    if not entry:
        tip = "Log what you ate with /fuel 60 eggs and toast (minutes until gym, then food)."
        snack = "a carb-heavy snack 30–60 min before" + (" (lower-body day: lean into carbs)" if lower_day else "")
        return {"status": "No pre-workout food logged", "tips": [f"Plan {snack}.", tip], "daily": daily}

    minutes = entry.get("minutes_to_gym")
    food = read_food(entry.get("food", ""))
    tips = []
    if minutes is not None and minutes <= 45:
        if food["fat"] or food["big"]:
            status = "Might sit heavy this close to training"
            tips.append("Skip more food; if anything, quick carbs (Gatorade, banana).")
        elif food["carbs"]:
            status = "Good: quick carbs close to the session"
        else:
            status = "Light on carbs for this close to training"
            tips.append("Add quick carbs now: banana, Gatorade, or a few dates.")
    elif minutes is not None and minutes <= 90:
        if food["carbs"] and (food["protein"] or not lower_day):
            status = f"Enough fuel for {split_day}"
        elif food["carbs"]:
            status = "Carbs covered; fine to train"
        else:
            status = "Mostly protein/fat so far"
            tips.append("Add some carbs in the next 30 min" + (" (lower-body day: worth it)" if lower_day else "") + ".")
    else:
        status = "Meal is a while before training"
        tips.append("Have a carb-heavy snack 30–60 min before" + (" (lower-body day)" if lower_day else "") + ".")
    if food["carbs"] and food["protein"] and not tips:
        tips.append("No need to force more food before you lift.")
    return {"status": status, "tips": tips, "daily": daily, "food": entry.get("food"), "minutes": minutes}
