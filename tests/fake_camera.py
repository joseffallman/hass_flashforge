"""A fake MJPEG camera that, like the Adventurer 5M, serves one client at a time."""

from __future__ import annotations

import asyncio

from aiohttp import web
from aiohttp.test_utils import TestServer

from custom_components.flashforge.mjpeg import EOI, SOI

BOUNDARY = "boundarydonotcross"


def jpeg(n: int) -> bytes:
    """Return a small, structurally valid JPEG-like frame."""
    sos = b"\xff\xda\x00\x08" + b"\x01\x01\x00\x00\x3f\x00"
    return SOI + sos + f"scan-data-{n}".encode() + EOI


def jpeg_with_thumbnail(n: int) -> bytes:
    """Return a frame whose EXIF segment holds a thumbnail with its own markers."""
    thumbnail = SOI + b"thumb" + EOI
    payload = b"Exif\x00\x00" + thumbnail
    app1 = b"\xff\xe1" + (len(payload) + 2).to_bytes(2) + payload
    sos = b"\xff\xda\x00\x08" + b"\x01\x01\x00\x00\x3f\x00"
    return SOI + app1 + sos + f"scan-data-{n}".encode() + EOI


class FakeSingleClientCamera:
    """MJPEG server that drops any second client, like the printer."""

    def __init__(self) -> None:
        """Initialize."""
        self.fps = 25.0
        self.frames_per_connection: int | None = None
        self.stall_after: int | None = None  # stop sending (but stay open)
        self.drop_abruptly = False  # end streams by cutting the connection
        self.with_headers = True
        self.make_frame = jpeg
        self.active = 0
        self.connections = 0
        self.rejected = 0
        self.counter = 0
        self.server: TestServer | None = None

    @property
    def url(self) -> str:
        """Return the stream URL."""
        assert self.server is not None  # noqa: S101
        return str(self.server.make_url("/?action=stream"))

    async def start(self) -> None:
        """Start serving on a free localhost port."""
        app = web.Application()
        app.router.add_get("/", self.handle)
        self.server = TestServer(app, host="127.0.0.1")
        await self.server.start_server()

    async def close(self) -> None:
        """Stop the server."""
        if self.server is not None:
            await self.server.close()

    async def handle(self, request: web.Request) -> web.StreamResponse:
        """Stream frames, or drop the connection if another client is watching."""
        self.connections += 1
        if self.active:
            self.rejected += 1
            if request.transport is not None:
                request.transport.close()
            return web.Response(status=503)

        self.active += 1
        response = web.StreamResponse()
        response.content_type = f"multipart/x-mixed-replace;boundary={BOUNDARY}"
        try:
            await response.prepare(request)
            sent = 0
            while (
                self.frames_per_connection is None or sent < self.frames_per_connection
            ):
                if self.stall_after is not None and sent >= self.stall_after:
                    await asyncio.sleep(3600)
                self.counter += 1
                frame = self.make_frame(self.counter)
                header = (
                    f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                    f"Content-Length: {len(frame)}\r\n\r\n"
                    if self.with_headers
                    else "garbage-between-frames"
                ).encode()
                await response.write(header + frame + b"\r\n")
                sent += 1
                await asyncio.sleep(1 / self.fps)
            if self.drop_abruptly and request.transport is not None:
                request.transport.close()
        except ConnectionError:
            pass
        finally:
            self.active -= 1
        return response
