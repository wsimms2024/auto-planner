"""Saving and reading workouts, recovery check-ins and fueling notes."""

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from autoplan import db


@dataclass
class SetLog:
    exercise: str
    reps: int
    weight: Optional[float] = None  # None = bodyweight
    rpe: Optional[float] = None
    target_reps: Optional[int] = None


@dataclass
class Session:
    date: date
    split_day: str
    sets: list[SetLog] = field(default_factory=list)
    notes: str = ""
    source: str = "manual"
    id: Optional[int] = None

    def sets_for(self, exercise: str) -> list[SetLog]:
        return [s for s in self.sets if s.exercise == exercise]


# ---------- Workouts ----------

def add_session(session: Session, external_id: str | None = None) -> int:
    with db.get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO workout_sessions (date, split_day, notes, source, external_id) VALUES (?, ?, ?, ?, ?)",
            (session.date.isoformat(), session.split_day, session.notes, session.source, external_id),
        )
        sid = cur.lastrowid
        counters: dict[str, int] = {}
        for s in session.sets:
            counters[s.exercise] = counters.get(s.exercise, 0) + 1
            conn.execute(
                "INSERT INTO workout_sets (session_id, exercise, set_no, weight, reps, target_reps, rpe) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sid, s.exercise, counters[s.exercise], s.weight, s.reps, s.target_reps, s.rpe),
            )
        return sid


def delete_session(session_id: int) -> None:
    with db.get_connection() as conn:
        conn.execute("DELETE FROM workout_sets WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM workout_sessions WHERE id = ?", (session_id,))


def list_sessions(since: date | None = None, limit: int | None = None) -> list[Session]:
    """Newest first."""
    sql, params = "SELECT * FROM workout_sessions", []
    if since:
        sql += " WHERE date >= ?"
        params.append(since.isoformat())
    sql += " ORDER BY date DESC, id DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    with db.get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            sets = conn.execute("SELECT * FROM workout_sets WHERE session_id = ? ORDER BY id", (r["id"],)).fetchall()
            out.append(Session(
                id=r["id"], date=date.fromisoformat(r["date"]), split_day=r["split_day"], notes=r["notes"], source=r["source"],
                sets=[SetLog(exercise=s["exercise"], reps=s["reps"], weight=s["weight"], rpe=s["rpe"], target_reps=s["target_reps"]) for s in sets],
            ))
        return out


def sessions_with(exercise: str, split_day: str | None = None, limit: int = 4) -> list[Session]:
    """Most recent sessions that include an exercise (optionally only on one split day)."""
    out = [s for s in list_sessions() if s.sets_for(exercise) and (split_day is None or s.split_day == split_day)]
    return out[:limit]


# ---------- Split day override ("Change split day" button) ----------

def set_split_override(day: date, split_day: str) -> None:
    db.set_preference("workout_split_override", {"date": day.isoformat(), "split_day": split_day})


def get_split_override(day: date) -> str | None:
    value = db.get_preference_value("workout_split_override")
    return value["split_day"] if value and value.get("date") == day.isoformat() else None


# ---------- Recovery ----------

RECOVERY_FIELDS = ["sleep_hours", "sleep_quality", "bodyweight", "soreness", "energy", "illness",
                   "elbow_pain", "joint_pain", "alcohol", "low_calories", "disrupted", "notes"]


def save_recovery(day: date, **values) -> dict:
    """Merge values into that day's check-in (so you can send it in pieces)."""
    current = get_recovery(day) or {}
    current.update({k: v for k, v in values.items() if k in RECOVERY_FIELDS and v is not None})
    with db.get_connection() as conn:
        cols = ["date"] + [k for k in RECOVERY_FIELDS if k in current]
        vals = [day.isoformat()] + [current[k] for k in cols[1:]]
        conn.execute(f"INSERT OR REPLACE INTO recovery_logs ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals)
    return current


def get_recovery(day: date) -> dict | None:
    with db.get_connection() as conn:
        row = conn.execute("SELECT * FROM recovery_logs WHERE date = ?", (day.isoformat(),)).fetchone()
    if not row:
        return None
    return {k: row[k] for k in RECOVERY_FIELDS if row[k] is not None and row[k] != ""}


def bodyweights(since: date) -> list[tuple[date, float]]:
    with db.get_connection() as conn:
        rows = conn.execute("SELECT date, bodyweight FROM recovery_logs WHERE bodyweight IS NOT NULL AND date >= ? ORDER BY date",
                            (since.isoformat(),)).fetchall()
    return [(date.fromisoformat(r["date"]), r["bodyweight"]) for r in rows]


def latest_bodyweight() -> float | None:
    with db.get_connection() as conn:
        row = conn.execute("SELECT bodyweight FROM recovery_logs WHERE bodyweight IS NOT NULL ORDER BY date DESC LIMIT 1").fetchone()
    return row["bodyweight"] if row else None


# ---------- Fueling ----------

def add_fueling(minutes_to_gym: int | None, food: str, carbs=None, protein=None, fat=None, at: datetime | None = None) -> None:
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO fueling_logs (logged_at, minutes_to_gym, food, carbs, protein, fat) VALUES (?, ?, ?, ?, ?, ?)",
            ((at or datetime.now()).isoformat(), minutes_to_gym, food, carbs, protein, fat),
        )


def latest_fueling(now: datetime, within_hours: int = 6) -> dict | None:
    with db.get_connection() as conn:
        row = conn.execute("SELECT * FROM fueling_logs WHERE logged_at >= ? ORDER BY logged_at DESC LIMIT 1",
                           ((now - timedelta(hours=within_hours)).isoformat(),)).fetchone()
    return dict(row) if row else None
