"""Tests for the shared MJPEG broadcaster."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

import aiohttp
import pytest

from custom_components.flashforge import mjpeg
from custom_components.flashforge.mjpeg import (
    EOI,
    SOI,
    MjpegBroadcaster,
    MjpegFrameParser,
)

from .fake_camera import FakeSingleClientCamera, jpeg, jpeg_with_thumbnail

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

FAST_RETRY = (0.05,)
GRACE = 0.3
MIN_RECONNECTS = 3  # 8 frames at 3 frames per connection
FAST_FAIL = 2.0  # seconds within which a busy-camera snapshot must give up
MAX_RECONNECT_TIME = 4.0  # 15 frames with 5 prompt reconnects


@pytest.fixture
async def camera(socket_enabled: None) -> AsyncIterator[FakeSingleClientCamera]:  # noqa: ARG001
    """Run a fake single-client camera on localhost."""
    cam = FakeSingleClientCamera()
    await cam.start()
    yield cam
    await cam.close()


@pytest.fixture
async def session(socket_enabled: None) -> AsyncIterator[aiohttp.ClientSession]:  # noqa: ARG001
    """Provide a client session."""
    async with aiohttp.ClientSession() as client_session:
        yield client_session


@pytest.fixture
async def broadcaster_factory(
    session: aiohttp.ClientSession, camera: FakeSingleClientCamera
) -> AsyncIterator[Callable[..., MjpegBroadcaster]]:
    """Create broadcasters for the fake camera and stop them after the test."""
    created: list[MjpegBroadcaster] = []

    def factory(**kwargs: float | tuple[float, ...]) -> MjpegBroadcaster:
        kwargs.setdefault("grace", GRACE)
        kwargs.setdefault("retry_delays", FAST_RETRY)
        broadcaster = MjpegBroadcaster(session, camera.url, **kwargs)
        created.append(broadcaster)
        return broadcaster

    yield factory
    for broadcaster in created:
        await broadcaster.async_stop()


async def wait_until(predicate: Callable[[], bool], limit: float = 3.0) -> None:
    """Poll until predicate() is true."""
    async with asyncio.timeout(limit):
        while not predicate():  # noqa: ASYNC110
            await asyncio.sleep(0.02)


# ---------------------------------------------------------------- parser


def test_parser_handles_markers_split_across_chunks() -> None:
    """Frames are found even when fed one byte at a time."""
    data = b"junk" + jpeg(1) + b"\r\n--b\r\n" + jpeg(2) + b"trailing"
    parser = MjpegFrameParser()
    frames = [frame for byte in data for frame in parser.feed(bytes([byte]))]
    assert frames == [jpeg(1), jpeg(2)]


def test_parser_handles_multiple_frames_in_one_chunk() -> None:
    """Several frames in a single chunk all come out, in order."""
    parser = MjpegFrameParser()
    assert parser.feed(jpeg(1) + jpeg(2) + jpeg(3)) == [jpeg(1), jpeg(2), jpeg(3)]


@pytest.mark.parametrize("chunk_size", [1, 7, 4096])
def test_parser_keeps_frames_with_embedded_thumbnail(chunk_size: int) -> None:
    """An EXIF thumbnail's own end marker does not cut the frame short."""
    data = jpeg_with_thumbnail(1) + b"\r\n" + jpeg_with_thumbnail(2)
    parser = MjpegFrameParser()
    frames = [
        frame
        for i in range(0, len(data), chunk_size)
        for frame in parser.feed(data[i : i + chunk_size])
    ]
    assert frames == [jpeg_with_thumbnail(1), jpeg_with_thumbnail(2)]


def test_parser_falls_back_for_malformed_headers() -> None:
    """Data that is not a well-formed JPEG header still splits on the markers."""
    parser = MjpegFrameParser()
    frame = SOI + b"not-a-segment" + EOI
    assert parser.feed(frame + frame) == [frame, frame]


def test_parser_resyncs_after_oversized_frame() -> None:
    """A frame that never ends is dropped once it exceeds the size limit."""
    parser = MjpegFrameParser(max_frame_bytes=100)
    assert parser.feed(SOI + b"x" * 200) == []
    assert parser.feed(jpeg(5)) == [jpeg(5)]


# ----------------------------------------------------------- broadcaster


async def test_viewers_share_one_connection(
    camera: FakeSingleClientCamera, broadcaster_factory: Callable[..., MjpegBroadcaster]
) -> None:
    """Three viewers get frames while the camera sees one connection."""
    broadcaster = broadcaster_factory()
    async with (
        broadcaster.subscribe(),
        broadcaster.subscribe(),
        broadcaster.subscribe(),
    ):
        first = await broadcaster.async_next_frame(0, wait=3)
        assert first is not None
        newer = await broadcaster.async_next_frame(first[0], wait=3)
        assert newer is not None
    assert camera.connections == 1
    assert camera.rejected == 0
    assert broadcaster.connection_attempts == 1


async def test_connection_closes_after_grace_and_frees_camera(
    camera: FakeSingleClientCamera,
    broadcaster_factory: Callable[..., MjpegBroadcaster],
    session: aiohttp.ClientSession,
) -> None:
    """After the last viewer leaves, the camera slot is released."""
    broadcaster = broadcaster_factory()
    async with broadcaster.subscribe():
        assert await broadcaster.async_next_frame(0, wait=3) is not None
        assert camera.active == 1
    await wait_until(lambda: camera.active == 0 and not broadcaster.is_running)

    # Another app (e.g. the printer's own app) can now use the camera.
    async with session.get(camera.url) as response:
        assert SOI in await response.content.readuntil(EOI)
    assert camera.rejected == 0


async def test_stalled_camera_released_after_grace(
    camera: FakeSingleClientCamera, broadcaster_factory: Callable[..., MjpegBroadcaster]
) -> None:
    """The slot is freed after the grace period even if the camera goes silent."""
    camera.stall_after = 1
    broadcaster = broadcaster_factory()
    async with broadcaster.subscribe():
        assert await broadcaster.async_next_frame(0, wait=3) is not None
    # Well under the 10 s socket read timeout.
    await wait_until(lambda: camera.active == 0, limit=GRACE + 1.5)


async def test_resubscribe_within_grace_reuses_connection(
    camera: FakeSingleClientCamera, broadcaster_factory: Callable[..., MjpegBroadcaster]
) -> None:
    """A viewer arriving within the grace period reuses the open connection."""
    broadcaster = broadcaster_factory(grace=1.0)
    async with broadcaster.subscribe():
        assert await broadcaster.async_next_frame(0, wait=3) is not None
    await asyncio.sleep(0.2)
    async with broadcaster.subscribe():
        seq = broadcaster.start_sequence()
        assert await broadcaster.async_next_frame(seq, wait=3) is not None
    assert camera.connections == 1


async def test_resubscribe_after_release_reconnects(
    camera: FakeSingleClientCamera, broadcaster_factory: Callable[..., MjpegBroadcaster]
) -> None:
    """A viewer arriving after the connection closed gets a new one."""
    broadcaster = broadcaster_factory()
    for _ in range(3):
        async with broadcaster.subscribe():
            seq = broadcaster.start_sequence()
            assert await broadcaster.async_next_frame(seq, wait=3) is not None
        await wait_until(lambda: camera.active == 0)
    assert camera.rejected == 0


async def test_snapshot_reuses_live_stream(
    camera: FakeSingleClientCamera, broadcaster_factory: Callable[..., MjpegBroadcaster]
) -> None:
    """A still image taken during live viewing does not open a connection."""
    broadcaster = broadcaster_factory()
    async with broadcaster.subscribe():
        assert await broadcaster.async_next_frame(0, wait=3) is not None
        for _ in range(5):
            image = await broadcaster.async_get_image()
            assert image is not None
            assert image.startswith(SOI)
            assert image.endswith(EOI)
    assert camera.connections == 1


async def test_snapshot_when_idle_connects_briefly(
    camera: FakeSingleClientCamera, broadcaster_factory: Callable[..., MjpegBroadcaster]
) -> None:
    """A snapshot with no viewers opens the stream, then lets it go."""
    broadcaster = broadcaster_factory()
    image = await broadcaster.async_get_image()
    assert image is not None
    assert image.startswith(SOI)
    await wait_until(lambda: camera.active == 0)


async def test_busy_camera_fails_fast_then_recovers(
    camera: FakeSingleClientCamera,
    broadcaster_factory: Callable[..., MjpegBroadcaster],
    session: aiohttp.ClientSession,
) -> None:
    """While another app holds the camera, snapshots fail fast, then recover."""
    broadcaster = broadcaster_factory(retry_delays=(5.0,))
    other_app = await session.get(camera.url)  # takes the camera's only slot
    try:
        await other_app.content.readuntil(EOI)
        started = time.monotonic()
        assert await broadcaster.async_get_image(wait=5) is None
        assert time.monotonic() - started < FAST_FAIL
        assert camera.rejected >= 1
    finally:
        other_app.close()
    await wait_until(lambda: camera.active == 0)
    # The next request is served straight away, not after the 5 s back-off.
    started = time.monotonic()
    image = await broadcaster.async_get_image(wait=3)
    assert time.monotonic() - started < FAST_FAIL
    assert image is not None


async def test_viewer_joining_during_backoff_is_served_promptly(
    camera: FakeSingleClientCamera,
    broadcaster_factory: Callable[..., MjpegBroadcaster],
    session: aiohttp.ClientSession,
) -> None:
    """A back-off wait is cut short when a new viewer arrives."""
    broadcaster = broadcaster_factory(retry_delays=(30.0,), grace=60)
    other_app = await session.get(camera.url)
    try:
        await other_app.content.readuntil(EOI)
        assert await broadcaster.async_get_image(wait=2) is None  # now backing off
    finally:
        other_app.close()
    await wait_until(lambda: camera.active == 0)
    async with broadcaster.subscribe():
        result = await broadcaster.async_next_frame(broadcaster.start_sequence(), 3)
        assert result is not None


async def test_stale_frame_not_returned_when_camera_fails(
    camera: FakeSingleClientCamera,
    broadcaster_factory: Callable[..., MjpegBroadcaster],
    session: aiohttp.ClientSession,
) -> None:
    """An old cached frame is not shown as if it were current."""
    broadcaster = broadcaster_factory(grace=0.05)
    assert await broadcaster.async_get_image() is not None
    await wait_until(lambda: camera.active == 0)
    other_app = await session.get(camera.url)
    try:
        await other_app.content.readuntil(EOI)
        # A recent frame is an acceptable fallback...
        assert await broadcaster.async_get_image(wait=1, max_age=0) is not None
        # ...but not once it is older than the stale limit.
        image = await broadcaster.async_get_image(wait=1, max_age=0, stale_limit=0)
        assert image is None
    finally:
        other_app.close()


async def test_reconnects_when_camera_drops_stream(
    camera: FakeSingleClientCamera, broadcaster_factory: Callable[..., MjpegBroadcaster]
) -> None:
    """If the camera ends the stream, viewers keep getting frames."""
    camera.frames_per_connection = 3
    broadcaster = broadcaster_factory()
    async with broadcaster.subscribe():
        seq = 0
        for _ in range(8):
            result = await broadcaster.async_next_frame(seq, wait=3)
            assert result is not None
            seq = result[0]
    assert camera.connections >= MIN_RECONNECTS
    assert camera.rejected == 0


@pytest.mark.parametrize("drop_abruptly", [False, True])
async def test_dropped_streams_reconnect_without_backoff(
    camera: FakeSingleClientCamera,
    broadcaster_factory: Callable[..., MjpegBroadcaster],
    caplog: pytest.LogCaptureFixture,
    drop_abruptly: bool,  # noqa: FBT001
) -> None:
    """A working stream that drops is reconnected promptly, not treated as busy."""
    camera.frames_per_connection = 3
    camera.drop_abruptly = drop_abruptly
    broadcaster = broadcaster_factory(retry_delays=(1.0, 2.0, 5.0))
    started = time.monotonic()
    with caplog.at_level(logging.DEBUG):
        async with broadcaster.subscribe():
            seq = 0
            for _ in range(15):  # five drops and reconnects
                result = await broadcaster.async_next_frame(seq, wait=3)
                assert result is not None
                seq = result[0]
    assert time.monotonic() - started < MAX_RECONNECT_TIME
    assert "busy" not in caplog.text
    assert "retrying camera" not in caplog.text


async def test_frames_without_part_headers(
    camera: FakeSingleClientCamera, broadcaster_factory: Callable[..., MjpegBroadcaster]
) -> None:
    """Cameras that send bare JPEGs (no multipart headers) still work."""
    camera.with_headers = False
    camera.make_frame = jpeg_with_thumbnail
    image = await broadcaster_factory().async_get_image()
    assert image is not None
    assert image == jpeg_with_thumbnail(1)


async def test_unexpected_error_is_logged_and_retried(
    camera: FakeSingleClientCamera,
    broadcaster_factory: Callable[..., MjpegBroadcaster],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A surprise exception in the reader is logged and does not kill it."""
    original_feed = MjpegFrameParser.feed
    calls = {"n": 0}

    def flaky_feed(self: MjpegFrameParser, data: bytes) -> list[bytes]:
        calls["n"] += 1
        if calls["n"] == 1:
            msg = "boom"
            raise ValueError(msg)
        return original_feed(self, data)

    monkeypatch.setattr(mjpeg.MjpegFrameParser, "feed", flaky_feed)
    broadcaster = broadcaster_factory()
    with caplog.at_level(logging.ERROR):
        async with broadcaster.subscribe():
            assert await broadcaster.async_next_frame(0, wait=3) is not None
    assert "unexpected camera stream error" in caplog.text
    assert camera.connections >= 2  # noqa: PLR2004


async def test_cancelled_waiter_leaves_broadcaster_usable(
    broadcaster_factory: Callable[..., MjpegBroadcaster],
) -> None:
    """A viewer whose request is cancelled mid-wait does not break the others."""
    broadcaster = broadcaster_factory()
    async with broadcaster.subscribe():
        first = await broadcaster.async_next_frame(0, wait=3)
        assert first is not None
        waiter = asyncio.create_task(broadcaster.async_next_frame(first[0] + 1000, 10))
        await asyncio.sleep(0.05)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert await broadcaster.async_next_frame(first[0], wait=3) is not None


async def test_stop_releases_camera_and_waiters(
    camera: FakeSingleClientCamera, broadcaster_factory: Callable[..., MjpegBroadcaster]
) -> None:
    """Stopping closes the connection and wakes anyone waiting for a frame."""
    broadcaster = broadcaster_factory(grace=60)
    async with broadcaster.subscribe():
        first = await broadcaster.async_next_frame(0, wait=3)
        assert first is not None
        waiter = asyncio.create_task(broadcaster.async_next_frame(first[0] + 1000, 10))
        await asyncio.sleep(0.05)
        await broadcaster.async_stop()
        assert await waiter is None
    await wait_until(lambda: camera.active == 0)
    assert not broadcaster.is_running
    assert broadcaster.closed
    with pytest.raises(RuntimeError):
        async with broadcaster.subscribe():
            pass


async def test_slow_viewer_does_not_hold_up_others(
    broadcaster_factory: Callable[..., MjpegBroadcaster],
) -> None:
    """A viewer that stops reading does not block the others."""
    broadcaster = broadcaster_factory()
    async with broadcaster.subscribe(), broadcaster.subscribe():
        # The first viewer never asks for frames; the second still gets many.
        seq = 0
        for _ in range(10):
            result = await broadcaster.async_next_frame(seq, wait=3)
            assert result is not None
            seq = result[0]
