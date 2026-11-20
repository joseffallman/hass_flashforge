"""FlashForge camera integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.camera import Camera, async_get_still_stream
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN
from .mjpeg import MjpegBroadcaster

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Coroutine

    from aiohttp import web
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import Event, HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .data_update_coordinator import FlashForgeDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# A live viewer that receives no new frame for this long has its stream ended,
# so a camera that has gone quiet does not leave the viewer hanging.
STREAM_FRAME_TIMEOUT = 15.0


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
    """
    FlashForge camera object.

    The printer's camera serves only one client at a time, so every live
    viewer and snapshot shares a single upstream connection through
    ``MjpegBroadcaster`` instead of opening its own.
    """

    def __init__(
        self, coordinator: FlashForgeDataUpdateCoordinator, mjpeg_url: str | None
    ) -> None:
        """Initialize."""
        super().__init__()
        self._mjpeg_url = mjpeg_url
        self.coordinator = coordinator
        self._broadcaster: MjpegBroadcaster | None = None

        self._device_id = coordinator.config_entry.unique_id
        self._attr_device_info = coordinator.device_info
        self._attr_name = "Camera"
        self._attr_unique_id = f"{coordinator.config_entry.unique_id}_camera"
        self._attr_is_streaming = True

    async def async_added_to_hass(self) -> None:
        """Create the shared stream once the entity is part of Home Assistant."""
        await super().async_added_to_hass()
        if not self._mjpeg_url:
            _LOGGER.warning("%s has no camera stream URL", self.name)
            return

        entry = self.platform.config_entry

        def create_task(
            coro: Coroutine[Any, Any, None], name: str
        ) -> asyncio.Task[None]:
            # Background tasks are cancelled automatically when the entry unloads.
            return entry.async_create_background_task(self.hass, coro, name)

        self._broadcaster = MjpegBroadcaster(
            async_get_clientsession(self.hass),
            self._mjpeg_url,
            name=self.entity_id,
            task_factory=create_task,
        )
        # Close the camera connection when Home Assistant shuts down, so a
        # viewer that is still connected cannot reopen it.
        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_HOMEASSISTANT_STOP, self._async_on_stop)
        )

    async def _async_on_stop(self, _event: Event) -> None:
        if self._broadcaster is not None:
            await self._broadcaster.async_stop()

    async def async_will_remove_from_hass(self) -> None:
        """Close the shared stream so the printer's camera slot is freed."""
        if self._broadcaster is not None:
            await self._broadcaster.async_stop()
            self._broadcaster = None
        await super().async_will_remove_from_hass()

    async def async_camera_image(
        self,
        width: int | None = None,  # noqa: ARG002
        height: int | None = None,  # noqa: ARG002
    ) -> bytes | None:
        """Return a still image, taken from the shared stream."""
        if self._broadcaster is None or self._broadcaster.closed:
            return None
        if not self.available:
            _LOGGER.debug("Unable to get still image while %s is offline", self.name)
            return None
        return await self._broadcaster.async_get_image()

    async def handle_async_mjpeg_stream(
        self, request: web.Request
    ) -> web.StreamResponse | None:
        """Serve a live MJPEG stream from the shared upstream connection."""
        broadcaster = self._broadcaster
        if broadcaster is None or broadcaster.closed:
            return None
        if not self.available:
            _LOGGER.warning("Attempt to stream when %s camera is offline", self.name)
            return None

        async with broadcaster.subscribe():
            last_seq = broadcaster.start_sequence()

            async def next_image() -> bytes | None:
                nonlocal last_seq
                result = await broadcaster.async_next_frame(
                    last_seq, STREAM_FRAME_TIMEOUT
                )
                if result is None:
                    return None  # ends this viewer's stream
                last_seq, frame = result
                return frame

            return await async_get_still_stream(request, next_image, "image/jpeg", 0)

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.coordinator.last_update_success
