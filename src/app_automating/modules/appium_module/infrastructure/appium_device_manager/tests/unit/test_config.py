"""The pure half of the config: resolution rules, and the URL it derives.

:meth:`AppiumConfig.from_environment` takes an explicit mapping, which is what
makes these unit tests rather than integration ones -- the rules are pure, and
only the *default argument* reaches for ``os.environ``. That half is covered in
``integration/test_config.py``.

No mocks and no ``monkeypatch.setenv``, per Rule 1 §6: a dict is passed in,
because the signature was designed to let it be.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from app_automating.modules.appium_module.infrastructure.appium_device_manager.config import (
    DEFAULT_SERVER_HOST,
    DEFAULT_SERVER_PORT,
    ENV_PREFIX,
    AppiumConfig,
)

pytestmark = pytest.mark.unit


def _from(**values: str) -> AppiumConfig:
    """Build a config from an explicit environment, prefixes applied for us."""
    return AppiumConfig.from_environment({f"{ENV_PREFIX}{k}": v for k, v in values.items()})


# -- defaults --------------------------------------------------------------


def test_an_empty_environment_yields_the_documented_defaults() -> None:
    config = AppiumConfig.from_environment({})

    assert config.appium_path == "appium"
    assert config.server_host == DEFAULT_SERVER_HOST
    assert config.server_port == DEFAULT_SERVER_PORT
    assert config.manage_server is True
    assert config.driver_name == "uiautomator2"


def test_the_default_host_is_loopback_rather_than_every_interface() -> None:
    """An Appium server drives every attached device with no authentication.
    Binding it to 0.0.0.0 by default would hand that to the local network."""
    assert DEFAULT_SERVER_HOST == "127.0.0.1"
    assert AppiumConfig().server_host == "127.0.0.1"


def test_the_config_is_frozen_so_nothing_downstream_can_retune_it() -> None:
    config = AppiumConfig()

    with pytest.raises(dataclasses.FrozenInstanceError):
        config.server_port = 1234  # type: ignore[misc]


def test_replace_is_what_a_test_uses_to_pin_a_timeout() -> None:
    """Rule 1 §4's payoff: a real 1ms timeout instead of a mocked one."""
    config = dataclasses.replace(AppiumConfig(), command_timeout_seconds=0.001)

    assert config.command_timeout_seconds == 0.001
    assert config.appium_path == "appium"


# -- reading the environment ----------------------------------------------


def test_every_documented_setting_is_read_from_its_prefixed_name() -> None:
    config = _from(
        PATH_BIN="/opt/appium",
        NODE_PATH="/opt/node",
        NPM_PATH="/opt/npm",
        SERVER_HOST="0.0.0.0",
        SERVER_PORT="4800",
        SERVER_BASE_PATH="/wd/hub",
        PLATFORM_NAME="Android",
        AUTOMATION_NAME="UiAutomator2",
        DRIVER_NAME="uiautomator2",
    )

    assert config.appium_path == "/opt/appium"
    assert config.node_path == "/opt/node"
    assert config.npm_path == "/opt/npm"
    assert config.server_host == "0.0.0.0"
    assert config.server_port == 4800
    assert config.server_base_path == "/wd/hub"


def test_an_unprefixed_name_is_ignored() -> None:
    """The AT_APPIUM_ namespace is the contract; a bare SERVER_PORT is someone
    else's variable and must not silently retune this server."""
    config = AppiumConfig.from_environment({"SERVER_PORT": "9999", "APPIUM_SERVER_PORT": "8888"})

    assert config.server_port == DEFAULT_SERVER_PORT


def test_a_blank_value_falls_back_to_the_default() -> None:
    """An unset variable in a .env file is usually written as an empty one."""
    config = _from(SERVER_HOST="   ", DRIVER_NAME="")

    assert config.server_host == DEFAULT_SERVER_HOST
    assert config.driver_name == "uiautomator2"


def test_values_are_stripped_of_surrounding_whitespace() -> None:
    assert _from(PATH_BIN="  /opt/appium  ").appium_path == "/opt/appium"


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_the_truthy_spellings_all_enable_a_flag(value: str) -> None:
    assert _from(MANAGE_SERVER=value).manage_server is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "anything else"])
def test_everything_else_disables_it(value: str) -> None:
    assert _from(MANAGE_SERVER=value).manage_server is False


def test_a_malformed_number_falls_back_rather_than_refusing_to_start() -> None:
    """A typo in a .env file is not a reason to refuse to boot the server; the
    documented default is the safer reading, and the setting is visible."""
    assert _from(SERVER_PORT="not-a-port").server_port == DEFAULT_SERVER_PORT
    assert _from(COMMAND_TIMEOUT_SECONDS="soon").command_timeout_seconds == 60.0


def test_timeouts_are_read_as_floats_so_sub_second_values_survive() -> None:
    config = _from(SERVER_POLL_INTERVAL_SECONDS="0.05", INTERACTION_TIMEOUT_SECONDS="90.5")

    assert config.server_poll_interval_seconds == 0.05
    assert config.interaction_timeout_seconds == 90.5


def test_a_log_path_is_expanded_and_becomes_a_path() -> None:
    config = _from(SERVER_LOG_PATH="~/logs/appium.log")

    assert isinstance(config.server_log_path, Path)
    assert "~" not in str(config.server_log_path)


def test_no_log_path_is_none_rather_than_an_empty_path() -> None:
    assert AppiumConfig.from_environment({}).server_log_path is None


# -- the derived URLs ------------------------------------------------------


def test_the_default_url_carries_no_base_path_segment() -> None:
    """Appium 3 serves at the root; a trailing '/' would make every request path
    start with a double slash."""
    assert AppiumConfig().server_url == "http://127.0.0.1:4723"


def test_the_status_url_hangs_off_the_server_url() -> None:
    assert AppiumConfig().status_url == "http://127.0.0.1:4723/status"


def test_a_legacy_base_path_is_kept_for_an_appium_1_server() -> None:
    """The reason the setting exists: pointing at an old server is config, not code."""
    config = AppiumConfig(server_base_path="/wd/hub")

    assert config.server_url == "http://127.0.0.1:4723/wd/hub"
    assert config.status_url == "http://127.0.0.1:4723/wd/hub/status"


def test_a_base_path_without_a_leading_slash_is_still_built_correctly() -> None:
    assert AppiumConfig(server_base_path="wd/hub").server_url.endswith("/wd/hub")


def test_a_trailing_slash_on_the_base_path_does_not_double_up() -> None:
    assert AppiumConfig(server_base_path="/wd/hub/").server_url.endswith("/wd/hub")


def test_the_url_follows_the_host_and_port() -> None:
    config = AppiumConfig(server_host="10.0.0.5", server_port=4800)

    assert config.server_url == "http://10.0.0.5:4800"
