"""The month's power figures per tariff block, from 15-minute readings.

Pure functions, no Home Assistant imports, so they can be tested on their own.

The rules are those of the Slovenian network-charge methodology (Akt o metodologiji
za obračunavanje omrežnine za elektrooperaterje, 12. člen, as amended 29 September
2025): the power of a 15-minute interval is its taken energy divided by 0.25 h, and
the reached power of a time block is the highest such value in the billing month.
Intervals above the agreed power give the excess power
``Cex = sqrt(sum((P - Cc) ** 2))`` over those intervals (the act's equation, with a
worked example from the Energy Agency's calculation guide).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import math
from zoneinfo import ZoneInfo

from .tariff import block_at

LJ = ZoneInfo("Europe/Ljubljana")
BLOCKS = (1, 2, 3, 4, 5)
QUARTER = timedelta(minutes=15)
HOURS_PER_QUARTER = 0.25


@dataclass(frozen=True)
class Interval:
    """One 15-minute reading: its start (Slovenian local time) and taken energy."""

    start: datetime
    kwh: float

    @property
    def kw(self) -> float:
        """Average power over the interval."""
        return self.kwh / HOURS_PER_QUARTER

    @property
    def block(self) -> int:
        """Tariff block of the interval, by the hour it starts in."""
        return block_at(self.start)


@dataclass(frozen=True)
class MonthData:
    """What the month's 15-minute readings hold so far."""

    month_first: date
    imports: tuple[Interval, ...] = ()
    last_import: Interval | None = None
    last_export: Interval | None = None

    @property
    def through(self) -> datetime | None:
        """End of the newest import interval, when the data stops."""
        return self.imports[-1].start + QUARTER if self.imports else None


@dataclass(frozen=True)
class BlockFigure:
    """One block's month so far, against its agreed power."""

    block: int
    agreed: float | None
    peak: float | None
    peak_at: datetime | None
    over: tuple[float, ...]
    energy: float

    @property
    def headroom(self) -> float | None:
        """Agreed power minus the peak; negative once the peak went over."""
        if self.agreed is None or self.peak is None:
            return None
        return self.agreed - self.peak

    @property
    def excess(self) -> float | None:
        """Excess power in kW (0 when no interval went over)."""
        if self.agreed is None:
            return None
        return excess_power(self.over, self.agreed)


def read_intervals(blocks: list[dict], reading_type: str) -> list[Interval]:
    """One reading type's quarter-hours, oldest first.

    A reading is stamped with the end of its quarter-hour, so the start is
    ``timestamp - 15 min``. A repeated start keeps its last value; a reading
    with no usable value is skipped.
    """
    by_start: dict[datetime, float] = {}
    for block in blocks:
        if block.get("readingType") != reading_type:
            continue
        for reading in block.get("intervalReadings", []):
            try:
                end = datetime.fromisoformat(reading["timestamp"].replace("Z", "+00:00"))
                by_start[(end - QUARTER).astimezone(LJ)] = float(reading["value"])
            except (KeyError, TypeError, ValueError):
                continue
    return [Interval(start, kwh) for start, kwh in sorted(by_start.items())]


def build_month(month_first: date, blocks: list[dict], import_type: str, export_type: str) -> MonthData:
    """The month's import intervals and the newest import and export interval."""
    imports = read_intervals(blocks, import_type)
    exports = read_intervals(blocks, export_type)
    return MonthData(
        month_first=month_first,
        imports=tuple(imports),
        last_import=imports[-1] if imports else None,
        last_export=exports[-1] if exports else None,
    )


def excess_power(over: tuple[float, ...] | list[float], agreed: float) -> float:
    """Presežna obračunska moč: the root of the summed squared overruns."""
    return math.sqrt(sum((value - agreed) ** 2 for value in over if value > agreed))


def block_figures(
    data: MonthData, agreed: dict[int, float | None]
) -> dict[int, BlockFigure]:
    """Per block: peak, intervals over the agreed power, and the month's energy."""
    by_block: dict[int, list[Interval]] = {block: [] for block in BLOCKS}
    for interval in data.imports:
        by_block[interval.block].append(interval)

    figures = {}
    for block in BLOCKS:
        intervals = by_block[block]
        limit = agreed.get(block)
        peak = max(intervals, key=lambda item: item.kw, default=None)
        over = (
            tuple(item.kw for item in intervals if item.kw > limit)
            if limit is not None
            else ()
        )
        figures[block] = BlockFigure(
            block=block,
            agreed=limit,
            peak=peak.kw if peak else None,
            peak_at=peak.start if peak else None,
            over=over,
            energy=sum(item.kwh for item in intervals),
        )
    return figures
