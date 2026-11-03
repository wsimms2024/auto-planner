"""Weekly grid tests."""

from datetime import date, datetime, time

from autoplan.models import Preferences
from autoplan.week_view import Block, build_week_grid, week_start_for

MON = date(2026, 11, 2)
PREFS = Preferences(work_hours={d: (time(9), time(20)) for d in range(7)})


def test_week_start_for():
    assert week_start_for(date(2026, 11, 5)) == MON
    assert week_start_for(MON) == MON


def test_block_fills_its_slots():
    b = Block("ISTM", datetime(2026, 11, 2, 10), datetime(2026, 11, 2, 11, 15), "class")
    labels, cats = build_week_grid([b], MON, PREFS)
    col = "Mon 11/02"
    assert labels.at["10:00 AM", col] == "ISTM"
    assert labels.at["10:30 AM", col] == ""  # continuation is colored, not labeled
    assert [cats.at[r, col] for r in ["10:00 AM", "10:30 AM", "11:00 AM", "11:30 AM"]] == ["class"] * 3 + [""]


def test_grid_shows_the_full_day():
    b = Block("Lift", datetime(2026, 11, 3, 6, 30), datetime(2026, 11, 3, 7, 30), "gym")
    labels, _ = build_week_grid([b], MON, PREFS)
    assert labels.index[0] == "12:00 AM"
    assert labels.index[-1] == "11:30 PM"
    assert len(labels) == 48
    assert labels.at["6:30 AM", "Tue 11/03"] == "Lift"


def test_overlap_marked_as_conflict():
    a = Block("A", datetime(2026, 11, 4, 12), datetime(2026, 11, 4, 13), "class")
    b = Block("B", datetime(2026, 11, 4, 12, 30), datetime(2026, 11, 4, 13, 30), "work")
    _, cats = build_week_grid([a, b], MON, PREFS)
    assert cats.at["12:30 PM", "Wed 11/04"] == "conflict"
    assert cats.at["12:00 PM", "Wed 11/04"] == "class"
