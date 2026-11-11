"""
Training settings. Edit these to match your program.

Exercise names here are what the engine and /log recognize. Aliases let you
type "bench" or "rdl" instead of the full name.
"""

SPLIT = ["Push", "Pull", "Legs", "Rest", "Upper", "Lower", "Rest"]

GOALS = {"Bench Press": 315, "Squat": 405, "Deadlift": 500}
MAIN_GOAL = "Bench Press"
BODYWEIGHT = {"current": 165, "goal_low": 170, "goal_high": 175}

# "pyramid" (5x5 -> 4x4 -> 3x3 -> 2x2) or "531"
BENCH_PROGRAM = "pyramid"
# 5/3/1 training maxes (only used when a lift's program is "531")
TRAINING_MAX = {"Bench Press": 275, "Squat": 330, "Deadlift": 420}
# Heavy hinge on Lower: "Deadlift" or "Romanian Deadlift"
LOWER_HEAVY_HINGE = "Deadlift"

# Main lifts never planned to failure; last set should land around here
MAIN_LIFT_RPE = (8.0, 8.5)
ACCESSORY_RPE = (8.0, 9.0)

# Smallest jumps (lb) per lift; DB numbers are per dumbbell
INCREMENT = {"Bench Press": 5, "Squat": 10, "Deadlift": 10, "Romanian Deadlift": 10, "default": 5, "dumbbell": 5}
# Smaller jump used when progressing conservatively (needs 1.25 lb plates)
MICRO_INCREMENT = 2.5

PYRAMID_STEPS = [(5, 5), (4, 4), (3, 3), (2, 2)]  # (sets, reps)

# Starting points, used only until you've logged that lift. (weight, sets, reps, rpe)
SEEDS = {
    "Bench Press": (240, 4, 4, 8.0),
    "Squat": (345, 4, 4, 8.0),
}

NUTRITION = {"calories": (2900, 3100), "protein": (170, 190), "fat": (75, 90), "carbs": (350, 420)}

ALIASES = {
    "bench": "Bench Press", "bp": "Bench Press", "bench press": "Bench Press",
    "squat": "Squat", "sq": "Squat", "back squat": "Squat",
    "deadlift": "Deadlift", "dl": "Deadlift", "conventional deadlift": "Deadlift",
    "rdl": "Romanian Deadlift", "romanian deadlift": "Romanian Deadlift",
    "pulldown": "Lat Pulldown", "lat pulldown": "Lat Pulldown",
    "tbar": "T-Bar Row", "t-bar": "T-Bar Row", "t-bar row": "T-Bar Row", "tbar row": "T-Bar Row",
    "iso row": "Iso-Lateral Row", "iso-lateral row": "Iso-Lateral Row", "iso lateral row": "Iso-Lateral Row",
    "pullups": "Pull-Ups", "pull-ups": "Pull-Ups", "pull ups": "Pull-Ups",
    "db curl": "Seated DB Curl", "seated db curl": "Seated DB Curl", "curl": "Seated DB Curl",
    "hammer": "Hammer Curl", "hammer curl": "Hammer Curl",
    "incline": "Incline DB Press", "incline db": "Incline DB Press", "incline db press": "Incline DB Press",
    "ohp": "Shoulder Press", "shoulder press": "Shoulder Press", "db shoulder press": "Shoulder Press",
    "lateral": "Lateral Raise", "lateral raise": "Lateral Raise", "laterals": "Lateral Raise",
    "pushdown": "Triceps Pushdown", "triceps": "Triceps Pushdown", "triceps pushdown": "Triceps Pushdown",
    "fly": "Chest Fly", "chest fly": "Chest Fly", "pec deck": "Chest Fly",
    "row": "Chest-Supported Row", "chest supported row": "Chest-Supported Row", "chest-supported row": "Chest-Supported Row",
    "leg press": "Leg Press", "leg curl": "Leg Curl", "calves": "Calf Raise", "calf raise": "Calf Raise",
    "split squat": "Bulgarian Split Squat", "bulgarian": "Bulgarian Split Squat",
}

DUMBBELL = {"Incline DB Press", "Shoulder Press", "Seated DB Curl", "Hammer Curl", "Lateral Raise", "Bulgarian Split Squat"}

MUSCLES = {
    "Bench Press": "Chest", "Incline DB Press": "Chest", "Chest Fly": "Chest",
    "Shoulder Press": "Shoulders", "Lateral Raise": "Shoulders",
    "Triceps Pushdown": "Triceps",
    "Lat Pulldown": "Back", "T-Bar Row": "Back", "Iso-Lateral Row": "Back", "Pull-Ups": "Back", "Chest-Supported Row": "Back",
    "Seated DB Curl": "Biceps", "Hammer Curl": "Biceps",
    "Squat": "Quads", "Leg Press": "Quads", "Bulgarian Split Squat": "Quads",
    "Deadlift": "Hamstrings/Back", "Romanian Deadlift": "Hamstrings", "Leg Curl": "Hamstrings",
    "Calf Raise": "Calves",
}

# Each day's plan. role: main lifts get programmed loads; accessories use double progression.
# sets can be a range string like "2-3".
TEMPLATES = {
    "Push": {
        "focus": ["Heavy bench strength", "Shoulders", "Triceps"],
        "exercises": [
            {"name": "Bench Press", "role": "main", "style": "strength"},
            {"name": "Incline DB Press", "sets": "3", "reps": (8, 10)},
            {"name": "Shoulder Press", "sets": "3", "reps": (8, 10)},
            {"name": "Lateral Raise", "sets": "3", "reps": (12, 15)},
            {"name": "Triceps Pushdown", "sets": "3", "reps": (10, 12)},
        ],
    },
    "Pull": {
        "focus": ["Back width", "Mid-back thickness", "Biceps"],
        "exercises": [
            {"name": "Lat Pulldown", "sets": "3", "reps": (6, 10)},
            {"name": "T-Bar Row", "sets": "3", "reps": (8, 10)},
            {"name": "Iso-Lateral Row", "sets": "2-3", "reps": (8, 12)},
            {"name": "Pull-Ups", "sets": "2", "reps": None, "note": "Stop 1 rep short of failure unless fully recovered."},
            {"name": "Seated DB Curl", "sets": "3", "reps": (6, 10)},
            {"name": "Hammer Curl", "sets": "2-3", "reps": (8, 12)},
        ],
    },
    "Legs": {
        "focus": ["Heavy squat", "Moderate hinge", "Quads and hamstrings"],
        "exercises": [
            {"name": "Squat", "role": "main", "style": "strength"},
            {"name": "Romanian Deadlift", "role": "main", "style": "moderate"},
            {"name": "Leg Press", "sets": "3", "reps": (8, 12)},
            {"name": "Leg Curl", "sets": "3", "reps": (10, 12)},
            {"name": "Calf Raise", "sets": "3", "reps": (10, 15)},
        ],
    },
    "Upper": {
        "focus": ["Volume bench", "Back", "Shoulders and arms"],
        "exercises": [
            {"name": "Bench Press", "role": "main", "style": "volume"},
            {"name": "Chest-Supported Row", "sets": "3", "reps": (8, 10)},
            {"name": "Shoulder Press", "sets": "3", "reps": (8, 10)},
            {"name": "Chest Fly", "sets": "2-3", "reps": (10, 15)},
            {"name": "Lateral Raise", "sets": "3", "reps": (12, 15)},
            {"name": "Seated DB Curl", "sets": "3", "reps": (8, 12)},
            {"name": "Triceps Pushdown", "sets": "2", "reps": (10, 12), "optional": True},
        ],
    },
    "Lower": {
        "focus": ["Heavy hinge", "Moderate squat", "Posterior chain"],
        "exercises": [
            {"name": "HEAVY_HINGE", "role": "main", "style": "heavy"},
            {"name": "Squat", "role": "main", "style": "moderate"},
            {"name": "Bulgarian Split Squat", "sets": "2-3", "reps": (8, 10)},
            {"name": "Leg Curl", "sets": "3", "reps": (10, 12)},
            {"name": "Calf Raise", "sets": "3", "reps": (10, 15)},
        ],
    },
    "Rest": {"focus": ["Recovery"], "exercises": []},
}
