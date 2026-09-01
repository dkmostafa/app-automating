"""Unit tests for the Android MCP tool surface.

The service is an ``AsyncMock`` -- required here, and the point of the layer's
tests (Rule 2 §3): what needs asserting is that a tool call reaches the right
service method with the arguments the caller gave, and that what comes back is
the payload the docstring advertises. Building the real service would drag an
Android SDK into a test about argument passing.

Tools are exercised through ``mcp.call_tool`` rather than by calling the closure
directly, so the registration, the schema coercion and the error translation are
all in the path a real client would take.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError, ValidationError

from modules.android_module.application.services import AndroidEmulatorService
from modules.android_module.domain.errors import EmulatorAlreadyRunning, EmulatorNotFound
from modules.android_module.domain.models import (
    AndroidDevice,
    AvdInfo,
    CreateEmulatorResult,
    DeleteEmulatorResult,
    InstallSystemImageResult,
    ListDevicesResult,
    ListEmulatorsResult,
    StartEmulatorResult,
    StopEmulatorResult,
)
from modules.android_module.presentation import register_android_emulator_tools

pytestmark = pytest.mark.unit

IMAGE = "system-images;android-34;google_apis;x86_64"

#: The whole surface. A tool added without a line here fails
#: `test_the_registered_tools_are_exactly_this_surface`, which is what stops the
#: MCP surface from growing silently.
TOOL_NAMES = (
    "android_get_available_devices",
    "android_get_all_devices",
    "android_get_installed_emulators",
    "android_run_emulator",
    "android_run_emulator_without_window",
    "android_stop_emulator",
    "android_create_device",
    "android_download_image",
    "android_delete_emulator",
)


@pytest.fixture
def service() -> AsyncMock:
    """A service that answers every call with a plausible domain result."""
    mock = AsyncMock(spec=AndroidEmulatorService)
    mock.get_available_devices.return_value = ListDevicesResult(
        devices=(
            AndroidDevice(
                device_id="emulator-5554", state="device", is_emulator=True, avd_name="Pixel_7"
            ),
        )
    )
    mock.get_all_devices.return_value = mock.get_available_devices.return_value
    mock.get_installed_emulators.return_value = ListEmulatorsResult(
        emulators=(AvdInfo(name="Pixel_7", path=Path("/avd/Pixel_7.avd")),)
    )
    started = StartEmulatorResult(
        name="Pixel_7",
        device_id="emulator-5554",
        pid=48213,
        headless=False,
        booted=True,
        startup_duration_seconds=42.7,
    )
    mock.run_emulator.return_value = started
    mock.run_emulator_without_window.return_value = StartEmulatorResult(
        name="Pixel_7",
        device_id="emulator-5554",
        pid=48219,
        headless=True,
        booted=True,
        startup_duration_seconds=38.1,
    )
    mock.stop_emulator.return_value = StopEmulatorResult(
        device_id="emulator-5554", avd_name="Pixel_7", stopped=True, duration_seconds=3.4
    )
    mock.create_device.return_value = CreateEmulatorResult(
        name="Pixel_7_API_34",
        path=Path("/avd/Pixel_7_API_34.avd"),
        config_path=Path("/avd/Pixel_7_API_34.avd/config.ini"),
        system_image=IMAGE,
        device="pixel_7",
        replaced_existing=False,
        duration_seconds=2.1,
    )
    mock.download_image.return_value = InstallSystemImageResult(
        system_image=IMAGE,
        path=Path("/sdk/system-images/android-34/google_apis/x86_64"),
        already_installed=True,
        duration_seconds=0.1,
    )
    mock.delete_emulator.return_value = DeleteEmulatorResult(
        name="Pixel_7_API_34",
        deleted_path=Path("/avd/Pixel_7_API_34.avd"),
        stopped_first=True,
        duration_seconds=4.2,
    )
    return mock


@pytest.fixture
def mcp(service: AsyncMock) -> FastMCP:
    server = FastMCP("test")
    register_android_emulator_tools(server, service)
    return server


# -- registration ----------------------------------------------------------


async def test_the_registered_tools_are_exactly_this_surface(mcp: FastMCP) -> None:
    registered = {tool.name for tool in await mcp.list_tools()}
    assert registered == set(TOOL_NAMES)


async def test_every_tool_name_carries_the_module_prefix(mcp: FastMCP) -> None:
    """Rule 3 §3: the second module must not be able to collide with this one."""
    for tool in await mcp.list_tools():
        assert tool.name.startswith("android_"), tool.name


async def test_every_tool_publishes_an_output_schema(mcp: FastMCP) -> None:
    """The reason results are pydantic models: the client reads field names and
    types rather than inferring them from the description."""
    for tool in await mcp.list_tools():
        assert tool.output_schema, f"{tool.name} returns an undeclared shape"


def test_registering_the_surface_twice_is_free_of_side_effects(service: AsyncMock) -> None:
    """Rule 3 §2: the register function builds nothing and keeps no state."""
    first, second = FastMCP("a"), FastMCP("b")
    register_android_emulator_tools(first, service)
    register_android_emulator_tools(second, service)
    service.get_available_devices.assert_not_awaited()


def test_registration_does_not_call_the_service(service: AsyncMock, mcp: FastMCP) -> None:
    assert not service.method_calls


# -- dispatch --------------------------------------------------------------


async def test_get_available_devices_reaches_the_service_and_renders_the_result(
    mcp: FastMCP, service: AsyncMock
) -> None:
    result = await mcp.call_tool("android_get_available_devices", {})

    service.get_available_devices.assert_awaited_once_with(resolve_avd_names=True)
    assert result.structured_content == {
        "devices": [
            {
                "device_id": "emulator-5554",
                "state": "device",
                "available": True,
                "is_emulator": True,
                "avd_name": "Pixel_7",
                "model": None,
                "product": None,
                "transport_id": None,
            }
        ],
        "count": 1,
        "emulator_count": 1,
    }


async def test_the_two_device_reads_call_two_different_service_methods(
    mcp: FastMCP, service: AsyncMock
) -> None:
    await mcp.call_tool("android_get_all_devices", {"resolve_avd_names": False})

    service.get_all_devices.assert_awaited_once_with(resolve_avd_names=False)
    service.get_available_devices.assert_not_awaited()


async def test_get_installed_emulators_passes_its_flag_through(
    mcp: FastMCP, service: AsyncMock
) -> None:
    result = await mcp.call_tool("android_get_installed_emulators", {"include_unloadable": False})

    service.get_installed_emulators.assert_awaited_once_with(include_unloadable=False)
    assert result.structured_content["names"] == ["Pixel_7"]
    assert result.structured_content["unloadable_count"] == 0


async def test_run_emulator_defaults_to_waiting_for_the_boot(
    mcp: FastMCP, service: AsyncMock
) -> None:
    result = await mcp.call_tool("android_run_emulator", {"avd_name": "Pixel_7"})

    service.run_emulator.assert_awaited_once_with(
        "Pixel_7",
        wait_for_boot=True,
        cold_boot=False,
        wipe_data=False,
        boot_timeout_seconds=None,
    )
    assert result.structured_content["device_id"] == "emulator-5554"
    assert result.structured_content["booted"] is True


async def test_run_emulator_passes_every_boot_option_through(
    mcp: FastMCP, service: AsyncMock
) -> None:
    await mcp.call_tool(
        "android_run_emulator",
        {
            "avd_name": "Pixel_7",
            "wait_for_boot": False,
            "cold_boot": True,
            "wipe_data": True,
            "boot_timeout_seconds": 12.5,
        },
    )

    service.run_emulator.assert_awaited_once_with(
        "Pixel_7",
        wait_for_boot=False,
        cold_boot=True,
        wipe_data=True,
        boot_timeout_seconds=12.5,
    )


async def test_the_headless_tool_uses_the_headless_service_method(
    mcp: FastMCP, service: AsyncMock
) -> None:
    """Two tools, two operations -- the headless one must not be the headed one
    with a flag the caller cannot see."""
    result = await mcp.call_tool("android_run_emulator_without_window", {"avd_name": "Pixel_7"})

    service.run_emulator_without_window.assert_awaited_once()
    service.run_emulator.assert_not_awaited()
    assert result.structured_content["headless"] is True


async def test_stop_emulator_takes_a_serial(mcp: FastMCP, service: AsyncMock) -> None:
    result = await mcp.call_tool(
        "android_stop_emulator", {"device_id": "emulator-5554", "wait_for_exit": False}
    )

    service.stop_emulator.assert_awaited_once_with(
        "emulator-5554", wait_for_exit=False, timeout_seconds=None
    )
    assert result.structured_content["avd_name"] == "Pixel_7"


async def test_create_device_forwards_its_optional_arguments(
    mcp: FastMCP, service: AsyncMock
) -> None:
    result = await mcp.call_tool(
        "android_create_device",
        {
            "avd_name": "Pixel_7_API_34",
            "system_image": IMAGE,
            "device": "pixel_7",
            "sdcard_size": "512M",
            "abi": "x86_64",
            "replace_existing": True,
        },
    )

    service.create_device.assert_awaited_once_with(
        "Pixel_7_API_34",
        IMAGE,
        "pixel_7",
        sdcard_size="512M",
        abi="x86_64",
        replace_existing=True,
    )
    assert result.structured_content["config_path"].endswith("config.ini")


async def test_create_device_does_not_overwrite_unless_asked(
    mcp: FastMCP, service: AsyncMock
) -> None:
    await mcp.call_tool(
        "android_create_device",
        {"avd_name": "Pixel_7_API_34", "system_image": IMAGE, "device": "pixel_7"},
    )
    assert service.create_device.await_args.kwargs["replace_existing"] is False


async def test_download_image_accepts_licences_by_default(mcp: FastMCP, service: AsyncMock) -> None:
    result = await mcp.call_tool("android_download_image", {"system_image": IMAGE})

    service.download_image.assert_awaited_once_with(IMAGE, accept_licenses=True, reinstall=False)
    assert result.structured_content["already_installed"] is True


async def test_delete_emulator_refuses_a_running_avd_unless_told_otherwise(
    mcp: FastMCP, service: AsyncMock
) -> None:
    await mcp.call_tool("android_delete_emulator", {"avd_name": "Pixel_7_API_34"})
    service.delete_emulator.assert_awaited_once_with("Pixel_7_API_34", stop_if_running=False)


async def test_delete_emulator_can_stop_the_device_first(mcp: FastMCP, service: AsyncMock) -> None:
    result = await mcp.call_tool(
        "android_delete_emulator", {"avd_name": "Pixel_7_API_34", "stop_if_running": True}
    )

    service.delete_emulator.assert_awaited_once_with("Pixel_7_API_34", stop_if_running=True)
    assert result.structured_content["stopped_first"] is True


async def test_a_required_argument_is_rejected_before_the_service_is_touched(
    mcp: FastMCP, service: AsyncMock
) -> None:
    """The schema is the first line of defence: a call with no avd_name never
    reaches the service, so there is nothing for this layer to translate."""
    with pytest.raises(ValidationError):
        await mcp.call_tool("android_run_emulator", {})
    service.run_emulator.assert_not_awaited()


# -- failures --------------------------------------------------------------


async def test_a_domain_error_becomes_a_tool_error_with_a_remedy(
    mcp: FastMCP, service: AsyncMock
) -> None:
    service.run_emulator.side_effect = EmulatorNotFound("Pixel_9")

    with pytest.raises(ToolError) as excinfo:
        await mcp.call_tool("android_run_emulator", {"avd_name": "Pixel_9"})

    message = str(excinfo.value)
    assert "EmulatorNotFound" in message
    assert "Pixel_9" in message
    assert "android_get_installed_emulators" in message


async def test_the_evidence_on_the_error_reaches_the_caller(
    mcp: FastMCP, service: AsyncMock
) -> None:
    """The serial is the actionable part of an already-running failure."""
    service.run_emulator.side_effect = EmulatorAlreadyRunning("Pixel_7", "emulator-5554")

    with pytest.raises(ToolError) as excinfo:
        await mcp.call_tool("android_run_emulator", {"avd_name": "Pixel_7"})

    assert "emulator-5554" in str(excinfo.value)


@pytest.mark.parametrize(
    "tool_name, arguments, method",
    [
        ("android_get_available_devices", {}, "get_available_devices"),
        ("android_get_all_devices", {}, "get_all_devices"),
        ("android_get_installed_emulators", {}, "get_installed_emulators"),
        ("android_run_emulator", {"avd_name": "Pixel_7"}, "run_emulator"),
        (
            "android_run_emulator_without_window",
            {"avd_name": "Pixel_7"},
            "run_emulator_without_window",
        ),
        ("android_stop_emulator", {"device_id": "emulator-5554"}, "stop_emulator"),
        (
            "android_create_device",
            {"avd_name": "x", "system_image": IMAGE, "device": "pixel_7"},
            "create_device",
        ),
        ("android_download_image", {"system_image": IMAGE}, "download_image"),
        ("android_delete_emulator", {"avd_name": "x"}, "delete_emulator"),
    ],
)
async def test_no_tool_lets_a_domain_failure_escape_untranslated(
    mcp: FastMCP, service: AsyncMock, tool_name: str, arguments: dict, method: str
) -> None:
    """Rule 3 §6, asserted for the whole surface rather than for one tool."""
    getattr(service, method).side_effect = EmulatorNotFound("whatever")

    with pytest.raises(ToolError) as excinfo:
        await mcp.call_tool(tool_name, arguments)
    assert "EmulatorNotFound" in str(excinfo.value)
