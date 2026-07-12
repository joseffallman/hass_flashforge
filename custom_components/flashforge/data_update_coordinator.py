"""DataUpdateCoordinator for flashforge integration."""

import logging
from datetime import timedelta

from ffpp.Printer import ConnectionStatus, Printer
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_NAME, DOMAIN, MAX_FAILED_UPDATES, SCAN_INTERVAL
from .protocol_guard import ProtocolGuard

_LOGGER = logging.getLogger(__name__)

# Repairs issue id raised when we suspect another client (e.g. FlashPrint) is
# also connected to the printer -- see protocol_guard.py and plan §3a/§3b.
CROSS_TALK_ISSUE_ID = "cross_talk_detected"


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
        # All access to `printer`/`printer.network` -- from this coordinator's
        # poll AND from every service-call handler in __init__.py -- goes
        # through this guard now, instead of touching the printer directly.
        # See protocol_guard.py for why.
        self.guard = ProtocolGuard(printer)

    async def async_update_data(self) -> dict[str, list[str] | str | None]:
        """Update data via API."""
        try:
            result = await self.guard.safe_update()
        except (TimeoutError, ConnectionError) as err:
            self.failedupdates += 1
            # Feed hard connection failures into the same cross-talk signal
            # as soft mismatches -- see protocol_guard.note_connection_failure()
            # for why this is needed (a real episode was dominated by resets,
            # not mismatches, and the old mismatch-only counter missed it
            # entirely). This can flip cross_talk_suspected on its own, so
            # sync the Repairs issue here too, not just on the success path.
            self.guard.note_connection_failure()
            self._sync_cross_talk_issue()
            if self.failedupdates >= MAX_FAILED_UPDATES:
                self.failedupdates = 0
                raise UpdateFailed(err) from err
            return await self.async_update_data()

        self.failedupdates = 0
        self._sync_cross_talk_issue()

        files = result.files or []
        files = [f.removeprefix("/data/") for f in files]

        return {"status": self.printer.machine_status, "files": files}

    def _sync_cross_talk_issue(self) -> None:
        """Create/clear the Repairs issue to match guard.cross_talk_suspected.

        Pulled out so both the successful-update path and the connection-
        failure path (which can now also flip cross_talk_suspected, via
        note_connection_failure()) keep the issue in sync the same way.
        """
        if self.guard.cross_talk_suspected:
            _LOGGER.warning(
                "FlashForge: recent responses/connections look like another "
                "client (e.g. FlashPrint) is also connected to the printer "
                "at the same time (consecutive soft mismatches: %d).",
                self.guard.consecutive_mismatches,
            )
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                CROSS_TALK_ISSUE_ID,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=CROSS_TALK_ISSUE_ID,
            )
        elif ir.async_get(self.hass).async_get_issue(DOMAIN, CROSS_TALK_ISSUE_ID) is not None:
            # A clean cycle happened -- the interfering client presumably
            # disconnected. Clear the issue rather than leaving it stuck.
            ir.async_delete_issue(self.hass, DOMAIN, CROSS_TALK_ISSUE_ID)

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
