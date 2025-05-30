"""Tests for the Flashforge sensors."""

from unittest.mock import MagicMock

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from custom_components.flashforge.const import DOMAIN, MAX_FAILED_UPDATES

from . import init_integration

SENSORS = (
    {
        "entity_id": "sensor.adventurer4_extruder_current",
        "state": "198.0",
        "name": "Adventurer4 Extruder Current",
        "unique_id": "SNADVA1234567_extruder_current",
    },
    {
        "entity_id": "sensor.adventurer4_extruder_target",
        "state": "210.0",
        "name": "Adventurer4 Extruder Target",
        "unique_id": "SNADVA1234567_extruder_target",
    },
    {
        "entity_id": "sensor.adventurer4_bed_current",
        "state": "48.0",
        "name": "Adventurer4 Bed Current",
        "unique_id": "SNADVA1234567_bed_current",
    },
    {
        "entity_id": "sensor.adventurer4_bed_target",
        "state": "64.0",
        "name": "Adventurer4 Bed Target",
        "unique_id": "SNADVA1234567_bed_target",
    },
    {
        "entity_id": "sensor.adventurer4_status",
        "state": "BUILDING_FROM_SD",
        "name": "Adventurer4 Status",
        "unique_id": "SNADVA1234567_status",
    },
    {
        "entity_id": "sensor.adventurer4_job_percentage",
        "state": "11",
        "name": "Adventurer4 Job Percentage",
        "unique_id": "SNADVA1234567_job_percentage",
    },
)


@pytest.mark.asyncio
async def test_sensors(
    enable_custom_integrations, hass: HomeAssistant, mock_printer_network: MagicMock
):
    """Test Flashforge sensors."""
    await init_integration(hass)
    registry = entity_registry.async_get(hass)

    for expected in SENSORS:
        state = hass.states.get(expected["entity_id"])
        assert state is not None
        assert state.state == expected["state"]
        assert state.name == expected["name"]
        entry = registry.async_get(expected["entity_id"])
        assert entry.unique_id == expected["unique_id"]


@pytest.mark.asyncio
async def test_unload_integration_and_sensors(
    enable_custom_integrations, hass: HomeAssistant, mock_printer_network: MagicMock
):
    """Test Flashforge sensors are unavailable and then deleted when integration."""
    entry = await init_integration(hass)

    # Sensor become unavailable when integration unloads.
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    for expected in SENSORS:
        state = hass.states.get(expected["entity_id"])
        assert state.state == STATE_UNAVAILABLE

    # Sensor become None when integration is deleted.
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get(SENSORS[0]["entity_id"])
    assert state is None


@pytest.mark.asyncio
async def test_sensor_update_error(
    enable_custom_integrations, hass: HomeAssistant, mock_printer_network: MagicMock
):
    """Test Flashforge sensors are unavailable after an update error."""
    entry = await init_integration(hass)

    # Change printer respond.
    mock_printer_network.sendStatusRequest.side_effect = ConnectionError("conn_error")

    # Request sensor update.
    coordinator: DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    for expected in SENSORS:
        state = hass.states.get(expected["entity_id"])
        assert state.state == STATE_UNAVAILABLE


@pytest.mark.asyncio
async def test_sensor_update_error2(
    enable_custom_integrations, hass: HomeAssistant, mock_printer_network: MagicMock
):
    """Test Flashforge sensors are unavailable after an update error."""
    entry = await init_integration(hass)
    INIT_CALL_COUNT = 3

    # Change printer respond.
    mock_printer_network.sendStatusRequest.side_effect = TimeoutError("timeout")

    # Request sensor update.
    coordinator: DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert (
        mock_printer_network.sendStatusRequest.call_count
        == INIT_CALL_COUNT + MAX_FAILED_UPDATES
    )
    for expected in SENSORS:
        state = hass.states.get(expected["entity_id"])
        assert state.state == STATE_UNAVAILABLE
