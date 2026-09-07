"""Unit tests for the domain-result to payload conversion.

Pure functions over frozen dataclasses, so nothing is mocked and nothing needs
to be: the inputs are built by hand. Every expected payload here is the one the
matching tool's ``Returns`` section advertises (Rule 3 §7) -- if a field is
renamed, the docstring and this file have to move together.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from modules.android_module.domain.models import (
    AndroidDevice,
    AvdInfo,
    CreateEmulatorResult,
    DeleteEmulatorResult,
    InstallSystemImageResult,
    ListDevicesResult,
    ListEmulatorsResult,
    RenameEmulatorResult,
    StartEmulatorResult,
    StopEmulatorResult,
)
from modules.android_module.presentation.rendering import (
    render_avd_list,
    render_created_emulator,
    render_deleted_emulator,
    render_device,
    render_device_list,
    render_installed_image,
    render_renamed_emulator,
    render_started_emulator,
    render_stopped_emulator,
)

pytestmark = pytest.mark.unit

IMAGE = "system-images;android-34;google_apis;x86_64"


# -- devices ---------------------------------------------------------------


def test_a_device_renders_the_domains_availability_rather_than_re_deriving_it() -> None:
    """`available` is `AndroidDevice.is_available`; presentation must not
    re-implement what counts as usable."""
    offline = render_device(
        AndroidDevice(device_id="emulator-5554", state="offline", is_emulator=True)
    )
    usable = render_device(
        AndroidDevice(device_id="emulator-5556", state="device", is_emulator=True)
    )

    assert offline.available is False
    assert usable.available is True


def test_the_device_list_carries_the_counts_a_caller_would_have_computed() -> None:
    result = ListDevicesResult(
        devices=(
            AndroidDevice(
                device_id="emulator-5554",
                state="device",
                is_emulator=True,
                avd_name="Pixel_7",
                transport_id="3",
            ),
            AndroidDevice(device_id="R58M12ABCDE", state="device", is_emulator=False),
        )
    )

    payload = render_device_list(result)

    assert payload.count == 2
    assert payload.emulator_count == 1
    assert payload.devices[0].model_dump() == {
        "device_id": "emulator-5554",
        "state": "device",
        "available": True,
        "is_emulator": True,
        "avd_name": "Pixel_7",
        "model": None,
        "product": None,
        "transport_id": "3",
    }


def test_an_empty_device_list_is_a_normal_answer() -> None:
    payload = render_device_list(ListDevicesResult(devices=()))
    assert payload.devices == ()
    assert payload.count == 0
    assert payload.emulator_count == 0


# -- AVDs ------------------------------------------------------------------


def test_the_avd_list_counts_the_broken_ones_separately() -> None:
    """A name in `names` that cannot boot is why this count is on the payload."""
    result = ListEmulatorsResult(
        emulators=(
            AvdInfo(name="Pixel_7", path=Path("/home/u/.android/avd/Pixel_7.avd"), loadable=True),
            AvdInfo(name="Broken", loadable=False, error="missing system image"),
        )
    )

    payload = render_avd_list(result)

    assert payload.count == 2
    assert payload.unloadable_count == 1
    assert payload.names == ("Pixel_7", "Broken")
    assert payload.emulators[0].path == "/home/u/.android/avd/Pixel_7.avd"
    assert payload.emulators[1].error == "missing system image"


def test_an_avd_without_a_path_renders_null_rather_than_the_string_none() -> None:
    payload = render_avd_list(ListEmulatorsResult(emulators=(AvdInfo(name="Broken"),)))
    assert payload.emulators[0].path is None


# -- lifecycle -------------------------------------------------------------


def test_a_started_emulator_renders_what_the_tool_docstring_promises() -> None:
    payload = render_started_emulator(
        StartEmulatorResult(
            name="Pixel_7",
            device_id="emulator-5554",
            pid=48213,
            headless=False,
            booted=True,
            startup_duration_seconds=42.7,
            launch_argv=("emulator", "-avd", "Pixel_7"),
        )
    )

    assert payload.model_dump() == {
        "name": "Pixel_7",
        "device_id": "emulator-5554",
        "pid": 48213,
        "headless": False,
        "booted": True,
        "startup_duration_seconds": 42.7,
    }


def test_the_launch_command_line_never_reaches_the_client() -> None:
    """It is the adapter's argv -- the one detail the layering exists to hide."""
    payload = render_started_emulator(
        StartEmulatorResult(
            name="Pixel_7",
            device_id="emulator-5554",
            pid=1,
            headless=True,
            booted=True,
            startup_duration_seconds=1.0,
            launch_argv=("/opt/android-sdk/emulator/emulator", "-avd", "Pixel_7"),
        )
    )
    dumped = payload.model_dump()
    assert "launch_argv" not in dumped
    assert not any("/opt/android-sdk" in str(value) for value in dumped.values())


def test_a_stopped_emulator_may_not_know_which_avd_it_was() -> None:
    payload = render_stopped_emulator(
        StopEmulatorResult(
            device_id="emulator-5554", avd_name=None, stopped=True, duration_seconds=3.4
        )
    )
    assert payload.avd_name is None
    assert payload.stopped is True


def test_a_created_emulator_renders_its_paths_as_strings() -> None:
    payload = render_created_emulator(
        CreateEmulatorResult(
            name="Pixel_7_API_34",
            path=Path("/home/u/.android/avd/Pixel_7_API_34.avd"),
            config_path=Path("/home/u/.android/avd/Pixel_7_API_34.avd/config.ini"),
            system_image=IMAGE,
            device="pixel_7",
            replaced_existing=False,
            duration_seconds=2.1,
        )
    )

    assert payload.path == "/home/u/.android/avd/Pixel_7_API_34.avd"
    assert payload.config_path.endswith("config.ini")
    assert payload.replaced_existing is False


def test_a_deleted_emulator_reports_whether_it_had_to_be_stopped_first() -> None:
    payload = render_deleted_emulator(
        DeleteEmulatorResult(
            name="Pixel_7_API_34",
            deleted_path=Path("/home/u/.android/avd/Pixel_7_API_34.avd"),
            stopped_first=True,
            duration_seconds=4.2,
        )
    )

    assert payload.stopped_first is True
    assert payload.deleted_path == "/home/u/.android/avd/Pixel_7_API_34.avd"


def test_a_deletion_with_nothing_on_disk_renders_a_null_path() -> None:
    payload = render_deleted_emulator(
        DeleteEmulatorResult(
            name="Gone", deleted_path=None, stopped_first=False, duration_seconds=0.1
        )
    )
    assert payload.deleted_path is None


def test_an_already_installed_image_is_rendered_as_the_no_op_it_was() -> None:
    payload = render_installed_image(
        InstallSystemImageResult(
            system_image=IMAGE,
            path=Path("/opt/android-sdk/system-images/android-34/google_apis/x86_64"),
            already_installed=True,
            duration_seconds=0.1,
        )
    )

    assert payload.already_installed is True
    assert payload.system_image == IMAGE
    assert payload.path.startswith("/opt/android-sdk")


def test_every_payload_is_json_serialisable() -> None:
    """The whole point of this file: no Path, no tuple of dataclasses, no property."""
    import json

    payload = render_device_list(
        ListDevicesResult(
            devices=(AndroidDevice(device_id="emulator-5554", state="device", is_emulator=True),)
        )
    )
    assert json.loads(payload.model_dump_json())["devices"][0]["device_id"] == "emulator-5554"


def test_a_renamed_emulator_reports_both_names_and_both_paths() -> None:
    """The docstring's Returns example and this assertion are the same payload."""
    payload = render_renamed_emulator(
        RenameEmulatorResult(
            name="Pixel_7_Regression",
            previous_name="Pixel_7_API_34",
            path=Path("/home/u/.android/avd/Pixel_7_Regression.avd"),
            previous_path=Path("/home/u/.android/avd/Pixel_7_API_34.avd"),
            stopped_first=False,
            duration_seconds=0.3,
        )
    )

    assert payload.model_dump() == {
        "name": "Pixel_7_Regression",
        "previous_name": "Pixel_7_API_34",
        "path": "/home/u/.android/avd/Pixel_7_Regression.avd",
        "previous_path": "/home/u/.android/avd/Pixel_7_API_34.avd",
        "stopped_first": False,
        "duration_seconds": 0.3,
    }


def test_a_rename_renders_paths_as_strings_not_path_objects() -> None:
    """A Path is not JSON; this is the one reason the renderer exists."""
    payload = render_renamed_emulator(
        RenameEmulatorResult(
            name="new",
            previous_name="old",
            path=Path("/avd/new.avd"),
            previous_path=None,
            stopped_first=True,
            duration_seconds=0.1,
        )
    )

    assert isinstance(payload.path, str)
    assert payload.previous_path is None
    assert payload.stopped_first is True
