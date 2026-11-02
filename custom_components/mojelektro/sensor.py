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
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import CONF_DECIMAL, CONF_METER_ID, CONF_TOKEN, DOMAIN, VERSION
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
    async_add_entities(sensors)


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
        self._attr_name = f"Moj Elektro {measurement_name.replace('_', ' ')}"

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
        return {
            "identifiers": {(DOMAIN, self.meter_id)},
            "name": f"Moj Elektro {self.meter_id}",
            "manufacturer": "Moj Elektro",
            # Home Assistant requires model to be a string, not a set.
            "model": self.meter_id,
            "sw_version": VERSION,
        }

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
