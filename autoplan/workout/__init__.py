"""
Daily Workout Digest: a deterministic strength-coach engine plus Telegram formatting.

- settings.py   your split, goals, programs, exercise templates (edit this)
- store.py      saving/reading workouts, recovery check-ins, fueling notes
- parser.py     turns "/log push; bench 240 4x4 @8" into sets
- engine.py     split day, e1RM, progression, target loads (all math lives here)
- recovery.py   readiness from sleep/energy/pain/etc.
- fueling.py    pre-workout food guidance
- digest.py     builds the structured digest and formats it for Telegram
- sources.py    where workouts come from (manual now, Liftoff later)
"""
