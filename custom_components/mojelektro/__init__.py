"""Moj Elektro integration."""

import asyncio
import json
import logging
from datetime import timedelta
import re

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_DECIMAL,
    CONF_IMPORT_HISTORY,
    CONF_METER_ID,
    CONF_TOKEN,
    CONF_USER_GROUP,
    DEFAULT_USER_GROUP,
    DOMAIN,
    SETUP_TAG_15_ARRAY,
    SETUP_TAG_ARRAY,
    SETUP_TAG_BLOCKS_ARRAY,
)

from .history import async_import_history, history_signal
from .moj_elektro_api import MojElektroApi, MojElektroAuthError, MojElektroError
from .month import MojElektroRuntime, MonthCoordinator

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]

SERVICE_IMPORT_HISTORY = "import_history"
IMPORT_HISTORY_SCHEMA = vol.Schema(
    {vol.Required("start_date"): cv.date, vol.Optional("config_entry"): cv.string}
)
# Moj Elektro publishes a day late, so the morning run finds yesterday complete.
DAILY_IMPORT_TIME = {"hour": 6, "minute": 17, "second": 0}
DAILY_MONTH_TIME = {"hour": 6, "minute": 32, "second": 0}


def _expected_sensor_names() -> list[str]:
    """Return all sensor keys created by this integration."""
    names: list[str] = []

    for item in json.loads(SETUP_TAG_15_ARRAY):
        names.append(item["sensor"])

    for item in json.loads(SETUP_TAG_ARRAY):
        names.append(item["sensor"])
        names.append(item["sensor"].replace("daily_", "monthly_", 1))

    for item in json.loads(SETUP_TAG_BLOCKS_ARRAY):
        names.append(item["sensor"])

    names.extend(f"casovni_blok_{block_number}" for block_number in range(1, 6))
    return names


def _migrate_legacy_sensor_unique_ids(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Migrate legacy suffixed unique IDs without changing entity IDs."""
    meter_id = entry.data[CONF_METER_ID]
    registry = er.async_get(hass)
    registry_entries = er.async_entries_for_config_entry(registry, entry.entry_id)

    for measurement_name in _expected_sensor_names():
        desired_unique_id = (
            f"{meter_id}-sensor.{DOMAIN}_{measurement_name.lower()}"
        )
        legacy_pattern = re.compile(
            rf"^{re.escape(desired_unique_id)}_(?P<suffix>(?:[2-9]|[1-9]\d+))$"
        )

        for entity_entry in registry_entries:
            if entity_entry.domain != Platform.SENSOR:
                continue
            if not legacy_pattern.fullmatch(entity_entry.unique_id):
                continue

            existing_entity_id = registry.async_get_entity_id(
                entity_entry.domain,
                entity_entry.platform,
                desired_unique_id,
            )
            if existing_entity_id not in (None, entity_entry.entity_id):
                continue

            registry.async_update_entity(
                entity_entry.entity_id,
                new_unique_id=desired_unique_id,
            )
            break


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration domain."""

    async def import_history(call: ServiceCall) -> None:
        entries = [
            entry
            for entry in hass.config_entries.async_entries(DOMAIN)
            if entry.state is ConfigEntryState.LOADED
            and call.data.get("config_entry") in (None, entry.entry_id)
        ]
        if not entries:
            raise ServiceValidationError("No loaded Moj Elektro entry matches")
        for entry in entries:
            await _async_import(hass, entry, call.data["start_date"])

    hass.services.async_register(
        DOMAIN, SERVICE_IMPORT_HISTORY, import_history, schema=IMPORT_HISTORY_SCHEMA
    )
    return True


async def _async_import(hass: HomeAssistant, entry: ConfigEntry, start=None) -> None:
    """Import hourly statistics for an entry, one run at a time per entry."""
    api = MojElektroApi(
        entry.data[CONF_TOKEN],
        entry.data[CONF_METER_ID],
        entry.data.get(CONF_DECIMAL),
        async_get_clientsession(hass),
    )
    lock = hass.data.setdefault(DOMAIN, {}).setdefault(entry.entry_id, asyncio.Lock())
    async with lock:
        try:
            await async_import_history(hass, entry.entry_id, api, start)
        except MojElektroError as err:
            _LOGGER.warning("Moj Elektro statistics import failed: %s", err)
        async_dispatcher_send(hass, history_signal(entry.entry_id))


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Moj Elektro from a config entry."""
    if entry.unique_id is None:
        meter_id = entry.data[CONF_METER_ID]
        duplicate_entries = [
            existing_entry
            for existing_entry in hass.config_entries.async_entries(DOMAIN)
            if existing_entry.entry_id != entry.entry_id
            and existing_entry.data.get(CONF_METER_ID) == meter_id
        ]
        if not duplicate_entries:
            hass.config_entries.async_update_entry(entry, unique_id=meter_id)

    _migrate_legacy_sensor_unique_ids(hass, entry)

    # Before 0.3.1 the meter was a "service" device, which Home Assistant lists
    # apart from devices; drop it so the sensors recreate it as a regular device.
    device_registry = dr.async_get(hass)
    old_device = device_registry.async_get_device(
        identifiers={(DOMAIN, entry.data[CONF_METER_ID])}
    )
    if old_device and old_device.entry_type is dr.DeviceEntryType.SERVICE:
        device_registry.async_remove_device(old_device.id)

    api = MojElektroApi(
        entry.data[CONF_TOKEN],
        entry.data[CONF_METER_ID],
        entry.data.get(CONF_DECIMAL),
        async_get_clientsession(hass),
    )

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
        config_entry=entry,
        name="mojelektro_sensor",
        update_method=async_update_data,
        update_interval=timedelta(seconds=30),
    )
    await coordinator.async_config_entry_first_refresh()

    # The month's power figures are optional: a failure here must not stop the meter's
    # other sensors, so refresh without raising and let the entities show unavailable.
    month = MonthCoordinator(hass, entry, api)
    await month.async_refresh()
    entry.runtime_data = MojElektroRuntime(
        api=api,
        coordinator=coordinator,
        month=month,
        user_group=entry.options.get(
            CONF_USER_GROUP, entry.data.get(CONF_USER_GROUP, DEFAULT_USER_GROUP)
        ),
    )
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def daily_month(_now) -> None:
        await month.async_refresh()

    entry.async_on_unload(
        async_track_time_change(hass, daily_month, **DAILY_MONTH_TIME)
    )

    if entry.data.get(CONF_IMPORT_HISTORY, True):
        entry.async_create_background_task(
            hass, _async_import(hass, entry), f"{DOMAIN}_import_history"
        )

        async def daily_import(_now) -> None:
            await _async_import(hass, entry)

        entry.async_on_unload(
            async_track_time_change(hass, daily_import, **DAILY_IMPORT_TIME)
        )
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload so a changed user group reaches the fee estimate."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Moj Elektro config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
