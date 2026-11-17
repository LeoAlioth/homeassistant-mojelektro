"""Tests for the network-charge prices and the monthly fee."""

from datetime import date
import math

import pytest

from custom_components.mojelektro.billing import BLOCKS, BlockFigure, blocks_in_month
from custom_components.mojelektro.prices import (
    USER_GROUPS,
    excess_weight,
    excess_charge,
    power_charge,
    prices_for,
)

AGREED = {1: 3.4, 2: 3.4, 3: 3.4, 4: 10.0, 5: 12.0}


def figures(agreed, energy=None, over=None):
    energy = energy or {}
    over = over or {}
    return {
        block: BlockFigure(
            block=block,
            agreed=agreed.get(block),
            peak=None,
            peak_at=None,
            over=tuple(over.get(block, ())),
            energy=energy.get(block, 0.0),
        )
        for block in BLOCKS
    }


def test_household_prices_in_the_lower_season_of_2026():
    prices = prices_for(0, date(2026, 7, 15))
    assert prices.power[1] == 3.82301 and prices.energy[1] == 0.02217
    assert prices.power[5] == 0.00245 and prices.energy[5] == 0.01299
    assert not prices.estimated


def test_block_one_power_is_cheaper_in_the_winter_months_of_the_phase_in():
    assert prices_for(0, date(2026, 10, 31)).power[1] == 3.82301
    assert prices_for(0, date(2026, 11, 1)).power[1] == 2.67611
    assert prices_for(0, date(2026, 12, 31)).power[1] == 2.67611
    # Only block 1 is reduced.
    assert prices_for(0, date(2026, 11, 1)).power[2] == 1.09230


def test_the_start_of_2027_uses_the_2026_prices_and_says_so():
    prices = prices_for(0, date(2027, 1, 15))
    assert prices.estimated
    assert prices.power[1] == 2.67611


def test_months_without_a_table_have_no_prices():
    assert prices_for(0, date(2026, 2, 28)) is None
    assert prices_for(0, date(2027, 3, 1)) is None
    assert prices_for(9, date(2026, 7, 1)) is None


def test_every_user_group_has_five_blocks():
    for group in USER_GROUPS:
        prices = prices_for(group, date(2026, 7, 1))
        assert sorted(prices.power) == [1, 2, 3, 4, 5] == sorted(prices.energy)


def test_the_weighting_factor_by_year():
    assert [excess_weight(y) for y in (2025, 2026, 2027, 2028, 2030)] == [0.9, 1.05, 1.05, 1.2, 1.2]


ALL = frozenset(BLOCKS)


def test_the_power_charge_is_the_agreed_power_times_the_monthly_price():
    charge = power_charge(prices_for(0, date(2026, 7, 15)), figures(AGREED), ALL)
    assert charge.total == pytest.approx(17.967722) and charge.basis == "agreed"


def test_the_phase_in_month_lowers_only_the_block_one_power_price():
    charge = power_charge(prices_for(0, date(2026, 11, 15)), figures(AGREED), ALL)
    assert charge.total == pytest.approx(17.967722 - 3.4 * (3.82301 - 2.67611))


def test_only_the_blocks_of_the_month_are_charged():
    july = blocks_in_month(date(2026, 7, 1))
    charge = power_charge(prices_for(0, date(2026, 7, 15)), figures(AGREED), july)
    assert 1 not in charge.per_block
    assert charge.total == pytest.approx(17.967722 - 3.4 * 3.82301)


def test_no_agreed_power_bills_the_reached_peak():
    peaks = {
        block: BlockFigure(block, None, 2.0, None, (), 0.0) for block in BLOCKS
    }
    charge = power_charge(prices_for(0, date(2026, 7, 15)), peaks, ALL)
    assert charge.basis == "reached"
    assert charge.total == pytest.approx(2.0 * sum(prices_for(0, date(2026, 7, 15)).power.values()))


def test_the_excess_charge_is_weight_times_price_times_excess_power():
    agreed = {**AGREED, 1: 4.6}
    charge = excess_charge(
        prices_for(0, date(2026, 7, 15)), 1.05, figures(agreed, over={1: (5.4, 4.9, 6.1)}), ALL
    )
    assert charge == pytest.approx(1.05 * 3.82301 * math.sqrt(2.98))


def test_no_excess_charge_without_agreed_power():
    assert excess_charge(prices_for(0, date(2026, 7, 15)), 1.05, figures({1: 3.4}), ALL) is None
