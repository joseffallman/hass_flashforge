"""Support for flashforge selects."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .data_update_coordinator import FlashForgeDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up FlashForge select based on a config entry."""
    coordinator: FlashForgeDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SelectEntity] = []
    files = await coordinator.printer.network.sendGetFileNames()
    files = [f.removeprefix("/data/") for f in files]
    entities.append(FlashForgeSelect(coordinator, files))
    async_add_entities(entities)


class FlashForgeSelect(SelectEntity):
    """Representation of a demo select entity."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False

    def __init__(self, coordinator, options) -> None:
        """Initialize the Demo select entity."""
        self._attr_unique_id = coordinator.config_entry.unique_id + "_select"
        self._attr_current_option = options[0]
        self._attr_icon = "mdi:file-cad"
        self._attr_options = options
        self._attr_device_info = coordinator.device_info
        self._coordinator = coordinator

    async def async_select_option(self, option: str) -> None:
        """Update the current selected option."""
        self._attr_current_option = option
        self.async_write_ha_state()
