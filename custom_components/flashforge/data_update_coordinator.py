"""DataUpdateCoordinator for flashforge integration."""

import logging
from datetime import timedelta

from ffpp.Printer import ConnectionStatus, Printer
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_NAME, DOMAIN, MAX_FAILED_UPDATES, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


class FlashForgeDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching FlashForgeprinter data."""

    config_entry: ConfigEntry

    def __init__(
        self, hass: HomeAssistant, printer: Printer, config_entry: ConfigEntry
    ) -> None:
        """Initialize."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DEFAULT_NAME}-{config_entry.entry_id}",
            update_interval=timedelta(seconds=SCAN_INTERVAL),
            update_method=self.async_update_data,
        )
        self.config_entry = config_entry
        self.printer = printer
        self._printer_offline = False
        self.data = {
            "status": None,
        }
        self.failedupdates = 0

    async def async_update_data(self) -> dict[str, list[str] | str | None]:
        """Update data via API."""
        try:
            await self.printer.update()
            files = await self.printer.network.sendGetFileNames()
        except (TimeoutError, ConnectionError) as err:
            self.failedupdates += 1
            if self.failedupdates >= MAX_FAILED_UPDATES:
                self.failedupdates = 0
                raise UpdateFailed(err) from err
            return await self.async_update_data()

        if not files:
            files = []
        files = [f.removeprefix("/data/") for f in files]
        self.failedupdates = 0

        return {"status": self.printer.machine_status, "files": files}

    async def async_config_entry_first_refresh(self) -> None:
        """Connect to printer and update with machine info."""
        self.printer.connected = ConnectionStatus.DISCONNECTED
        await self.printer.connect()

        return await super().async_config_entry_first_refresh()

    @property
    def device_info(self) -> DeviceInfo:
        """Device info."""
        unique_id = self.config_entry.unique_id or ""
        model = self.printer.machine_type
        name = self.printer.machine_name or self.config_entry.title
        firmware = self.printer.firmware
        sn = self.printer.serial
        mac = self.printer.mac_address

        return DeviceInfo(
            identifiers={(DOMAIN, unique_id)},
            manufacturer="FlashForge",
            model=model,
            name=name,
            sw_version=firmware,
            serial_number=sn,
            hw_version=mac,
        )
