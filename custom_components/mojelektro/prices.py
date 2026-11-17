"""Slovenian network-charge prices and the monthly fee they give.

Pure functions, no Home Assistant imports, so they can be tested on their own.

The prices are the Energy Agency's "Tarifne postavke omrežnine za prenosni in
distribucijski sistem" (transmission and distribution together, without taxes and
levies), published in Uradni list RS 105/25 and as a table on agen-rs.si. There is
no machine-readable source, so a new tariff year is a new row of ``PERIODS``.

The phase-in of block 1's power price (50 %, 70 % and 90 % of the target in the
winter months of 2025-2027, 56.b člen) is already in the published price of the
months it applies to, so it is not modelled: ``reduced_months`` names those months.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import NamedTuple

from .billing import BLOCKS, BlockFigure

# Per user group and block: (power EUR/kW/month, block 1's reduced power price in the
# higher season or None, energy EUR/kWh). Group 0: low voltage, outlet 400/230 V
# (households); 1: low voltage at a transformer-station busbar; 2: medium voltage
# outlet; 3: medium voltage at a substation busbar; 4: high voltage.
TABLE_2026: dict[int, dict[int, tuple[float, float | None, float]]] = {
    0: {
        1: (3.82301, 2.67611, 0.02217),
        2: (1.09230, None, 0.01998),
        3: (0.28902, None, 0.01717),
        4: (0.02436, None, 0.01805),
        5: (0.00245, None, 0.01299),
    },
    1: {
        1: (4.90409, 3.43287, 0.01432),
        2: (1.29280, None, 0.01550),
        3: (0.28782, None, 0.01270),
        4: (0.01070, None, 0.01275),
        5: (0.00175, None, 0.00906),
    },
    2: {
        1: (3.93968, 2.75777, 0.01239),
        2: (1.20210, None, 0.01352),
        3: (0.25096, None, 0.01096),
        4: (0.00447, None, 0.01152),
        5: (0.00236, None, 0.00758),
    },
    3: {
        1: (1.81419, 1.26994, 0.00744),
        2: (0.55075, None, 0.00822),
        3: (0.13672, None, 0.00714),
        4: (0.00368, None, 0.00756),
        5: (0.00165, None, 0.00591),
    },
    4: {
        1: (0.36702, 0.27693, 0.00658),
        2: (0.27031, None, 0.00754),
        3: (0.09791, None, 0.00604),
        4: (0.00384, None, 0.00777),
        5: (0.00093, None, 0.00558),
    },
}
USER_GROUPS = tuple(TABLE_2026)


class Period(NamedTuple):
    """One published set of prices and the months it is used for."""

    first: date
    last: date
    table: dict[int, dict[int, tuple[float, float | None, float]]]
    reduced_months: frozenset[tuple[int, int]]
    estimated: bool
    source: str


PERIODS = (
    Period(
        date(2026, 3, 1),
        date(2026, 12, 31),
        TABLE_2026,
        frozenset({(2026, 11), (2026, 12)}),
        False,
        "AGEN-RS, Tarifne postavke omrežnine 1. 3. - 31. 12. 2026",
    ),
    # The 2027 prices are published by the end of November 2026. Until then the 2026
    # prices stand in, with the same 70 % phase-in the act sets for January and February.
    Period(
        date(2027, 1, 1),
        date(2027, 2, 28),
        TABLE_2026,
        frozenset({(2027, 1), (2027, 2)}),
        True,
        "2026 prices, the 2027 table is not published yet",
    ),
)


@dataclass(frozen=True)
class Prices:
    """The prices in force for one user group in one month."""

    power: dict[int, float]
    energy: dict[int, float]
    estimated: bool
    source: str


def prices_for(group: int, day: date) -> Prices | None:
    """The prices for a user group on a day, or None when no table covers it."""
    for period in PERIODS:
        if period.first <= day <= period.last:
            rows = period.table.get(group)
            if rows is None:
                return None
            reduced = (day.year, day.month) in period.reduced_months
            return Prices(
                power={
                    block: (row[1] if reduced and row[1] is not None else row[0])
                    for block, row in rows.items()
                },
                energy={block: row[2] for block, row in rows.items()},
                estimated=period.estimated,
                source=period.source,
            )
    return None


def excess_weight(year: int) -> float:
    """Faktor utežitve presežne moči (Annex 2, chapter 5), by calendar year."""
    if year <= 2025:
        return 0.9
    if year <= 2027:
        return 1.05
    return 1.2


@dataclass(frozen=True)
class FeeParts:
    """A month's network fee in EUR, without VAT."""

    power: float
    energy: float
    excess: float

    @property
    def total(self) -> float:
        """Power and energy; the excess is billed only if the agreed power was changed."""
        return self.power + self.energy


def network_fee(
    prices: Prices, weight: float, figures: dict[int, BlockFigure]
) -> FeeParts | None:
    """The month's fee, or None while any block's agreed power is unknown.

    Power: agreed power times the block's monthly price. Energy: the month's taken
    kWh times the block's energy price. Excess: the weighting factor times the block's
    power price times its excess power.
    """
    if any(figures[block].agreed is None for block in BLOCKS):
        return None
    power = sum(figures[block].agreed * prices.power[block] for block in BLOCKS)
    energy = sum(figures[block].energy * prices.energy[block] for block in BLOCKS)
    excess = sum(
        weight * prices.power[block] * (figures[block].excess or 0.0) for block in BLOCKS
    )
    return FeeParts(power=power, energy=energy, excess=excess)
