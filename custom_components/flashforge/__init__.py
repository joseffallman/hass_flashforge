"""The Flashforge integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ffpp.Printer import Printer
from homeassistant.const import CONF_IP_ADDRESS, CONF_PORT, Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError

from .const import DOMAIN
from .data_update_coordinator import FlashForgeDataUpdateCoordinator

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

PLATFORMS = [
    Platform.SENSOR,
    Platform.CAMERA,
    Platform.SELECT,
    Platform.BUTTON,
    Platform.LIGHT,
]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:  # noqa: PLR0915
    """Set up Flashforge from a config entry."""
    printer = Printer(entry.data[CONF_IP_ADDRESS], port=entry.data[CONF_PORT])
    _LOGGER.debug("FlashForge printer setup")
    coordinator = FlashForgeDataUpdateCoordinator(hass, printer, entry)
    try:
        await coordinator.async_config_entry_first_refresh()
    except (TimeoutError, ConnectionError) as err:
        _LOGGER.debug("Printer not responding: %s", err)
        raise ConfigEntryNotReady(err) from err
    # Save the coordinator object to be able to access it later on.
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    async def pause(_: ServiceCall) -> None:
        """Handle the service call."""
        _LOGGER.debug("Pause")
        await printer.connect()
        pr = await printer.network.sendPauseRequest()
        _LOGGER.debug("pauseRequest: %s", pr)

    async def continue_print(_: ServiceCall) -> None:
        """Handle the service call."""
        _LOGGER.debug("Continue")
        await printer.connect()
        pr = await printer.network.sendContinueRequest()
        _LOGGER.debug("ContinueRequest: %s", pr)

    async def abort(_: ServiceCall) -> None:
        """Handle the service call."""
        _LOGGER.debug("Abort")
        await printer.connect()
        pr = await printer.network.sendAbortRequest()
        _LOGGER.debug("AbortRequest: %s", pr)

    async def get_file_names(_: ServiceCall) -> ServiceResponse:
        """Handle the service call."""
        _LOGGER.debug("Get file names")
        await printer.connect()
        files_list = await printer.network.sendGetFileNames()
        _LOGGER.debug("FileNames: %s", files_list)
        if not files_list:
            return {"files": []}

        files_list = [f.removeprefix("/data/") for f in files_list]
        return {"files": files_list}

    async def print_file(call: ServiceCall) -> None:
        """Handle the service call."""
        _LOGGER.debug("print_file")
        filename = call.data.get("file_name")
        await printer.connect()
        if printer.machine_status != "READY":
            msg = "printer status is not READY"
            raise HomeAssistantError(msg)
        pr = await printer.network.sendPrintRequest(file=filename)
        _LOGGER.debug("print_file: %s", pr)

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

    await coordinator.async_request_refresh()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
