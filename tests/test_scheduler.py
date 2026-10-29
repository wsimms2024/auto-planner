"""Tests for the scheduling engine. Phase 3 adds the suggestion tests."""

from datetime import date, datetime

from autoplan import scheduler
from autoplan.models import Event


def weekly(name, first_day, start_hm, end_hm, days, until, id=None):
    return Event(
        id=id,
        name=name,
        start=datetime.combine(first_day, datetime.strptime(start_hm, "%H:%M").time()),
        end=datetime.combine(first_day, datetime.strptime(end_hm, "%H:%M").time()),
        category="class",
        recurrence_days=days,
        recurrence_until=until,
    )


# 2026-11-02 is a Monday
MON = date(2026, 11, 2)


def test_one_time_event_in_and_out_of_range():
    e = Event(name="Dentist", start=datetime(2026, 11, 4, 9), end=datetime(2026, 11, 4, 10))
    assert scheduler.expand_events([e], MON, date(2026, 11, 8)) == [e]
    assert scheduler.expand_events([e], date(2026, 11, 5), date(2026, 11, 8)) == []


def test_weekly_event_expands_on_chosen_days():
    e = weekly("ISTM", MON, "10:00", "11:15", [0, 2], date(2026, 12, 12))
    occ = scheduler.expand_events([e], MON, date(2026, 11, 8))
    assert [o.start for o in occ] == [datetime(2026, 11, 2, 10), datetime(2026, 11, 4, 10)]
    assert all(o.end - o.start == e.duration for o in occ)
    assert all(not o.is_recurring for o in occ)


def test_weekly_event_respects_start_and_until():
    # Starts Wednesday, so Monday of that week is skipped; ends Monday the 9th
    e = weekly("Gym", date(2026, 11, 4), "07:00", "08:00", [0, 2, 4], date(2026, 11, 9))
    occ = scheduler.expand_events([e], MON, date(2026, 11, 15))
    assert [o.start.date() for o in occ] == [date(2026, 11, 4), date(2026, 11, 6), date(2026, 11, 9)]


def test_overlaps_touching_is_not_overlap():
    a, b, c = datetime(2026, 1, 1, 9), datetime(2026, 1, 1, 10), datetime(2026, 1, 1, 11)
    assert not scheduler.overlaps(a, b, b, c)
    assert scheduler.overlaps(a, c, b, c)


def test_find_conflicts_between_weekly_and_one_time():
    cls = weekly("ISTM", MON, "10:00", "11:15", [0, 2], date(2026, 12, 12), id=1)
    shift = Event(name="Shift", start=datetime(2026, 11, 4, 11), end=datetime(2026, 11, 4, 15), category="work")
    conflicts = scheduler.find_conflicts(shift, [cls])
    assert len(conflicts) == 1
    assert conflicts[0][1].name == "ISTM"


def test_find_conflicts_ignores_the_event_being_edited():
    cls = weekly("ISTM", MON, "10:00", "11:15", [0, 2], date(2026, 12, 12), id=1)
    moved = weekly("ISTM", MON, "10:30", "11:45", [0, 2], date(2026, 12, 12), id=1)
    assert scheduler.find_conflicts(moved, [cls]) == []


def test_no_conflict_on_different_days():
    cls = weekly("ISTM", MON, "10:00", "11:15", [0, 2], date(2026, 12, 12), id=1)
    gym = weekly("Gym", MON, "10:00", "11:00", [1, 3], date(2026, 12, 12))
    assert scheduler.find_conflicts(gym, [cls]) == []


def test_scheduler_has_expected_functions():
    for name in ["expand_events", "find_free_slots", "prioritize_tasks", "suggest_sessions"]:
        assert callable(getattr(scheduler, name))
