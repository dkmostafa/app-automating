"""The Appium server process: probing it, launching one, and taking it down.

The component is configured to *manage* a server (Rule 1 §5: own every process
you start), which means three responsibilities live here and nowhere else:

* **probe** -- ask ``/status`` whether something is already listening, because
  a developer with ``appium`` running in a terminal should not get a second
  server fighting for the same port;
* **launch** -- spawn one when nothing answers, drain its output into a bounded
  buffer, and wait for it to actually serve rather than merely exist;
* **own it** -- kill the whole process group on shutdown, and *only* when this
  process was the one that started it. A server we merely connected to is
  someone else's, and killing it on the way out would be a surprise.

The probe is stdlib ``urllib`` on a worker thread rather than a new HTTP
dependency: it is one GET against loopback, and adding an async client to the
dependency tree to make that call would be the tail wagging the dog.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import urllib.error
import urllib.request
from collections import deque

from .config import AppiumConfig
from .errors import ServerNotAnsweringError, ServerNotReadyError
from .models import ServerStatus
from .parsing import parse_server_status
from .process import CommandRunner

__all__ = ["AppiumServer", "SERVER_LOG_LINES"]

#: How many lines of a managed server's output to keep. Enough to show why a
#: launch failed, bounded so a long-running server cannot grow without limit.
SERVER_LOG_LINES = 200


class AppiumServer:
    """The Appium server this adapter talks to, whoever started it."""

    def __init__(self, config: AppiumConfig, runner: CommandRunner) -> None:
        self._config = config
        self._runner = runner
        self._process: asyncio.subprocess.Process | None = None
        self._drains: tuple[asyncio.Task[None], ...] = ()
        self._log: deque[str] = deque(maxlen=SERVER_LOG_LINES)
        # Guards the launch: two sessions starting at once must not race into
        # two servers on the same port.
        self._lock = asyncio.Lock()

    @property
    def config(self) -> AppiumConfig:
        """The settings this server was built with.

        Exposed because a caller that wants a *differently* configured server --
        a shorter start timeout, a different port -- must build one from these
        rather than reach into the object.
        """
        return self._config

    @property
    def url(self) -> str:
        return self._config.server_url

    @property
    def managed(self) -> bool:
        """True when this process launched the server that is running now."""
        return self._process is not None and self._process.returncode is None

    @property
    def log_tail(self) -> str:
        return "\n".join(self._log)

    # -- probing -----------------------------------------------------------

    async def probe(self) -> ServerStatus:
        """Ask ``/status``. Never raises: "nothing is listening" is an answer."""
        version = await asyncio.to_thread(self._get_status, self._config.status_url)
        running = version is not None
        return ServerStatus(
            url=self.url,
            running=running,
            managed=self.managed and running,
            pid=self._process.pid if self._process is not None and running else None,
            # An empty string means "answered but did not say"; None means the
            # request itself failed. Collapsing them would lose that.
            version=version or None if running else None,
        )

    @staticmethod
    def _get_status(status_url: str) -> str | None:
        """The blocking half of :meth:`probe`. Returns ``""`` for a live server
        that reported no version, and ``None`` when nothing answered."""
        try:
            with urllib.request.urlopen(status_url, timeout=5) as response:  # noqa: S310
                body = response.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError, ValueError):
            return None
        return parse_server_status(body) or ""

    # -- launching ---------------------------------------------------------

    async def ensure_running(self) -> tuple[ServerStatus, bool]:
        """Guarantee a server at :attr:`url`. Returns ``(status, started_here)``.

        Idempotent and safe to call concurrently. When ``manage_server`` is off
        this is a probe with a typed failure attached, which is the whole
        difference between the two modes.
        """
        async with self._lock:
            status = await self.probe()
            if status.running:
                return status, False
            if not self._config.manage_server:
                raise ServerNotAnsweringError(
                    self.url,
                    "manage_server is off, so this module will not start one for you",
                )
            await self._launch()
            return await self._wait_until_serving(), True

    async def _launch(self) -> None:
        """Start the server process and begin draining its output."""
        await self._reap()
        self._log.clear()
        base_path = self._config.server_base_path or "/"
        self._process = await self._runner.spawn_tool(
            "appium",
            "--address",
            self._config.server_host,
            "--port",
            str(self._config.server_port),
            "--base-path",
            base_path,
        )
        self._drains = self._runner.drain_into(self._process, self._log)

    async def _wait_until_serving(self) -> ServerStatus:
        """Poll ``/status`` until the launched server answers, or give up.

        A dead process is detected as well as a silent one: without that check a
        server that exits instantly (port already bound, driver broken) would
        cost the full timeout before reporting anything.
        """
        deadline = time.monotonic() + self._config.server_start_timeout_seconds
        while time.monotonic() < deadline:
            if self._process is not None and self._process.returncode is not None:
                await self._reap()
                raise ServerNotReadyError(
                    self.url,
                    self._config.server_start_timeout_seconds,
                    self.log_tail,
                )
            status = await self.probe()
            if status.running:
                return status
            await asyncio.sleep(self._config.server_poll_interval_seconds)

        tail = self.log_tail
        await self._reap()
        raise ServerNotReadyError(self.url, self._config.server_start_timeout_seconds, tail)

    # -- shutdown ----------------------------------------------------------

    async def aclose(self) -> None:
        """Take down the server, if and only if this process started it."""
        await self._reap()

    async def _reap(self) -> None:
        for task in self._drains:
            task.cancel()
        for task in self._drains:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._drains = ()
        if self._process is not None:
            await self._runner.terminate(self._process)
            self._process = None
