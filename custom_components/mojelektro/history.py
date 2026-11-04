"""Import Moj Elektro hourly import/export into Home Assistant's statistics.

The hours are written as the long-term statistics of the two total sensors
(``Total import`` / ``Total export``). Moj Elektro publishes a day late, so this
runs once a day and once at setup, from where the last import stopped (or from
the oldest date the API still serves, the first time).

The sensors have the ``total_increasing`` state class, so Home Assistant keeps
compiling their statistics too: every 5 minutes it adds the change of the state
since its newest 5-minute row. A day's data arriving at once would be booked
into the hour it arrived in, so before the state moves to the new total the
import also writes:

- the hours from the end of the data up to the current hour, flat at the new
  total (Home Assistant compiled them at the old one), and
- the newest 5-minute row with the new total as state and sum, so Home
  Assistant continues from it and sees no change when the state moves.
"""

import asyncio
from datetime import date, datetime, timedelta, timezone
import logging

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.db_schema import (
    Statistics,
    StatisticsShortTerm,
)
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter

from .const import DOMAIN
from .hourly import (
    READING_EXPORT,
    READING_IMPORT,
    compile_wait,
    cumulative,
    date_chunks,
    flat_hours,
    hourly_kwh,
    last_compiled_period,
)
from .moj_elektro_api import MojElektroApi, MojElektroRequestError

_LOGGER = logging.getLogger(__name__)

# The API refuses dates more than about two years back (measured: it serves
# from December 2023 in November 2026), so the first import starts from before
# that and skips the refused chunks.
BACKFILL_YEARS = 3
PAUSE_BETWEEN_REQUESTS = 0.5
# Without a stored import end (installs from 0.3.4) the last week is fetched again.
REFETCH_DAYS = 7

KINDS = (("import", READING_IMPORT), ("export", READING_EXPORT))
STORE_VERSION = 1


def history_signal(entry_id: str) -> str:
    """Dispatcher signal sent after an import, so the total sensors re-read."""
    return f"{DOMAIN}_history_{entry_id}"


def total_unique_id(meter_id: str, kind: str) -> str:
    """Unique id of a meter's total sensor; ``kind`` is import or export."""
    return f"{meter_id}-total_{kind}"


def statistic_id(hass: HomeAssistant, meter_id: str, kind: str) -> str | None:
    """Entity id of the total sensor, which is also its statistic id."""
    return er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, total_unique_id(meter_id, kind)
    )


def _store(hass: HomeAssistant, entry_id: str) -> Store:
    return Store(hass, STORE_VERSION, f"{DOMAIN}.{entry_id}")


async def async_imported_totals(
    hass: HomeAssistant, entry_id: str
) -> dict[str, tuple[datetime, float]]:
    """Per kind, the end of the imported data and the running total there."""
    stored = await _store(hass, entry_id).async_load() or {}
    return {
        kind: (dt_util.parse_datetime(row["through"]), row["total"])
        for kind, row in stored.items()
    }


async def _sum_before(hass: HomeAssistant, stat_id: str, first_hour: datetime) -> float:
    """The stored running total of the last hour before ``first_hour`` (0 if none)."""
    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        first_hour - timedelta(days=3),
        first_hour,
        {stat_id},
        "hour",
        None,
        {"sum"},
    )
    stored = rows.get(stat_id)
    return float(stored[-1]["sum"]) if stored else 0.0


async def _last_stored_hour(hass: HomeAssistant, stat_id: str) -> datetime | None:
    last = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, stat_id, False, {"sum"}
    )
    rows = last.get(stat_id)
    return dt_util.utc_from_timestamp(rows[0]["start"]) if rows else None


def _metadata(stat_id: str) -> StatisticMetaData:
    return StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=None,
        source="recorder",
        statistic_id=stat_id,
        unit_class=EnergyConverter.UNIT_CLASS,
        unit_of_measurement="kWh",
    )


async def async_import_history(
    hass: HomeAssistant, entry_id: str, api: MojElektroApi, start: date | None = None
) -> int:
    """Import from ``start`` (default: where the last run stopped) up to today.

    Returns the number of hours written. Re-running a range replaces its rows,
    and the running totals continue from the hour before the range.
    """
    stat_ids = {kind: statistic_id(hass, api.meter_id, kind) for kind, _ in KINDS}
    if not all(stat_ids.values()):
        _LOGGER.warning("Moj Elektro total sensors are not registered yet")
        return 0
    end = dt_util.now().date()
    backfill_start = date(end.year - BACKFILL_YEARS, 1, 1)
    totals = await async_imported_totals(hass, entry_id)
    if start is None:
        if "import" in totals:
            # The hour before the end, so the last imported day is fetched again.
            start = dt_util.as_local(totals["import"][0] - timedelta(hours=1)).date()
        elif last := await _last_stored_hour(hass, stat_ids["import"]):
            start = dt_util.as_local(last).date() - timedelta(days=REFETCH_DAYS)
        else:
            start = backfill_start
    first_hour = dt_util.start_of_local_day(start).astimezone(timezone.utc)
    resuming = start > backfill_start

    imported: dict[str, dict[datetime, float]] = {kind: {} for kind, _ in KINDS}
    seen_data = False
    for chunk_start, chunk_end in date_chunks(start, end):
        try:
            blocks = await api.get_energy_intervals(chunk_start, chunk_end)
        except MojElektroRequestError:
            if seen_data or resuming:
                raise
            # Before the first chunk with data a refusal means "too old".
            _LOGGER.debug("Moj Elektro serves nothing for %s yet", chunk_start)
            continue
        for kind, reading_type in KINDS:
            rows = hourly_kwh(blocks, reading_type)
            seen_data = seen_data or bool(rows)
            imported[kind].update(rows)
        await asyncio.sleep(PAUSE_BETWEEN_REQUESTS)

    data: dict[str, list[StatisticData]] = {}
    for kind, _ in KINDS:
        hours = {h: v for h, v in imported[kind].items() if h >= first_hour}
        if not hours:
            continue
        base = await _sum_before(hass, stat_ids[kind], min(hours))
        rows = cumulative(hours, base)
        data[kind] = [
            StatisticData(start=hour, state=round(total, 6), sum=round(total, 6))
            for hour, total in rows
        ]
        totals[kind] = (rows[-1][0] + timedelta(hours=1), round(rows[-1][1], 6))

    # Write between Home Assistant's 5-minute runs, with no await in between,
    # so its runs see either none or all of it.
    if wait := compile_wait(dt_util.utcnow()):
        await asyncio.sleep(wait)
    now = dt_util.utcnow()
    recorder = get_instance(hass)
    written = 0
    for kind, rows in data.items():
        through, total = totals[kind]
        flat = [StatisticData(start=h, state=total, sum=total) for h in flat_hours(through, now)]
        meta = _metadata(stat_ids[kind])
        recorder.async_import_statistics(meta, rows + flat, Statistics)
        recorder.async_import_statistics(
            meta,
            [StatisticData(start=last_compiled_period(now), state=total, sum=total)],
            StatisticsShortTerm,
        )
        written += len(rows)

    if data:
        await _store(hass, entry_id).async_save(
            {kind: {"through": t.isoformat(), "total": v} for kind, (t, v) in totals.items()}
        )
    _LOGGER.info("Moj Elektro %s: imported %s hourly rows", api.meter_id, written)
    return written
