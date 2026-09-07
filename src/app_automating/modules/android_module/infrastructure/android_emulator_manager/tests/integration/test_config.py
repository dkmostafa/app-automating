"""Integration tests for ``config.py``: resolution against the real host."""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

from app_automating.modules.android_module.infrastructure.android_emulator_manager import (
    AndroidSdkConfig,
    display_env_from_env,
)
from app_automating.modules.android_module.infrastructure.android_emulator_manager.config import (
    lowest_owned_x_display,
    x_authority_file,
)

pytestmark = pytest.mark.integration


def test_from_environment_finds_the_sdk_installed_on_this_host(
    sdk_config: AndroidSdkConfig,
) -> None:
    assert sdk_config.sdk_root is not None
    assert (sdk_config.sdk_root / "platform-tools").is_dir()
    assert sdk_config.avd_home is not None


def test_from_environment_resolves_an_avd_home_that_exists(
    sdk_config: AndroidSdkConfig,
) -> None:
    """The composition root relies on this being filled, so nothing below it
    ever has to consult the environment."""
    assert sdk_config.avd_home is not None
    assert sdk_config.avd_home.is_absolute()


# --------------------------------------------------------------------------
# display discovery, against real sockets and real cookie files
#
# Rule 2 §3: nothing here is mocked. The X sockets below are real ``AF_UNIX``
# sockets in ``tmp_path`` and the cookies are real files.
# --------------------------------------------------------------------------


def _x_socket(directory: Path, number: int) -> socket.socket:
    """Bind a real unix socket named the way an X server names its own."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(directory / f"X{number}"))
    return sock


def test_the_lowest_display_this_user_owns_wins(tmp_path: Path) -> None:
    with _x_socket(tmp_path, 5), _x_socket(tmp_path, 2):
        assert lowest_owned_x_display(tmp_path, os.getuid()) == 2


def test_a_regular_file_named_like_a_socket_is_not_a_display(tmp_path: Path) -> None:
    (tmp_path / "X0").write_text("not a socket")
    assert lowest_owned_x_display(tmp_path, os.getuid()) is None


def test_a_socket_owned_by_another_user_is_skipped(tmp_path: Path) -> None:
    """A GNOME host advertises ``gdm``'s greeter sockets too, and connecting to
    one fails authentication with the same error as having no display at all."""
    with _x_socket(tmp_path, 1024):
        assert lowest_owned_x_display(tmp_path, os.getuid() + 1) is None


def test_a_missing_socket_directory_is_not_an_error(tmp_path: Path) -> None:
    assert lowest_owned_x_display(tmp_path / "nowhere", os.getuid()) is None


def test_the_xwayland_cookie_is_found_by_pattern(tmp_path: Path) -> None:
    """Its suffix is regenerated at every login, so it cannot be remembered."""
    cookie = tmp_path / ".mutter-Xwaylandauth.QK3J1Z"
    cookie.write_bytes(b"")
    assert x_authority_file(tmp_path, tmp_path) == cookie


def test_the_classic_cookie_is_the_fallback(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    classic = home / ".Xauthority"
    classic.write_bytes(b"")
    assert x_authority_file(tmp_path, home) == classic


def test_no_cookie_anywhere_is_not_an_error(tmp_path: Path) -> None:
    assert x_authority_file(tmp_path, tmp_path) is None


def test_a_scrubbed_environment_still_finds_this_hosts_display(tmp_path: Path) -> None:
    """The bug this fixes: an MCP server is handed an environment with no
    ``DISPLAY``, and a child that inherits it cannot open a window."""
    if lowest_owned_x_display(Path("/tmp/.X11-unix"), os.getuid()) is None:
        pytest.skip("this host has no X display owned by the current user")
    scrubbed = {"HOME": str(Path.home()), "PATH": "/usr/bin"}
    resolved = display_env_from_env(scrubbed)
    assert resolved["DISPLAY"].startswith(":")


def test_from_environment_hands_children_a_usable_environment() -> None:
    config = AndroidSdkConfig.from_environment({"HOME": str(Path.home()), "PATH": "/usr/bin"})
    assert config.child_environ is not None
    assert config.child_environ["PATH"] == "/usr/bin"
