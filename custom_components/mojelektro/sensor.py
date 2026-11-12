"""Sensor platform for the Moj Elektro integration."""

from datetime import timedelta
import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .const import CONF_DECIMAL, CONF_METER_ID, CONF_TOKEN, DOMAIN, VERSION
from .history import async_imported_totals, history_signal, total_unique_id
from .tariff import block_at
from .moj_elektro_api import (
    MojElektroApi,
    MojElektroAuthError,
    MojElektroError,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up Moj Elektro sensors from a config entry."""
    token = entry.data[CONF_TOKEN]
    meter_id = entry.data[CONF_METER_ID]
    decimal = entry.data.get(CONF_DECIMAL)
    session = async_get_clientsession(hass)

    api = MojElektroApi(token, meter_id, decimal, session)

    async def async_update_data():
        """Fetch data and translate API errors to Home Assistant errors."""
        try:
            return await api.getData()
        except MojElektroAuthError as err:
            raise ConfigEntryAuthFailed("Moj Elektro authentication failed") from err
        except MojElektroError as err:
            raise UpdateFailed(str(err)) from err
        except Exception as err:
            raise UpdateFailed(f"Unexpected Moj Elektro update error: {err}") from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="mojelektro_sensor",
        update_method=async_update_data,
        update_interval=timedelta(seconds=30),
    )

    await coordinator.async_config_entry_first_refresh()

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
    async_add_entities(sensors)


def _device_info(meter_id: str) -> dict:
    """Device information for grouping a meter's sensors under one device."""
    return {
        "identifiers": {(DOMAIN, meter_id)},
        "name": f"Moj Elektro {meter_id}",
        "manufacturer": "Anže Košir",
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
