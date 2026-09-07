"""The component's internal value objects. Pure, so unit tests -- and no fakes.

Rule 1 §6 forbids mocking anywhere in this layer, including here. These are unit
tests because :mod:`.models` touches nothing, not because anything was replaced.
"""

from __future__ import annotations

import pytest

from app_automating.modules.appium_module.infrastructure.appium_device_manager.models import (
    MAX_OUTPUT_TAIL,
    CommandResult,
    ServerStatus,
    display_command,
)

pytestmark = pytest.mark.unit


def _result(returncode: int = 0, stdout: str = "", stderr: str = "") -> CommandResult:
    return CommandResult(
        argv=("appium", "-v"),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=0.1,
    )


# -- display_command -------------------------------------------------------


def test_a_command_renders_as_something_a_person_could_paste() -> None:
    """Error messages carry this; a half-quoted argv is worse than none."""
    assert display_command(("appium", "driver", "install", "uiautomator2")) == (
        "appium driver install uiautomator2"
    )


def test_an_argument_with_a_space_is_quoted() -> None:
    rendered = display_command(("appium", "--base-path", "/two words"))

    assert "'/two words'" in rendered


def test_a_list_renders_the_same_as_a_tuple() -> None:
    """The helper is called with both; disagreeing would make messages differ by
    the caller's container type."""
    assert display_command(["a", "b"]) == display_command(("a", "b"))


# -- CommandResult ---------------------------------------------------------


def test_ok_is_exit_zero_and_nothing_else() -> None:
    assert _result(0).ok is True
    assert _result(1).ok is False
    assert _result(-9).ok is False


def test_output_joins_both_streams_because_tools_disagree_about_which_to_use() -> None:
    combined = _result(stdout="on out", stderr="on err").output

    assert "on out" in combined
    assert "on err" in combined


def test_an_empty_stream_is_dropped_rather_than_leaving_a_blank_line() -> None:
    assert _result(stdout="only out", stderr="   ").output == "only out"
    assert _result(stdout="", stderr="only err").output == "only err"


def test_a_short_tail_is_the_whole_output() -> None:
    assert _result(stdout="brief").tail == "brief"


def test_a_long_tail_is_bounded_and_marked_as_cut() -> None:
    """A chatty npm install must not put a megabyte into an exception message."""
    result = _result(stdout="x" * (MAX_OUTPUT_TAIL * 3))

    tail = result.tail

    assert len(tail) == MAX_OUTPUT_TAIL + 3
    assert tail.startswith("...")


def test_the_tail_keeps_the_end_because_that_is_where_the_error_is() -> None:
    result = _result(stdout="noise " * 2000 + "THE ACTUAL ERROR")

    assert result.tail.endswith("THE ACTUAL ERROR")


def test_command_renders_the_argv_that_ran() -> None:
    assert _result().command == "appium -v"


# -- ServerStatus ----------------------------------------------------------


def test_a_status_defaults_to_unmanaged_with_no_pid() -> None:
    """The safe default: a server we did not start is one we must not kill."""
    status = ServerStatus(url="http://127.0.0.1:4723", running=True)

    assert status.managed is False
    assert status.pid is None


def test_managed_and_running_are_independent_facts() -> None:
    """A managed server can be down (it died) and an unmanaged one can be up."""
    ours_dead = ServerStatus(url="u", running=False, managed=True, pid=42)
    theirs_alive = ServerStatus(url="u", running=True, managed=False)

    assert (ours_dead.running, ours_dead.managed) == (False, True)
    assert (theirs_alive.running, theirs_alive.managed) == (True, False)
