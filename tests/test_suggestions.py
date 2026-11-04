"""
Scheduling engine tests: free time, ranking, and session suggestions.

Times are fixed (not "now") so results never depend on when tests run.
2026-11-02 is a Monday.
"""

from datetime import date, datetime, time, timedelta

from autoplan import scheduler
from autoplan.models import Event, Preferences, StudySession, Task

MON = date(2026, 11, 2)


def prefs(**overrides):
    base = dict(
        work_hours={d: (time(9), time(20)) for d in range(7)},
        min_session_minutes=30,
        max_session_minutes=90,
        buffer_minutes=15,
    )
    base.update(overrides)
    return Preferences(**base)


def at(day, h, m=0):
    return datetime.combine(day, time(h, m))


def event(name, day, start, end, **kw):
    return Event(name=name, start=at(day, *start), end=at(day, *end), category="class", **kw)


def task(id, name, deadline, minutes, priority="Medium", status="open"):
    return Task(id=id, name=name, deadline=deadline, estimated_minutes=minutes, priority=priority, status=status)


def minutes(s):
    return int((s.end - s.start).total_seconds() // 60)


# ---------- Free time ----------

def test_free_slots_respect_working_hours_and_buffer():
    cls = event("Class", MON, (10, 0), (11, 0))
    slots = scheduler.find_free_slots([cls], prefs(), MON, MON)
    assert slots == [(at(MON, 9), at(MON, 9, 45)), (at(MON, 11, 15), at(MON, 20))]


def test_overlapping_events_block_their_combined_time():
    a = event("A", MON, (12, 0), (13, 0))
    b = event("B", MON, (12, 30), (14, 0))
    slots = scheduler.find_free_slots([a, b], prefs(buffer_minutes=0), MON, MON)
    assert slots == [(at(MON, 9), at(MON, 12)), (at(MON, 14), at(MON, 20))]


def test_short_gaps_are_dropped():
    a = event("A", MON, (9, 0), (10, 0))
    b = event("B", MON, (10, 20), (20, 0))
    assert scheduler.find_free_slots([a, b], prefs(buffer_minutes=0), MON, MON) == []


def test_past_time_is_not_free():
    slots = scheduler.find_free_slots([], prefs(), MON, MON, now=at(MON, 14, 7))
    assert slots == [(at(MON, 14, 15), at(MON, 20))]


def test_day_off_has_no_free_time():
    hours = {d: (time(9), time(20)) for d in range(6)}  # Sunday off
    sunday = MON + timedelta(days=6)
    assert scheduler.find_free_slots([], prefs(work_hours=hours), sunday, sunday) == []


def test_recurring_events_block_every_occurrence():
    cls = Event(
        name="ISTM", start=at(MON, 9), end=at(MON, 20), category="class",
        recurrence_days=[0, 2], recurrence_until=date(2026, 12, 12),
    )
    slots = scheduler.find_free_slots([cls], prefs(), MON, MON + timedelta(days=2))
    assert {s.date() for s, _ in slots} == {MON + timedelta(days=1)}  # only Tuesday is open


# ---------- Ranking ----------

def test_priority_weighting():
    now = at(MON, 9)
    high_far = task(1, "High in 4d", now + timedelta(days=4), 60, "High")
    med_near = task(2, "Med in 1d", now + timedelta(days=1), 60, "Medium")
    low_near = task(3, "Low in 1d", now + timedelta(days=1), 60, "Low")
    order = [t.id for t in scheduler.prioritize_tasks([low_near, high_far, med_near], now)]
    # Scores: Med 24/1 = 24, High 96/2 = 48, Low 24/0.5 = 48. Tie goes to the earlier deadline.
    assert order == [2, 3, 1]


def test_overdue_tasks_come_first_and_completed_are_skipped():
    now = at(MON, 9)
    overdue = task(1, "Late", now - timedelta(hours=2), 60, "Low")
    urgent = task(2, "Soon", now + timedelta(hours=3), 60, "High")
    done = task(3, "Done", now + timedelta(hours=1), 60, status="done")
    assert [t.id for t in scheduler.prioritize_tasks([urgent, done, overdue], now)] == [1, 2]


# ---------- Suggestions ----------

def test_simple_task_gets_one_session():
    t = task(1, "Reading", at(MON + timedelta(days=3), 23, 59), 60)
    result = scheduler.suggest_sessions([t], [], [], prefs(), now=at(MON, 9))
    assert [(s.start, s.end) for s in result.sessions] == [(at(MON, 9), at(MON, 10))]
    assert result.unscheduled == {}


def test_large_task_split_across_days():
    t = task(1, "Essay", at(MON + timedelta(days=5), 23, 59), 240)
    result = scheduler.suggest_sessions([t], [], [], prefs(), now=at(MON, 9))
    assert [minutes(s) for s in result.sessions] == [90, 90, 60]
    assert len({s.start.date() for s in result.sessions}) == 3  # one per day


def test_sessions_double_up_on_a_day_when_deadline_is_close():
    t = task(1, "Cram", at(MON, 18), 180)
    result = scheduler.suggest_sessions([t], [], [], prefs(), now=at(MON, 9))
    assert [minutes(s) for s in result.sessions] == [90, 90]
    s1, s2 = result.sessions
    assert s2.start - s1.end >= timedelta(minutes=15)  # buffer between them
    assert s2.end <= at(MON, 18)


def test_urgent_task_scheduled_before_later_one():
    now = at(MON, 9)
    later = task(1, "Later", now + timedelta(days=5), 60)
    soon = task(2, "Soon", now + timedelta(hours=6), 60)
    result = scheduler.suggest_sessions([later, soon], [], [], prefs(), now=now)
    first = result.sessions[0]
    assert first.task_id == 2 and first.start == now


def test_not_enough_time_reports_unscheduled_minutes():
    busy = event("Work", MON, (9, 0), (17, 0))
    t = task(1, "Lab", at(MON, 19), 180)
    result = scheduler.suggest_sessions([t], [busy], [], prefs(), now=at(MON, 8))
    # Only 17:15-19:00 is free. One 90-min session fits, then the 15-min
    # buffer leaves nothing usable before the deadline.
    assert sum(minutes(s) for s in result.sessions) == 90
    assert result.unscheduled == {1: 90}


def test_sessions_never_overlap_events_or_each_other():
    cls = Event(
        name="ISTM", start=at(MON, 10), end=at(MON, 11, 15), category="class",
        recurrence_days=[0, 1, 2, 3, 4], recurrence_until=date(2026, 12, 12),
    )
    tasks = [task(i, f"T{i}", at(MON + timedelta(days=4), 23, 59), 150) for i in range(1, 6)]
    result = scheduler.suggest_sessions(tasks, [cls], [], prefs(), now=at(MON, 9))
    occ = scheduler.expand_events([cls], MON, MON + timedelta(days=6))
    blocks = [(s.start, s.end) for s in result.sessions]
    for i, (a_s, a_e) in enumerate(blocks):
        assert at(a_s.date(), 9) <= a_s and a_e <= at(a_s.date(), 20)
        for e in occ:
            assert not scheduler.overlaps(a_s, a_e, e.start - timedelta(minutes=15), e.end + timedelta(minutes=15))
        for b_s, b_e in blocks[i + 1:]:
            assert not scheduler.overlaps(a_s, a_e, b_s, b_e)


def test_spills_into_next_day_when_today_is_full():
    busy = event("Shift", MON, (9, 0), (20, 0))
    t = task(1, "HW", at(MON + timedelta(days=2), 12), 60)
    result = scheduler.suggest_sessions([t], [busy], [], prefs(), now=at(MON, 8))
    assert result.sessions[0].start == at(MON + timedelta(days=1), 9)


def test_completed_tasks_get_no_sessions():
    t = task(1, "Done", at(MON, 20), 60, status="done")
    assert scheduler.suggest_sessions([t], [], [], prefs(), now=at(MON, 9)).sessions == []


def test_accepted_sessions_count_as_progress_and_busy_time():
    t = task(1, "Essay", at(MON + timedelta(days=3), 23, 59), 120)
    other = task(2, "Quiz prep", at(MON + timedelta(days=3), 23, 59), 30)
    accepted = StudySession(id=9, task_id=1, start=at(MON, 9), end=at(MON, 10, 30), status="accepted")
    result = scheduler.suggest_sessions([t, other], [], [accepted], prefs(), now=at(MON, 8))
    essay = [s for s in result.sessions if s.task_id == 1]
    assert sum(minutes(s) for s in essay) == 30  # 120 - 90 already planned
    assert essay[0].start.date() != MON  # prefers a day without an Essay session
    assert all(not scheduler.overlaps(s.start, s.end, accepted.start, accepted.end) for s in result.sessions)


def test_rejected_slot_is_not_suggested_again():
    t = task(1, "Reading", at(MON + timedelta(days=3), 23, 59), 60)
    rejected = StudySession(task_id=1, start=at(MON, 9), end=at(MON, 10), status="rejected")
    result = scheduler.suggest_sessions([t], [], [rejected], prefs(), now=at(MON, 9))
    s = result.sessions[0]
    assert not scheduler.overlaps(s.start, s.end, rejected.start, rejected.end)
    assert s.start == at(MON, 10)


def test_overdue_task_still_scheduled_asap():
    t = task(1, "Late HW", at(MON, 8), 60)
    result = scheduler.suggest_sessions([t], [], [], prefs(), now=at(MON, 9))
    assert result.sessions[0].start == at(MON, 9)


def test_remainder_shorter_than_minimum_rounds_up():
    t = task(1, "Short", at(MON, 20), 20)
    result = scheduler.suggest_sessions([t], [], [], prefs(), now=at(MON, 9))
    assert minutes(result.sessions[0]) == 30


def test_odd_now_snaps_to_quarter_hour():
    t = task(1, "HW", at(MON, 20), 60)
    result = scheduler.suggest_sessions([t], [], [], prefs(), now=at(MON, 9, 7))
    assert result.sessions[0].start == at(MON, 9, 15)


def test_prefers_full_session_over_squeezing_into_a_short_gap():
    cls = event("Class", MON, (10, 0), (11, 0))  # leaves only 9:00-9:45 before it
    t = task(1, "Reading", at(MON + timedelta(days=3), 23, 59), 60)
    result = scheduler.suggest_sessions([t], [cls], [], prefs(), now=at(MON, 9))
    assert [(s.start, s.end) for s in result.sessions] == [(at(MON, 11, 15), at(MON, 12, 15))]


def test_short_gap_used_when_nothing_else_fits_before_deadline():
    cls = event("Class", MON, (10, 0), (11, 0))
    t = task(1, "Quiz prep", at(MON, 10), 60)  # due at class time
    result = scheduler.suggest_sessions([t], [cls], [], prefs(), now=at(MON, 9))
    assert [minutes(s) for s in result.sessions] == [45]
    assert result.unscheduled == {1: 15}



def test_low_priority_and_unplanned_tasks_get_no_sessions():
    deadline = at(MON + timedelta(days=3), 23, 59)
    low = task(1, "Safety Awareness Training", deadline, 30, "Low")
    off = task(2, "Event participation", deadline, 60)
    off.plan_sessions = False
    real = task(3, "Quiz prep", deadline, 45)
    result = scheduler.suggest_sessions([low, off, real], [], [], prefs(), now=at(MON, 9))
    assert {s.task_id for s in result.sessions} == {3}

    with_low = scheduler.suggest_sessions([low, off, real], [], [], prefs(suggest_low_priority=True), now=at(MON, 9))
    assert {s.task_id for s in with_low.sessions} == {1, 3}
