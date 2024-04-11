"""Button platform that offers a PrinterButton entity."""
from __future__ import annotations
import logging
from typing import Final


from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .data_update_coordinator import FlashForgeDataUpdateCoordinator
from homeassistant.helpers.entity_registry import async_get as get_entity_registry

from . import DOMAIN

_LOGGER: Final = logging.getLogger(__name__)
async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Printer Button platform."""
    coordinator: FlashForgeDataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ]

    printer_network=coordinator.printer.network
    async_add_entities(
        [
            PrinterButton(
                name="abort",
                icon="mdi:stop",
                coordinator=coordinator,
                hass=hass,
                action=printer_network.sendAbortRequest

            ),
            PrinterButton(
                name="play",
                icon="mdi:play",
                hass=hass,
                coordinator=coordinator,
                action=printer_network.sendContinueRequest

            ),
            PrinterButton(
                name="pause",
                icon="mdi:pause",
                hass=hass,
                coordinator=coordinator,
                action=printer_network.sendPauseRequest

            ),
            PrinterButton(
                name="print",
                icon="mdi:printer-3d-nozzle",
                hass=hass,
                coordinator=coordinator,
                action=printer_network.sendPrintRequest

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
        coordinator,
        action,
    ) -> None:
        """Initialize the Demo button entity."""
        self._attr_unique_id = coordinator.config_entry.unique_id+"_"+name
        self._attr_icon = icon
        self._attr_name=name
        self._action=action
        self._attr_device_info = coordinator.device_info
        self._coordinator=coordinator
        self._hass=hass


    async def async_press(self) -> None:
        """Send out a persistent notification."""
        if self.name=='print':
            registry = get_entity_registry(self._hass)
            file_select_entity=next((x for x in registry.entities if registry.entities.get(x).unique_id == self._coordinator.config_entry.unique_id+"_select" ))
            result=await self._action(file=self._hass.states.get(file_select_entity).state)
        else:
            result=await self._action()
        _LOGGER.debug(f"result: {result} ")
        #self.hass.bus.async_fire("demo_button_pressed")
