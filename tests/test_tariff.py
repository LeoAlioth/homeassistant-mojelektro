"""Tests for the Slovenian network tariff profile."""

from datetime import date, datetime

import pytest

from custom_components.mojelektro.tariff import (
    SCHEDULE_2024,
    SCHEDULE_2027,
    block_at,
    is_slovenian_day_off,
    network_tariff_block,
)


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        # 2026, higher season, workday (Monday 12 January 2026).
        ("2026-01-12T06:15:00+01:00", 2),
        ("2026-01-12T07:15:00+01:00", 1),
        ("2026-01-12T12:15:00+01:00", 1),
        ("2026-01-12T14:15:00+01:00", 2),
        ("2026-01-12T16:15:00+01:00", 1),
        ("2026-01-12T20:15:00+01:00", 2),
        ("2026-01-12T22:15:00+01:00", 3),
        # 2026, higher season, day off (Sunday 11 January 2026).
        ("2026-01-11T07:15:00+01:00", 2),
        ("2026-01-11T14:15:00+01:00", 3),
        # 2026, lower season, workday (Monday 13 July 2026) and day off.
        ("2026-07-13T07:15:00+02:00", 2),
        ("2026-07-13T22:15:00+02:00", 4),
        ("2026-07-12T07:15:00+02:00", 3),
        ("2026-07-12T00:15:00+02:00", 5),
        # 2027, higher season, workday (Tuesday 12 January 2027).
        ("2027-01-12T06:15:00+01:00", 1),
        ("2027-01-12T12:15:00+01:00", 2),
        ("2027-01-12T17:15:00+01:00", 1),
        ("2027-01-12T20:15:00+01:00", 2),
        ("2027-01-12T22:15:00+01:00", 3),
        # 2027, higher season, day off (Sunday 10 January 2027).
        ("2027-01-10T06:15:00+01:00", 3),
        ("2027-01-10T12:15:00+01:00", 4),
        # 2027, lower season, workday (Tuesday 13 July 2027).
        ("2027-07-13T06:15:00+02:00", 3),
        ("2027-07-13T12:15:00+02:00", 4),
        ("2027-07-13T22:15:00+02:00", 5),
        # 2027, lower season, day off (Sunday 11 July 2027).
        ("2027-07-11T06:15:00+02:00", 4),
        ("2027-07-11T12:15:00+02:00", 5),
        # The interval ending at midnight still belongs to the old year
        # (Thursday 31 December 2026, 23:45, a workday).
        ("2027-01-01T00:00:00+01:00", 3),
    ],
)
def test_network_tariff_schedule(timestamp, expected):
    assert network_tariff_block(timestamp) == expected


def test_block_at_switches_on_new_year():
    # noon on a higher-season workday: block 1 until 2026, block 2 from 2027
    assert block_at(datetime(2026, 12, 31, 12)) == 1
    assert block_at(datetime(2027, 1, 4, 12)) == 2


def test_every_table_covers_the_day():
    for schedule in (SCHEDULE_2024, SCHEDULE_2027):
        for table in schedule.values():
            assert len(table) == 24
            assert set(table) <= {1, 2, 3, 4, 5}


def test_2027_day_off_has_two_blocks():
    assert set(SCHEDULE_2027[("high", "day_off")]) == {3, 4}
    assert set(SCHEDULE_2027[("low", "day_off")]) == {4, 5}


def test_easter_monday_is_day_off():
    assert is_slovenian_day_off(date(2026, 4, 6))


def test_non_work_free_state_holiday_is_not_forced_day_off():
    assert not is_slovenian_day_off(date(2026, 6, 8))
