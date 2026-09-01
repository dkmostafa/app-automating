"""Finding and running the host binaries this component depends on.

The component's only contact with the operating system's process table. It
resolves a tool to an absolute path, spawns it in its own process group so a
timeout can take down the whole tree, drains its pipes so a chatty child cannot
block on a full buffer, and kills and reaps it on every exit path including
cancellation (Rule 1 §5).

It knows nothing about Appium, sessions or devices. That is deliberate: the
shape is the one the Android module's component already uses, and a third
component can take the same runner. Resolution here is plain ``PATH`` lookup --
unlike the Android SDK there is no vendored layout to prefer, and an
nvm-managed ``appium`` lives on ``PATH`` and nowhere predictable.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import time
from collections import deque
from collections.abc import Mapping, Sequence
from pathlib import Path

from .config import AppiumConfig
from .errors import (
    AppiumComponentError,
    AppiumToolNotFoundError,
    CommandTimeoutError,
)
from .models import CommandResult, display_command

__all__ = [
    "ToolResolver",
    "CommandRunner",
    "spawn",
    "run_command",
    "drain",
    "terminate",
]


class ToolResolver:
    """Turns a configured tool name into an absolute path, once, then caches it.

    An absolute path in the config is taken as given and only checked for
    existence; a bare name goes through ``PATH``. Resolution is cached because
    a ``PATH`` walk per command is wasted work, and because a tool that moves
    out from under a running server is not a case worth handling silently.
    """

    def __init__(self, config: AppiumConfig) -> None:
        self._config = config
        self._cache: dict[str, str] = {}

    def path(self, tool: str) -> str:
        """Absolute path of a host tool, or :class:`AppiumToolNotFoundError`."""
        cached = self._cache.get(tool)
        if cached is not None:
            return cached
        resolved = self._resolve(tool, self._configured(tool))
        self._cache[tool] = resolved
        return resolved

    def find(self, tool: str) -> str | None:
        """The same lookup, as a question rather than a demand.

        The environment check needs "is it there" without an exception, because
        a missing tool is the answer it exists to report rather than a failure.
        """
        try:
            return self.path(tool)
        except AppiumToolNotFoundError:
            return None

    def _configured(self, tool: str) -> str:
        return {
            "appium": self._config.appium_path,
            "node": self._config.node_path,
            "npm": self._config.npm_path,
        }.get(tool, tool)

    def _resolve(self, tool: str, configured: str) -> str:
        candidate = Path(configured).expanduser()
        if candidate.is_absolute() or os.sep in configured:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
            raise AppiumToolNotFoundError(tool, str(candidate))
        found = shutil.which(configured)
        if found:
            return found
        raise AppiumToolNotFoundError(tool, f"{configured!r} on PATH")


async def spawn(
    argv: Sequence[str], *, env: Mapping[str, str] | None = None
) -> asyncio.subprocess.Process:
    """Start a long-lived child with piped output and its own process group."""
    try:
        return await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Own process group, so a timeout can take down the whole tree.
            # The Appium server forks driver processes; killing only the parent
            # leaves those holding the device.
            start_new_session=True,
            env=None if env is None else dict(env),
        )
    except (FileNotFoundError, NotADirectoryError, PermissionError) as exc:
        raise AppiumToolNotFoundError(Path(argv[0]).name, argv[0]) from exc


async def run_command(
    argv: Sequence[str],
    *,
    operation: str,
    timeout: float,
    env: Mapping[str, str] | None = None,
) -> CommandResult:
    """Run a host tool to completion, or kill it and raise on the deadline."""
    started = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            env=None if env is None else dict(env),
        )
    except (FileNotFoundError, NotADirectoryError, PermissionError) as exc:
        raise AppiumToolNotFoundError(Path(argv[0]).name, argv[0]) from exc
    except OSError as exc:
        raise AppiumComponentError(f"could not start {display_command(argv)}: {exc}") from exc

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        await terminate(proc)
        raise CommandTimeoutError(operation, tuple(argv), timeout) from None
    except asyncio.CancelledError:
        await terminate(proc)
        raise

    return CommandResult(
        argv=tuple(argv),
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=stdout.decode("utf-8", "replace"),
        stderr=stderr.decode("utf-8", "replace"),
        duration_seconds=time.monotonic() - started,
    )


async def drain(stream: asyncio.StreamReader | None, sink: deque[str]) -> None:
    """Copy a child's output into a bounded buffer.

    Without this the Appium server fills its pipe and blocks the moment it gets
    chatty, which under a real driver install is immediately.
    """
    if stream is None:
        return
    while True:
        try:
            line = await stream.readline()
        except (ValueError, asyncio.LimitOverrunError):
            # One absurdly long line; skip it rather than stop draining.
            continue
        except Exception:  # noqa: BLE001 - draining must never fail a launch
            return
        if not line:
            return
        sink.append(line.decode("utf-8", "replace").rstrip("\r\n"))


async def terminate(proc: asyncio.subprocess.Process, grace_seconds: float = 10.0) -> None:
    """Kill a child and everything it spawned, then reap it."""
    if proc.returncode is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.kill()
    with contextlib.suppress(asyncio.TimeoutError, TimeoutError, Exception):
        await asyncio.wait_for(proc.wait(), grace_seconds)


class CommandRunner:
    """The host toolchain as one injectable collaborator.

    Bundles tool resolution with the process primitives above, so a component
    takes a runner rather than importing four module functions and re-deriving
    where ``appium`` lives.

    Not an abstraction for testing -- Rule 1 §6 forbids mocking this layer and
    that stands. It is a seam for *reuse* and for configuration: one resolved,
    configured runner, shared by everything that shells out.
    """

    def __init__(self, config: AppiumConfig) -> None:
        self._config = config
        self._resolver = ToolResolver(config)

    @property
    def config(self) -> AppiumConfig:
        return self._config

    def tool_path(self, tool: str) -> str:
        return self._resolver.path(tool)

    def find_tool(self, tool: str) -> str | None:
        return self._resolver.find(tool)

    async def run(self, argv: Sequence[str], *, operation: str, timeout: float) -> CommandResult:
        return await run_command(argv, operation=operation, timeout=timeout)

    async def run_tool(
        self, tool: str, *args: str, operation: str, timeout: float
    ) -> CommandResult:
        """Resolve ``tool`` and run it with ``args``."""
        return await run_command(
            [self.tool_path(tool), *args], operation=operation, timeout=timeout
        )

    async def spawn_tool(self, tool: str, *args: str) -> asyncio.subprocess.Process:
        return await spawn([self.tool_path(tool), *args])

    async def terminate(self, proc: asyncio.subprocess.Process) -> None:
        await terminate(proc)

    def drain_into(
        self, proc: asyncio.subprocess.Process, sink: deque[str]
    ) -> tuple[asyncio.Task[None], ...]:
        """Start the pipe drains for a long-lived child, returning their tasks."""
        return (
            asyncio.create_task(drain(proc.stdout, sink)),
            asyncio.create_task(drain(proc.stderr, sink)),
        )
