"""
App-wide settings and defaults.

Edit this file to change where the database lives or what the default
preferences are for a brand-new database. Once the app has created the
database, change preferences on the Settings page instead.
"""

from pathlib import Path

# Project root is the folder that contains app.py
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# SQLite file location (the data/ folder is gitignored)
DB_PATH = PROJECT_ROOT / "data" / "autoplan.db"

# Weekdays in Python order: Monday = 0 ... Sunday = 6
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# Event categories shown in dropdowns
EVENT_CATEGORIES = ["class", "work", "gym", "personal", "other"]

# Task priorities, highest first
PRIORITIES = ["High", "Medium", "Low"]

# Task statuses
TASK_STATUSES = ["open", "done"]

# Study session statuses
SESSION_STATUSES = ["suggested", "accepted", "rejected"]

# Defaults written to the preferences table the first time the app runs.
# Working hours are per weekday so you can change any single day later.
DEFAULT_PREFERENCES = {
    "work_hours": {day: ["09:00", "20:00"] for day in WEEKDAYS},
    "min_session_minutes": 30,
    "max_session_minutes": 90,
    "buffer_minutes": 15,
}
