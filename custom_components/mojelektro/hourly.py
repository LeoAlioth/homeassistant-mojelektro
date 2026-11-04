"""Turn Moj Elektro 15-minute readings into hourly energy rows.

Pure functions, no Home Assistant imports, so they can be tested on their own.
"""

from datetime import date, datetime, timedelta, timezone

READING_IMPORT = "32.0.2.4.1.2.12.0.0.0.0.0.0.0.0.3.72.0"
READING_EXPORT = "32.0.2.4.19.2.12.0.0.0.0.0.0.0.0.3.72.0"

# The API accepts at most 35 days between startTime and endTime.
MAX_REQUEST_DAYS = 35
READINGS_PER_HOUR = 4


def date_chunks(start: date, end: date) -> list[tuple[date, date]]:
    """Split [start, end) into ranges the API accepts, oldest first."""
    chunks = []
    while start < end:
        stop = min(start + timedelta(days=MAX_REQUEST_DAYS), end)
        chunks.append((start, stop))
        start = stop
    return chunks


def hourly_kwh(blocks: list[dict], reading_type: str) -> dict[datetime, float]:
    """Sum one reading type's quarter-hours into hours, keyed by UTC hour start.

    A reading is stamped with the end of its quarter-hour (the day's first one
    is 00:15), so it belongs to the hour of ``timestamp - 15 min``. Hours after
    the last complete one are dropped, because the rest of that hour has not
    been published yet; an incomplete hour before a complete one is kept with
    what it has, since nothing more will arrive for it.
    """
    sums: dict[datetime, float] = {}
    counts: dict[datetime, int] = {}
    for block in blocks:
        if block.get("readingType") != reading_type:
            continue
        for reading in block.get("intervalReadings", []):
            slot = datetime.fromisoformat(reading["timestamp"]) - timedelta(minutes=15)
            hour = slot.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
            sums[hour] = sums.get(hour, 0.0) + float(reading["value"])
            counts[hour] = counts.get(hour, 0) + 1

    complete = [hour for hour, count in counts.items() if count >= READINGS_PER_HOUR]
    if not complete:
        return {}
    last = max(complete)
    return {hour: value for hour, value in sorted(sums.items()) if hour <= last}


def cumulative(hours: dict[datetime, float], base_sum: float) -> list[tuple[datetime, float]]:
    """Running total of ``hours`` (oldest first) starting from ``base_sum``."""
    rows = []
    total = base_sum
    for hour, value in sorted(hours.items()):
        total += value
        rows.append((hour, total))
    return rows


# Home Assistant compiles the 5-minute period [t - 5 min, t) at t + 10 s.
COMPILE_PERIOD = timedelta(minutes=5)
COMPILE_DONE = timedelta(seconds=15)


def flat_hours(through: datetime, now: datetime) -> list[datetime]:
    """Hour starts from ``through`` up to, not including, the hour ``now`` is in."""
    current = now.replace(minute=0, second=0, microsecond=0)
    hours = []
    hour = through
    while hour < current:
        hours.append(hour)
        hour += timedelta(hours=1)
    return hours


def compile_wait(now: datetime) -> float:
    """Seconds until Home Assistant has compiled the 5-minute period that just ended."""
    elapsed = timedelta(minutes=now.minute % 5, seconds=now.second, microseconds=now.microsecond)
    return max(0.0, (COMPILE_DONE - elapsed).total_seconds())


def last_compiled_period(now: datetime) -> datetime:
    """Start of the newest 5-minute period compiled by ``now`` (after ``compile_wait``)."""
    end = now.replace(minute=now.minute - now.minute % 5, second=0, microsecond=0)
    return end - COMPILE_PERIOD
