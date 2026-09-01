"""Unit tests for ``parsing.py``.

Everything here is a pure function over real tool output, so it needs no host.
The samples are the actual shapes the Android tools emit -- daemon noise, states
containing spaces, the Target continuation line that packs two fields into one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from modules.android_module.domain import CreateEmulatorRequest
from modules.android_module.infrastructure.android_emulator_manager import (
    AvdAlreadyExistsError,
    CommandFailedError,
    CommandResult,
    InvalidAvdNameError,
    InvalidSystemImageError,
    SystemImageNotInstalledError,
    UnknownDeviceProfileError,
)
from modules.android_module.infrastructure.android_emulator_manager.parsing import (
    classify_create_failure,
    classify_install_failure,
    is_emulator_serial,
    parse_adb_devices,
    parse_avd_list,
    system_image_path,
    validate_avd_name,
    validate_system_image_id,
)

pytestmark = pytest.mark.unit

VALID_IMAGE = "system-images;android-34;google_apis;x86_64"


# -- serials and names ------------------------------------------------------


@pytest.mark.parametrize("serial", ["emulator-5554", "emulator-5556", "emulator-0"])
def test_emulator_serials_are_recognised(serial: str) -> None:
    assert is_emulator_serial(serial)


@pytest.mark.parametrize(
    "serial", ["1234567890abcdef", "emulator-", "emulator-abc", "Emulator-5554", ""]
)
def test_non_emulator_serials_are_rejected(serial: str) -> None:
    assert not is_emulator_serial(serial)


@pytest.mark.parametrize("name", ["pixel", "Pixel_6", "api-34.test", "a"])
def test_valid_avd_names_pass(name: str) -> None:
    validate_avd_name(name)


@pytest.mark.parametrize("name", ["", "has space", "slash/name", "quote'name", "semi;colon"])
def test_invalid_avd_names_raise_with_the_name_attached(name: str) -> None:
    with pytest.raises(InvalidAvdNameError) as excinfo:
        validate_avd_name(name)
    assert excinfo.value.name == name


# -- system image ids -------------------------------------------------------


def test_a_well_formed_package_id_passes() -> None:
    validate_system_image_id(VALID_IMAGE)


@pytest.mark.parametrize(
    "package_id",
    [
        "android-34",
        "system-images;android-34",
        "system-images;android-34;google_apis",
        "system-images;android-34;google_apis;x86_64;extra",
        "platforms;android-34;google_apis;x86_64",
        "system-images;;google_apis;x86_64",
        "",
    ],
)
def test_malformed_package_ids_raise(package_id: str) -> None:
    with pytest.raises(InvalidSystemImageError) as excinfo:
        validate_system_image_id(package_id)
    assert excinfo.value.system_image == package_id


def test_system_image_path_maps_a_package_id_onto_the_sdk_layout() -> None:
    assert system_image_path(Path("/sdk"), VALID_IMAGE) == Path(
        "/sdk/system-images/android-34/google_apis/x86_64"
    )


# -- adb devices -l ---------------------------------------------------------


def test_parse_adb_devices_reads_serial_state_and_properties() -> None:
    stdout = (
        "* daemon not running; starting now at tcp:5037\n"
        "* daemon started successfully\n"
        "List of devices attached\n"
        "emulator-5554          device product:sdk_gphone64_x86_64 "
        "model:sdk_gphone64 device:emu64xa transport_id:7\n"
    )
    (device,) = parse_adb_devices(stdout)
    assert device.device_id == "emulator-5554"
    assert device.state == "device"
    assert device.is_emulator
    assert device.is_available
    assert device.product == "sdk_gphone64_x86_64"
    assert device.model == "sdk_gphone64"
    assert device.transport_id == "7"


def test_parse_adb_devices_keeps_a_multi_word_state_intact() -> None:
    """``no permissions; see [http://...]`` is a state, not a key:value pair."""
    stdout = (
        "List of devices attached\n"
        "1234567890abcdef       no permissions; see [http://developer.android.com/x]\n"
    )
    (device,) = parse_adb_devices(stdout)
    assert device.device_id == "1234567890abcdef"
    assert device.state.startswith("no permissions")
    assert not device.is_emulator
    assert not device.is_available


def test_parse_adb_devices_handles_offline_and_an_empty_list() -> None:
    (device,) = parse_adb_devices("List of devices attached\nemulator-5554\toffline\n")
    assert device.state == "offline"
    assert not device.is_available
    assert parse_adb_devices("List of devices attached\n") == []


def test_parse_adb_devices_ignores_anything_before_the_header() -> None:
    assert parse_adb_devices("adb server version mismatch\nkilling...\n") == []


# -- avdmanager list avd ----------------------------------------------------

AVD_LIST = """Available Android Virtual Devices:
    Name: pixel_6_api_34
  Device: pixel_6 (Google)
    Path: /home/x/.android/avd/pixel_6_api_34.avd
  Target: Google APIs
          Based on: Android API 34 Tag/ABI: google_apis/x86_64
  Sdcard: 512 MB
---------
    Name: second_avd
    Path: /home/x/.android/avd/second_avd.avd
  Target: Google APIs

The following Android Virtual Devices could not be loaded:
    Name: broken_avd
    Path: /home/x/.android/avd/broken_avd.avd
   Error: Missing system image for Google APIs x86_64.
"""


def test_parse_avd_list_reads_every_entry_in_both_sections() -> None:
    avds = parse_avd_list(AVD_LIST)
    assert [a.name for a in avds] == ["pixel_6_api_34", "second_avd", "broken_avd"]


def test_parse_avd_list_splits_the_packed_target_continuation_line() -> None:
    first = parse_avd_list(AVD_LIST)[0]
    assert first.based_on == "Android API 34"
    assert first.tag_abi == "google_apis/x86_64"
    assert first.device == "pixel_6 (Google)"
    assert first.path == Path("/home/x/.android/avd/pixel_6_api_34.avd")
    assert first.sdcard == "512 MB"


def test_parse_avd_list_marks_the_unloadable_section_with_its_reason() -> None:
    broken = parse_avd_list(AVD_LIST)[2]
    assert not broken.loadable
    assert broken.error is not None and "Missing system image" in broken.error
    assert all(a.loadable for a in parse_avd_list(AVD_LIST)[:2])


def test_parse_avd_list_of_nothing_is_empty() -> None:
    assert parse_avd_list("Available Android Virtual Devices:\n") == []


# -- failure classification (the OCP tables) --------------------------------


def _failure(output: str) -> CommandResult:
    return CommandResult(
        argv=("avdmanager", "create", "avd"),
        returncode=1,
        stdout="",
        stderr=output,
        duration_seconds=0.1,
    )


REQUEST = CreateEmulatorRequest(name="pixel", system_image=VALID_IMAGE, device="pixel_6")


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("Error: Device pixel_6 not found", UnknownDeviceProfileError),
        ("Error: no device found matching", UnknownDeviceProfileError),
        ("Error: package path is not valid", SystemImageNotInstalledError),
        ("Error: AVD 'pixel' already exists", AvdAlreadyExistsError),
        ("Error: something nobody has seen before", CommandFailedError),
    ],
)
def test_create_failures_are_classified_by_table(output: str, expected: type[Exception]) -> None:
    assert isinstance(classify_create_failure(REQUEST, _failure(output)), expected)


def test_a_bare_not_found_is_only_a_device_profile_error_when_it_names_the_device() -> None:
    """``not found`` alone is far too broad a marker to match on."""
    unrelated = classify_create_failure(REQUEST, _failure("Error: license not found"))
    assert isinstance(unrelated, CommandFailedError)


def test_install_failures_are_classified_by_table() -> None:
    invalid = classify_install_failure(VALID_IMAGE, _failure("Failed to find package"))
    assert isinstance(invalid, InvalidSystemImageError)
    assert invalid.system_image == VALID_IMAGE

    unclassified = classify_install_failure(VALID_IMAGE, _failure("disk full"))
    assert isinstance(unclassified, CommandFailedError)
