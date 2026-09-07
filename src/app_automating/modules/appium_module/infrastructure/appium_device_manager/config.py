"""The injected configuration of the Appium adapter, and the one place it is
resolved from the environment.

Rule 1 §4: the component takes this dataclass and nothing else. No operation
below reads ``os.environ``, and no timeout, path or port is hardcoded inside a
method -- which is what makes ``dataclasses.replace(config,
server_start_timeout_seconds=0.001)`` a real timeout test instead of a mock.

:meth:`AppiumConfig.from_environment` is the only reader, it is called by the
composition root, and every value it produces is handed downwards explicitly.
Settings answer to the ``AT_APPIUM_`` prefix, matching the rest of the product's
``AT_`` namespace.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

__all__ = ["AppiumConfig", "DEFAULT_SERVER_HOST", "DEFAULT_SERVER_PORT", "ENV_PREFIX"]

ENV_PREFIX = "AT_APPIUM_"

#: Loopback, deliberately. An Appium server binds a port that can drive every
#: attached device with no authentication of any kind; binding it to 0.0.0.0 on
#: a developer's machine hands that to the local network. A caller that really
#: wants a shared server sets the host explicitly and owns that decision.
DEFAULT_SERVER_HOST = "127.0.0.1"

#: Appium's own default port.
DEFAULT_SERVER_PORT = 4723


def _env(source: Mapping[str, str], name: str) -> str | None:
    value = source.get(f"{ENV_PREFIX}{name}")
    return value.strip() if value and value.strip() else None


def _float(source: Mapping[str, str], name: str, fallback: float) -> float:
    value = _env(source, name)
    if value is None:
        return fallback
    try:
        return float(value)
    except ValueError:
        # A malformed timeout is a typo in a .env file, not a reason to refuse
        # to start; the documented default is the safer reading.
        return fallback


def _int(source: Mapping[str, str], name: str, fallback: int) -> int:
    value = _env(source, name)
    if value is None:
        return fallback
    try:
        return int(value)
    except ValueError:
        return fallback


def _bool(source: Mapping[str, str], name: str, fallback: bool) -> bool:
    value = _env(source, name)
    if value is None:
        return fallback
    return value.lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True, slots=True)
class AppiumConfig:
    """Everything the Appium adapter needs to know about this host."""

    # -- host tools --------------------------------------------------------
    #: The Appium CLI. A bare name is resolved through ``PATH``, which is where
    #: an nvm-managed install lives; an absolute path pins a specific one.
    appium_path: str = "appium"
    #: Used only to report the Node version in the environment check. Appium is
    #: launched through its own executable, not through node.
    node_path: str = "node"
    #: Used only to install drivers.
    npm_path: str = "npm"

    # -- the server --------------------------------------------------------
    server_host: str = DEFAULT_SERVER_HOST
    server_port: int = DEFAULT_SERVER_PORT
    #: Appium 3 serves at the root. Appium 1 served at ``/wd/hub``; a caller
    #: pointing at an old server sets this rather than editing code.
    server_base_path: str = "/"
    #: Launch a server when nothing is listening. False makes an absent server a
    #: :class:`~...domain.errors.ServerUnreachable` instead of a spawn.
    manage_server: bool = True
    #: Where a managed server's log is written. ``None`` keeps it in memory and
    #: surfaces only the tail, on failure.
    server_log_path: Path | None = None

    # -- timeouts ----------------------------------------------------------
    #: How long to wait for a launched server to answer ``/status``.
    server_start_timeout_seconds: float = 60.0
    server_poll_interval_seconds: float = 0.5
    #: How long an ``appium`` CLI call (``-v``, ``driver list``) may take.
    command_timeout_seconds: float = 60.0
    #: Driver installs pull from the npm registry and are measured in minutes.
    driver_install_timeout_seconds: float = 900.0
    #: How long to wait for a session to be created. A cold emulator or a real
    #: device over USB is slow, and failing early just hides a working setup.
    session_startup_timeout_seconds: float = 180.0
    #: Seconds the server keeps an idle session alive. Long, because a session
    #: here waits on a human or a model deciding what to do next.
    new_command_timeout_seconds: float = 600.0
    #: How long a single gesture or query may take.
    interaction_timeout_seconds: float = 60.0

    # -- automation defaults ----------------------------------------------
    platform_name: str = "Android"
    #: The Appium driver sessions are created with. ``UiAutomator2`` drives both
    #: emulators and physical Android devices; they differ only in the serial.
    automation_name: str = "UiAutomator2"
    #: The driver package name, as ``appium driver install`` spells it.
    driver_name: str = "uiautomator2"

    @property
    def server_url(self) -> str:
        """The URL a session is created against."""
        raw = self.server_base_path
        base = raw if raw.startswith("/") else f"/{raw}"
        base = "" if base == "/" else base.rstrip("/")
        return f"http://{self.server_host}:{self.server_port}{base}"

    @property
    def status_url(self) -> str:
        """The endpoint that answers "is a server alive here"."""
        return f"{self.server_url}/status"

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> AppiumConfig:
        """Read the host environment. The only place in the module that does.

        Takes an explicit mapping so the resolution rules are testable without
        touching the real environment -- the rules are pure, and only the
        default argument is not.
        """
        source = os.environ if environ is None else environ
        log_path = _env(source, "SERVER_LOG_PATH")
        return cls(
            appium_path=_env(source, "PATH_BIN") or "appium",
            node_path=_env(source, "NODE_PATH") or "node",
            npm_path=_env(source, "NPM_PATH") or "npm",
            server_host=_env(source, "SERVER_HOST") or DEFAULT_SERVER_HOST,
            server_port=_int(source, "SERVER_PORT", DEFAULT_SERVER_PORT),
            server_base_path=_env(source, "SERVER_BASE_PATH") or "/",
            manage_server=_bool(source, "MANAGE_SERVER", True),
            server_log_path=Path(log_path).expanduser() if log_path else None,
            server_start_timeout_seconds=_float(source, "SERVER_START_TIMEOUT_SECONDS", 60.0),
            server_poll_interval_seconds=_float(source, "SERVER_POLL_INTERVAL_SECONDS", 0.5),
            command_timeout_seconds=_float(source, "COMMAND_TIMEOUT_SECONDS", 60.0),
            driver_install_timeout_seconds=_float(source, "DRIVER_INSTALL_TIMEOUT_SECONDS", 900.0),
            session_startup_timeout_seconds=_float(
                source, "SESSION_STARTUP_TIMEOUT_SECONDS", 180.0
            ),
            new_command_timeout_seconds=_float(source, "NEW_COMMAND_TIMEOUT_SECONDS", 600.0),
            interaction_timeout_seconds=_float(source, "INTERACTION_TIMEOUT_SECONDS", 60.0),
            platform_name=_env(source, "PLATFORM_NAME") or "Android",
            automation_name=_env(source, "AUTOMATION_NAME") or "UiAutomator2",
            driver_name=_env(source, "DRIVER_NAME") or "uiautomator2",
        )
