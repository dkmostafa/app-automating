"""The service: does each operation build the right request and call the right port.

Rule 2 §3: the application layer is tested with unit tests and mocks, and only
those. A service's job is to turn the product's words into the domain's
``*Request`` objects and hand them to a port, so what needs asserting is
*exactly that* -- which port was called, once, with which request. Building real
collaborators would drag a real Appium into a test that is not about Appium.

The mocks here are the four ports, never an adapter. That is the whole point of
Rule 0 §2: this file proves the service is written against abstractions, because
it never imports a concrete class to write it.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from modules.appium_module.application.services import AppiumDeviceService
from modules.appium_module.domain.models import (
    CheckEnvironmentRequest,
    EndSessionRequest,
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
from modules.appium_module.domain.ports import (
    AppiumEnvironment,
    DeviceInteraction,
    ScreenInspector,
    SessionLifecycle,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def ports() -> dict[str, AsyncMock]:
    """The four ports, mocked to their Protocols so a typo in a method name fails."""
    return {
        "environment": AsyncMock(spec=AppiumEnvironment),
        "sessions": AsyncMock(spec=SessionLifecycle),
        "screen": AsyncMock(spec=ScreenInspector),
        "interaction": AsyncMock(spec=DeviceInteraction),
    }


@pytest.fixture
def service(ports: dict[str, AsyncMock]) -> AppiumDeviceService:
    return AppiumDeviceService(**ports)


def _request(mock: AsyncMock):
    """The single request the port was called with."""
    mock.assert_awaited_once()
    (request,), _ = mock.call_args
    return request


# -- the host --------------------------------------------------------------


async def test_check_environment_probes_the_server_by_default(
    service: AppiumDeviceService, ports: dict[str, AsyncMock]
) -> None:
    await service.check_environment()

    request = _request(ports["environment"].check_environment)
    assert isinstance(request, CheckEnvironmentRequest)
    assert request.probe_server is True


async def test_probing_can_be_turned_off(
    service: AppiumDeviceService, ports: dict[str, AsyncMock]
) -> None:
    await service.check_environment(probe_server=False)

    assert _request(ports["environment"].check_environment).probe_server is False


async def test_installing_a_driver_passes_the_name_and_does_not_reinstall_by_default(
    service: AppiumDeviceService, ports: dict[str, AsyncMock]
) -> None:
    await service.install_driver("uiautomator2")

    request = _request(ports["environment"].install_driver)
    assert isinstance(request, InstallDriverRequest)
    assert request.driver_name == "uiautomator2"
    assert request.reinstall is False


async def test_reinstalling_is_opt_in(
    service: AppiumDeviceService, ports: dict[str, AsyncMock]
) -> None:
    """It re-downloads and takes minutes, so it must never be the default."""
    await service.install_driver("uiautomator2", reinstall=True)

    assert _request(ports["environment"].install_driver).reinstall is True


# -- sessions --------------------------------------------------------------


async def test_starting_a_session_needs_only_a_serial(
    service: AppiumDeviceService, ports: dict[str, AsyncMock]
) -> None:
    """The common case -- attach to whatever the device is showing -- is one
    argument, and the app fields stay unset rather than being guessed at."""
    await service.start_session("emulator-5554")

    request = _request(ports["sessions"].start_session)
    assert isinstance(request, StartSessionRequest)
    assert request.device_id == "emulator-5554"
    assert request.app_package is None
    assert request.app_path is None
    assert request.no_reset is True


async def test_no_reset_defaults_to_keeping_app_data(
    service: AppiumDeviceService, ports: dict[str, AsyncMock]
) -> None:
    """Driving a device as a person left it is the default; wiping is opt-in."""
    await service.start_session("emulator-5554", no_reset=False)

    assert _request(ports["sessions"].start_session).no_reset is False


async def test_every_app_argument_reaches_the_request(
    service: AppiumDeviceService, ports: dict[str, AsyncMock]
) -> None:
    await service.start_session(
        "emulator-5554",
        app_path=Path("/tmp/app.apk"),
        app_package="com.app",
        app_activity=".Main",
        startup_timeout_seconds=90.0,
    )

    request = _request(ports["sessions"].start_session)
    assert request.app_path == Path("/tmp/app.apk")
    assert request.app_package == "com.app"
    assert request.app_activity == ".Main"
    assert request.startup_timeout_seconds == 90.0


async def test_ending_a_session_addresses_it_by_id(
    service: AppiumDeviceService, ports: dict[str, AsyncMock]
) -> None:
    await service.end_session("s1")

    assert _request(ports["sessions"].end_session) == EndSessionRequest(session_id="s1")


async def test_listing_sessions_does_not_verify_them_by_default(
    service: AppiumDeviceService, ports: dict[str, AsyncMock]
) -> None:
    """Verification costs a round trip per session, so it is opt-in."""
    await service.get_open_sessions()

    request = _request(ports["sessions"].list_sessions)
    assert isinstance(request, ListSessionsRequest)
    assert request.verify_alive is False


async def test_verification_can_be_asked_for(
    service: AppiumDeviceService, ports: dict[str, AsyncMock]
) -> None:
    await service.get_open_sessions(verify_alive=True)

    assert _request(ports["sessions"].list_sessions).verify_alive is True


# -- reading the screen ----------------------------------------------------


async def test_a_screenshot_returns_inline_when_no_path_is_given(
    service: AppiumDeviceService, ports: dict[str, AsyncMock]
) -> None:
    await service.take_screenshot("s1")

    request = _request(ports["screen"].take_screenshot)
    assert isinstance(request, TakeScreenshotRequest)
    assert request.save_path is None


async def test_a_screenshot_path_is_passed_through(
    service: AppiumDeviceService, ports: dict[str, AsyncMock]
) -> None:
    await service.take_screenshot("s1", save_path=Path("/tmp/x.png"))

    assert _request(ports["screen"].take_screenshot).save_path == Path("/tmp/x.png")


async def test_the_page_source_is_uncapped_by_default(
    service: AppiumDeviceService, ports: dict[str, AsyncMock]
) -> None:
    await service.get_page_source("s1")

    request = _request(ports["screen"].get_page_source)
    assert isinstance(request, GetPageSourceRequest)
    assert request.max_characters is None


async def test_the_page_source_can_be_capped(
    service: AppiumDeviceService, ports: dict[str, AsyncMock]
) -> None:
    await service.get_page_source("s1", max_characters=4000)

    assert _request(ports["screen"].get_page_source).max_characters == 4000


# -- driving the screen ----------------------------------------------------


async def test_a_tap_carries_its_coordinates_and_defaults_to_no_hold(
    service: AppiumDeviceService, ports: dict[str, AsyncMock]
) -> None:
    await service.tap("s1", 540, 1200)

    request = _request(ports["interaction"].tap)
    assert isinstance(request, TapRequest)
    assert (request.x, request.y) == (540, 1200)
    assert request.duration_ms == 0


async def test_a_long_press_is_a_tap_with_a_duration(
    service: AppiumDeviceService, ports: dict[str, AsyncMock]
) -> None:
    await service.tap("s1", 540, 1200, duration_ms=1000)

    assert _request(ports["interaction"].tap).duration_ms == 1000


async def test_tapping_an_element_carries_the_locator_and_a_default_wait(
    service: AppiumDeviceService, ports: dict[str, AsyncMock]
) -> None:
    """The wait is what makes it reliable on a settling screen, so it defaults on."""
    await service.tap_element("s1", "accessibility_id", "Wi-Fi")

    request = _request(ports["interaction"].tap_element)
    assert isinstance(request, TapElementRequest)
    assert (request.strategy, request.selector) == ("accessibility_id", "Wi-Fi")
    assert request.timeout_seconds == 10.0


async def test_a_swipe_carries_both_endpoints(
    service: AppiumDeviceService, ports: dict[str, AsyncMock]
) -> None:
    await service.swipe("s1", 1, 2, 3, 4, duration_ms=500)

    request = _request(ports["interaction"].swipe)
    assert isinstance(request, SwipeRequest)
    assert (request.start_x, request.start_y, request.end_x, request.end_y) == (1, 2, 3, 4)
    assert request.duration_ms == 500


async def test_a_scroll_defaults_to_half_a_screen(
    service: AppiumDeviceService, ports: dict[str, AsyncMock]
) -> None:
    """A comfortable page: 1.0 overshoots past what you were looking for."""
    await service.scroll("s1", "down")

    request = _request(ports["interaction"].scroll)
    assert isinstance(request, ScrollRequest)
    assert request.direction == "down"
    assert request.distance == 0.5


async def test_typing_without_a_locator_leaves_both_locator_fields_unset(
    service: AppiumDeviceService, ports: dict[str, AsyncMock]
) -> None:
    """That pairing is what tells the adapter to use the focused element."""
    await service.type_text("s1", "hello")

    request = _request(ports["interaction"].type_text)
    assert isinstance(request, TypeTextRequest)
    assert request.text == "hello"
    assert request.strategy is None
    assert request.selector is None
    assert request.clear_first is False


async def test_typing_into_a_named_field_carries_the_locator(
    service: AppiumDeviceService, ports: dict[str, AsyncMock]
) -> None:
    await service.type_text("s1", "coffee", strategy="id", selector="x:id/s", clear_first=True)

    request = _request(ports["interaction"].type_text)
    assert (request.strategy, request.selector) == ("id", "x:id/s")
    assert request.clear_first is True


async def test_pressing_a_key_carries_the_name_not_a_keycode(
    service: AppiumDeviceService, ports: dict[str, AsyncMock]
) -> None:
    """The service speaks the product's words; the mapping is the adapter's job."""
    await service.press_key("s1", "back")

    assert _request(ports["interaction"].press_key) == PressKeyRequest(session_id="s1", key="back")


# -- the shape of the service ---------------------------------------------


async def test_each_operation_touches_exactly_one_port(
    service: AppiumDeviceService, ports: dict[str, AsyncMock]
) -> None:
    """Rule 3 §4 in spirit: an operation is one port call, not orchestration
    spread across collaborators."""
    await service.tap("s1", 1, 2)

    ports["environment"].check_environment.assert_not_awaited()
    ports["sessions"].start_session.assert_not_awaited()
    ports["screen"].take_screenshot.assert_not_awaited()
    ports["interaction"].tap.assert_awaited_once()


def test_the_service_holds_only_the_authority_it_needs() -> None:
    """Rule 0 §3 (ISP): it is constructed from four narrow ports, so it is
    structurally incapable of anything they do not offer."""
    import inspect

    parameters = list(inspect.signature(AppiumDeviceService.__init__).parameters)

    assert parameters == ["self", "environment", "sessions", "screen", "interaction"]


def test_the_service_takes_its_collaborators_with_no_defaults() -> None:
    """Rule 0 §4: a constructor argument defaulting to a live object is a hidden
    dependency with a polite signature. The composition root passes these in."""
    import inspect

    for name, parameter in inspect.signature(AppiumDeviceService.__init__).parameters.items():
        if name == "self":
            continue
        assert parameter.default is inspect.Parameter.empty, f"{name} has a default"
