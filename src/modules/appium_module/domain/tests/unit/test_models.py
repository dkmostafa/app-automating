"""The domain's DTOs: the derived reads, and the invariants that hold without I/O.

Unit tests because the code is pure (Rule 2 §2), not because anything was
replaced -- there is nothing here to replace. Every assertion below runs on a
host with no Appium, no Node and no device.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from modules.appium_module.domain.models import (
    FOCUSED_STRATEGY,
    LOCATOR_STRATEGIES,
    SCROLL_DIRECTIONS,
    AppiumSession,
    EnvironmentStatus,
    ListSessionsResult,
    StartSessionRequest,
    ToolStatus,
)

pytestmark = pytest.mark.unit


def _session(session_id: str = "s1", device_id: str = "emulator-5554", **kwargs) -> AppiumSession:
    return AppiumSession(
        session_id=session_id,
        device_id=device_id,
        platform_name="Android",
        automation_name="UiAutomator2",
        **kwargs,
    )


def _status(*tools: ToolStatus, drivers: tuple[str, ...] = ("uiautomator2",)) -> EnvironmentStatus:
    return EnvironmentStatus(
        tools=tools,
        drivers=drivers,
        server_url="http://127.0.0.1:4723",
        server_running=False,
    )


# -- requests are frozen and default-complete ------------------------------


def test_a_request_with_only_its_required_field_is_complete() -> None:
    """Rule 1 §2: optional inputs get defaults, so a call site stays one line."""
    request = StartSessionRequest(device_id="emulator-5554")

    assert request.app_path is None
    assert request.app_package is None
    assert request.no_reset is True
    assert request.extra_capabilities == ()


def test_requests_are_frozen_so_a_caller_cannot_be_edited_underneath() -> None:
    request = StartSessionRequest(device_id="emulator-5554")

    with pytest.raises(dataclasses.FrozenInstanceError):
        request.device_id = "emulator-5556"  # type: ignore[misc]


def test_replace_is_how_a_request_is_narrowed() -> None:
    """The adapter normalises a serial with `replace`; the caller's copy is untouched."""
    original = StartSessionRequest(device_id=" emulator-5554 ", app_package="com.app")
    narrowed = dataclasses.replace(original, device_id="emulator-5554")

    assert narrowed.device_id == "emulator-5554"
    assert narrowed.app_package == "com.app"
    assert original.device_id == " emulator-5554 "


# -- AppiumSession ---------------------------------------------------------


def test_a_session_without_a_screen_size_says_so() -> None:
    """`has_screen_size` is what stops the manager deriving a scroll from None."""
    assert _session().has_screen_size is False
    assert _session(screen_width=1080, screen_height=2400).has_screen_size is True


def test_a_half_known_screen_size_does_not_count_as_known() -> None:
    """One dimension is not enough to place a gesture, so it must read as absent."""
    assert _session(screen_width=1080).has_screen_size is False
    assert _session(screen_height=2400).has_screen_size is False


# -- ListSessionsResult ----------------------------------------------------


def test_the_session_list_exposes_the_ids_a_caller_would_otherwise_collect() -> None:
    result = ListSessionsResult(sessions=(_session("a"), _session("b")))

    assert result.session_ids == ("a", "b")


def test_a_session_can_be_found_by_id_and_missing_is_none_not_an_error() -> None:
    """A finder answering "no" is a read, not a failure; only operations raise."""
    result = ListSessionsResult(sessions=(_session("a"),))

    assert result.by_session_id("a") is not None
    assert result.by_session_id("nope") is None


def test_sessions_can_be_found_by_device_because_a_device_may_have_had_several() -> None:
    result = ListSessionsResult(
        sessions=(_session("a", "emulator-5554"), _session("b", "R58M12"), _session("c", "R58M12"))
    )

    assert result.by_device_id("R58M12") == (result.sessions[1], result.sessions[2])
    assert result.by_device_id("emulator-9999") == ()


def test_an_empty_session_list_is_a_normal_value() -> None:
    """Zero open sessions is an answer, and must not need a None check."""
    assert ListSessionsResult().sessions == ()
    assert ListSessionsResult().session_ids == ()


# -- EnvironmentStatus -----------------------------------------------------


def test_a_host_is_ready_only_when_every_tool_and_a_driver_are_there() -> None:
    ready = _status(ToolStatus("node", True, "25.2.1"), ToolStatus("appium", True, "3.1.1"))

    assert ready.ready is True
    assert ready.missing == ()


def test_a_missing_tool_makes_the_host_not_ready_and_is_named() -> None:
    status = _status(ToolStatus("node", True, "25.2.1"), ToolStatus("appium", False))

    assert status.ready is False
    assert status.missing == ("appium",)


def test_every_tool_present_but_no_driver_is_still_not_ready() -> None:
    """The failure this guards: a perfect toolchain that cannot drive anything."""
    status = _status(ToolStatus("node", True), ToolStatus("appium", True), drivers=())

    assert status.ready is False
    assert status.missing == ()


def test_a_tool_can_be_looked_up_by_name() -> None:
    status = _status(ToolStatus("node", True, "25.2.1", Path("/usr/bin/node")))

    assert status.tool("node") is not None
    assert status.tool("node").version == "25.2.1"
    assert status.tool("rustc") is None


# -- the shared vocabularies ----------------------------------------------


def test_the_locator_strategies_lead_with_the_most_stable_one() -> None:
    """The order is documentation: a caller reading the tuple should reach for
    accessibility_id first and xpath last."""
    assert LOCATOR_STRATEGIES[0] == "accessibility_id"
    assert LOCATOR_STRATEGIES[-2] == "xpath"


def test_the_four_scroll_directions_are_the_whole_set() -> None:
    assert set(SCROLL_DIRECTIONS) == {"up", "down", "left", "right"}


def test_the_focused_strategy_is_not_a_locator_a_caller_may_pass() -> None:
    """It reports how an element was reached, never how to reach one. Accepting
    it as input would promise a lookup nothing can perform."""
    assert FOCUSED_STRATEGY not in LOCATOR_STRATEGIES
