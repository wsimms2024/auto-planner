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
    "work_hours": {day: ["00:00", "23:59"] for day in WEEKDAYS},
    "min_session_minutes": 30,
    "max_session_minutes": 90,
    "buffer_minutes": 15,
    "planning_days": 7,
    "suggest_low_priority": False,
}

# ---------- Integrations ----------

# All times in AutoPlan are local to this timezone
TIMEZONE = "America/New_York"

# Sync automatically when the app is opened if the last sync is older than this
AUTO_SYNC_MINUTES = 30

# Google Calendar: how far back/ahead to import, and the reminder on study sessions
GOOGLE_SYNC_DAYS_BACK = 7
GOOGLE_SYNC_WEEKS_AHEAD = 8
SESSION_REMINDER_MINUTES = 10

# Gmail: how far back the first scan looks, and a cap per scan
GMAIL_DAYS_BACK = 14
GMAIL_MAX_MESSAGES = 100
# Pause between emails so a scan stays under Gmail's per-minute limit
GMAIL_PAUSE_SECONDS = 0.25

# Priority and time estimates for imported tasks are guessed in task_rules.py
