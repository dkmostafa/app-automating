"""The tools: what they register, what they call, and what they hand back.

Rule 2 §3 places this file and says what it may fake: **the service is a mock,
and the infrastructure never is**. That is the point of the layer's tests -- a
tool body is three statements, and the only thing worth asserting is that it
calls the service method it claims to, with the arguments the caller gave, and
renders the result rather than inventing one.

Registration happens on a throwaway ``FastMCP``, which is only safe because
``register_appium_device_tools`` builds nothing and has no side effects
(Rule 3 §2). If that ever stops being true, this file is where it fails.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from app_automating.modules.appium_module.application.services import AppiumDeviceService
from app_automating.modules.appium_module.domain.errors import ElementNotFound, SessionNotFound
from app_automating.modules.appium_module.domain.models import (
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
from app_automating.modules.appium_module.presentation import register_appium_device_tools

pytestmark = pytest.mark.unit

#: Every tool this surface publishes. Listed rather than derived so that adding
#: or renaming one is a deliberate edit here, not a silent change to the API.
EXPECTED_TOOLS = {
    "appium_check_environment",
    "appium_install_driver",
    "appium_start_session",
    "appium_end_session",
    "appium_get_open_sessions",
    "appium_take_screenshot",
    "appium_get_page_source",
    "appium_tap",
    "appium_tap_element",
    "appium_swipe",
    "appium_scroll",
    "appium_type_text",
    "appium_press_key",
}

#: tool name -> the service method it must call. Rule 3 §3: if these disagree
#: about what the operation is called, one of them is wrong.
TOOL_TO_SERVICE_METHOD = {
    "appium_check_environment": "check_environment",
    "appium_install_driver": "install_driver",
    "appium_start_session": "start_session",
    "appium_end_session": "end_session",
    "appium_get_open_sessions": "get_open_sessions",
    "appium_take_screenshot": "take_screenshot",
    "appium_get_page_source": "get_page_source",
    "appium_tap": "tap",
    "appium_tap_element": "tap_element",
    "appium_swipe": "swipe",
    "appium_scroll": "scroll",
    "appium_type_text": "type_text",
    "appium_press_key": "press_key",
}


def _session(**kwargs) -> AppiumSession:
    return AppiumSession(
        session_id="8f2c",
        device_id="emulator-5554",
        platform_name="Android",
        automation_name="UiAutomator2",
        **kwargs,
    )


@pytest.fixture
def service() -> AsyncMock:
    """The application service, mocked to its class so a typo fails the test."""
    mock = AsyncMock(spec=AppiumDeviceService)
    mock.check_environment.return_value = EnvironmentStatus(
        tools=(ToolStatus("node", True, "25.2.1"), ToolStatus("appium", True, "3.1.1")),
        drivers=("uiautomator2",),
        server_url="http://127.0.0.1:4723",
        server_running=False,
    )
    mock.install_driver.return_value = InstallDriverResult("uiautomator2", "6.3.0", True, 0.9)
    mock.start_session.return_value = StartSessionResult(
        session=_session(screen_width=1080, screen_height=2400),
        server_started=True,
        duration_seconds=12.4,
    )
    mock.end_session.return_value = EndSessionResult("8f2c", "emulator-5554", 0.6)
    mock.get_open_sessions.return_value = ListSessionsResult(sessions=(_session(),))
    mock.take_screenshot.return_value = ScreenshotResult(
        session_id="8f2c",
        path=None,
        image_base64="iVBORw0KGgo=",
        width=1080,
        height=2400,
        size_bytes=12,
    )
    mock.get_page_source.return_value = PageSourceResult("8f2c", "<hierarchy/>", False, 12)
    for name in ("tap", "swipe", "scroll", "press_key"):
        getattr(mock, name).return_value = InteractionResult("8f2c", name, "detail", 0.3)
    for name in ("tap_element", "type_text"):
        getattr(mock, name).return_value = ElementInteractionResult(
            "8f2c", name, "accessibility_id", "Wi-Fi", "Wi-Fi", 0.7
        )
    return mock


@pytest.fixture
def server(service: AsyncMock) -> FastMCP:
    instance = FastMCP("test")
    register_appium_device_tools(instance, service)
    return instance


async def _call(server: FastMCP, name: str, **arguments):
    tool = await server.get_tool(name)
    return await tool.run(arguments)


# -- registration ----------------------------------------------------------


async def test_the_expected_tools_are_registered_and_no_others(server: FastMCP) -> None:
    names = {tool.name for tool in await server.list_tools()}

    assert names == EXPECTED_TOOLS


async def test_every_tool_name_carries_the_module_prefix(server: FastMCP) -> None:
    """Rule 3 §3: the next module must not be able to collide with this one."""
    for tool in await server.list_tools():
        assert tool.name.startswith("appium_")


def test_registering_builds_nothing_and_is_safe_to_repeat(service: AsyncMock) -> None:
    """Rule 3 §2. Registration must be free of side effects, which is exactly
    what lets a test register into a throwaway server."""
    register_appium_device_tools(FastMCP("one"), service)
    register_appium_device_tools(FastMCP("two"), service)

    service.check_environment.assert_not_awaited()
    service.start_session.assert_not_awaited()


def test_the_register_function_takes_the_server_and_a_service_and_returns_none() -> None:
    import inspect

    signature = inspect.signature(register_appium_device_tools)

    assert list(signature.parameters) == ["mcp", "service"]
    assert signature.return_annotation in (None, "None")
    for parameter in signature.parameters.values():
        assert parameter.default is inspect.Parameter.empty


# -- each tool calls the service method it claims to ----------------------


async def test_checking_the_environment_passes_the_probe_flag(
    server: FastMCP, service: AsyncMock
) -> None:
    await _call(server, "appium_check_environment", probe_server=False)

    service.check_environment.assert_awaited_once_with(probe_server=False)


async def test_installing_a_driver_passes_its_arguments(
    server: FastMCP, service: AsyncMock
) -> None:
    await _call(server, "appium_install_driver", driver_name="uiautomator2", reinstall=True)

    service.install_driver.assert_awaited_once_with("uiautomator2", reinstall=True)


async def test_starting_a_session_passes_every_argument_through(
    server: FastMCP, service: AsyncMock
) -> None:
    await _call(
        server,
        "appium_start_session",
        device_id="emulator-5554",
        app_package="com.app",
        app_activity=".Main",
        no_reset=False,
    )

    service.start_session.assert_awaited_once_with(
        "emulator-5554",
        app_path=None,
        app_package="com.app",
        app_activity=".Main",
        no_reset=False,
        startup_timeout_seconds=None,
    )


async def test_a_string_app_path_becomes_a_path_for_the_service(
    server: FastMCP, service: AsyncMock
) -> None:
    """The client fills in a JSON schema, so the tool takes a string; the domain
    speaks Path. Converting is part of building the call (Rule 3 §4)."""
    await _call(server, "appium_start_session", device_id="emulator-5554", app_path="/tmp/app.apk")

    assert service.start_session.call_args.kwargs["app_path"] == Path("/tmp/app.apk")


async def test_an_absent_app_path_stays_none_rather_than_becoming_an_empty_path(
    server: FastMCP, service: AsyncMock
) -> None:
    """`Path("")` is the current directory, which would be a very confusing APK."""
    await _call(server, "appium_start_session", device_id="emulator-5554")

    assert service.start_session.call_args.kwargs["app_path"] is None


async def test_ending_a_session_passes_the_id(server: FastMCP, service: AsyncMock) -> None:
    await _call(server, "appium_end_session", session_id="8f2c")

    service.end_session.assert_awaited_once_with("8f2c")


async def test_listing_sessions_passes_the_verify_flag(server: FastMCP, service: AsyncMock) -> None:
    await _call(server, "appium_get_open_sessions", verify_alive=True)

    service.get_open_sessions.assert_awaited_once_with(verify_alive=True)


async def test_a_screenshot_path_is_converted_and_an_absent_one_stays_none(
    server: FastMCP, service: AsyncMock
) -> None:
    await _call(server, "appium_take_screenshot", session_id="8f2c", save_path="/tmp/x.png")
    assert service.take_screenshot.call_args.kwargs["save_path"] == Path("/tmp/x.png")

    await _call(server, "appium_take_screenshot", session_id="8f2c")
    assert service.take_screenshot.call_args.kwargs["save_path"] is None


async def test_the_page_source_cap_is_passed_through(server: FastMCP, service: AsyncMock) -> None:
    await _call(server, "appium_get_page_source", session_id="8f2c", max_characters=4000)

    service.get_page_source.assert_awaited_once_with("8f2c", max_characters=4000)


async def test_a_tap_passes_its_coordinates(server: FastMCP, service: AsyncMock) -> None:
    await _call(server, "appium_tap", session_id="8f2c", x=540, y=1200, duration_ms=1000)

    service.tap.assert_awaited_once_with("8f2c", 540, 1200, duration_ms=1000)


async def test_tapping_an_element_passes_the_locator_and_the_wait(
    server: FastMCP, service: AsyncMock
) -> None:
    await _call(
        server,
        "appium_tap_element",
        session_id="8f2c",
        strategy="accessibility_id",
        selector="Wi-Fi",
        timeout_seconds=5.0,
    )

    service.tap_element.assert_awaited_once_with(
        "8f2c", "accessibility_id", "Wi-Fi", timeout_seconds=5.0
    )


async def test_a_swipe_passes_both_endpoints(server: FastMCP, service: AsyncMock) -> None:
    await _call(
        server,
        "appium_swipe",
        session_id="8f2c",
        start_x=1,
        start_y=2,
        end_x=3,
        end_y=4,
        duration_ms=500,
    )

    service.swipe.assert_awaited_once_with("8f2c", 1, 2, 3, 4, duration_ms=500)


async def test_a_scroll_passes_its_direction_and_distance(
    server: FastMCP, service: AsyncMock
) -> None:
    await _call(server, "appium_scroll", session_id="8f2c", direction="down", distance=0.25)

    service.scroll.assert_awaited_once_with("8f2c", "down", distance=0.25, duration_ms=300)


async def test_typing_passes_the_optional_locator(server: FastMCP, service: AsyncMock) -> None:
    await _call(
        server,
        "appium_type_text",
        session_id="8f2c",
        text="coffee",
        strategy="id",
        selector="x:id/s",
        clear_first=True,
    )

    service.type_text.assert_awaited_once_with(
        "8f2c", "coffee", strategy="id", selector="x:id/s", clear_first=True
    )


async def test_pressing_a_key_passes_the_name(server: FastMCP, service: AsyncMock) -> None:
    await _call(server, "appium_press_key", session_id="8f2c", key="back")

    service.press_key.assert_awaited_once_with("8f2c", "back")


@pytest.mark.parametrize("tool_name", sorted(TOOL_TO_SERVICE_METHOD))
async def test_each_tool_awaits_its_service_method_exactly_once(
    tool_name: str, server: FastMCP, service: AsyncMock
) -> None:
    """Rule 3 §4: the body is one service call. No retries, no second call, no
    branching that could reach a different method."""
    arguments = {
        "appium_check_environment": {},
        "appium_install_driver": {},
        "appium_start_session": {"device_id": "emulator-5554"},
        "appium_end_session": {"session_id": "8f2c"},
        "appium_get_open_sessions": {},
        "appium_take_screenshot": {"session_id": "8f2c"},
        "appium_get_page_source": {"session_id": "8f2c"},
        "appium_tap": {"session_id": "8f2c", "x": 1, "y": 2},
        "appium_tap_element": {"session_id": "8f2c", "strategy": "id", "selector": "x"},
        "appium_swipe": {"session_id": "8f2c", "start_x": 1, "start_y": 2, "end_x": 3, "end_y": 4},
        "appium_scroll": {"session_id": "8f2c", "direction": "down"},
        "appium_type_text": {"session_id": "8f2c", "text": "x"},
        "appium_press_key": {"session_id": "8f2c", "key": "back"},
    }[tool_name]

    await _call(server, tool_name, **arguments)

    method = getattr(service, TOOL_TO_SERVICE_METHOD[tool_name])
    method.assert_awaited_once()
    for other_name, other in TOOL_TO_SERVICE_METHOD.items():
        if other_name != tool_name and other != TOOL_TO_SERVICE_METHOD[tool_name]:
            getattr(service, other).assert_not_awaited()


# -- what comes back -------------------------------------------------------


async def test_a_tool_returns_the_rendered_payload_not_the_domain_object(
    server: FastMCP, service: AsyncMock
) -> None:
    """Rule 3 §4: never a raw domain object, and never a bare string."""
    result = await _call(server, "appium_start_session", device_id="emulator-5554")

    assert result.structured_content["session_id"] == "8f2c"
    assert result.structured_content["server_started"] is True


async def test_the_start_session_payload_is_the_shape_its_docstring_promises(
    server: FastMCP, service: AsyncMock
) -> None:
    """Rule 3 §7: the example in the docstring and the assertion here are the
    same payload."""
    result = await _call(server, "appium_start_session", device_id="emulator-5554")

    payload = result.structured_content
    assert payload["session"]["device_id"] == "emulator-5554"
    assert payload["session"]["screen_width"] == 1080
    assert payload["session"]["screen_height"] == 2400


async def test_the_environment_payload_is_the_shape_its_docstring_promises(
    server: FastMCP, service: AsyncMock
) -> None:
    result = await _call(server, "appium_check_environment")

    payload = result.structured_content
    assert payload["ready"] is True
    assert payload["drivers"] == ["uiautomator2"]
    assert payload["server_running"] is False


# -- error translation -----------------------------------------------------


async def test_a_domain_error_from_the_service_becomes_a_tool_error(
    server: FastMCP, service: AsyncMock
) -> None:
    service.tap_element.side_effect = ElementNotFound("text", "Submit", 10.0)

    with pytest.raises(ToolError) as excinfo:
        await _call(
            server, "appium_tap_element", session_id="8f2c", strategy="text", selector="Submit"
        )

    assert "ElementNotFound" in str(excinfo.value)
    assert "Submit" in str(excinfo.value)


async def test_a_translated_error_carries_its_remedy_to_the_caller(
    server: FastMCP, service: AsyncMock
) -> None:
    """The whole reason the table exists: the client is a model picking its next
    call, and the next call is named in the message."""
    service.take_screenshot.side_effect = SessionNotFound("8f2c", ("other",))

    with pytest.raises(ToolError) as excinfo:
        await _call(server, "appium_take_screenshot", session_id="8f2c")

    assert "appium_get_open_sessions" in str(excinfo.value)


async def test_an_unexpected_exception_is_not_dressed_up_as_a_tool_result(
    server: FastMCP, service: AsyncMock
) -> None:
    """Rule 3 §6: a bug in this server must surface as one, not as a payload
    claiming success."""
    service.press_key.side_effect = ZeroDivisionError("a real bug")

    with pytest.raises(Exception) as excinfo:
        await _call(server, "appium_press_key", session_id="8f2c", key="back")

    assert not isinstance(excinfo.value, ToolError) or "ZeroDivision" in str(excinfo.value)
