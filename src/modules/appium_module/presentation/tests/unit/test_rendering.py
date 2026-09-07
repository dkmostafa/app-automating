"""The converter: domain results become the exact payloads the docstrings promise.

Rule 3 §7 names the property this file exists to hold: *the example in the
docstring and the assertion in the test are the same payload*. Where a tool's
**Returns** section shows a shape, the test below builds the domain object and
asserts that shape comes out.

Everything here is pure -- no mocks, no awaits, no I/O, because rendering has
none.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from modules.appium_module.domain.models import (
    AppiumSession,
    ElementInteractionResult,
    EndSessionResult,
    EnvironmentStatus,
    InstallDriverResult,
    InteractionResult,
    ListSessionsResult,
    PageSourceResult,
    ScreenshotResult,
    StartSessionResult,
    ToolStatus,
)
from modules.appium_module.presentation.rendering import (
    render_element_interaction,
    render_ended_session,
    render_environment,
    render_installed_driver,
    render_interaction,
    render_page_source,
    render_screenshot,
    render_session,
    render_session_list,
    render_started_session,
    render_tool_status,
)

pytestmark = pytest.mark.unit


def _session(**kwargs) -> AppiumSession:
    defaults: dict = dict(
        session_id="8f2c",
        device_id="emulator-5554",
        platform_name="Android",
        automation_name="UiAutomator2",
    )
    return AppiumSession(**{**defaults, **kwargs})


# -- the host --------------------------------------------------------------


def test_a_tool_status_renders_its_path_as_a_string() -> None:
    """A Path is not JSON; this is the single reason the helper exists."""
    payload = render_tool_status(
        ToolStatus(name="node", present=True, version="25.2.1", path=Path("/usr/bin/node"))
    )

    assert payload.path == "/usr/bin/node"
    assert isinstance(payload.path, str)


def test_a_missing_tool_renders_its_reason_and_no_path() -> None:
    payload = render_tool_status(ToolStatus(name="appium", present=False, detail="not on PATH"))

    assert payload.present is False
    assert payload.path is None
    assert payload.detail == "not on PATH"


def test_the_environment_payload_matches_the_docstrings_example() -> None:
    """The shape `appium_check_environment` promises under **Returns**."""
    status = EnvironmentStatus(
        tools=(
            ToolStatus("node", True, "25.2.1", Path("/usr/bin/node")),
            ToolStatus("appium", True, "3.1.1", Path("/usr/bin/appium")),
        ),
        drivers=("uiautomator2",),
        server_url="http://127.0.0.1:4723",
        server_running=False,
    )

    payload = render_environment(status)

    assert payload.model_dump() == {
        "ready": True,
        "tools": (
            {
                "name": "node",
                "present": True,
                "version": "25.2.1",
                "path": "/usr/bin/node",
                "detail": None,
            },
            {
                "name": "appium",
                "present": True,
                "version": "3.1.1",
                "path": "/usr/bin/appium",
                "detail": None,
            },
        ),
        "missing": (),
        "drivers": ("uiautomator2",),
        "server_url": "http://127.0.0.1:4723",
        "server_running": False,
    }


def test_readiness_comes_from_the_domain_rather_than_being_re_derived() -> None:
    """ "Can I start a session" is a business question, and presentation must not
    be the second place that answers it."""
    status = EnvironmentStatus(
        tools=(ToolStatus("node", True), ToolStatus("appium", True)),
        drivers=(),
        server_url="http://x",
        server_running=False,
    )

    payload = render_environment(status)

    assert payload.ready is status.ready is False


def test_an_installed_driver_renders_its_result() -> None:
    payload = render_installed_driver(
        InstallDriverResult(
            driver_name="uiautomator2",
            version="6.3.0",
            already_installed=True,
            duration_seconds=0.9,
        )
    )

    assert payload.model_dump() == {
        "driver_name": "uiautomator2",
        "version": "6.3.0",
        "already_installed": True,
        "duration_seconds": 0.9,
    }


# -- sessions --------------------------------------------------------------


def test_a_session_renders_every_field_the_caller_needs() -> None:
    payload = render_session(
        _session(app_package="com.android.settings", screen_width=1080, screen_height=2400)
    )

    assert payload.model_dump() == {
        "session_id": "8f2c",
        "device_id": "emulator-5554",
        "platform_name": "Android",
        "automation_name": "UiAutomator2",
        "app_package": "com.android.settings",
        "app_activity": None,
        "screen_width": 1080,
        "screen_height": 2400,
    }


def test_a_started_session_lifts_its_id_to_the_top_level() -> None:
    """Exactly what `appium_start_session`'s **Returns** section shows, and the
    reason it shows it: the id is the next call's argument."""
    result = StartSessionResult(
        session=_session(screen_width=1080, screen_height=2400),
        server_started=True,
        duration_seconds=12.4,
    )

    payload = render_started_session(result)

    assert payload.session_id == "8f2c"
    assert payload.session.session_id == "8f2c"
    assert payload.server_started is True


def test_an_ended_session_names_the_device_that_is_now_free() -> None:
    payload = render_ended_session(
        EndSessionResult(session_id="8f2c", device_id="emulator-5554", duration_seconds=0.6)
    )

    assert payload.model_dump() == {
        "session_id": "8f2c",
        "device_id": "emulator-5554",
        "duration_seconds": 0.6,
    }


def test_the_session_list_carries_the_ids_and_the_count() -> None:
    result = ListSessionsResult(sessions=(_session(), _session(session_id="b")))

    payload = render_session_list(result)

    assert payload.session_ids == ("8f2c", "b")
    assert payload.count == 2


def test_an_empty_session_list_renders_as_a_zero_count_not_an_error() -> None:
    payload = render_session_list(ListSessionsResult())

    assert payload.count == 0
    assert payload.sessions == ()


# -- reading the screen ----------------------------------------------------


def test_a_screenshot_written_to_a_file_renders_its_path_and_no_image() -> None:
    payload = render_screenshot(
        ScreenshotResult(
            session_id="8f2c",
            path=Path("/tmp/settings.png"),
            image_base64=None,
            width=1080,
            height=2400,
            size_bytes=184320,
        )
    )

    assert payload.model_dump() == {
        "session_id": "8f2c",
        "path": "/tmp/settings.png",
        "image_base64": None,
        "width": 1080,
        "height": 2400,
        "size_bytes": 184320,
    }


def test_an_inline_screenshot_renders_its_base64_and_no_path() -> None:
    payload = render_screenshot(
        ScreenshotResult(
            session_id="8f2c",
            path=None,
            image_base64="iVBORw0KGgo=",
            width=1080,
            height=2400,
            size_bytes=12,
        )
    )

    assert payload.path is None
    assert payload.image_base64 == "iVBORw0KGgo="


def test_page_source_renders_its_truncation_facts() -> None:
    payload = render_page_source(
        PageSourceResult(
            session_id="8f2c", source="<hierarchy", truncated=True, total_characters=14820
        )
    )

    assert payload.truncated is True
    assert payload.total_characters == 14820
    assert len(payload.source) < payload.total_characters


# -- driving the screen ----------------------------------------------------


def test_an_interaction_renders_what_was_actually_sent() -> None:
    payload = render_interaction(
        InteractionResult(
            session_id="8f2c", action="tap", detail="tapped (540, 1200)", duration_seconds=0.3
        )
    )

    assert payload.model_dump() == {
        "session_id": "8f2c",
        "action": "tap",
        "detail": "tapped (540, 1200)",
        "duration_seconds": 0.3,
    }


def test_an_element_interaction_renders_the_label_it_captured() -> None:
    """`element_text` is the cheapest confirmation the right thing was hit, which
    is why it is on the payload rather than only in a log."""
    payload = render_element_interaction(
        ElementInteractionResult(
            session_id="8f2c",
            action="tap_element",
            strategy="accessibility_id",
            selector="Wi-Fi",
            element_text="Wi-Fi",
            duration_seconds=0.7,
        )
    )

    assert payload.model_dump() == {
        "session_id": "8f2c",
        "action": "tap_element",
        "strategy": "accessibility_id",
        "selector": "Wi-Fi",
        "element_text": "Wi-Fi",
        "duration_seconds": 0.7,
    }


def test_typing_into_the_focused_field_renders_as_the_focused_strategy() -> None:
    """No locator was given, so none is invented: the payload says `focused`."""
    payload = render_element_interaction(
        ElementInteractionResult(
            session_id="8f2c",
            action="type_text",
            strategy="focused",
            selector="",
            element_text=None,
            duration_seconds=0.8,
        )
    )

    assert payload.strategy == "focused"
    assert payload.selector == ""
