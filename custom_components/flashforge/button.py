"""Button platform that offers a PrinterButton entity."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DOMAIN
from .data_update_coordinator import FlashForgeDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Printer Button platform."""
    coordinator: FlashForgeDataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ]

    printer_network = coordinator.printer.network
    async_add_entities(
        [
            PrinterButton(
                name="abort",
                icon="mdi:stop",
                coordinator=coordinator,
                hass=hass,
                action=printer_network.sendAbortRequest,
            ),
            PrinterButton(
                name="continue",
                icon="mdi:play",
                hass=hass,
                coordinator=coordinator,
                action=printer_network.sendContinueRequest,
            ),
            PrinterButton(
                name="pause",
                icon="mdi:pause",
                hass=hass,
                coordinator=coordinator,
                action=printer_network.sendPauseRequest,
            ),
            FilePrinterButton(
                name="print_file",
                icon="mdi:printer-3d-nozzle",
                hass=hass,
                coordinator=coordinator,
                action=printer_network.sendPrintRequest,
            ),
        ]
    )


class PrinterButton(ButtonEntity):
    """Representation of a demo button entity."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False

    def __init__(
        self,
        name,
        icon,
        hass: HomeAssistant,
        coordinator: FlashForgeDataUpdateCoordinator,
        action,
    ) -> None:
        """Initialize the Demo button entity."""
        self._attr_unique_id = f"{coordinator.config_entry.unique_id}_{name}"
        self._attr_icon = icon
        self._attr_name = f"{name.replace('_', ' ').title()}"
        self._action = action
        self._attr_device_info = coordinator.device_info
        self.coordinator = coordinator

    async def async_press(self) -> None:
        """Send out a persistent notification."""
        result = await self._action()
        _LOGGER.debug(f"Flashforge printer responded with: {result}")


class FilePrinterButton(PrinterButton):
    """Representation of a file print button entity."""

    async def async_press(self) -> None:
        """Send out a persistent notification."""
        entityRegistry = entity_registry.async_get(self.coordinator.hass)
        select_entity = entityRegistry.async_get_entity_id(
            Platform.SELECT,
            DOMAIN,
            f"{self.coordinator.config_entry.unique_id}_select",
        )
        select_state = self.coordinator.hass.states.get(select_entity)
        result = await self._action(file=select_state.state)
        _LOGGER.debug(f"Flashforge printer responded with: {result}")
