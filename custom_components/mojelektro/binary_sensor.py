"""Binary sensor: has this month's power gone over the agreed power."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_METER_ID
from .sensor import _device_info


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Set up the over-agreed-power sensor."""
    async_add_entities([OverAgreedPowerSensor(entry.runtime_data, entry.data[CONF_METER_ID])])


class OverAgreedPowerSensor(CoordinatorEntity, BinarySensorEntity):
    """On once any block's 15-minute power passed its agreed power this month."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_name = "Over agreed power"

    def __init__(self, runtime, meter_id: str) -> None:
        super().__init__(runtime.month)
        self._runtime = runtime
        self._attr_unique_id = f"{meter_id}-over_agreed_power"
        self._attr_device_info = _device_info(meter_id)

    @property
    def is_on(self) -> bool:
        return any(figure.over for figure in self._runtime.figures().values())

    @property
    def extra_state_attributes(self) -> dict:
        return {
            f"block_{block}": {
                "intervals_over": len(figure.over),
                "excess_power": figure.excess,
            }
            for block, figure in self._runtime.figures().items()
        }
