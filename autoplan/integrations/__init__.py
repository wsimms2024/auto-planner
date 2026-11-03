"""
Connections to outside systems: Google Calendar, Gmail, and Blackboard.

Each module fetches data and saves it through autoplan.db. The scheduler
never talks to these directly; it just sees the events and tasks they add.
sync.py runs them and records how each sync went.
"""
