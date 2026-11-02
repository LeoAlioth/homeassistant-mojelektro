"""Import Moj Elektro hourly import/export into Home Assistant's statistics.

The hours are written as the long-term statistics of the two total sensors
(``Total import`` / ``Total export``). The sensors have no state class, so Home
Assistant never compiles statistics of its own for them and these stay on the
hours the energy was used. Moj Elektro publishes a day late, so this runs once a
day and once at setup, from the last stored hour (or from the oldest date the
API still serves, the first time).
"""

import asyncio
from datetime import date, datetime, timedelta, timezone
import logging

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter

from .const import DOMAIN
from .hourly import (
    READING_EXPORT,
    READING_IMPORT,
    cumulative,
    date_chunks,
    hourly_kwh,
)
from .moj_elektro_api import MojElektroApi, MojElektroRequestError

_LOGGER = logging.getLogger(__name__)

# The API refuses dates more than about two years back (measured: it serves
# from December 2023 in November 2026), so the first import starts from before
# that and skips the refused chunks.
BACKFILL_YEARS = 3
PAUSE_BETWEEN_REQUESTS = 0.5

KINDS = (("import", READING_IMPORT), ("export", READING_EXPORT))


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


async def async_stored_total(
    hass: HomeAssistant, stat_id: str
) -> tuple[datetime, float] | None:
    """Start of the newest stored hour and its running total, None before any import."""
    last = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, stat_id, False, {"sum"}
    )
    rows = last.get(stat_id)
    if not rows:
        return None
    return dt_util.utc_from_timestamp(rows[0]["start"]), float(rows[0]["sum"])


async def async_import_history(
    hass: HomeAssistant, api: MojElektroApi, start: date | None = None
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
    stored = await async_stored_total(hass, stat_ids["import"])
    last = stored[0] if stored else None
    if start is None:
        if last is not None:
            start = dt_util.as_local(last).date()
        else:
            start = date(end.year - BACKFILL_YEARS, 1, 1)
    first_hour = dt_util.start_of_local_day(start).astimezone(timezone.utc)

    imported: dict[str, dict[datetime, float]] = {kind: {} for kind, _ in KINDS}
    seen_data = False
    for chunk_start, chunk_end in date_chunks(start, end):
        try:
            blocks = await api.get_energy_intervals(chunk_start, chunk_end)
        except MojElektroRequestError:
            if seen_data or last is not None:
                raise
            # Before the first chunk with data a refusal means "too old".
            _LOGGER.debug("Moj Elektro serves nothing for %s yet", chunk_start)
            continue
        for kind, reading_type in KINDS:
            rows = hourly_kwh(blocks, reading_type)
            seen_data = seen_data or bool(rows)
            imported[kind].update(rows)
        await asyncio.sleep(PAUSE_BETWEEN_REQUESTS)

    written = 0
    for kind, _ in KINDS:
        hours = {h: v for h, v in imported[kind].items() if h >= first_hour}
        if not hours:
            continue
        stat_id = stat_ids[kind]
        base = await _sum_before(hass, stat_id, min(hours))
        rows = [
            StatisticData(start=hour, state=round(total, 6), sum=round(total, 6))
            for hour, total in cumulative(hours, base)
        ]
        async_import_statistics(
            hass,
            StatisticMetaData(
                mean_type=StatisticMeanType.NONE,
                has_sum=True,
                name=None,
                source="recorder",
                statistic_id=stat_id,
                unit_class=EnergyConverter.UNIT_CLASS,
                unit_of_measurement="kWh",
            ),
            rows,
        )
        written += len(rows)

    _LOGGER.info("Moj Elektro %s: imported %s hourly rows", api.meter_id, written)
    return written
