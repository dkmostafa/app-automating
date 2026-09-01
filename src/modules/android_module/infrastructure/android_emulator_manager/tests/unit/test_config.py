"""Unit tests for the pure half of ``config.py``.

``from_environment`` takes the environment as an argument, so its resolution
rules are testable without touching the real one. The half that reads the actual
host lives in ``../integration/test_config.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from modules.android_module.infrastructure.android_emulator_manager import (
    DEFAULT_HEADLESS_ARGS,
    AndroidSdkConfig,
    avd_home_from_env,
    child_env_from_env,
    display_env_from_env,
    sdk_root_from_env,
)

pytestmark = pytest.mark.unit


def test_sdk_root_prefers_android_sdk_root_over_android_home() -> None:
    env = {"ANDROID_SDK_ROOT": "/first", "ANDROID_HOME": "/second"}
    assert sdk_root_from_env(env) == Path("/first")


def test_sdk_root_falls_back_to_android_home() -> None:
    assert sdk_root_from_env({"ANDROID_HOME": "/second"}) == Path("/second")


def test_avd_home_prefers_the_explicit_variable() -> None:
    assert avd_home_from_env({"ANDROID_AVD_HOME": "/avds"}) == Path("/avds")


def test_avd_home_understands_the_legacy_sdk_home_layout() -> None:
    assert avd_home_from_env({"ANDROID_SDK_HOME": "/legacy"}) == Path("/legacy/.android/avd")


def test_avd_home_defaults_under_the_users_home() -> None:
    assert avd_home_from_env({}) == Path.home() / ".android" / "avd"


def test_from_environment_reads_the_mapping_it_is_given() -> None:
    config = AndroidSdkConfig.from_environment(
        {"ANDROID_SDK_ROOT": "/sdk", "ANDROID_AVD_HOME": "/avds"}
    )
    assert config.sdk_root == Path("/sdk")
    assert config.avd_home == Path("/avds")


def test_overrides_win_over_the_environment() -> None:
    config = AndroidSdkConfig.from_environment(
        {"ANDROID_SDK_ROOT": "/sdk"}, sdk_root=Path("/pinned"), adb_timeout_seconds=1.5
    )
    assert config.sdk_root == Path("/pinned")
    assert config.adb_timeout_seconds == 1.5


def test_every_timeout_is_a_field_so_a_test_can_pin_it() -> None:
    """Rule 1 §4: no timeout is hardcoded inside a method."""
    config = AndroidSdkConfig()
    for field in (
        "adb_timeout_seconds",
        "avdmanager_timeout_seconds",
        "sdkmanager_timeout_seconds",
        "emulator_boot_timeout_seconds",
        "emulator_stop_timeout_seconds",
        "boot_poll_interval_seconds",
    ):
        assert isinstance(getattr(config, field), float)


def test_headless_args_default_to_a_windowless_launch() -> None:
    assert "-no-window" in AndroidSdkConfig().headless_args
    assert AndroidSdkConfig().headless_args == DEFAULT_HEADLESS_ARGS


def test_config_is_frozen() -> None:
    with pytest.raises(AttributeError):
        AndroidSdkConfig().adb_timeout_seconds = 5.0  # type: ignore[misc]


# --------------------------------------------------------------------------
# the child environment
#
# An environment that already names a display and a cookie is fully specified,
# so these stay pure: nothing below is discovered from the filesystem. The
# discovery half is exercised for real in ``../integration/test_config.py``.
# --------------------------------------------------------------------------

FULLY_SPECIFIED = {"DISPLAY": ":3", "XAUTHORITY": "/cookies/jar"}


def test_display_env_carries_through_what_the_environment_already_has() -> None:
    assert display_env_from_env(FULLY_SPECIFIED) == {"DISPLAY": ":3", "XAUTHORITY": "/cookies/jar"}


def test_display_env_keeps_a_runtime_dir_it_is_given() -> None:
    env = {**FULLY_SPECIFIED, "XDG_RUNTIME_DIR": "/run/user/7"}
    assert display_env_from_env(env)["XDG_RUNTIME_DIR"] == "/run/user/7"


def test_display_env_never_advertises_wayland() -> None:
    """The emulator's bundled Qt has no wayland plugin; naming a wayland socket
    only invites it to pick a platform it cannot load."""
    env = {**FULLY_SPECIFIED, "WAYLAND_DISPLAY": "wayland-0"}
    assert "WAYLAND_DISPLAY" not in display_env_from_env(env)


def test_an_empty_display_is_treated_as_absent_not_as_a_value() -> None:
    env = {**FULLY_SPECIFIED, "DISPLAY": ""}
    assert display_env_from_env(env, x11_socket_dir=Path("/nonexistent")).get("DISPLAY") != ""


def test_child_env_keeps_every_variable_it_was_given() -> None:
    env = {**FULLY_SPECIFIED, "PATH": "/bin", "ANDROID_SDK_ROOT": "/sdk"}
    assert dict(child_env_from_env(env)) == env


def test_child_env_adds_the_display_to_an_environment_without_one() -> None:
    env = {**FULLY_SPECIFIED, "PATH": "/bin"}
    pairs = child_env_from_env(env)
    assert ("DISPLAY", ":3") in pairs
    assert ("PATH", "/bin") in pairs


def test_child_env_is_sorted_so_a_config_compares_equal_across_builds() -> None:
    env = {"PATH": "/bin", **FULLY_SPECIFIED}
    assert child_env_from_env(env) == tuple(sorted(child_env_from_env(env)))


def test_from_environment_fills_the_child_environment() -> None:
    config = AndroidSdkConfig.from_environment({**FULLY_SPECIFIED, "ANDROID_SDK_ROOT": "/sdk"})
    assert config.child_environ is not None
    assert config.child_environ["DISPLAY"] == ":3"


def test_a_hand_written_config_lets_the_child_inherit() -> None:
    """``None`` means "inherit", which is what every existing caller expects."""
    assert AndroidSdkConfig().child_environ is None


def test_child_env_stays_hashable_so_the_config_can_be_frozen() -> None:
    config = AndroidSdkConfig.from_environment(FULLY_SPECIFIED)
    assert isinstance(config.child_env, tuple)
    with pytest.raises(AttributeError):
        config.child_env = ()  # type: ignore[misc]
