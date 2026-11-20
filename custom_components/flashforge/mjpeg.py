"""
Share one MJPEG camera connection between many viewers.

Some printer cameras (for example the FlashForge Adventurer 5M on stock
firmware) serve their MJPEG stream to only one client at a time: a second
connection is closed straight away. Home Assistant opens a new upstream
connection for every live viewer and every snapshot, so viewers end up
fighting over that single slot and the picture freezes or never loads.

``MjpegBroadcaster`` keeps at most one upstream connection open while anyone
is watching, hands the newest frame to every viewer and closes the connection
a few seconds after the last viewer leaves, so other apps (the printer's own
app, a slicer) can use the camera again.

This module deliberately has no Home Assistant imports so it can be tested on
its own.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import aiohttp

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Coroutine

    TaskFactory = Callable[[Coroutine[Any, Any, None], str], asyncio.Task[None]]

_LOGGER = logging.getLogger(__name__)

SOI = b"\xff\xd8"  # JPEG start-of-image marker
EOI = b"\xff\xd9"  # JPEG end-of-image marker
_MARKER = 0xFF  # every JPEG marker starts with this byte
_SOS = 0xDA  # start-of-scan: compressed image data follows
_EOI = 0xD9
# Markers that stand alone, with no length field after them.
_STANDALONE_MARKERS = frozenset({0x01, *range(0xD0, 0xD8)})
_MIN_SEGMENT_LENGTH = 2  # a segment length counts its own two bytes
# A stuffed zero or a second start marker cannot begin a header segment.
_NOT_HEADER_MARKERS = frozenset({0x00, 0xD8})

MAX_FRAME_BYTES = 5 * 1024 * 1024
DEFAULT_GRACE = 4.0  # seconds the upstream stays open after the last viewer leaves
CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 10.0  # shorter than a viewer's patience, so one reconnect fits
RETRY_DELAYS = (1.0, 2.0, 5.0, 10.0, 15.0)
RECONNECT_PAUSE = 0.2  # pause before reconnecting after a stream that did work

SNAPSHOT_MAX_AGE = 3.0  # reuse a frame this fresh instead of waiting for a new one
SNAPSHOT_TIMEOUT = 8.0  # Home Assistant gives up on a still image after 10 s
SNAPSHOT_STALE_LIMIT = 45.0  # older frames are not shown when the camera fails
LIVE_START_MAX_AGE = 2.0  # a new live viewer starts with the cached frame if fresher


class MjpegFrameParser:
    """
    Split a byte stream into complete JPEG frames.

    Does not rely on multipart headers. After the start-of-image marker it
    walks the JPEG header segments by their declared lengths, so an embedded
    EXIF thumbnail (which has its own start and end markers) cannot end the
    frame early. Only once the compressed scan data begins does it look for
    the end-of-image marker, which cannot occur inside that data. Malformed
    headers fall back to a plain search for the end marker.
    """

    def __init__(self, max_frame_bytes: int = MAX_FRAME_BYTES) -> None:
        """Initialize the parser."""
        self._buffer = bytearray()
        self._max_frame_bytes = max_frame_bytes
        self._reset()

    def _reset(self) -> None:
        self._in_frame = False
        self._pos = 0  # next header segment to read, while walking headers
        self._in_scan = False  # past the headers, searching for the end marker
        self._scan_from = 0

    def feed(self, data: bytes) -> list[bytes]:
        """Add received bytes and return every frame they complete."""
        self._buffer += data
        frames: list[bytes] = []
        while True:
            if not self._in_frame and not self._find_start():
                return frames
            end = self._find_end()
            if end is None:
                if len(self._buffer) > self._max_frame_bytes:
                    _LOGGER.debug(
                        "MJPEG frame larger than %s bytes, resynchronising",
                        self._max_frame_bytes,
                    )
                    self._buffer.clear()
                    self._reset()
                return frames
            frames.append(bytes(self._buffer[:end]))
            del self._buffer[:end]
            self._reset()

    def _find_start(self) -> bool:
        start = self._buffer.find(SOI)
        if start == -1:
            # Drop the junk, but keep a trailing 0xFF in case it is the first
            # half of a start marker split across chunks.
            keep = 1 if self._buffer.endswith(b"\xff") else 0
            del self._buffer[: len(self._buffer) - keep]
            return False
        del self._buffer[:start]
        self._in_frame = True
        self._pos = len(SOI)
        return True

    def _find_end(self) -> int | None:
        """Return the frame length once the whole frame has arrived."""
        buf = self._buffer
        while not self._in_scan:
            if len(buf) < self._pos + 2:
                return None
            if buf[self._pos] != _MARKER:  # not a marker: malformed, search instead
                self._start_scan(self._pos)
                break
            marker = buf[self._pos + 1]
            if marker == _MARKER:  # fill byte before a marker
                self._pos += 1
            elif marker in _NOT_HEADER_MARKERS:  # malformed, search instead
                self._start_scan(self._pos)
                break
            elif marker == _EOI:
                return self._pos + 2
            elif marker in _STANDALONE_MARKERS:
                self._pos += 2
            else:
                if len(buf) < self._pos + 4:
                    return None
                length = int.from_bytes(buf[self._pos + 2 : self._pos + 4])
                if length < _MIN_SEGMENT_LENGTH:
                    self._start_scan(self._pos)
                    break
                if marker == _SOS:
                    self._start_scan(self._pos + 2 + length)
                    break
                self._pos += 2 + length

        end = buf.find(EOI, self._scan_from)
        if end == -1:
            # Resume one byte back next time in case the marker is split.
            self._scan_from = max(self._scan_from, len(buf) - 1)
            return None
        return end + len(EOI)

    def _start_scan(self, pos: int) -> None:
        self._in_scan = True
        self._scan_from = pos


def _default_task_factory(
    coro: Coroutine[Any, Any, None], name: str
) -> asyncio.Task[None]:
    """Create a plain asyncio task (used outside Home Assistant, e.g. tests)."""
    return asyncio.get_running_loop().create_task(coro, name=name)


class MjpegBroadcaster:
    """Fan one upstream MJPEG connection out to any number of viewers."""

    def __init__(  # noqa: PLR0913
        self,
        session: aiohttp.ClientSession,
        url: str,
        *,
        name: str = "camera",
        grace: float = DEFAULT_GRACE,
        retry_delays: tuple[float, ...] = RETRY_DELAYS,
        task_factory: TaskFactory | None = None,
    ) -> None:
        """Initialize the broadcaster. Nothing connects until someone subscribes."""
        self._session = session
        self._url = url
        self._name = name
        self._grace = grace
        self._retry_delays = retry_delays or (1.0,)
        self._task_factory = task_factory or _default_task_factory

        self._subscribers = 0
        self._task: asyncio.Task[None] | None = None  # the current reader
        self._readers: set[asyncio.Task[None]] = set()  # incl. ones still closing
        self._idle_handle: asyncio.TimerHandle | None = None
        self._wake = asyncio.Event()
        self._closed = False
        self._stream_ok = False  # the current connection has delivered a frame
        self._busy_logged = False
        self._error_logged = False

        self._condition = asyncio.Condition()
        self._frame: bytes | None = None
        self._frame_time = 0.0
        self._seq = 0
        self._errors = 0

        self.connection_attempts = 0  # diagnostics

    @property
    def subscribers(self) -> int:
        """Return the number of current viewers."""
        return self._subscribers

    @property
    def closed(self) -> bool:
        """Return True once the broadcaster has been stopped for good."""
        return self._closed

    @property
    def is_running(self) -> bool:
        """Return True while the upstream reader is active."""
        return self._task is not None and not self._task.done()

    @property
    def latest_frame(self) -> bytes | None:
        """Return the newest frame received, if any."""
        return self._frame

    def frame_age(self) -> float | None:
        """Return the age of the newest frame in seconds, or None."""
        if self._frame is None:
            return None
        return time.monotonic() - self._frame_time

    def start_sequence(self) -> int:
        """
        Return the sequence number a new live viewer should start after.

        A fresh cached frame is handed out straight away so the picture appears
        immediately; a stale one is skipped.
        """
        age = self.frame_age()
        if age is not None and age <= LIVE_START_MAX_AGE:
            return self._seq - 1
        return self._seq

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[MjpegBroadcaster]:
        """Hold the upstream connection open for as long as the context lasts."""
        if self._closed:
            msg = "MJPEG broadcaster has been stopped"
            raise RuntimeError(msg)
        self._subscribers += 1
        try:
            if self._subscribers == 1:
                self._cancel_idle_timer()
                self._wake.set()  # cut short a retry wait: someone is watching
            task = self._task
            if task is None or task.done() or task.cancelling():
                self._start_reader()
            yield self
        finally:
            self._subscribers -= 1
            if self._subscribers == 0 and not self._closed:
                self._idle_handle = asyncio.get_running_loop().call_later(
                    self._grace, self._stop_if_idle
                )

    async def async_next_frame(
        self, after: int, wait: float, *, stop_on_error: bool = False
    ) -> tuple[int, bytes] | None:
        """
        Wait up to ``wait`` seconds for a frame newer than sequence ``after``.

        Returns ``(sequence, frame)``, or None on timeout, when the broadcaster
        stops, or (with ``stop_on_error``) when the upstream connection fails.
        """
        errors_at_start = self._errors

        def ready() -> bool:
            if self._closed or self._seq > after:
                return True
            return stop_on_error and self._errors != errors_at_start

        async with self._condition:
            try:
                await asyncio.wait_for(self._condition.wait_for(ready), wait)
            except TimeoutError:
                return None
            if self._seq > after and self._frame is not None:
                return self._seq, self._frame
            return None

    async def async_get_image(
        self,
        *,
        max_age: float = SNAPSHOT_MAX_AGE,
        wait: float = SNAPSHOT_TIMEOUT,
        stale_limit: float = SNAPSHOT_STALE_LIMIT,
    ) -> bytes | None:
        """Return a still image, reusing the shared connection or a recent frame."""
        age = self.frame_age()
        if age is not None and age <= max_age:
            return self._frame

        async with self.subscribe():
            result = await self.async_next_frame(self._seq, wait, stop_on_error=True)
        if result is not None:
            return result[1]

        # The camera is busy or unreachable: show a recent frame rather than
        # nothing, but never a stale one that would misrepresent the printer.
        age = self.frame_age()
        if age is not None and age <= stale_limit:
            return self._frame
        return None

    async def async_stop(self) -> None:
        """Close the upstream connection for good and release waiting viewers."""
        self._closed = True
        self._cancel_idle_timer()
        self._task = None
        async with self._condition:
            self._condition.notify_all()
        readers = list(self._readers)
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)

    def _cancel_idle_timer(self) -> None:
        if self._idle_handle is not None:
            self._idle_handle.cancel()
            self._idle_handle = None

    def _stop_if_idle(self) -> None:
        """Close the upstream connection once the grace period has passed."""
        self._idle_handle = None
        if self._subscribers == 0 and self._task is not None:
            # Cancelling the reader closes its connection, even mid-read.
            self._task.cancel()

    def _start_reader(self) -> None:
        task = self._task_factory(self._run(), f"{self._name} MJPEG reader")
        self._task = task
        self._readers.add(task)
        task.add_done_callback(self._readers.discard)

    async def _run(self) -> None:
        """Keep one upstream connection alive until cancelled."""
        current = asyncio.current_task()
        closing = [reader for reader in self._readers if reader is not current]
        if closing:
            # Let older readers release the camera's only slot first, and give
            # the camera a moment to notice before connecting again.
            await asyncio.gather(*closing, return_exceptions=True)
            await asyncio.sleep(RECONNECT_PAUSE)

        failures = 0
        while not self._closed:
            # A viewer arriving from here on triggers an immediate retry.
            self._wake.clear()
            self._stream_ok = False
            try:
                await self._read_stream()
            except (aiohttp.ClientError, OSError, TimeoutError) as err:
                _LOGGER.debug("%s: camera stream ended: %s", self._name, err)
            except Exception:
                # Keep serving viewers after a bug or an unexpected error, but
                # log the full traceback only once.
                if not self._error_logged:
                    self._error_logged = True
                    _LOGGER.exception("%s: unexpected camera stream error", self._name)
                else:
                    _LOGGER.debug(
                        "%s: unexpected camera stream error", self._name, exc_info=True
                    )

            if self._stream_ok:
                # The stream worked and then ended or dropped; reconnect promptly.
                failures = 0
                await asyncio.sleep(RECONNECT_PAUSE)
                continue

            # No frames at all: the camera is busy (another client holds its
            # only slot) or unreachable. Back off instead of hammering it, but
            # retry at once if a new viewer arrives.
            await self._signal_error()
            delay = self._retry_delays[min(failures, len(self._retry_delays) - 1)]
            failures += 1
            self._log_busy(delay)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), delay)
                failures = 0

    async def _read_stream(self) -> None:
        """Read one upstream connection until it ends or the task is cancelled."""
        timeout = aiohttp.ClientTimeout(
            total=None, sock_connect=CONNECT_TIMEOUT, sock_read=READ_TIMEOUT
        )
        self.connection_attempts += 1
        async with self._session.get(self._url, timeout=timeout) as response:
            response.raise_for_status()
            parser = MjpegFrameParser()
            async for chunk in response.content.iter_any():
                for frame in parser.feed(chunk):
                    if not self._stream_ok:
                        self._stream_ok = True
                        self._log_recovered()
                    await self._publish(frame)
        # Leaving the context closes the connection, freeing the camera's slot.

    def _log_busy(self, delay: float) -> None:
        if not self._busy_logged:
            self._busy_logged = True
            _LOGGER.info(
                "%s: camera is busy (another app may be using it) or unreachable;"
                " retrying",
                self._name,
            )
        _LOGGER.debug("%s: retrying camera in %.1f s", self._name, delay)

    def _log_recovered(self) -> None:
        if self._busy_logged:
            self._busy_logged = False
            _LOGGER.info("%s: camera stream available again", self._name)

    async def _publish(self, frame: bytes) -> None:
        async with self._condition:
            self._frame = frame
            self._frame_time = time.monotonic()
            self._seq += 1
            self._condition.notify_all()

    async def _signal_error(self) -> None:
        async with self._condition:
            self._errors += 1
            self._condition.notify_all()
