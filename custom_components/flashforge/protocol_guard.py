"""Guards shared ffpp Printer/Network access against two known failure modes.

Both are documented in detail in the project plan (docs, §3a / §3b). Short version:

1. Internal race (§3b): the coordinator's poll and any service call (pause,
   continue_print, abort, print_file, get_file_names, set LED, set temp) all touch
   the SAME ffpp ``Printer``/``Network`` object, and nothing serializes that access
   today. When a poll and a service call overlap, asyncio itself raises --
   two coroutines calling ``.read()`` on the same ``StreamReader`` at once is not
   allowed and throws ``RuntimeError('read() called while another coroutine is
   already waiting for incoming data')``. Confirmed reproducible: 7 of 12 attempts
   failed in a real stress test, including the very first overlap.
   Fix: every access goes through this module's lock.

2. External cross-talk (§3a): a second, unrelated client (e.g. FlashPrint/
   FlashPrint-MP) connected to the printer at the same time can cause the printer
   to hand back a response meant for the OTHER client's request -- e.g. asking for
   status (M119) and getting back a stale answer to someone else's control-lock
   attempt (M601) instead. This can't be prevented in code (it's a separate
   process/socket outside the integration's control) -- it can only be detected:
   every response is checked against the command that was actually sent, and a
   response that doesn't match is discarded rather than silently parsed into
   sensor data that looks fine but isn't. A real episode observed live showed
   this can also surface as outright connection failures (ConnectionResetError
   etc.), not just soft mismatches -- see note_connection_failure() below for
   why both count toward suspicion. A second real episode showed a mismatch
   whose response was a well-formed echo of a DIFFERENT known command (e.g.
   asked for M119, got back a FlashPrint M601 response) -- that's unambiguous
   proof of a second client and is treated as an immediate confirmed hit
   rather than needing to repeat, see safe_update()'s confirmed_cross_talk
   handling below.

This module intentionally has NO dependency on Home Assistant -- it only needs
``ffpp`` and the standard library -- so it can be exercised directly in a plain
asyncio script against a real printer without standing up HA at all. See
``scripts/test_protocol_guard.py`` (repo root) for that harness.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from ffpp.Printer import Printer

_LOGGER = logging.getLogger(__name__)

# Command code actually sent -> substring that MUST appear in a genuine response.
# Mirrors the "CMD <code> Received" echo every ffpp/FlashForge M-code response
# starts with (see ffpp.Network docstrings for example raw responses).
EXPECTED_ECHO: dict[str, str] = {
    "M115": "CMD M115 Received",
    "M119": "CMD M119 Received",
    "M105": "CMD M105 Received",
    "M27": "CMD M27 Received",
    "M661": "CMD M661 Received",
}


def echo_ok(sent_code: str, response: str | None) -> bool:
    """Return True if response looks like a genuine answer to sent_code."""
    if not response:
        return False
    expected = EXPECTED_ECHO.get(sent_code)
    return bool(expected and expected in response)


def looks_like_other_command(response: str | None) -> str | None:
    """If response matches a DIFFERENT known command's echo, return that code.

    Used purely for diagnostics/logging -- helps confirm "this looks like
    cross-talk with someone else's request" vs. "this is just garbage".
    """
    if not response:
        return None
    for code, echo in EXPECTED_ECHO.items():
        if echo in response:
            return code
    return None


@dataclass
class UpdateResult:
    """Outcome of one ProtocolGuard.safe_update() cycle."""

    ok: bool
    files: list[str] | None = None
    mismatched_codes: list[str] = field(default_factory=list)
    # Subset of mismatched_codes where the response wasn't just garbled --
    # it was a well-formed echo of a DIFFERENT known command (e.g. asked for
    # M119, got back "CMD M601 Received..."). This is unambiguous: there is
    # no legitimate reason our own request would get someone else's command's
    # echo, so a single one of these is treated as confirmed cross-talk
    # rather than needing to accumulate like an ambiguous signal. See
    # ProtocolGuard.safe_update() for how this feeds cross_talk_suspected.
    confirmed_cross_talk_codes: list[str] = field(default_factory=list)
    cross_talk_suspected: bool = False


class ProtocolGuard:
    """Serializes access to a shared ffpp Printer and validates responses.

    One instance per config entry, shared between the coordinator and every
    service-call handler (they should all go through safe_update()/safe_call()
    instead of touching printer/printer.network directly).
    """

    def __init__(
        self,
        printer: Printer,
        mismatch_threshold: int = 2,
        clear_threshold: int = 2,
    ) -> None:
        self.printer = printer
        self.lock = asyncio.Lock()
        self.mismatch_threshold = mismatch_threshold
        self.clear_threshold = clear_threshold
        self.cross_talk_suspected = False
        # Hysteresis via two consecutive-run counters instead of a sliding
        # window -- found live (real hardware run, §3a): a fixed-size window
        # makes RECOVERY slow and confusing, because old "problem" entries
        # only fall out once enough new entries have been appended to push
        # them past the window edge. With window_size=8 and only 2 problem
        # cycles near the start, it took until cycle 9-10 for the LAST of
        # those two entries to finally slide out, even though FlashPrint had
        # disconnected and every intervening cycle was clean -- looked from
        # the outside like detection was stuck "on" and never recovering.
        # This tracks two independent streaks instead: `consecutive_mismatches`
        # (any problem: soft mismatch, connection failure, or confirmed hit)
        # resets to 0 on any clean cycle, and `consecutive_clean` resets to 0
        # on any problem. Suspicion is raised once consecutive_mismatches
        # reaches mismatch_threshold (or immediately on a confirmed hit) and
        # only cleared once consecutive_clean reaches clear_threshold --
        # so recovery time is a fixed, predictable `clear_threshold` clean
        # cycles after the LAST problem, regardless of how long the episode
        # before it was.
        self.consecutive_mismatches = 0
        self.consecutive_clean = 0

    def _register_problem(self, *, confirmed: bool = False) -> None:
        """Count one bad cycle (soft mismatch, confirmed hit, or connection failure)."""
        self.consecutive_mismatches += 1
        self.consecutive_clean = 0
        if confirmed or self.consecutive_mismatches >= self.mismatch_threshold:
            self.cross_talk_suspected = True

    def _register_clean(self) -> None:
        """Count one good cycle -- only actually clears suspicion after clear_threshold in a row."""
        self.consecutive_mismatches = 0
        self.consecutive_clean += 1
        if self.cross_talk_suspected and self.consecutive_clean >= self.clear_threshold:
            self.cross_talk_suspected = False

    async def safe_update(self) -> UpdateResult:
        """Poll status/temp/progress/file-list, validating every response.

        Only touches the printer while holding the lock, so this can never
        race a concurrent safe_call(). A response that doesn't echo the
        command that was sent is discarded (logged, counted) rather than
        parsed -- so a stale-but-correct value is kept on the printer object
        instead of being overwritten with garbage. Real connection failures
        (TimeoutError/ConnectionError) are NOT caught here -- they propagate
        to the caller so the existing failed-update/backoff handling still
        applies; only "got *a* response, but it's the wrong one" is handled
        as a soft failure in this method.
        """
        async with self.lock:
            if not self.printer.connected:
                await self.printer.connect()

            mismatched: list[str] = []
            confirmed_cross_talk: list[str] = []

            async def _get(code: str, coro: Callable[..., Awaitable[str | None]]) -> str | None:
                resp = await coro(disconnect=False)
                if echo_ok(code, resp):
                    return resp
                culprit = looks_like_other_command(resp)
                if culprit:
                    _LOGGER.warning(
                        "FlashForge: asked for %s, got a response that looks like "
                        "an answer to %s instead (%r). Another client (e.g. "
                        "FlashPrint) is probably also connected to the printer.",
                        code,
                        culprit,
                        resp,
                    )
                    # Unambiguous: a well-formed echo of a DIFFERENT known
                    # command can only mean another client's request landed
                    # on our socket. No legitimate cause on our own end
                    # produces this, so it doesn't need to repeat before
                    # counting as confirmed -- see how confirmed_cross_talk
                    # is used below.
                    confirmed_cross_talk.append(code)
                elif resp:
                    _LOGGER.warning(
                        "FlashForge: asked for %s, got an unrecognized response: %r",
                        code,
                        resp,
                    )
                else:
                    _LOGGER.debug("FlashForge: asked for %s, got no response.", code)
                mismatched.append(code)
                return None

            status_resp = await _get("M119", self.printer.network.sendStatusRequest)
            if status_resp:
                for f in (
                    self.printer._machine_status,  # noqa: SLF001
                    self.printer._move_mode,  # noqa: SLF001
                    self.printer._status,  # noqa: SLF001
                    self.printer._led,  # noqa: SLF001
                    self.printer._job_file,  # noqa: SLF001
                ):
                    m = f.regex.search(status_resp)
                    if m:
                        f.value = m.group(1)

            temp_resp = await _get("M105", self.printer.network.sendTempRequest)
            if temp_resp:
                self._apply_temp(temp_resp)

            progress_resp = await _get("M27", self.printer.network.sendProgressRequest)
            if progress_resp:
                m = self.printer._print_percent.regex.search(progress_resp)  # noqa: SLF001
                if m:
                    self.printer._print_percent.value = m.group(1)  # noqa: SLF001
                m = self.printer._print_layer.regex.search(progress_resp)  # noqa: SLF001
                if m:
                    self.printer._print_layer.value = m.group(1)  # noqa: SLF001
                    self.printer._job_layers.value = m.group(2)  # noqa: SLF001

            # NOTE: M661 (file list) is deliberately NOT echo-validated like the
            # others above. First attempt tried peeking at
            # printer.network.responseData for a "CMD M661 Received" echo, but
            # live testing against a real Voxel with zero stored files showed
            # ffpp.Network.sendGetFileNames() returns None early (leaving
            # responseData as the stale `[]` left over from sendMessage()'s
            # reset) any time the printer has no files to report -- a normal,
            # valid "zero files" result, not cross-talk. That produced false
            # positives. So: just accept whatever sendGetFileNames() returns,
            # same as the original coordinator code did (`if not files: files
            # = []`). Real connection failures still raise and propagate
            # normally; only the M119/M105/M27 sensor-relevant fields above
            # get the stricter echo check, since those are the ones that can
            # silently show wrong-but-plausible data if cross-talk happens.
            files = await self.printer.network.sendGetFileNames(disconnect=False) or []

            # Release the connection at the end of every cycle instead of
            # holding it open between polls. Found live: an early version of
            # this method kept `disconnect=False` all the way through and
            # never closed the socket afterward, so it held one continuous
            # connection to the printer for the entire run. That was enough
            # to make FlashPrint fail to connect at all ("This machine has
            # been occupied") -- i.e. THIS integration became the "other
            # client" causing the exact interference it's supposed to detect.
            # The original (pre-fix) coordinator code always disconnected
            # after each poll, which is apparently what left enough of a gap
            # for FlashPrint to coexist. Network.sendMessage() transparently
            # reconnects on the next call (it checks its own `self.connection
            # is None`, unrelated to `printer.connected`), so closing here is
            # free -- it doesn't undo the lock-based fix for §3b, it just
            # stops this integration from monopolizing the printer between
            # polls the way it accidentally did during testing.
            await self.printer.network.disconnect()

            if confirmed_cross_talk:
                # Unambiguous proof, seen live twice now (plan §3a): a
                # single confirmed hit raises suspicion immediately, no
                # need to repeat like an ambiguous mismatch/connection
                # failure does.
                self._register_problem(confirmed=True)
            elif mismatched:
                self._register_problem()
            else:
                self._register_clean()

            return UpdateResult(
                ok=not mismatched,
                files=files,
                mismatched_codes=mismatched,
                confirmed_cross_talk_codes=confirmed_cross_talk,
                cross_talk_suspected=self.cross_talk_suspected,
            )

    def note_connection_failure(self) -> None:
        """Record a hard connection failure around a safe_update() call.

        safe_update() deliberately lets TimeoutError/ConnectionError
        propagate uncaught, so the coordinator's existing failed-update /
        backoff handling still applies -- this method exists so the
        *caller's* except block can still feed that failure into the same
        cross-talk-suspicion signal as a soft mismatch.

        Why this exists: a real cross-talk episode observed live (plan §3a,
        "Additional live evidence") ran for ~14 seconds while FlashPrint was
        connected, and was dominated by ConnectionResetError /
        RuntimeError('read() called while another coroutine is already
        waiting...') rather than soft mismatches -- only one cycle in that
        whole episode was a soft mismatch. Under the old consecutive-soft-
        mismatch-only counter, cross_talk_suspected never flipped True during
        that entire episode, so the Repairs issue never fired even though
        cross-talk was definitely happening. Folding connection failures into
        the same consecutive-problem counter as soft mismatches fixes that: a
        mix of resets and mismatches (as was actually observed) now crosses
        the threshold, while a single isolated network blip on an otherwise
        healthy connection still doesn't.
        """
        self._register_problem()

    def _apply_temp(self, resp: str) -> None:
        from ffpp.Printer import temperatures  # noqa: PLC0415

        m = self.printer._extruder_temp.regex.search(resp)  # noqa: SLF001
        if m:
            self.printer.extruder_tools.add(temperatures(m.group(1), m.group(2), m.group(3)))
        m = self.printer._bed_temp.regex.search(resp)  # noqa: SLF001
        if m:
            self.printer.bed_tools.add(temperatures(m.group(1), m.group(2), m.group(3)))

    async def safe_call(self, factory: Callable[[], Awaitable]):
        """Run a service-call coroutine under the same lock as safe_update().

        This is the actual fix for §3b: as long as every service handler and
        the coordinator's poll both go through this method/safe_update(),
        they can never call .read() on the shared connection at the same
        time, so the asyncio "already waiting" crash can't happen.

        Usage: ``await guard.safe_call(lambda: printer.network.sendPauseRequest())``
        """
        async with self.lock:
            if not self.printer.connected:
                await self.printer.connect()
            return await factory()
