"""Tests for the Flashforge sensors."""

import asyncio
from datetime import timedelta
from itertools import count
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from custom_components.flashforge.const import DOMAIN, MAX_FAILED_UPDATES

from . import init_integration
from .const_response import (
    MACHINE_INFO_TWO_EXTRUDERS,
    PROGRESS_PRINTING_PER_MILLE,
    STATUS_PRINTING,
    TEMP_PRINTING_TWO_EXTRUDERS,
)

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
        "state": "11.0",
        "name": "Adventurer4 Job Percentage",
        "unique_id": "SNADVA1234567_job_percentage",
    },
    {
        # Not enough progress for an estimate.
        "entity_id": "sensor.adventurer4_job_remaining",
        "state": "unknown",
        "name": "Adventurer4 Job Remaining",
        "unique_id": "SNADVA1234567_job_remaining",
    },
    {
        "entity_id": "sensor.adventurer4_job_end",
        "state": "unknown",
        "name": "Adventurer4 Job End",
        "unique_id": "SNADVA1234567_job_end",
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


@pytest.mark.asyncio
async def test_two_extruders_and_per_mille_progress(
    enable_custom_integrations, hass: HomeAssistant, mock_printer_network: MagicMock
):
    """Test a Creator 3: two extruders and progress reported in per mille."""
    mock_printer_network.sendInfoRequest.return_value = MACHINE_INFO_TWO_EXTRUDERS
    mock_printer_network.sendTempRequest.side_effect = None
    mock_printer_network.sendTempRequest.return_value = TEMP_PRINTING_TWO_EXTRUDERS
    mock_printer_network.sendProgressRequest.side_effect = None
    mock_printer_network.sendProgressRequest.return_value = PROGRESS_PRINTING_PER_MILLE
    await init_integration(hass)

    expected_states = {
        "sensor.adventurer4_extruder0_current": "210.0",
        "sensor.adventurer4_extruder0_target": "210.0",
        "sensor.adventurer4_extruder1_current": "33.0",
        "sensor.adventurer4_extruder1_target": "0.0",
        "sensor.adventurer4_bed_current": "45.0",
        "sensor.adventurer4_bed_target": "45.0",
        "sensor.adventurer4_job_percentage": "46.1",
    }
    for entity_id, expected in expected_states.items():
        state = hass.states.get(entity_id)
        assert state is not None, entity_id
        assert state.state == expected, entity_id
    assert hass.states.get("sensor.adventurer4_extruder_current") is None


@pytest.mark.asyncio
async def test_sensor_update_hanging_printer(
    enable_custom_integrations, hass: HomeAssistant, mock_printer_network: MagicMock
):
    """Test that a printer that never answers can't stall the updates."""
    entry = await init_integration(hass)

    async def never_answers(*_args: object, **_kwargs: object) -> str:
        await asyncio.Event().wait()
        return ""

    mock_printer_network.sendStatusRequest.side_effect = never_answers

    coordinator: DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    with patch(
        "custom_components.flashforge.data_update_coordinator.UPDATE_TIMEOUT", 0.01
    ):
        await asyncio.wait_for(coordinator.async_refresh(), 5)
    await hass.async_block_till_done()

    assert not coordinator.last_update_success
    for expected in SENSORS:
        state = hass.states.get(expected["entity_id"])
        assert state.state == STATE_UNAVAILABLE


@pytest.mark.asyncio
async def test_remaining_time_estimate(
    enable_custom_integrations, hass: HomeAssistant, mock_printer_network: MagicMock
):
    """Test the estimated remaining time and end of a print at 1 % per minute."""
    mock_printer_network.sendStatusRequest.side_effect = None
    mock_printer_network.sendStatusRequest.return_value = STATUS_PRINTING
    mock_printer_network.sendProgressRequest.side_effect = (
        f"CMD M27 Received.\r\nSD printing byte {per_mille}/1000\r\nok\r\n"
        for per_mille in count(0, 10)
    )

    with patch(
        "custom_components.flashforge.data_update_coordinator.time"
    ) as mock_time:
        mock_time.monotonic.side_effect = count(0, 60)
        entry = await init_integration(hass)
        coordinator: DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
        # Connecting read 0 %, the two coordinator updates during setup 1 % and
        # 2 %. The first step was to 2 %, so 7 % is needed for an estimate.
        for _ in range(5):
            await coordinator.async_refresh()
        await hass.async_block_till_done()

    # 7 % done, 93 % left.
    assert hass.states.get("sensor.adventurer4_job_percentage").state == "7.0"
    assert hass.states.get("sensor.adventurer4_job_remaining").state == "93"
    job_end = dt_util.parse_datetime(
        hass.states.get("sensor.adventurer4_job_end").state
    )
    expected_end = dt_util.utcnow() + timedelta(minutes=93)
    assert abs(job_end - expected_end) < timedelta(minutes=2)
