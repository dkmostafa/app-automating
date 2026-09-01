"""The half of the config that is not pure: does it find this host's tools.

``from_environment()`` with no argument reads ``os.environ``, and "does the
resolved path actually point at something on this machine" is not a question a
unit test can ask. Everything here touches the real host, so it lives in
``integration/`` (Rule 2 §2) -- and touches it read-only.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from modules.appium_module.infrastructure.appium_device_manager import (
    AppiumConfig,
    AppiumToolNotFoundError,
    CommandRunner,
)

pytestmark = pytest.mark.integration


def test_the_real_environment_is_readable_and_yields_a_usable_config() -> None:
    """The default argument path -- the only line in the component that touches
    ``os.environ``, and the one a unit test deliberately cannot reach."""
    config = AppiumConfig.from_environment()

    assert config.appium_path
    assert config.server_port > 0
    assert config.server_url.startswith("http://")


def test_reading_the_environment_twice_gives_the_same_answer() -> None:
    """Resolution has no hidden state; nothing is cached between calls."""
    assert AppiumConfig.from_environment() == AppiumConfig.from_environment()


def test_the_explicit_mapping_form_agrees_with_the_ambient_one() -> None:
    """The pure form is tested exhaustively in unit/; this is the one assertion
    that the two paths are the same code."""
    assert AppiumConfig.from_environment(dict(os.environ)) == AppiumConfig.from_environment()


def test_a_configured_appium_resolves_to_a_real_executable(appium_installed: None) -> None:
    """The whole point of resolution: the config names a tool, and this host has it."""
    runner = CommandRunner(AppiumConfig.from_environment())

    resolved = runner.tool_path("appium")

    assert Path(resolved).is_file()
    assert os.access(resolved, os.X_OK)


def test_resolution_finds_the_same_appium_the_shell_would(appium_installed: None) -> None:
    """A config that quietly picks a different binary from the developer's own
    ``appium`` would make every failure impossible to reproduce by hand."""
    runner = CommandRunner(AppiumConfig.from_environment())

    assert runner.tool_path("appium") == shutil.which("appium")


def test_an_absolute_path_to_a_real_binary_is_taken_as_given(appium_installed: None) -> None:
    real = shutil.which("appium")
    config = AppiumConfig(appium_path=str(real))

    assert CommandRunner(config).tool_path("appium") == str(real)


def test_a_config_pointing_at_a_path_that_is_not_there_fails_with_where_it_looked() -> None:
    """A real nonexistent path, not a patched lookup -- Rule 1 §6."""
    config = AppiumConfig(appium_path="/nonexistent/appium-does-not-exist")

    with pytest.raises(AppiumToolNotFoundError) as excinfo:
        CommandRunner(config).tool_path("appium")

    assert excinfo.value.tool == "appium"
    assert "/nonexistent/appium-does-not-exist" in excinfo.value.searched


def test_a_bare_name_that_is_on_no_path_fails_too() -> None:
    config = AppiumConfig(appium_path="definitely-not-a-real-binary-name-xyz")

    with pytest.raises(AppiumToolNotFoundError) as excinfo:
        CommandRunner(config).tool_path("appium")

    assert "PATH" in excinfo.value.searched


def test_a_directory_is_not_an_executable(tmp_path: Path) -> None:
    """`is_file()` and the executable bit are both checked, so a directory whose
    name matches does not resolve."""
    config = AppiumConfig(appium_path=str(tmp_path))

    with pytest.raises(AppiumToolNotFoundError):
        CommandRunner(config).tool_path("appium")


def test_a_non_executable_file_is_not_a_tool(tmp_path: Path) -> None:
    candidate = tmp_path / "appium"
    candidate.write_text("#!/bin/sh\necho hi\n")
    candidate.chmod(0o644)

    with pytest.raises(AppiumToolNotFoundError):
        CommandRunner(AppiumConfig(appium_path=str(candidate))).tool_path("appium")


def test_find_tool_answers_the_same_question_without_raising() -> None:
    """`check_environment` needs "is it there" as data, not as an exception."""
    present = CommandRunner(AppiumConfig.from_environment())
    absent = CommandRunner(AppiumConfig(appium_path="/nonexistent/appium-xyz"))

    assert absent.find_tool("appium") is None
    assert present.find_tool("appium") == shutil.which("appium")
