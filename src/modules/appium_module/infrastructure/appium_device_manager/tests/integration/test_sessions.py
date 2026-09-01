"""The session registry against a real Appium server and a real device.

Rule 1 §6: no mocked driver, no stubbed WebDriver. The registry's whole job is
to hold live network objects and translate what they throw, and a fake one would
test the fake's exception vocabulary rather than Selenium's.

Everything that needs a device shares the module's one ``session`` fixture,
because creating a session costs 5-30 seconds. The tests that need only the
registry's bookkeeping need no device at all and run everywhere.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from modules.appium_module.domain.errors import ElementNotFound, SessionNotFound
from modules.appium_module.domain.models import StartSessionRequest
from modules.appium_module.infrastructure.appium_device_manager import (
    AppiumConfig,
    SessionRegistry,
    UnknownSessionError,
)

pytestmark = pytest.mark.integration


# -- bookkeeping: no device needed ----------------------------------------


def test_a_fresh_registry_is_empty() -> None:
    registry = SessionRegistry(AppiumConfig())

    assert len(registry) == 0
    assert registry.all() == ()
    assert registry.session_ids == ()


def test_asking_for_a_session_that_was_never_opened_names_what_is_open() -> None:
    """So a caller can pick a real id rather than guess a second wrong one."""
    registry = SessionRegistry(AppiumConfig())

    with pytest.raises(UnknownSessionError) as excinfo:
        registry.get("never-existed")

    assert excinfo.value.session_id == "never-existed"
    assert excinfo.value.known == ()


def test_an_unknown_session_is_a_domain_session_not_found() -> None:
    """Rule 0 §2: the application layer catches the domain type, never this one."""
    registry = SessionRegistry(AppiumConfig())

    with pytest.raises(SessionNotFound):
        registry.get("nope")


async def test_closing_a_session_that_is_not_there_raises_rather_than_passing() -> None:
    """Silently succeeding would let a caller believe a device was released."""
    registry = SessionRegistry(AppiumConfig())

    with pytest.raises(SessionNotFound):
        await registry.close("nope")


async def test_closing_an_empty_registry_is_a_no_op() -> None:
    """`aclose` runs on server shutdown whether or not anything was ever opened."""
    registry = SessionRegistry(AppiumConfig())

    await registry.aclose()

    assert len(registry) == 0


# -- against a real device -------------------------------------------------


async def test_a_created_session_is_registered_and_describable(manager, session: str) -> None:
    """The session fixture did the creating; this asserts what was recorded."""
    from modules.appium_module.domain.models import ListSessionsRequest

    result = await manager.list_sessions(ListSessionsRequest())

    found = result.by_session_id(session)
    assert found is not None
    assert found.platform_name == "Android"
    assert found.automation_name == "UiAutomator2"


async def test_a_real_session_reports_the_devices_screen_size(manager, session: str) -> None:
    """Read once at session start, because every coordinate-free gesture needs it
    and asking per scroll would be a round trip per swipe."""
    from modules.appium_module.domain.models import ListSessionsRequest

    found = (await manager.list_sessions(ListSessionsRequest())).by_session_id(session)

    assert found.has_screen_size
    assert found.screen_width > 0
    assert found.screen_height > 0


async def test_a_locator_that_matches_nothing_raises_element_not_found(
    manager, session: str
) -> None:
    """A real lookup against a real screen, with a short timeout so it is quick."""
    from modules.appium_module.domain.models import TapElementRequest

    with pytest.raises(ElementNotFound) as excinfo:
        await manager.tap_element(
            TapElementRequest(
                session_id=session,
                strategy="accessibility_id",
                selector="no-such-element-at-all-xyz",
                timeout_seconds=1.0,
            )
        )

    assert excinfo.value.selector == "no-such-element-at-all-xyz"
    assert excinfo.value.timeout_seconds == 1.0


async def test_a_session_can_be_closed_and_is_then_gone(manager, ready_host, device_id) -> None:
    """Its own session rather than the shared fixture's, because it destroys it."""
    from modules.appium_module.domain.models import EndSessionRequest, ListSessionsRequest

    started = await manager.start_session(StartSessionRequest(device_id=device_id))
    session_id = started.session.session_id

    ended = await manager.end_session(EndSessionRequest(session_id=session_id))

    assert ended.session_id == session_id
    assert ended.device_id == device_id
    assert session_id not in (await manager.list_sessions(ListSessionsRequest())).session_ids


async def test_closing_the_same_session_twice_is_an_error_the_second_time(
    manager, ready_host, device_id
) -> None:
    from modules.appium_module.domain.models import EndSessionRequest

    started = await manager.start_session(StartSessionRequest(device_id=device_id))
    await manager.end_session(EndSessionRequest(session_id=started.session.session_id))

    with pytest.raises(SessionNotFound):
        await manager.end_session(EndSessionRequest(session_id=started.session.session_id))


async def test_an_apk_that_is_not_on_disk_fails_before_a_server_is_touched(
    manager, tmp_path: Path, device_id: str
) -> None:
    """Rule 1 §3: validate before you spawn. This must be fast because nothing
    was launched -- not slow because a session was attempted and refused."""
    from modules.appium_module.domain.errors import AppNotFound

    with pytest.raises(AppNotFound):
        await manager.start_session(
            StartSessionRequest(device_id=device_id, app_path=tmp_path / "missing.apk")
        )
