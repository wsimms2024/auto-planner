"""
Builds the weekly calendar grid as a pandas table.

Rows are time slots, columns are days. Each block (event or study session)
fills the slots it covers. The block's name shows in its first slot and the
rest are just colored in. Overlapping blocks show in red.

If this ever feels too plain, it can be swapped for the streamlit-calendar
component without touching anything else.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import pandas as pd

from autoplan.models import Preferences

# Background colors per category. Text is always dark so it reads in light and dark themes.
CATEGORY_COLORS = {
    "class": "#BFDBFE",
    "work": "#FED7AA",
    "gym": "#BBF7D0",
    "personal": "#E9D5FF",
    "other": "#E5E7EB",
    "study": "#99F6E4",
    "conflict": "#FCA5A5",
}


@dataclass
class Block:
    """Anything that takes up time on the calendar."""

    label: str
    start: datetime
    end: datetime
    category: str


def week_start_for(day: date) -> date:
    """Monday of the week containing `day`."""
    return day - timedelta(days=day.weekday())


def _floor(t: time, slot: int) -> time:
    minutes = (t.hour * 60 + t.minute) // slot * slot
    return time(minutes // 60, minutes % 60)


def build_week_grid(
    blocks: list[Block],
    week_start: date,
    prefs: Preferences,
    slot_minutes: int = 30,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Returns (labels, categories): two tables with the same shape.
    labels holds the text for each cell, categories holds the color key.
    The visible hours cover your working hours plus anything outside them.
    """
    days = [week_start + timedelta(days=i) for i in range(7)]
    week_end = datetime.combine(days[-1], time.max)
    week_blocks = [b for b in blocks if b.end > datetime.combine(week_start, time.min) and b.start <= week_end]

    # Visible hours: working hours, stretched to fit any block this week
    first = min((s for s, _ in prefs.work_hours.values()), default=time(9))
    last = max((e for _, e in prefs.work_hours.values()), default=time(20))
    for b in week_blocks:
        first = min(first, b.start.time())
        last = max(last, b.end.time() if b.end.date() == b.start.date() else time(23, 59))
    first = _floor(first, slot_minutes)

    slot_starts = []
    t = datetime.combine(date.min, first)
    stop = datetime.combine(date.min, last)
    while t < stop:
        slot_starts.append(t.time())
        t += timedelta(minutes=slot_minutes)

    columns = [d.strftime("%a %m/%d") for d in days]
    index = [s.strftime("%I:%M %p").lstrip("0") for s in slot_starts]
    labels = pd.DataFrame("", index=index, columns=columns)
    cats = pd.DataFrame("", index=index, columns=columns)

    for col, day in zip(columns, days):
        for row, slot in zip(index, slot_starts):
            slot_start = datetime.combine(day, slot)
            slot_end = slot_start + timedelta(minutes=slot_minutes)
            here = [b for b in week_blocks if b.start < slot_end and slot_start < b.end]
            if not here:
                continue
            starting = [b.label for b in here if slot_start <= b.start < slot_end or (b.start < slot_start and slot == slot_starts[0])]
            labels.at[row, col] = " + ".join(starting)
            cats.at[row, col] = "conflict" if len(here) > 1 else here[0].category

    return labels, cats


def style_week_grid(labels: pd.DataFrame, cats: pd.DataFrame):
    """Return a pandas Styler with each cell colored by category."""

    def colors(_):
        return cats.map(
            lambda c: f"background-color: {CATEGORY_COLORS[c]}; color: #111827" if c else ""
        )

    return labels.style.apply(colors, axis=None)
