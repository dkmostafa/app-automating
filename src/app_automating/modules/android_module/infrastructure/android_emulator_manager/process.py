"""Finding and running the Android host binaries.

This is the component's only contact with the operating system's process table.
It resolves a tool to an absolute path, spawns it in its own process group so a
timeout can take down the whole tree, drains its pipes so a chatty child cannot
block on a full buffer, and kills and reaps it on every exit path.

It knows nothing about AVDs or emulators, so a second infrastructure component
can reuse it unchanged -- and because :class:`CommandRunner` is a constructor
argument of the manager rather than a module import (Rule 0 §3, DIP), the two
components can be handed the *same* runner. They will need to be: adb serialises
badly when two callers race its server, and a shared runner is where that lock
will go.
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

from .config import AndroidSdkConfig
from .errors import AndroidEmulatorError, AndroidToolNotFoundError, CommandTimeoutError
from .models import CommandResult, display_command

__all__ = ["CommandRunner", "ToolResolver", "spawn", "run_command", "drain", "terminate"]

#: Directories under the SDK root searched before ``PATH``, per tool.
TOOL_SDK_DIRS: dict[str, tuple[str, ...]] = {
    "adb": ("platform-tools",),
    "emulator": ("emulator",),
    "sdkmanager": ("cmdline-tools/latest/bin", "tools/bin"),
    "avdmanager": ("cmdline-tools/latest/bin", "tools/bin"),
}

CMDLINE_TOOLS = ("sdkmanager", "avdmanager")


class ToolResolver:
    """Turns a configured tool name into an absolute path, once, then caches it.

    SDK-local wins over ``PATH`` on purpose: a distro-packaged ``/usr/bin/adb``
    is routinely a different major version from the SDK's ``emulator``, and the
    two then fight over the adb server.
    """

    def __init__(self, config: AndroidSdkConfig) -> None:
        self._config = config
        self._sdk_root = config.sdk_root
        self._cache: dict[str, str] = {}

    def path(self, tool: str) -> str:
        cached = self._cache.get(tool)
        if cached is not None:
            return cached
        try:
            configured = getattr(self._config, f"{tool}_path")
        except AttributeError:
            raise AndroidToolNotFoundError(tool, tool) from None
        resolved = self._resolve(tool, str(configured))
        self._cache[tool] = resolved
        return resolved

    def _sdk_dirs(self, tool: str) -> tuple[Path, ...]:
        if self._sdk_root is None:
            return ()
        dirs = [self._sdk_root / rel for rel in TOOL_SDK_DIRS.get(tool, ())]
        if tool in CMDLINE_TOOLS:
            cmdline_tools = self._sdk_root / "cmdline-tools"
            if cmdline_tools.is_dir():
                with contextlib.suppress(OSError):
                    for child in sorted(cmdline_tools.iterdir(), reverse=True):
                        candidate = child / "bin"
                        if candidate.is_dir() and candidate not in dirs:
                            dirs.append(candidate)
        return tuple(dirs)

    def _resolve(self, tool: str, configured: str) -> str:
        candidate = Path(configured).expanduser()
        if os.sep in configured or candidate.is_absolute():
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
            raise AndroidToolNotFoundError(tool, configured, (str(candidate),))

        searched: list[str] = []
        for directory in self._sdk_dirs(tool):
            probe = directory / configured
            searched.append(str(probe))
            if probe.is_file() and os.access(probe, os.X_OK):
                return str(probe)
        found = shutil.which(configured)
        if found:
            return found
        searched.append("PATH")
        raise AndroidToolNotFoundError(tool, configured, tuple(searched))


async def spawn(
    argv: Sequence[str], *, env: Mapping[str, str] | None = None
) -> asyncio.subprocess.Process:
    """Start a long-lived child with piped output and its own process group.

    ``env=None`` lets the child inherit this process's environment, which is
    what an emulator must *not* do when the server was started without a
    display. The full environment is resolved once on the config (Rule 1 §4);
    nothing here reads it.
    """
    try:
        return await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Own process group, so a timeout can take down the whole tree.
            start_new_session=True,
            env=None if env is None else dict(env),
        )
    except (FileNotFoundError, NotADirectoryError, PermissionError) as exc:
        raise AndroidToolNotFoundError(Path(argv[0]).name, argv[0]) from exc


async def run_command(
    argv: Sequence[str],
    *,
    timeout: float,
    stdin_data: bytes | None = None,
    env: Mapping[str, str] | None = None,
) -> CommandResult:
    """Run a host tool to completion, or kill it and raise on the deadline."""
    started = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE if stdin_data is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            env=None if env is None else dict(env),
        )
    except (FileNotFoundError, NotADirectoryError, PermissionError) as exc:
        raise AndroidToolNotFoundError(Path(argv[0]).name, argv[0]) from exc
    except OSError as exc:
        raise AndroidEmulatorError(f"could not start {display_command(argv)}: {exc}") from exc

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(stdin_data), timeout)
    except TimeoutError:
        await terminate(proc)
        raise CommandTimeoutError(argv, timeout) from None
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

    Without this the emulator fills its pipe and blocks forever the moment it
    gets chatty.
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

    Bundles tool resolution with the process primitives above and holds the
    timeouts, so a component takes a runner rather than importing five module
    functions and re-deriving where ``adb`` lives.

    Not an abstraction for testing -- Rule 1 §6 forbids mocking this layer and
    that stands. It is a seam for *reuse*: one resolved, configured runner
    shared by every component that talks to the same SDK.
    """

    def __init__(self, config: AndroidSdkConfig) -> None:
        self._config = config
        self._resolver = ToolResolver(config)

    @property
    def config(self) -> AndroidSdkConfig:
        return self._config

    def tool_path(self, tool: str) -> str:
        """Absolute path of a host tool, raising if it cannot be resolved."""
        return self._resolver.path(tool)

    async def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        stdin_data: bytes | None = None,
    ) -> CommandResult:
        return await run_command(
            argv, timeout=timeout, stdin_data=stdin_data, env=self._config.child_environ
        )

    async def run_tool(
        self,
        tool: str,
        *args: str,
        timeout: float,
        stdin_data: bytes | None = None,
    ) -> CommandResult:
        """Resolve ``tool`` and run it with ``args``."""
        return await run_command(
            [self.tool_path(tool), *args],
            timeout=timeout,
            stdin_data=stdin_data,
            env=self._config.child_environ,
        )

    async def spawn_tool(self, argv: Sequence[str]) -> asyncio.subprocess.Process:
        return await spawn(argv, env=self._config.child_environ)

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
