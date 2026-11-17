"""The running month's 15-minute readings, for the network-charge figures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .billing import BLOCKS, BlockFigure, MonthData, block_figures, build_month
from .hourly import READING_EXPORT, READING_IMPORT
from .moj_elektro_api import MojElektroApi, MojElektroAuthError, MojElektroError

_LOGGER = logging.getLogger(__name__)

# The data is a day late and moves once a day; three hours keeps the API calls few.
MONTH_REFRESH = timedelta(hours=3)


class MonthCoordinator(DataUpdateCoordinator[MonthData]):
    """Fetches the readings from the first of the month up to yesterday."""

    def __init__(self, hass, entry, api: MojElektroApi) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="mojelektro_month",
            update_interval=MONTH_REFRESH,
        )
        self._api = api
        # So the entities have a month to read even when the first fetch fails.
        self.data = MonthData(dt_util.now().date().replace(day=1))

    async def _async_update_data(self) -> MonthData:
        today = dt_util.now().date()
        first = today.replace(day=1)
        if today == first:
            return MonthData(first)  # the new month has no finished day yet
        try:
            blocks = await self._api.get_energy_intervals(first, today)
        except MojElektroAuthError as err:
            raise ConfigEntryAuthFailed("Moj Elektro authentication failed") from err
        except MojElektroError as err:
            raise UpdateFailed(str(err)) from err
        return build_month(first, blocks, READING_IMPORT, READING_EXPORT)


@dataclass
class MojElektroRuntime:
    """What a loaded entry shares between its platforms."""

    api: MojElektroApi
    coordinator: DataUpdateCoordinator
    month: MonthCoordinator
    user_group: int

    def agreed(self) -> dict[int, float | None]:
        """Agreed power per block from the meter point's contract, in kW."""
        values = {}
        for block in BLOCKS:
            try:
                values[block] = float((self.coordinator.data or {}).get(f"casovni_blok_{block}"))
            except (TypeError, ValueError):
                values[block] = None
        return values

    def figures(self) -> dict[int, BlockFigure]:
        """The month's figures per block against the agreed powers."""
        return block_figures(self.month.data, self.agreed())
