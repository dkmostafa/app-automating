"""Injected configuration for the Android emulator infrastructure component.

:meth:`AndroidSdkConfig.from_environment` is the only place in this component
that reads ``os.environ``, and it is called by the composition root rather than
by anything under here. No operation consults the environment, no constructor
falls back to it, and no path, timeout or flag is hardcoded inside a method --
everything a caller might need to pin lives here as a field.

:func:`avd_home_from_env`, :func:`sdk_root_from_env` and
:func:`display_env_from_env` are exported for the composition root's benefit
only. A component that calls one of them directly has reintroduced exactly the
ambient dependency this module exists to remove.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "AndroidSdkConfig",
    "DEFAULT_HEADLESS_ARGS",
    "DISPLAY_VARS",
    "avd_home_from_env",
    "child_env_from_env",
    "display_env_from_env",
    "sdk_root_from_env",
]


#: Flags added to an emulator launch when ``headless`` is requested. Overridable
#: through :attr:`AndroidSdkConfig.headless_args` -- a machine without a GPU
#: usually also wants ``-gpu swiftshader_indirect``.
DEFAULT_HEADLESS_ARGS: tuple[str, ...] = ("-no-window", "-no-audio", "-no-boot-anim")

#: The variables a *windowed* emulator needs in order to find the user's screen.
#: ``WAYLAND_DISPLAY`` is deliberately not among them: the emulator's bundled Qt
#: ships xcb, linuxfb, minimal, offscreen and vnc platform plugins and no wayland
#: one, so advertising a wayland socket only invites Qt to pick a platform it
#: cannot load. On a wayland session Xwayland serves it through ``DISPLAY``.
DISPLAY_VARS: tuple[str, ...] = ("DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR")

#: Where an X server advertises its sockets, and the name GNOME's Xwayland gives
#: its cookie inside ``XDG_RUNTIME_DIR``.
X11_SOCKET_DIR = Path("/tmp/.X11-unix")
XWAYLAND_AUTH_GLOB = ".mutter-Xwaylandauth.*"


@dataclass(frozen=True, slots=True)
class AndroidSdkConfig:
    """Everything the manager needs to reach the host toolchain.

    Tool resolution order for each tool is: the configured value when it looks
    like a path, then the tool's own directory under :attr:`sdk_root`, then
    ``PATH``. SDK-local wins over ``PATH`` on purpose -- a distro-packaged
    ``/usr/bin/adb`` is routinely a different major version from the SDK's
    ``emulator``, and the two fight over the adb server.
    """

    adb_path: str = "adb"
    emulator_path: str = "emulator"
    sdkmanager_path: str = "sdkmanager"
    avdmanager_path: str = "avdmanager"
    sdk_root: Path | None = None
    avd_home: Path | None = None

    adb_timeout_seconds: float = 30.0
    avdmanager_timeout_seconds: float = 600.0
    #: System images run to gigabytes, so this is deliberately long.
    sdkmanager_timeout_seconds: float = 3600.0
    emulator_boot_timeout_seconds: float = 300.0
    boot_poll_interval_seconds: float = 2.0
    emulator_stop_timeout_seconds: float = 60.0

    #: Added to every emulator launch, headless or not.
    emulator_launch_args: tuple[str, ...] = ()
    headless_args: tuple[str, ...] = DEFAULT_HEADLESS_ARGS

    #: The *complete* environment handed to every child process, as pairs so the
    #: config stays frozen and hashable. ``None`` means "let the child inherit
    #: this process's own", which is right for a hand-written config and wrong
    #: for a server that was started without a display (see
    #: :func:`display_env_from_env`).
    child_env: tuple[tuple[str, str], ...] | None = None

    @property
    def child_environ(self) -> dict[str, str] | None:
        """:attr:`child_env` as the mapping :mod:`asyncio` wants, or ``None``."""
        return None if self.child_env is None else dict(self.child_env)

    @classmethod
    def from_environment(
        cls, env: Mapping[str, str] | None = None, **overrides: object
    ) -> AndroidSdkConfig:
        """Build a config from the ambient Android environment.

        This is the *only* place the environment is consulted. ``overrides`` are
        applied last so a caller can pin any field.
        """
        environ = os.environ if env is None else env
        resolved: dict[str, object] = {
            "sdk_root": sdk_root_from_env(environ),
            "avd_home": avd_home_from_env(environ),
            "child_env": child_env_from_env(environ),
        }
        resolved.update(overrides)
        return cls(**resolved)  # type: ignore[arg-type]


def sdk_root_from_env(env: Mapping[str, str]) -> Path | None:
    for key in ("ANDROID_SDK_ROOT", "ANDROID_HOME"):
        value = env.get(key)
        if value:
            return Path(value).expanduser()
    default = Path.home() / "Android" / "Sdk"
    return default if default.is_dir() else None


def avd_home_from_env(env: Mapping[str, str] | None = None) -> Path:
    """Resolve the AVD home the way the Android tools do.

    ``env=None`` reads ``os.environ``; that is the fallback a manager built from
    a hand-written config uses once, at construction.
    """
    env = os.environ if env is None else env
    value = env.get("ANDROID_AVD_HOME")
    if value:
        return Path(value).expanduser()
    legacy = env.get("ANDROID_SDK_HOME")
    if legacy:
        return Path(legacy).expanduser() / ".android" / "avd"
    return Path.home() / ".android" / "avd"


def display_env_from_env(
    env: Mapping[str, str],
    *,
    x11_socket_dir: Path | None = None,
    runtime_dir: Path | None = None,
    home: Path | None = None,
    uid: int | None = None,
) -> dict[str, str]:
    """Resolve the display variables a windowed emulator needs to open a window.

    A server launched by an MCP client is typically handed a scrubbed
    environment -- no ``DISPLAY``, no ``XAUTHORITY`` -- and a child that inherits
    it cannot reach the screen however healthy the host's session is. Qt reports
    that as "could not connect to display" followed by a complaint that no
    platform plugin is available; both halves are this one cause.

    Whatever ``env`` already carries wins. Anything missing is discovered from
    the sockets and cookies the running session left on disk, so a windowed
    launch does not depend on the launcher passing them down. Discovery is
    best-effort: a host with no session at all yields whatever was found, and a
    headless launch never needed any of it.
    """
    resolved = {key: env[key] for key in DISPLAY_VARS if env.get(key)}
    if "DISPLAY" in resolved and "XAUTHORITY" in resolved:
        return resolved  # fully specified; the filesystem has nothing to add

    uid = os.getuid() if uid is None else uid
    if "XDG_RUNTIME_DIR" not in resolved:
        candidate = Path(f"/run/user/{uid}") if runtime_dir is None else runtime_dir
        if candidate.is_dir():
            resolved["XDG_RUNTIME_DIR"] = str(candidate)
    runtime = Path(resolved["XDG_RUNTIME_DIR"]) if "XDG_RUNTIME_DIR" in resolved else None

    if "DISPLAY" not in resolved:
        number = lowest_owned_x_display(
            X11_SOCKET_DIR if x11_socket_dir is None else x11_socket_dir, uid
        )
        if number is not None:
            resolved["DISPLAY"] = f":{number}"
    if "XAUTHORITY" not in resolved:
        cookie = x_authority_file(runtime, Path.home() if home is None else home)
        if cookie is not None:
            resolved["XAUTHORITY"] = str(cookie)
    return resolved


def lowest_owned_x_display(socket_dir: Path, uid: int) -> int | None:
    """The lowest ``:N`` this user actually owns a socket for.

    Ownership is the point rather than a nicety: a GNOME host also advertises
    ``X1024`` and friends belonging to ``gdm``'s greeter, and connecting to one
    of those fails authentication with the same error as having no display.
    """
    try:
        entries = list(socket_dir.iterdir())
    except OSError:
        return None
    best: int | None = None
    for entry in entries:
        suffix = entry.name[1:]
        if not entry.name.startswith("X") or not suffix.isdigit():
            continue
        try:
            if not entry.is_socket() or entry.stat().st_uid != uid:
                continue
        except OSError:
            continue
        number = int(suffix)
        if best is None or number < best:
            best = number
    return best


def x_authority_file(runtime_dir: Path | None, home: Path) -> Path | None:
    """The X cookie: GNOME's Xwayland one first, then the classic file.

    Xwayland's cookie carries a random suffix regenerated at every login, so it
    is matched by pattern rather than remembered -- pinning the name into a
    launcher's config buys a setup that breaks at the next reboot.
    """
    if runtime_dir is not None:
        try:
            newest, newest_mtime = None, -1.0
            for candidate in runtime_dir.glob(XWAYLAND_AUTH_GLOB):
                try:
                    mtime = candidate.stat().st_mtime if candidate.is_file() else None
                except OSError:
                    continue
                if mtime is not None and mtime > newest_mtime:
                    newest, newest_mtime = candidate, mtime
            if newest is not None:
                return newest
        except OSError:
            pass
    classic = home / ".Xauthority"
    try:
        return classic if classic.is_file() else None
    except OSError:
        return None


def child_env_from_env(env: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    """``env`` plus whatever display variables it was missing, as sorted pairs."""
    merged = dict(env)
    merged.update(display_env_from_env(env))
    return tuple(sorted(merged.items()))
