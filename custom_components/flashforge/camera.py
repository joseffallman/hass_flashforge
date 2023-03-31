"""FlashForge camera integration."""
from __future__ import annotations

from contextlib import closing
import logging

import requests

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import (
    async_aiohttp_proxy_web,
    async_get_clientsession,
)
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .data_update_coordinator import FlashForgeDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


def extract_image_from_mjpeg(stream):
    """Take in a MJPEG stream object, return the jpg from it."""
    data = b""

    for chunk in stream:
        data += chunk
        jpg_end = data.find(b"\xff\xd9")

        if jpg_end == -1:
            continue

        jpg_start = data.find(b"\xff\xd8")

        if jpg_start == -1:
            continue

        return data[jpg_start : jpg_end + 2]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the available FlashForge camera platform."""
    coordinator: FlashForgeDataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ]
    mjpeg_url = await coordinator.printer.network.getCameraStream()
    async_add_entities([FlashForgeCamera(coordinator, mjpeg_url)])


class FlashForgeCamera(Camera):
    """FlashForge camera object."""

    def __init__(
        self, coordinator: FlashForgeDataUpdateCoordinator, mjpeg_url: str
    ) -> None:
        """Initialize."""
        super().__init__()
        self._mjpeg_url = mjpeg_url
        self.coordinator = coordinator

        self._device_id = coordinator.config_entry.unique_id
        self._attr_device_info = coordinator.device_info
        self._attr_name = f"{coordinator.printer.machine_name} Camera"
        self._attr_unique_id = f"{coordinator.config_entry.unique_id}_camera"
        self._attr_is_streaming = True

    def camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image response from the camera."""

        if not self.available:
            self._attr_is_streaming = False
            _LOGGER.warning(
                "Unable to get still image when %s camera is offline",
                self.name,
            )
            return None

        try:
            req = requests.get(self._mjpeg_url, stream=True, timeout=10)

            with closing(req) as response:
                return extract_image_from_mjpeg(response.iter_content(102400))
        except requests.exceptions.ConnectionError:
            self._attr_is_streaming = False
            return None

    async def handle_async_mjpeg_stream(self, request):
        """Generate an HTTP MJPEG stream from the camera."""

        if not self.available:
            self._attr_is_streaming = False
            _LOGGER.warning(
                "Attempt to stream when %s camera is offline",
                self.name,
            )
            return None

        # connect to stream
        websession = async_get_clientsession(self.hass)
        stream_coro = websession.get(self._mjpeg_url)

        return await async_aiohttp_proxy_web(self.hass, request, stream_coro)

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.coordinator.last_update_success
