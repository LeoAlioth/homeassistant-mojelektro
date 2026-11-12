"""Slovenian network tariff-block rules.

The five-block schedule follows the Slovenian Energy Agency's tariff
methodology (Akt o metodologiji za obračunavanje omrežnine za elektrooperaterje,
Priloga 2, poglavje 3). The hours changed on 1 January 2027 (Uradni list RS
2025, the amending act of 29 September 2025), so each schedule is kept with the
date it applies from. Meter-reading timestamps represent interval ends, so the
interval start is used when selecting the applicable block.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

HIGH_SEASON_MONTHS = frozenset({11, 12, 1, 2})

# Slovenian public holidays which are legally work-free days.
FIXED_DAYS_OFF = frozenset(
    {
        (1, 1),
        (1, 2),
        (2, 8),
        (4, 27),
        (5, 1),
        (5, 2),
        (6, 25),
        (8, 15),
        (10, 31),
        (11, 1),
        (12, 25),
        (12, 26),
    }
)

# Hour -> time block, one table per (season, day type).
# Until 2026: 00-06, 06-07, 07-14, 14-16, 16-20, 20-22, 22-24.
SCHEDULE_2024 = {
    ("high", "workday"): (3,) * 6 + (2,) + (1,) * 7 + (2,) * 2 + (1,) * 4 + (2,) * 2 + (3,) * 2,
    ("high", "day_off"): (4,) * 6 + (3,) + (2,) * 7 + (3,) * 2 + (2,) * 4 + (3,) * 2 + (4,) * 2,
    ("low", "workday"): (4,) * 6 + (3,) + (2,) * 7 + (3,) * 2 + (2,) * 4 + (3,) * 2 + (4,) * 2,
    ("low", "day_off"): (5,) * 6 + (4,) + (3,) * 7 + (4,) * 2 + (3,) * 4 + (4,) * 2 + (5,) * 2,
}
# From 2027: 00-06, 06-12, 12-17, 17-20, 20-22, 22-24; work-free days have two blocks.
SCHEDULE_2027 = {
    ("high", "workday"): (3,) * 6 + (1,) * 6 + (2,) * 5 + (1,) * 3 + (2,) * 2 + (3,) * 2,
    ("high", "day_off"): (4,) * 6 + (3,) * 6 + (4,) * 5 + (3,) * 3 + (3,) * 2 + (4,) * 2,
    ("low", "workday"): (5,) * 6 + (3,) * 6 + (4,) * 5 + (3,) * 3 + (3,) * 2 + (5,) * 2,
    ("low", "day_off"): (5,) * 6 + (4,) * 6 + (5,) * 5 + (4,) * 3 + (4,) * 2 + (5,) * 2,
}
# (first day it applies, schedule), oldest first; a new methodology is one more row.
SCHEDULES = (
    (date(2024, 10, 1), SCHEDULE_2024),
    (date(2027, 1, 1), SCHEDULE_2027),
)


def easter_sunday(year: int) -> date:
    """Return Gregorian Easter Sunday."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def is_slovenian_day_off(value: date) -> bool:
    """Return whether the date uses the work-free-day tariff schedule."""
    if value.weekday() >= 5:
        return True

    if (value.month, value.day) in FIXED_DAYS_OFF:
        return True

    easter = easter_sunday(value.year)
    return value == easter + timedelta(days=1)


def block_at(moment: datetime) -> int:
    """Return the network tariff block 1-5 in force at a local time."""
    day = moment.date()
    schedule = SCHEDULES[0][1]
    for since, table in SCHEDULES:
        if day >= since:
            schedule = table
    season = "high" if day.month in HIGH_SEASON_MONTHS else "low"
    kind = "day_off" if is_slovenian_day_off(day) else "workday"
    return schedule[(season, kind)][moment.hour]


def network_tariff_block(timestamp: str) -> int:
    """Return network tariff block 1-5 for an interval-end timestamp."""
    interval_end = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return block_at(interval_end - timedelta(minutes=15))
