"""Tests for the month's peak, excess power and energy per tariff block."""

from datetime import date, datetime, timedelta
import math

import pytest

from custom_components.mojelektro.billing import (
    BLOCKS,
    Interval,
    MonthData,
    block_figures,
    build_month,
    excess_power,
    read_intervals,
)
from custom_components.mojelektro.hourly import READING_EXPORT, READING_IMPORT


def readings(first_end: str, values: list[float], reading_type=READING_IMPORT):
    """One API block; each reading is stamped with the end of its quarter-hour."""
    first = datetime.fromisoformat(first_end)
    return {
        "readingType": reading_type,
        "intervalReadings": [
            {"timestamp": (first + timedelta(minutes=15 * i)).isoformat(), "value": str(v)}
            for i, v in enumerate(values)
        ],
    }


def test_the_energy_agencys_worked_example():
    # Agreed 4.6 kW, three overruns of 5.4, 4.9 and 6.1 kW: the root of 0.64 + 0.09 + 2.25.
    assert excess_power([5.4, 4.9, 6.1], 4.6) == pytest.approx(math.sqrt(2.98))
    assert excess_power([5.4, 4.9, 6.1], 4.6) == pytest.approx(1.726, abs=0.001)


def test_no_interval_over_the_agreed_power_means_no_excess():
    assert excess_power([], 4.6) == 0.0
    assert excess_power([4.6, 3.0], 4.6) == 0.0


def test_an_interval_is_its_energy_over_a_quarter_of_an_hour():
    interval = Interval(datetime.fromisoformat("2026-07-13T08:00:00+02:00"), 0.5)
    assert interval.kw == 2.0


def test_a_reading_starts_a_quarter_hour_before_its_stamp():
    block = readings("2026-01-12T07:15:00+01:00", [1.0])
    (interval,) = read_intervals([block], READING_IMPORT)
    assert (interval.start.hour, interval.start.minute) == (7, 0)


def test_a_utc_stamp_is_read_as_slovenian_local_time():
    # 2026-01-12T06:15Z is 07:15 local, so the interval starts at 07:00.
    block = readings("2026-01-12T06:15:00+00:00", [1.0])
    (interval,) = read_intervals([block], READING_IMPORT)
    assert interval.start.hour == 7 and interval.block == 1


def test_the_hour_after_the_spring_clock_change_is_hour_three():
    # 2026-03-29: 02:00 CET jumps to 03:00 CEST. The reading ending 01:15 UTC starts at 03:00 local.
    block = readings("2026-03-29T00:45:00+00:00", [1.0, 1.0, 1.0])
    first, second, third = read_intervals([block], READING_IMPORT)
    assert (first.start.hour, first.start.minute) == (1, 30)
    assert (second.start.hour, second.start.minute) == (1, 45)
    assert (third.start.hour, third.start.minute) == (3, 0)


def test_an_interval_belongs_to_the_block_of_the_hour_it_starts_in():
    # Tuesday 12 January 2027, higher season: block 1 until 12:00, block 2 from 12:00.
    block = readings("2027-01-12T12:00:00+01:00", [1.0, 1.0])
    ending_at_noon, ending_after = read_intervals([block], READING_IMPORT)
    assert ending_at_noon.block == 1
    assert ending_after.block == 2


def test_only_the_asked_reading_type_is_read_and_bad_values_are_skipped():
    blocks = [
        readings("2026-01-12T07:15:00+01:00", [1.0, 2.0], READING_IMPORT),
        readings("2026-01-12T07:15:00+01:00", [9.0], READING_EXPORT),
        {"readingType": READING_IMPORT, "intervalReadings": [{"timestamp": "2026-01-12T08:15:00+01:00", "value": "n/a"}]},
    ]
    assert [item.kwh for item in read_intervals(blocks, READING_IMPORT)] == [1.0, 2.0]


def test_the_peak_is_the_highest_interval_of_the_block_and_when_it_was():
    # Monday 12 January 2026 (higher season, workday): 07:00-14:00 is block 1.
    block = readings("2026-01-12T07:15:00+01:00", [0.5, 1.5, 1.0])
    data = build_month(date(2026, 1, 1), [block], READING_IMPORT, READING_EXPORT)
    figures = block_figures(data, {b: 3.0 for b in BLOCKS})
    assert figures[1].peak == 6.0
    assert figures[1].peak_at == datetime.fromisoformat("2026-01-12T07:15:00+01:00")
    assert figures[1].energy == 3.0
    assert figures[2].peak is None and figures[2].energy == 0.0


def test_headroom_goes_negative_when_the_peak_passes_the_agreed_power():
    block = readings("2026-01-12T07:15:00+01:00", [1.5])  # 6 kW
    data = build_month(date(2026, 1, 1), [block], READING_IMPORT, READING_EXPORT)
    figures = block_figures(data, {1: 4.6, 2: 3.0, 3: 3.0, 4: 3.0, 5: 3.0})
    assert figures[1].headroom == pytest.approx(-1.4)
    assert figures[2].headroom is None


def test_the_intervals_over_the_agreed_power_give_the_excess():
    # kWh 1.35, 1.225, 1.525 are 5.4, 4.9 and 6.1 kW in block 1.
    block = readings("2026-01-12T07:15:00+01:00", [1.35, 1.225, 1.525, 1.0])
    data = build_month(date(2026, 1, 1), [block], READING_IMPORT, READING_EXPORT)
    figure = block_figures(data, {1: 4.6, 2: 3.0, 3: 3.0, 4: 3.0, 5: 3.0})[1]
    assert len(figure.over) == 3
    assert figure.excess == pytest.approx(math.sqrt(2.98))


def test_an_unknown_agreed_power_gives_no_excess_but_still_a_peak():
    block = readings("2026-01-12T07:15:00+01:00", [1.0])
    data = build_month(date(2026, 1, 1), [block], READING_IMPORT, READING_EXPORT)
    figure = block_figures(data, {})[1]
    assert figure.peak == 4.0 and figure.excess is None and figure.headroom is None


def test_the_month_knows_where_its_data_ends_and_the_newest_import_and_export():
    blocks = [
        readings("2026-01-12T00:15:00+01:00", [1.0, 2.0], READING_IMPORT),
        readings("2026-01-12T00:15:00+01:00", [0.25, 0.5], READING_EXPORT),
    ]
    data = build_month(date(2026, 1, 1), blocks, READING_IMPORT, READING_EXPORT)
    assert data.through == datetime.fromisoformat("2026-01-12T00:30:00+01:00")
    assert data.last_import.kw == 8.0 and data.last_export.kw == 2.0


def test_an_empty_month_has_no_end_and_no_peaks():
    data = MonthData(date(2026, 2, 1))
    assert data.through is None
    assert all(f.peak is None for f in block_figures(data, {}).values())
