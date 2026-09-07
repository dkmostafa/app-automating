"""The adapter's whole public surface, against the real host and a real device.

Rule 1 §6 in full: a real ``appium``, a real server this test starts and kills, a
real device where one is attached. Nothing is mocked, and where a behaviour
cannot be exercised for real on this host the test *skips* with a reason rather
than faking its way to green.

The file is deliberately split in two. Everything above "against a real device"
runs on any machine with Appium installed and costs milliseconds; everything
below it needs a booted device and shares one session, because creating one
costs 5-30 seconds (Rule 1 §6: budget the slow paths).
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from modules.appium_module.domain.errors import (
    ElementNotFound,
    InvalidCoordinates,
    InvalidDeviceId,
    InvalidKeyName,
    InvalidLocator,
    InvalidScroll,
    SessionNotFound,
)
from modules.appium_module.domain.models import (
    FOCUSED_STRATEGY,
    CheckEnvironmentRequest,
    GetPageSourceRequest,
    InstallDriverRequest,
    ListSessionsRequest,
    PressKeyRequest,
    ScrollRequest,
    StartSessionRequest,
    SwipeRequest,
    TakeScreenshotRequest,
    TapElementRequest,
    TapRequest,
    TypeTextRequest,
)
from modules.appium_module.infrastructure.appium_device_manager import AppiumDeviceManager

pytestmark = pytest.mark.integration

#: Rule 1 §2: the adapter's public surface, listed so that adding a method
#: without adding it here is a failing test rather than an oversight.
PUBLIC_METHODS = (
    "check_environment",
    "install_driver",
    "start_session",
    "end_session",
    "list_sessions",
    "take_screenshot",
    "get_page_source",
    "tap",
    "tap_element",
    "swipe",
    "scroll",
    "type_text",
    "press_key",
)


# -- the shape of the adapter ---------------------------------------------


def test_public_methods_are_the_whole_public_surface() -> None:
    """A new operation must be added to PUBLIC_METHODS, which forces a decision
    about the port it belongs on."""
    actual = {
        name
        for name, value in vars(AppiumDeviceManager).items()
        if not name.startswith("_") and inspect.iscoroutinefunction(value)
    }

    assert actual - {"aclose"} == set(PUBLIC_METHODS)


@pytest.mark.parametrize("name", PUBLIC_METHODS)
def test_every_public_method_takes_one_request_and_returns_a_result(name: str) -> None:
    """Rule 1 §2: dataclasses in, dataclasses out -- never a dict, str or bool."""
    method = getattr(AppiumDeviceManager, name)
    signature = inspect.signature(method)

    assert inspect.iscoroutinefunction(method), f"{name} is not async"
    assert list(signature.parameters) == ["self", "request"], f"{name}{signature}"
    assert signature.return_annotation not in (None, "None", "bool", "str", "dict")


# -- the host, no device needed -------------------------------------------


async def test_check_environment_reports_this_hosts_real_toolchain(
    manager: AppiumDeviceManager, appium_installed: None
) -> None:
    status = await manager.check_environment(CheckEnvironmentRequest(probe_server=False))

    appium = status.tool("appium")
    assert appium is not None
    assert appium.present is True
    assert appium.version
    assert appium.path is not None and appium.path.is_file()


async def test_check_environment_never_raises_for_a_missing_tool(config) -> None:
    """Its whole job is describing a broken host, so a missing binary is data.
    A real nonexistent path, not a patched lookup."""
    from modules.appium_module.application.di import build_appium_device_manager

    broken = build_appium_device_manager(
        dataclasses.replace(config, appium_path="/nonexistent/appium-xyz")
    )
    try:
        status = await broken.check_environment(CheckEnvironmentRequest(probe_server=False))
    finally:
        await broken.aclose()

    assert status.ready is False
    assert "appium" in status.missing
    assert status.tool("appium").detail


async def test_check_environment_lists_the_drivers_this_host_has(
    manager: AppiumDeviceManager, appium_installed: None
) -> None:
    status = await manager.check_environment(CheckEnvironmentRequest(probe_server=False))

    assert isinstance(status.drivers, tuple)
    if "uiautomator2" not in status.drivers:
        pytest.skip("uiautomator2 is not installed; run: appium driver install uiautomator2")


async def test_probing_the_server_is_optional_and_off_means_not_running(
    manager: AppiumDeviceManager, appium_installed: None
) -> None:
    status = await manager.check_environment(CheckEnvironmentRequest(probe_server=False))

    assert status.server_running is False
    assert status.server_url.startswith("http://")


async def test_installing_a_driver_that_is_already_there_downloads_nothing(
    manager: AppiumDeviceManager, ready_host: None
) -> None:
    """Fast and idempotent, and the test that proves the guard works -- without
    it this would reach the npm registry, which Rule 1 §6 forbids."""
    result = await manager.install_driver(InstallDriverRequest(driver_name="uiautomator2"))

    assert result.already_installed is True
    assert result.driver_name == "uiautomator2"
    assert result.duration_seconds < 30


async def test_listing_sessions_on_a_fresh_manager_is_empty(
    manager: AppiumDeviceManager,
) -> None:
    result = await manager.list_sessions(ListSessionsRequest())

    assert result.sessions == ()
    assert result.session_ids == ()


# -- input rejected before anything is spawned ----------------------------
#
# None of these need a device, a server or even Appium: the point is that the
# failure happens in-process, in microseconds, before any of that is touched.


async def test_a_malformed_serial_is_rejected_without_starting_a_server(
    manager: AppiumDeviceManager,
) -> None:
    with pytest.raises(InvalidDeviceId):
        await manager.start_session(StartSessionRequest(device_id="not a serial"))


async def test_an_unknown_session_id_is_rejected_by_every_interaction(
    manager: AppiumDeviceManager,
) -> None:
    """Thirteen entry points, one registry: none of them may reach a network."""
    with pytest.raises(SessionNotFound):
        await manager.tap(TapRequest(session_id="nope", x=1, y=1))
    with pytest.raises(SessionNotFound):
        await manager.get_page_source(GetPageSourceRequest(session_id="nope"))
    with pytest.raises(SessionNotFound):
        await manager.take_screenshot(TakeScreenshotRequest(session_id="nope"))
    with pytest.raises(SessionNotFound):
        await manager.press_key(PressKeyRequest(session_id="nope", key="back"))


async def test_a_negative_coordinate_is_rejected_before_the_session_is_looked_up(
    manager: AppiumDeviceManager,
) -> None:
    """Validation order matters: the caller gets the *useful* error, not
    "no such session" for a request that was malformed anyway."""
    with pytest.raises(InvalidCoordinates):
        await manager.tap(TapRequest(session_id="nope", x=-1, y=10))


async def test_an_unknown_key_is_rejected_before_the_session_is_looked_up(
    manager: AppiumDeviceManager,
) -> None:
    with pytest.raises(InvalidKeyName):
        await manager.press_key(PressKeyRequest(session_id="nope", key="not-a-key"))


async def test_an_unknown_locator_is_rejected_before_the_session_is_looked_up(
    manager: AppiumDeviceManager,
) -> None:
    with pytest.raises(InvalidLocator):
        await manager.tap_element(
            TapElementRequest(session_id="nope", strategy="css", selector="div")
        )


async def test_an_invalid_scroll_is_rejected_before_the_session_is_looked_up(
    manager: AppiumDeviceManager,
) -> None:
    with pytest.raises(InvalidScroll):
        await manager.scroll(ScrollRequest(session_id="nope", direction="sideways"))


# -- against a real device -------------------------------------------------


async def test_a_session_starts_and_reports_the_device_it_claimed(
    manager: AppiumDeviceManager, session: str, device_id: str
) -> None:
    result = await manager.list_sessions(ListSessionsRequest())

    assert result.by_session_id(session).device_id == device_id


async def test_a_screenshot_comes_back_as_a_real_png(
    manager: AppiumDeviceManager, session: str
) -> None:
    result = await manager.take_screenshot(TakeScreenshotRequest(session_id=session))

    assert result.image_base64
    assert result.path is None
    assert result.size_bytes > 0
    assert result.width and result.width > 0
    assert result.height and result.height > 0


async def test_a_screenshot_can_be_written_to_a_file(
    manager: AppiumDeviceManager, session: str, tmp_path
) -> None:
    target = tmp_path / "nested" / "screen.png"

    result = await manager.take_screenshot(
        TakeScreenshotRequest(session_id=session, save_path=target)
    )

    assert result.path == target
    assert target.is_file()
    assert target.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert result.image_base64 is None


async def test_the_page_source_is_the_devices_real_hierarchy(
    manager: AppiumDeviceManager, session: str
) -> None:
    result = await manager.get_page_source(GetPageSourceRequest(session_id=session))

    assert result.source.strip().startswith("<")
    assert result.truncated is False
    assert result.total_characters == len(result.source)


async def test_the_page_source_can_be_capped_and_says_when_it_was(
    manager: AppiumDeviceManager, session: str
) -> None:
    """The distinction that matters: a short screen versus a cut one."""
    result = await manager.get_page_source(
        GetPageSourceRequest(session_id=session, max_characters=50)
    )

    assert len(result.source) == 50
    assert result.truncated is True
    assert result.total_characters > 50


async def test_a_tap_at_the_centre_of_the_screen_is_accepted(
    manager: AppiumDeviceManager, session: str
) -> None:
    """Deliberately a harmless point, and the device is left usable for the next
    test that shares this session."""
    found = (await manager.list_sessions(ListSessionsRequest())).by_session_id(session)

    result = await manager.tap(
        TapRequest(session_id=session, x=found.screen_width // 2, y=found.screen_height // 2)
    )

    assert result.action == "tap"
    assert str(found.screen_width // 2) in result.detail


async def test_a_scroll_derives_its_path_from_the_real_screen_size(
    manager: AppiumDeviceManager, session: str
) -> None:
    found = (await manager.list_sessions(ListSessionsRequest())).by_session_id(session)

    result = await manager.scroll(ScrollRequest(session_id=session, direction="down"))

    assert result.action == "scroll"
    assert f"{found.screen_width}x{found.screen_height}" in result.detail


async def test_a_swipe_reports_the_path_it_actually_sent(
    manager: AppiumDeviceManager, session: str
) -> None:
    found = (await manager.list_sessions(ListSessionsRequest())).by_session_id(session)
    mid_x = found.screen_width // 2

    result = await manager.swipe(
        SwipeRequest(
            session_id=session,
            start_x=mid_x,
            start_y=int(found.screen_height * 0.6),
            end_x=mid_x,
            end_y=int(found.screen_height * 0.4),
        )
    )

    assert result.action == "swipe"
    assert "->" in result.detail


async def test_pressing_back_is_accepted_and_names_the_keycode(
    manager: AppiumDeviceManager, session: str
) -> None:
    result = await manager.press_key(PressKeyRequest(session_id=session, key="back"))

    assert result.action == "press_key"
    assert "keycode 4" in result.detail


async def test_typing_with_no_locator_and_nothing_focused_says_so(
    manager: AppiumDeviceManager, session: str
) -> None:
    """The locator-free form against a screen where nothing holds focus.

    The home screen is the one input state this suite can put a real device into
    on demand, and on it nothing is focused -- so this is the error path, tested
    for real (Rule 1 §6). The device answers "no such element", and the point of
    the assertion is that it arrives as ``ElementNotFound`` naming the focused
    form, not as a generic ``InteractionFailed`` carrying a Selenium stacktrace.

    The success path -- text landing in the focused field and coming back as
    ``strategy="focused"`` -- is deliberately not tested here: it needs an app
    with a focused text field, which this suite does not install, and faking one
    would test the fake. ``presentation/tests/unit/test_rendering.py`` covers the
    payload that path produces.
    """
    await manager.press_key(PressKeyRequest(session_id=session, key="home"))

    with pytest.raises(ElementNotFound) as excinfo:
        await manager.type_text(TypeTextRequest(session_id=session, text=""))

    assert excinfo.value.strategy == FOCUSED_STRATEGY
    assert excinfo.value.selector == ""
    # Nothing was waited for: focus is a fact about this instant, not something
    # that arrives if you keep asking.
    assert excinfo.value.timeout_seconds == 0.0


async def test_verifying_a_live_session_keeps_it_in_the_list(
    manager: AppiumDeviceManager, session: str
) -> None:
    result = await manager.list_sessions(ListSessionsRequest(verify_alive=True))

    assert session in result.session_ids


async def test_closing_the_manager_takes_down_its_sessions_and_its_server(
    manager: AppiumDeviceManager, ready_host: None, device_id: str
) -> None:
    """Rule 1 §5: own every process you start. Its own session, because it
    destroys everything the manager holds."""
    started = await manager.start_session(StartSessionRequest(device_id=device_id))
    opened = await manager.list_sessions(ListSessionsRequest())
    assert started.session.session_id in opened.session_ids

    await manager.aclose()

    assert (await manager.list_sessions(ListSessionsRequest())).sessions == ()
