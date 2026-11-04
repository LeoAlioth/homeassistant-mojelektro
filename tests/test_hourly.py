"""Tests for turning 15-minute readings into hourly statistics rows."""

from datetime import date, datetime, timedelta, timezone

from custom_components.mojelektro.hourly import (
    READING_EXPORT,
    READING_IMPORT,
    compile_wait,
    cumulative,
    date_chunks,
    flat_hours,
    hourly_kwh,
    last_compiled_period,
)


def quarter_hours(first_end: str, values: list[float], reading_type=READING_IMPORT):
    """One API block; each reading is stamped with the end of its quarter-hour."""
    first = datetime.fromisoformat(first_end)
    return {
        "readingType": reading_type,
        "intervalReadings": [
            {"timestamp": (first + timedelta(minutes=15 * i)).isoformat(), "value": str(v)}
            for i, v in enumerate(values)
        ],
    }


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def test_a_reading_belongs_to_the_hour_it_started_in():
    # 00:15 .. 01:00 (CET) is the hour 00:00-01:00 local = 23:00 UTC the day before.
    block = quarter_hours("2026-01-15T00:15:00+01:00", [0.1] * 4 + [0.2] * 4)
    assert hourly_kwh([block], READING_IMPORT) == {
        utc(2026, 1, 14, 23): 0.4,
        utc(2026, 1, 15, 0): 0.8,
    }


def test_only_the_asked_reading_type_is_summed():
    blocks = [
        quarter_hours("2026-01-15T00:15:00+01:00", [1.0] * 4, READING_IMPORT),
        quarter_hours("2026-01-15T00:15:00+01:00", [0.5] * 4, READING_EXPORT),
    ]
    assert hourly_kwh(blocks, READING_EXPORT) == {utc(2026, 1, 14, 23): 2.0}


def test_hours_after_the_last_complete_one_wait_for_more_data():
    block = quarter_hours("2026-01-15T00:15:00+01:00", [1.0] * 4 + [1.0] * 4 + [1.0, 1.0])
    assert list(hourly_kwh([block], READING_IMPORT)) == [utc(2026, 1, 14, 23), utc(2026, 1, 15, 0)]


def test_a_gap_inside_the_data_is_kept_with_what_it_has():
    values = [1.0] * 4 + [1.0] * 3 + [1.0] * 4
    block = quarter_hours("2026-01-15T00:15:00+01:00", values)
    hours = hourly_kwh([block], READING_IMPORT)
    assert hours[utc(2026, 1, 15, 0)] == 3.0 and len(hours) == 3


def test_no_complete_hour_gives_nothing():
    block = quarter_hours("2026-01-15T00:15:00+01:00", [1.0, 1.0])
    assert hourly_kwh([block], READING_IMPORT) == {}


def test_the_day_the_clocks_go_back_has_25_hours():
    # 2026-10-25: 25 hours = 100 quarter-hours, 00:15 CEST through 00:00 on the 26th.
    block = quarter_hours("2026-10-25T00:15:00+02:00", [0.25] * 100)
    hours = hourly_kwh([block], READING_IMPORT)
    assert len(hours) == 25 and set(hours.values()) == {1.0}


def test_running_total_continues_from_the_stored_one():
    hours = {utc(2026, 1, 1, 0): 1.5, utc(2026, 1, 1, 1): 0.5}
    assert cumulative(hours, 100.0) == [(utc(2026, 1, 1, 0), 101.5), (utc(2026, 1, 1, 1), 102.0)]


def test_ranges_never_exceed_what_the_api_accepts():
    chunks = date_chunks(date(2023, 1, 1), date(2026, 11, 2))
    assert chunks[0][0] == date(2023, 1, 1) and chunks[-1][1] == date(2026, 11, 2)
    assert all((stop - start).days <= 35 for start, stop in chunks)
    assert all(a[1] == b[0] for a, b in zip(chunks, chunks[1:]))
    assert date_chunks(date(2026, 11, 2), date(2026, 11, 2)) == []


def test_flat_hours_stop_before_the_hour_home_assistant_still_compiles():
    hours = flat_hours(utc(2026, 11, 4, 23), utc(2026, 11, 5, 5, 17))
    assert hours[0] == utc(2026, 11, 4, 23) and hours[-1] == utc(2026, 11, 5, 4)
    assert len(hours) == 6
    assert flat_hours(utc(2026, 11, 5, 5), utc(2026, 11, 5, 5, 59)) == []


def test_writes_wait_until_the_period_that_just_ended_is_compiled():
    assert compile_wait(utc(2026, 11, 5, 5, 15, 3)) == 12.0
    assert compile_wait(utc(2026, 11, 5, 5, 15, 20)) == 0.0
    assert compile_wait(utc(2026, 11, 5, 5, 17)) == 0.0


def test_the_newest_compiled_period_is_the_one_before_the_current():
    assert last_compiled_period(utc(2026, 11, 5, 5, 17, 30)) == utc(2026, 11, 5, 5, 10)
    assert last_compiled_period(utc(2026, 11, 5, 6, 0, 20)) == utc(2026, 11, 5, 5, 55)
