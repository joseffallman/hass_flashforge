"""The Flashforge integration."""
from __future__ import annotations
import logging

from ffpp.Printer import Printer

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_IP_ADDRESS, CONF_PORT, Platform
from homeassistant.core import (HomeAssistant,
    ServiceResponse,
    SupportsResponse)
import voluptuous as vol
from .const import DOMAIN
from .data_update_coordinator import FlashForgeDataUpdateCoordinator
from homeassistant.exceptions import HomeAssistantError


PLATFORMS = [Platform.SENSOR, Platform.CAMERA,Platform.SELECT,Platform.BUTTON]

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Flashforge from a config entry."""
    _LOGGER.debug("async_setup_entry")
    printer = Printer(entry.data[CONF_IP_ADDRESS], port=entry.data[CONF_PORT])
    _LOGGER.debug("printer setup")
    coordinator = FlashForgeDataUpdateCoordinator(hass, printer, entry)
    await coordinator.async_config_entry_first_refresh()
    # Save the coordinator object to be able to access it later on.
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator


    async def pause(call):
            """Handle the service call."""
            _LOGGER.debug("pause")
            await printer.connect()
            pr= await printer.network.sendPauseRequest()
            _LOGGER.debug(f"pauseRequest: {pr} ")
    async def continue_print(call):
            """Handle the service call."""
            _LOGGER.debug("pause")
            await printer.connect()
            pr= await printer.network.sendContinueRequest()
            _LOGGER.debug(f"ContinueRequest: {pr} ")
    async def abort(call):
            """Handle the service call."""
            _LOGGER.debug("pause")
            await printer.connect()
            pr= await printer.network.sendAbortRequest()
            _LOGGER.debug(f"AbortRequest: {pr} ")
    async def get_file_names(call)-> ServiceResponse:
            """Handle the service call."""
            _LOGGER.debug("pause")
            await printer.connect()
            files_list= await printer.network.sendGetFileNames()
            _LOGGER.debug(f"FileNames: {files_list} ")
            for n, value in enumerate(files_list):
                files_list[n] = value.removeprefix("/data/")
            return  {"files": files_list}
    async def print_file(call):
            """Handle the service call."""
            _LOGGER.debug("print_file")
            filename=call.data.get("file_name")
            await printer.connect()
            if printer.machine_status != 'READY':
                raise HomeAssistantError("printer status is not READY")
            pr= await printer.network.sendPrintRequest(file=filename)
            _LOGGER.debug(f"print_file: {pr} ")

    hass.services.async_register(DOMAIN, "pause", pause)
    hass.services.async_register(DOMAIN, "continue_print", continue_print)
    hass.services.async_register(DOMAIN, "abort", abort)
    hass.services.async_register(DOMAIN, "print_file", print_file)
    hass.services.async_register(
        DOMAIN,
        "get_file_names",
        get_file_names,
        supports_response=SupportsResponse.ONLY,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
