"""Unit tests for ``AndroidEmulatorService``, against mocked ports.

The service's whole job is to turn an intent ("get the available devices") into
the right domain request and hand it to the right port. So that is what is
asserted: which port was called, and what request it was given. Mocking the
ports is required here (Rule 2 §3) -- a real Android SDK has no business in a
test about argument construction, and the ports are exactly the seam that lets
it stay out.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from modules.android_module.application.services import AndroidEmulatorService
from modules.android_module.domain import EmulatorNotFound
from modules.android_module.domain.ports import (
    DeviceCatalog,
    EmulatorLifecycle,
    SystemImageInstaller,
)

pytestmark = pytest.mark.unit

IMAGE = "system-images;android-34;google_apis;x86_64"


@pytest.fixture
def devices() -> AsyncMock:
    return AsyncMock(spec=DeviceCatalog)


@pytest.fixture
def emulators() -> AsyncMock:
    return AsyncMock(spec=EmulatorLifecycle)


@pytest.fixture
def images() -> AsyncMock:
    return AsyncMock(spec=SystemImageInstaller)


@pytest.fixture
def service(devices: AsyncMock, emulators: AsyncMock, images: AsyncMock) -> AndroidEmulatorService:
    return AndroidEmulatorService(devices=devices, emulators=emulators, images=images)


# -- reading ---------------------------------------------------------------


async def test_get_available_devices_excludes_the_unavailable_ones(
    service: AndroidEmulatorService, devices: AsyncMock
) -> None:
    result = await service.get_available_devices()

    request = devices.list_devices.await_args.args[0]
    assert request.include_unavailable is False
    assert request.resolve_avd_names is True
    assert result is devices.list_devices.return_value


async def test_get_all_devices_includes_the_unavailable_ones(
    service: AndroidEmulatorService, devices: AsyncMock
) -> None:
    await service.get_all_devices()

    request = devices.list_devices.await_args.args[0]
    assert request.include_unavailable is True


async def test_the_two_device_reads_differ_only_in_that_one_flag(
    service: AndroidEmulatorService, devices: AsyncMock
) -> None:
    """The flag is the entire reason these are two methods; if it ever stopped
    differing, one of them would be lying about what it returns."""
    await service.get_available_devices()
    available = devices.list_devices.await_args.args[0]
    await service.get_all_devices()
    every = devices.list_devices.await_args.args[0]

    assert available.include_unavailable != every.include_unavailable
    assert available.resolve_avd_names == every.resolve_avd_names


async def test_avd_name_resolution_can_be_turned_off_because_it_costs_a_call(
    service: AndroidEmulatorService, devices: AsyncMock
) -> None:
    await service.get_available_devices(resolve_avd_names=False)
    assert devices.list_devices.await_args.args[0].resolve_avd_names is False


async def test_get_installed_emulators_asks_the_catalogue_not_the_device_list(
    service: AndroidEmulatorService, devices: AsyncMock
) -> None:
    """An AVD that has never booted is invisible to `list_devices`; this is the
    call that can still see it."""
    result = await service.get_installed_emulators()

    devices.list_emulators.assert_awaited_once()
    devices.list_devices.assert_not_awaited()
    assert result is devices.list_emulators.return_value


async def test_get_installed_emulators_keeps_the_broken_ones_by_default(
    service: AndroidEmulatorService, devices: AsyncMock
) -> None:
    """Hiding an unloadable AVD turns a broken AVD into a missing one."""
    await service.get_installed_emulators()
    assert devices.list_emulators.await_args.args[0].include_unloadable is True


async def test_get_installed_emulators_can_hide_the_broken_ones(
    service: AndroidEmulatorService, devices: AsyncMock
) -> None:
    await service.get_installed_emulators(include_unloadable=False)
    assert devices.list_emulators.await_args.args[0].include_unloadable is False


# -- running ---------------------------------------------------------------


async def test_run_emulator_asks_for_a_window(
    service: AndroidEmulatorService, emulators: AsyncMock
) -> None:
    result = await service.run_emulator("pixel")

    emulators.start_emulator.assert_awaited_once()
    emulators.start_emulator_headless.assert_not_awaited()
    request = emulators.start_emulator.await_args.args[0]
    assert request.name == "pixel"
    assert request.headless is False
    assert request.wait_for_boot is True
    assert result is emulators.start_emulator.return_value


async def test_run_emulator_without_window_uses_the_headless_entry_point(
    service: AndroidEmulatorService, emulators: AsyncMock
) -> None:
    await service.run_emulator_without_window("pixel")

    emulators.start_emulator_headless.assert_awaited_once()
    emulators.start_emulator.assert_not_awaited()
    request = emulators.start_emulator_headless.await_args.args[0]
    assert request.name == "pixel"
    assert request.headless is True


@pytest.mark.parametrize("method_name", ["run_emulator", "run_emulator_without_window"])
async def test_boot_options_reach_the_port_unchanged(
    service: AndroidEmulatorService, emulators: AsyncMock, method_name: str
) -> None:
    await getattr(service, method_name)(
        "pixel",
        wait_for_boot=False,
        cold_boot=True,
        wipe_data=True,
        boot_timeout_seconds=12.5,
    )

    port = (
        emulators.start_emulator
        if method_name == "run_emulator"
        else (emulators.start_emulator_headless)
    )
    request = port.await_args.args[0]
    assert request.wait_for_boot is False
    assert request.cold_boot is True
    assert request.wipe_data is True
    assert request.boot_timeout_seconds == 12.5


async def test_stop_emulator_addresses_the_device_by_serial(
    service: AndroidEmulatorService, emulators: AsyncMock
) -> None:
    result = await service.stop_emulator("emulator-5554")

    request = emulators.stop_emulator.await_args.args[0]
    assert request.device_id == "emulator-5554"
    assert result is emulators.stop_emulator.return_value


async def test_stop_emulator_waits_for_the_serial_to_go_away_by_default(
    service: AndroidEmulatorService, emulators: AsyncMock
) -> None:
    """Without the wait, `stopped` reports False while the device is still up."""
    await service.stop_emulator("emulator-5554")
    assert emulators.stop_emulator.await_args.args[0].wait_for_exit is True


async def test_stop_emulator_passes_its_options_through(
    service: AndroidEmulatorService, emulators: AsyncMock
) -> None:
    await service.stop_emulator("emulator-5554", wait_for_exit=False, timeout_seconds=3.5)

    request = emulators.stop_emulator.await_args.args[0]
    assert request.wait_for_exit is False
    assert request.timeout_seconds == 3.5


# -- provisioning ----------------------------------------------------------


async def test_create_device_builds_the_request_from_plain_arguments(
    service: AndroidEmulatorService, emulators: AsyncMock
) -> None:
    result = await service.create_device(
        "pixel", IMAGE, "pixel_6", sdcard_size="512M", abi="x86_64"
    )

    request = emulators.create_emulator.await_args.args[0]
    assert request.name == "pixel"
    assert request.system_image == IMAGE
    assert request.device == "pixel_6"
    assert request.sdcard_size == "512M"
    assert request.abi == "x86_64"
    assert result is emulators.create_emulator.return_value


async def test_create_device_does_not_overwrite_unless_asked(
    service: AndroidEmulatorService, emulators: AsyncMock
) -> None:
    await service.create_device("pixel", IMAGE, "pixel_6")
    assert emulators.create_emulator.await_args.args[0].force is False


async def test_replace_existing_is_what_force_is_called_out_here(
    service: AndroidEmulatorService, emulators: AsyncMock
) -> None:
    """A caller should have to say what it is forcing."""
    await service.create_device("pixel", IMAGE, "pixel_6", replace_existing=True)
    assert emulators.create_emulator.await_args.args[0].force is True


async def test_download_image_accepts_licences_by_default(
    service: AndroidEmulatorService, images: AsyncMock
) -> None:
    """Without this the installer blocks forever on the prompt."""
    result = await service.download_image(IMAGE)

    request = images.install_system_image.await_args.args[0]
    assert request.system_image == IMAGE
    assert request.accept_licenses is True
    assert request.reinstall is False
    assert result is images.install_system_image.return_value


async def test_download_image_can_be_forced_to_reinstall(
    service: AndroidEmulatorService, images: AsyncMock
) -> None:
    await service.download_image(IMAGE, reinstall=True, accept_licenses=False)

    request = images.install_system_image.await_args.args[0]
    assert request.reinstall is True
    assert request.accept_licenses is False


async def test_delete_emulator_refuses_a_running_avd_unless_told_otherwise(
    service: AndroidEmulatorService, emulators: AsyncMock
) -> None:
    """The safe default: the caller has to say it knows the device is in use."""
    result = await service.delete_emulator("pixel")

    request = emulators.delete_emulator.await_args.args[0]
    assert request.name == "pixel"
    assert request.stop_if_running is False
    assert result is emulators.delete_emulator.return_value


async def test_delete_emulator_can_stop_the_avd_first(
    service: AndroidEmulatorService, emulators: AsyncMock
) -> None:
    await service.delete_emulator("pixel", stop_if_running=True)
    assert emulators.delete_emulator.await_args.args[0].stop_if_running is True


# -- the layering itself ---------------------------------------------------


async def test_the_service_holds_only_the_authority_it_needs(
    service: AndroidEmulatorService, devices: AsyncMock, emulators: AsyncMock
) -> None:
    """ISP: reading devices must not be able to delete one.

    ``spec=DeviceCatalog`` is the assertion -- if the service ever reached for
    ``delete_emulator`` on the catalog port, the mock would raise.
    """
    await service.get_all_devices()
    assert not hasattr(devices, "delete_emulator")
    assert hasattr(emulators, "delete_emulator")


async def test_domain_errors_propagate_untouched(
    service: AndroidEmulatorService, emulators: AsyncMock
) -> None:
    """The service adds no error handling of its own; a port's failure is the
    product's failure, already in the domain's vocabulary."""
    emulators.start_emulator.side_effect = EmulatorNotFound("pixel")

    with pytest.raises(EmulatorNotFound) as excinfo:
        await service.run_emulator("pixel")
    assert excinfo.value.name == "pixel"


def test_the_service_names_no_adapter_and_no_sdk_concept() -> None:
    """Rule 0: the service is written against ports and domain models only."""
    import inspect

    source = inspect.getsource(AndroidEmulatorService)
    for leak in ("adb", "avdmanager", "sdkmanager", "AndroidEmulatorManager", "subprocess"):
        assert leak not in source, f"{leak!r} leaked into the service"
