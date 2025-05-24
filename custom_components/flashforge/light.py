"""Platform for light integration."""

from __future__ import annotations

import logging

# Import the device class from the component that you want to support
from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .data_update_coordinator import FlashForgeDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Flashforge Light platform."""

    coordinator: FlashForgeDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities([FlashForgeLightEntity(coordinator)])


class FlashForgeLightEntity(CoordinatorEntity, LightEntity):
    """An entity using CoordinatorEntity.

    The CoordinatorEntity class provides:
      should_poll
      async_update
      async_added_to_hass
      available

    """

    _attr_has_entity_name = True

    def __init__(self, coordinator):
        """Pass coordinator to CoordinatorEntity."""
        super().__init__(coordinator)
        self._device_id = coordinator.config_entry.unique_id
        self._attr_device_info = coordinator.device_info
        self._attr_name = "Light"
        self._attr_unique_id = coordinator.config_entry.unique_id + "_light"

        self.supported_color_modes = ColorMode.ONOFF
        self.color_mode = ColorMode.ONOFF

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_is_on = self.coordinator.printer.led
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs):
        """Turn the light on."""
        await self.coordinator.printer.setLed(True)

        # Update the data
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):
        """Turn the light off."""
        await self.coordinator.printer.setLed(False)

        # Update the data
        await self.coordinator.async_request_refresh()
