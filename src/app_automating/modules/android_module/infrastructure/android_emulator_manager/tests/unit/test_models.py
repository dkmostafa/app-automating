"""Unit tests for ``models.py`` -- the component's internal value objects."""

from __future__ import annotations

import pytest

from app_automating.modules.android_module.infrastructure.android_emulator_manager import (
    CommandResult,
)
from app_automating.modules.android_module.infrastructure.android_emulator_manager.models import (
    display_command,
)

pytestmark = pytest.mark.unit


def _result(stdout: str = "", stderr: str = "", returncode: int = 0) -> CommandResult:
    return CommandResult(
        argv=("adb", "devices", "-l"),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=0.25,
    )


def test_display_command_joins_the_argv_for_a_human() -> None:
    assert display_command(("adb", "-s", "emulator-5554", "emu", "kill")) == (
        "adb -s emulator-5554 emu kill"
    )
    assert _result().display_command == "adb devices -l"


def test_output_merges_both_pipes() -> None:
    """Android tools are inconsistent about which pipe an error lands on."""
    merged = _result(stdout="on stdout", stderr="on stderr").output
    assert "on stdout" in merged
    assert "on stderr" in merged


def test_first_error_line_skips_blank_lines() -> None:
    assert _result(stdout="\n\n   \nthe real problem\nlater noise").first_error_line() == (
        "the real problem"
    )


def test_first_error_line_falls_back_to_stderr_then_to_a_placeholder() -> None:
    assert _result(stderr="only on stderr").first_error_line() == "only on stderr"
    assert _result().first_error_line() == "(no output)"


def test_command_result_is_frozen() -> None:
    with pytest.raises(AttributeError):
        _result().returncode = 2  # type: ignore[misc]
