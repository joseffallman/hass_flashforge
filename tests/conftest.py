"""Fixtures for Flashforge integration tests."""

from itertools import cycle
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.flashforge.const import DOMAIN

from .const_response import (
    MACHINE_INFO,
    PROGRESS_PRINTING,
    PROGRESS_READY,
    STATUS_PRINTING,
    STATUS_READY,
    TEMP_PRINTING,
    TEMP_READY,
)


@pytest.fixture
def mock_printer_network() -> MagicMock:
    """Change the values that the printer responds with."""
    with patch("ffpp.Printer.Network", autospec=True) as mock_network:
        network = mock_network.return_value
        network.sendInfoRequest.return_value = MACHINE_INFO

        # Integration is reading two times when it starts.
        network.sendStatusRequest.side_effect = cycle(
            [STATUS_READY, STATUS_READY, STATUS_PRINTING]
        )
        network.sendTempRequest.side_effect = cycle(
            [TEMP_READY, TEMP_READY, TEMP_PRINTING]
        )
        network.sendProgressRequest.side_effect = cycle(
            [PROGRESS_READY, PROGRESS_READY, PROGRESS_PRINTING]
        )

        yield network


@pytest.fixture
def mock_printer_discovery() -> MagicMock:
    """Mock printer discovery."""
    with patch("ffpp.Discovery.getPrinters", autospec=True) as get_printers:
        get_printers.return_value = [("Adventurer4", "192.168.0.64")]
        yield get_printers


@pytest_asyncio.fixture(autouse=True)
async def unload_integration(hass: HomeAssistant) -> None:
    """Try to unload the Flashforge integration after each test."""
    yield

    entries = hass.config_entries.async_entries(DOMAIN)
    if entries:
        entry: MockConfigEntry
        for entry in entries:
            await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()
