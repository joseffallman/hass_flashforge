"""Tests for the Flashforge camera entity."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from homeassistant.helpers import entity_registry as er

from custom_components.flashforge.const import DOMAIN
from custom_components.flashforge.mjpeg import EOI, SOI

from . import init_integration
from .fake_camera import FakeSingleClientCamera

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from unittest.mock import MagicMock

    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

UNIQUE_ID = "SNADVA1234567_camera"
STREAM_END_LIMIT = 5.0  # seconds for a live stream to end after unload


@pytest.fixture
async def camera(socket_enabled: None) -> AsyncIterator[FakeSingleClientCamera]:  # noqa: ARG001
    """Run a fake single-client camera on localhost."""
    cam = FakeSingleClientCamera()
    await cam.start()
    yield cam
    await cam.close()


async def setup_camera(
    hass: HomeAssistant, network: MagicMock, camera: FakeSingleClientCamera
) -> tuple[str, str]:
    """Set up the integration pointing at the fake camera."""
    network.getCameraStream.return_value = camera.url
    entry = await init_integration(hass)
    entity_id = er.async_get(hass).async_get_entity_id("camera", DOMAIN, UNIQUE_ID)
    assert entity_id is not None
    return entry.entry_id, entity_id


async def wait_until(predicate, limit: float = 3.0) -> None:  # noqa: ANN001
    """Poll until predicate() is true."""
    async with asyncio.timeout(limit):
        while not predicate():  # noqa: ASYNC110
            await asyncio.sleep(0.02)


async def test_camera_entity(
    enable_custom_integrations: None,  # noqa: ARG001
    hass: HomeAssistant,
    mock_printer_network: MagicMock,
    camera: FakeSingleClientCamera,
) -> None:
    """The camera entity keeps its name and unique ID."""
    _entry_id, entity_id = await setup_camera(hass, mock_printer_network, camera)
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.name == "Camera"


async def test_snapshot(
    enable_custom_integrations: None,  # noqa: ARG001
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_printer_network: MagicMock,
    camera: FakeSingleClientCamera,
) -> None:
    """A still image comes from the camera stream."""
    _entry_id, entity_id = await setup_camera(hass, mock_printer_network, camera)
    client = await hass_client()

    response = await client.get(f"/api/camera_proxy/{entity_id}")
    assert response.status == 200  # noqa: PLR2004
    body = await response.read()
    assert body.startswith(SOI)
    assert body.endswith(EOI)


async def test_live_viewers_and_snapshots_share_one_connection(
    enable_custom_integrations: None,  # noqa: ARG001
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_printer_network: MagicMock,
    camera: FakeSingleClientCamera,
) -> None:
    """Two live viewers and repeated snapshots use one camera connection."""
    entry_id, entity_id = await setup_camera(hass, mock_printer_network, camera)
    client = await hass_client()

    viewer1 = await client.get(f"/api/camera_proxy_stream/{entity_id}")
    viewer2 = await client.get(f"/api/camera_proxy_stream/{entity_id}")
    try:
        for viewer in (viewer1, viewer2):
            assert viewer.status == 200  # noqa: PLR2004
            for _ in range(3):
                assert SOI in await viewer.content.readuntil(EOI)
        for _ in range(3):
            snapshot = await client.get(f"/api/camera_proxy/{entity_id}")
            assert snapshot.status == 200  # noqa: PLR2004
            assert (await snapshot.read()).startswith(SOI)
        assert camera.connections == 1
        assert camera.rejected == 0
    finally:
        viewer1.close()
        viewer2.close()

    # Unloading closes the shared connection, freeing the printer's camera.
    assert await hass.config_entries.async_unload(entry_id)
    await hass.async_block_till_done()
    await wait_until(lambda: camera.active == 0)


async def test_busy_camera_snapshot_fails_cleanly(
    enable_custom_integrations: None,  # noqa: ARG001
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_printer_network: MagicMock,
    camera: FakeSingleClientCamera,
) -> None:
    """With another app holding the camera, a snapshot errors instead of hanging."""
    _entry_id, entity_id = await setup_camera(hass, mock_printer_network, camera)
    client = await hass_client()

    other_app = await client.session.get(camera.url)
    try:
        await other_app.content.readuntil(EOI)
        response = await client.get(f"/api/camera_proxy/{entity_id}")
        assert response.status >= 500  # noqa: PLR2004
    finally:
        other_app.close()


async def test_unload_stops_stream(
    enable_custom_integrations: None,  # noqa: ARG001
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_printer_network: MagicMock,
    camera: FakeSingleClientCamera,
) -> None:
    """Unloading while someone is watching ends their stream and frees the camera."""
    entry_id, entity_id = await setup_camera(hass, mock_printer_network, camera)
    client = await hass_client()

    viewer = await client.get(f"/api/camera_proxy_stream/{entity_id}")
    try:
        assert SOI in await viewer.content.readuntil(EOI)
        assert await hass.config_entries.async_unload(entry_id)
        await hass.async_block_till_done()
        await wait_until(lambda: camera.active == 0)
        # The viewer's stream ends rather than hanging.
        async with asyncio.timeout(STREAM_END_LIMIT):
            await viewer.content.read()
    finally:
        viewer.close()
