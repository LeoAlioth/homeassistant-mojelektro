"""Sensor platform for the Moj Elektro integration."""

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .billing import BLOCKS, QUARTER
from .const import CONF_METER_ID, DOMAIN, VERSION
from .history import async_imported_totals, history_signal, total_unique_id
from .prices import excess_weight, network_fee, prices_for
from .tariff import block_at

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up Moj Elektro sensors from a config entry."""
    meter_id = entry.data[CONF_METER_ID]
    runtime = entry.runtime_data
    coordinator = runtime.coordinator

    sensors = [
        MojElektroSensor(coordinator, measurement, meter_id)
        for measurement in coordinator.data
    ]
    sensors += [
        MojElektroTotalSensor(entry.entry_id, meter_id, kind)
        for kind in ("import", "export")
    ]
    sensors += [
        MojElektroCurrentBlockSensor(coordinator, meter_id),
        MojElektroCurrentBlockPowerSensor(coordinator, meter_id),
    ]
    for block in BLOCKS:
        sensors += [
            MonthlyPeakSensor(runtime, meter_id, block),
            PeakHeadroomSensor(runtime, meter_id, block),
            ExcessPowerSensor(runtime, meter_id, block),
        ]
    sensors += [
        LastIntervalPowerSensor(runtime, meter_id, "import"),
        LastIntervalPowerSensor(runtime, meter_id, "export"),
        NetworkFeeSensor(runtime, meter_id),
    ]
    async_add_entities(sensors)


def _device_info(meter_id: str) -> dict:
    """Device information for grouping a meter's sensors under one device."""
    return {
        "identifiers": {(DOMAIN, meter_id)},
        "name": f"Moj Elektro {meter_id}",
        "manufacturer": "LeoAlioth",
        # Home Assistant requires model to be a string, not a set.
        "model": meter_id,
        "sw_version": VERSION,
    }


class MojElektroTotalSensor(SensorEntity):
    """Lifetime import or export: the running total where the imported data ends.

    The history import writes the hourly statistics of these sensors and lines
    up Home Assistant's own 5-minute statistics with the new total before the
    state moves, so the day-late update is not booked into the hour it arrived in.
    The state is unknown until the first import.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:transmission-tower"
    _attr_suggested_display_precision = 2

    def __init__(self, entry_id: str, meter_id: str, kind: str) -> None:
        self._entry_id = entry_id
        self.meter_id = meter_id
        self._kind = kind
        self._attr_unique_id = total_unique_id(meter_id, kind)
        self._attr_name = f"Total {kind}"
        self._attr_device_info = _device_info(meter_id)
        self._attr_native_value = None
        self._attr_extra_state_attributes = {"data_through": None}

    async def async_added_to_hass(self) -> None:
        """Read the stored total now and after every import."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, history_signal(self._entry_id), self._async_refresh
            )
        )
        await self._async_refresh()

    async def _async_refresh(self) -> None:
        imported = await async_imported_totals(self.hass, self._entry_id)
        if self._kind in imported:
            through, total = imported[self._kind]
            self._attr_native_value = total
            self._attr_extra_state_attributes = {"data_through": through.isoformat()}
        self.async_write_ha_state()


class MojElektroCurrentBlockSensor(CoordinatorEntity, SensorEntity):
    """The network tariff block (1-5) in force now, from the tariff schedule.

    Worked out locally (season, work-free day, hour; tariff.py), so it is right
    even while the API is unreachable; it moves on the coordinator's 30 s beat.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:clock-time-four-outline"

    def __init__(self, coordinator, meter_id: str) -> None:
        super().__init__(coordinator)
        self.meter_id = meter_id
        self._attr_unique_id = f"{meter_id}-current_block"
        self._attr_name = "Current block"
        self._attr_device_info = _device_info(meter_id)

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> int:
        return block_at(dt_util.now())


class MojElektroCurrentBlockPowerSensor(CoordinatorEntity, SensorEntity):
    """The contracted (agreed) power of the block in force now, in kW."""

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_icon = "mdi:flash"

    def __init__(self, coordinator, meter_id: str) -> None:
        super().__init__(coordinator)
        self.meter_id = meter_id
        self._attr_unique_id = f"{meter_id}-current_block_power"
        self._attr_name = "Current block power"
        self._attr_device_info = _device_info(meter_id)
        self._last_known: dict[int, float] = {}

    @property
    def native_value(self) -> float | None:
        block = block_at(dt_util.now())
        try:
            self._last_known[block] = float(self.coordinator.data.get(f"casovni_blok_{block}"))
        except (TypeError, ValueError):
            pass
        # contracted powers change at most monthly: keep the last one through an API outage
        return self._last_known.get(block)

    @property
    def extra_state_attributes(self) -> dict:
        return {"block": block_at(dt_util.now())}


class MonthEntity(CoordinatorEntity, SensorEntity):
    """Base of the entities worked out from the month's 15-minute readings.

    They follow the month coordinator and read the agreed powers from the main one
    when asked, so a changed agreed power shows at the next read.
    """

    _attr_has_entity_name = True
    _attr_suggested_display_precision = 2

    def __init__(self, runtime, meter_id: str, key: str, name: str) -> None:
        super().__init__(runtime.month)
        self._runtime = runtime
        self.meter_id = meter_id
        self._attr_unique_id = f"{meter_id}-{key}"
        self._attr_name = name
        self._attr_device_info = _device_info(meter_id)


class _BlockPowerSensor(MonthEntity):
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_icon = "mdi:flash"

    def __init__(self, runtime, meter_id: str, block: int, key: str, name: str) -> None:
        super().__init__(runtime, meter_id, f"{key}_blok_{block}", f"{name} block {block}")
        self._block = block

    @property
    def _figure(self):
        return self._runtime.figures()[self._block]


class MonthlyPeakSensor(_BlockPowerSensor):
    """Highest 15-minute power of the month so far in this block (what is billed)."""

    def __init__(self, runtime, meter_id: str, block: int) -> None:
        super().__init__(runtime, meter_id, block, "monthly_peak", "Monthly peak")

    @property
    def native_value(self) -> float | None:
        return self._figure.peak

    @property
    def extra_state_attributes(self) -> dict:
        peak_at = self._figure.peak_at
        return {
            "peak_at": peak_at.isoformat() if peak_at else None,
            "data_through": _iso(self.coordinator.data.through),
        }


class PeakHeadroomSensor(_BlockPowerSensor):
    """Agreed power minus the month's peak; negative once the peak went over."""

    def __init__(self, runtime, meter_id: str, block: int) -> None:
        super().__init__(runtime, meter_id, block, "peak_headroom", "Peak headroom")

    @property
    def native_value(self) -> float | None:
        return self._figure.headroom


class ExcessPowerSensor(_BlockPowerSensor):
    """Excess power of the month so far: the root of the summed squared overruns."""

    _attr_icon = "mdi:flash-alert"

    def __init__(self, runtime, meter_id: str, block: int) -> None:
        super().__init__(runtime, meter_id, block, "excess_power", "Excess power")

    @property
    def native_value(self) -> float | None:
        return self._figure.excess

    @property
    def extra_state_attributes(self) -> dict:
        return {"intervals_over": len(self._figure.over)}


class LastIntervalPowerSensor(MonthEntity):
    """Average power of the newest finished 15-minute interval the API has."""

    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_icon = "mdi:flash"

    def __init__(self, runtime, meter_id: str, kind: str) -> None:
        super().__init__(
            runtime, meter_id, f"last_interval_power_{kind}", f"Last interval power {kind}"
        )
        self._kind = kind

    @property
    def _interval(self):
        data = self.coordinator.data
        return data.last_import if self._kind == "import" else data.last_export

    @property
    def native_value(self) -> float | None:
        return self._interval.kw if self._interval else None

    @property
    def extra_state_attributes(self) -> dict:
        interval = self._interval
        return {"interval_end": _iso(interval.start + QUARTER) if interval else None}


class NetworkFeeSensor(MonthEntity):
    """Estimated network fee of the month so far, in EUR without VAT.

    Power part (agreed power times price) plus energy part (kWh taken so far times
    price). Excluded: the connection-power charge, contributions and VAT. The excess
    charge is shown as an attribute: it is billed only if the agreed power was changed.
    """

    _attr_native_unit_of_measurement = "EUR"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_icon = "mdi:cash"

    def __init__(self, runtime, meter_id: str) -> None:
        super().__init__(runtime, meter_id, "network_fee_month_estimate", "Network fee estimate")

    def _estimate(self):
        month_first = self.coordinator.data.month_first
        prices = prices_for(self._runtime.user_group, month_first)
        if prices is None:
            return None, None
        fee = network_fee(prices, excess_weight(month_first.year), self._runtime.figures())
        return prices, fee

    @property
    def native_value(self) -> float | None:
        _, fee = self._estimate()
        return round(fee.total, 2) if fee else None

    @property
    def extra_state_attributes(self) -> dict:
        prices, fee = self._estimate()
        attrs = {
            "user_group": self._runtime.user_group,
            "data_through": _iso(self.coordinator.data.through),
            "excludes": ["connection power", "contributions", "VAT"],
        }
        if prices:
            attrs |= {
                "prices_from": prices.source,
                "prices_estimated": prices.estimated,
                "power_price": prices.power,
                "energy_price": prices.energy,
            }
        if fee:
            attrs |= {
                "power_part": round(fee.power, 2),
                "energy_part": round(fee.energy, 2),
                "excess_if_billed": round(fee.excess, 2),
            }
        return attrs


def _iso(moment) -> str | None:
    return moment.isoformat() if moment else None


class MojElektroSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Moj Elektro sensor."""

    def __init__(self, coordinator, measurement_name: str, meter_id: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)

        self.meter_id = meter_id
        self.measurement_name = measurement_name
        self._last_known_state = None

        # Preserve the historic default unique-id format, but make it deterministic.
        self._attr_unique_id = (
            f"{meter_id}-sensor.{DOMAIN}_{measurement_name.lower()}"
        )
        self._attr_has_entity_name = True
        self._attr_name = measurement_name.replace("_", " ")

        if measurement_name.startswith("casovni_blok"):
            self._attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
            self._attr_device_class = SensorDeviceClass.POWER
            self._attr_icon = "mdi:flash"
        else:
            self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
            self._attr_icon = "mdi:transmission-tower"

    @property
    def device_info(self):
        """Return device information for grouping sensors under one device."""
        return _device_info(self.meter_id)

    @property
    def native_value(self):
        """Return the native sensor value."""
        data = self.coordinator.data.get(self.measurement_name)
        if data is not None:
            try:
                self._last_known_state = float(data)
                return self._last_known_state
            except (TypeError, ValueError):
                _LOGGER.debug(
                    "Invalid value for %s: %r", self.measurement_name, data
                )

        # Contracted powers are configuration-like values. Keep the last known
        # value if that auxiliary API endpoint is temporarily unavailable.
        if self.measurement_name.startswith("casovni_blok"):
            return self._last_known_state

        return None
