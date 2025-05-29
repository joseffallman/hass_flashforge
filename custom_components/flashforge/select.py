"""Support for flashforge selects."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .data_update_coordinator import FlashForgeDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up FlashForge select based on a config entry."""
    coordinator: FlashForgeDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities([FlashForgeSelect(coordinator)])


class FlashForgeSelect(CoordinatorEntity, SelectEntity):
    """Representation of a demo select entity."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, coordinator: FlashForgeDataUpdateCoordinator, options: list = []
    ) -> None:
        """Initialize the Demo select entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.unique_id}_select"
        self._attr_current_option = options[0] if options else None
        self._attr_icon = "mdi:file-cad"
        self._attr_name = "File list"
        self._attr_options = options
        self._attr_device_info = coordinator.device_info

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        try:
            self._attr_options = self.coordinator.data["files"]
        except KeyError:
            self._attr_options = []
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        """Update the current selected option."""
        self._attr_current_option = option
        self.async_write_ha_state()

    # async def async_update(self) -> None:
    #     """Get the latest data."""
    #     _LOGGER.debug("async_update")
    #     files = await self._coordinator.printer.network.sendGetFileNames()
    #     for n, value in enumerate(files):
    #         files[n] = value.removeprefix("/data/")
    #     _LOGGER.debug(f"list of files: {files}")
    #     self._attr_options = files
