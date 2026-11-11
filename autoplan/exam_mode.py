"""
Exam mode: turn exams on your calendar into study tasks.

If an event like "ACCY 2001 Midterm" shows up (typed in, or from Google
Calendar), AutoPlan adds a "Study for ACCY 2001 Midterm" task due when the
exam starts. The scheduler then spreads review sessions over the week before
(see scheduler.exam_days). Skipped if you already have an exam task that day.
"""

from datetime import datetime, timedelta

from autoplan import config, db, scheduler, task_rules
from autoplan.models import Task

LOOK_AHEAD_DAYS = 30


def sync_exam_tasks(now: datetime | None = None) -> int:
    """Create study tasks for upcoming exam events. Returns how many were new."""
    now = now or datetime.now()
    occurrences = scheduler.expand_events(db.list_events(), now.date(), now.date() + timedelta(days=LOOK_AHEAD_DAYS))
    exams = [e for e in occurrences if task_rules.is_exam(e.name) and e.start > now]
    if not exams:
        return 0
    existing = [t for t in db.list_tasks(include_deleted=True) if task_rules.is_exam(t.name) and t.source != "exam"]
    new = 0
    for e in exams:
        if any(t.deadline.date() == e.start.date() for t in existing):
            continue  # you already track this exam as a task
        task = Task(
            name=f"Study for {e.name}",
            deadline=e.start,
            estimated_minutes=config.EXAM_STUDY_MINUTES,
            priority="High",
            source="exam",
            external_id=f"{e.source}:{e.external_id or e.id}:{e.start.date().isoformat()}",
        )
        _, is_new = db.upsert_external_task(task)
        new += is_new
    return new
