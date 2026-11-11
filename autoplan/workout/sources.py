"""
Where workouts come from. Manual logging (/log) writes straight to the store.

To connect Liftoff later: write a class with fetch(since) that returns
store.Session objects (source="liftoff"), and call import_from(source) from the
sync job. The digest engine only reads the store, so nothing else changes.
"""

from datetime import date

from autoplan.workout import store


class WorkoutSource:
    name = "base"

    def fetch(self, since: date) -> list[tuple[str, store.Session]]:
        """Return (external_id, Session) pairs for workouts since a date."""
        raise NotImplementedError


def import_from(source: WorkoutSource, since: date) -> int:
    """Save workouts from a source, skipping ones already imported."""
    from autoplan import db

    with db.get_connection() as conn:
        seen = {r[0] for r in conn.execute("SELECT external_id FROM workout_sessions WHERE source = ?", (source.name,))}
    new = 0
    for external_id, session in source.fetch(since):
        if external_id in seen:
            continue
        session.source = source.name
        store.add_session(session, external_id=external_id)
        new += 1
    return new
