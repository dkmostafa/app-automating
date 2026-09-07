"""Integration tests for ``manager.py``: the component's public operations.

Everything here goes through :class:`AndroidEmulatorManager` against the real
host -- real ``adb``, real ``avdmanager``, real AVDs, and one real headless
emulator boot. Nothing is faked, mocked, patched or stubbed (Rule 1 §6).

Its collaborators are covered on their own: tool resolution and child processes
in ``test_process.py``, the AVD home in ``test_filesystem.py``, tool output in
``../unit/test_parsing.py``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import typing

import pytest

from app_automating.modules.android_module.application import build_android_emulator_manager
from app_automating.modules.android_module.domain import (
    AndroidDevice,
    AvdInfo,
    CreateEmulatorRequest,
    DeleteEmulatorRequest,
    InstallSystemImageRequest,
    ListDevicesRequest,
    ListDevicesResult,
    ListEmulatorsRequest,
    ListEmulatorsResult,
    RenameEmulatorRequest,
    StartEmulatorRequest,
    StopEmulatorRequest,
)
from app_automating.modules.android_module.infrastructure import android_emulator_manager as module
from app_automating.modules.android_module.infrastructure.android_emulator_manager import (
    AndroidEmulatorManager,
    AndroidSdkConfig,
    AvdAlreadyExistsError,
    AvdInUseError,
    AvdNotFoundError,
    CommandTimeoutError,
    DeviceNotFoundError,
    EmulatorAlreadyRunningError,
    EmulatorBootTimeoutError,
    InvalidAvdNameError,
    InvalidSystemImageError,
    NotAnEmulatorError,
    SdkRootNotConfiguredError,
    SystemImageNotInstalledError,
)

from .conftest import BOOT_TIMEOUT_SECONDS, DEVICE_PROFILE, TEST_AVD_PREFIX

pytestmark = pytest.mark.integration

PUBLIC_METHODS = (
    "list_devices",
    "list_emulators",
    "install_system_image",
    "create_emulator",
    "start_emulator",
    "start_emulator_headless",
    "stop_emulator",
    "delete_emulator",
    "rename_emulator",
)


def _is_frozen_dataclass(annotation: object) -> bool:
    return (
        dataclasses.is_dataclass(annotation)
        and isinstance(annotation, type)
        and annotation.__dataclass_params__.frozen  # type: ignore[attr-defined]
    )


# ---------------------------------------------------------------------------
# the layer contract itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method_name", PUBLIC_METHODS)
def test_public_method_takes_one_request_dataclass_and_returns_a_result_dataclass(
    method_name: str,
) -> None:
    """Rule 1 of the infrastructure layer, asserted rather than documented."""
    method = getattr(AndroidEmulatorManager, method_name)
    assert inspect.iscoroutinefunction(method), f"{method_name} must be async"

    parameters = list(inspect.signature(method).parameters)
    assert parameters == ["self", "request"], (
        f"{method_name} must take exactly one parameter named 'request', got {parameters}"
    )

    # Resolved against manager.py's own globals, which is where the method is defined.
    hints = typing.get_type_hints(method)
    assert _is_frozen_dataclass(hints["request"]), (
        f"{method_name}'s request must be a frozen dataclass, got {hints['request']!r}"
    )
    assert _is_frozen_dataclass(hints["return"]), (
        f"{method_name}'s return must be a frozen dataclass, got {hints['return']!r}"
    )


def test_public_methods_are_the_whole_public_surface() -> None:
    """A new operation must be added to PUBLIC_METHODS so the contract test sees it."""
    exposed = {
        name
        for name, value in vars(AndroidEmulatorManager).items()
        if not name.startswith("_") and inspect.iscoroutinefunction(value)
    }
    assert exposed == set(PUBLIC_METHODS) | {"aclose"}


# ---------------------------------------------------------------------------
# list_devices
# ---------------------------------------------------------------------------


async def test_list_devices_queries_the_real_adb_server(
    manager: AndroidEmulatorManager,
) -> None:
    result = await manager.list_devices(ListDevicesRequest())
    assert isinstance(result, ListDevicesResult)
    assert isinstance(result.devices, tuple)
    for device in result.devices:
        assert isinstance(device, AndroidDevice)
        assert device.device_id
        assert device.state
        assert device.is_emulator == device.device_id.startswith("emulator-")


async def test_list_devices_excluding_unavailable_returns_a_subset(
    manager: AndroidEmulatorManager,
) -> None:
    everything = await manager.list_devices(ListDevicesRequest())
    available = await manager.list_devices(
        ListDevicesRequest(include_unavailable=False, resolve_avd_names=False)
    )
    assert all(device.is_available for device in available.devices)
    assert len(available.devices) <= len(everything.devices)


async def test_list_devices_honours_the_configured_adb_timeout(
    sdk_config: AndroidSdkConfig,
) -> None:
    impatient = build_android_emulator_manager(
        dataclasses.replace(sdk_config, adb_timeout_seconds=0.001)
    )
    with pytest.raises(CommandTimeoutError) as excinfo:
        await impatient.list_devices(ListDevicesRequest())
    assert excinfo.value.timeout_seconds == 0.001
    assert "adb" in " ".join(excinfo.value.argv)


# ---------------------------------------------------------------------------
# list_emulators
# ---------------------------------------------------------------------------


async def test_list_emulators_reports_the_avds_on_disk(
    manager: AndroidEmulatorManager, created_avd: str
) -> None:
    result = await manager.list_emulators(ListEmulatorsRequest())
    assert isinstance(result, ListEmulatorsResult)

    listed = result.by_name(created_avd)
    assert listed is not None, f"{created_avd} was created but not listed"
    assert isinstance(listed, AvdInfo)
    assert listed.loadable
    assert listed.error is None
    assert listed.path is not None and listed.path.is_dir()
    assert listed.tag_abi  # parsed off the Target continuation line
    assert listed.device is not None and DEVICE_PROFILE in listed.device


async def test_list_emulators_reports_an_unloadable_avd_with_its_reason(
    manager: AndroidEmulatorManager, created_avd: str
) -> None:
    """An AVD pointed at a system image that is not there still exists on disk;
    avdmanager lists it in a separate section with an error instead."""
    config_ini = manager.avd_home / f"{created_avd}.avd" / "config.ini"
    original = config_ini.read_text(encoding="utf-8")
    config_ini.write_text(
        original.replace("image.sysdir.1=", "image.sysdir.1=no/such/system/image/"),
        encoding="utf-8",
    )

    result = await manager.list_emulators(ListEmulatorsRequest(include_unloadable=True))
    broken = result.by_name(created_avd)
    assert broken is not None
    assert not broken.loadable
    assert broken.error is not None and broken.error.strip()
    assert broken.path is not None and broken.path.is_dir()

    excluded = await manager.list_emulators(ListEmulatorsRequest(include_unloadable=False))
    assert excluded.by_name(created_avd) is None
    assert all(avd.loadable for avd in excluded.emulators)


async def test_list_emulators_honours_the_configured_avdmanager_timeout(
    sdk_config: AndroidSdkConfig,
) -> None:
    impatient = build_android_emulator_manager(
        dataclasses.replace(sdk_config, avdmanager_timeout_seconds=0.01)
    )
    with pytest.raises(CommandTimeoutError) as excinfo:
        await impatient.list_emulators(ListEmulatorsRequest())
    assert excinfo.value.timeout_seconds == 0.01


# ---------------------------------------------------------------------------
# install_system_image
# ---------------------------------------------------------------------------


async def test_install_system_image_is_a_noop_when_already_unpacked(
    manager: AndroidEmulatorManager, installed_system_image: str
) -> None:
    result = await manager.install_system_image(
        InstallSystemImageRequest(system_image=installed_system_image)
    )
    assert result.already_installed is True
    assert result.path.is_dir()
    assert result.system_image == installed_system_image
    # No sdkmanager run at all, so this cannot have taken meaningful time.
    assert result.duration_seconds < 1.0


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
async def test_install_system_image_rejects_a_non_package_id(
    manager: AndroidEmulatorManager, package_id: str
) -> None:
    with pytest.raises(InvalidSystemImageError):
        await manager.install_system_image(InstallSystemImageRequest(system_image=package_id))


async def test_install_system_image_without_an_sdk_root_is_reported(
    installed_system_image: str,
) -> None:
    rootless = build_android_emulator_manager(AndroidSdkConfig(sdk_root=None))
    with pytest.raises(SdkRootNotConfiguredError):
        await rootless.install_system_image(
            InstallSystemImageRequest(system_image=installed_system_image)
        )


# ---------------------------------------------------------------------------
# create_emulator
# ---------------------------------------------------------------------------


async def test_create_emulator_writes_a_real_avd_to_disk(
    manager: AndroidEmulatorManager, avd_name: str, installed_system_image: str
) -> None:
    result = await manager.create_emulator(
        CreateEmulatorRequest(
            name=avd_name,
            system_image=installed_system_image,
            device=DEVICE_PROFILE,
            sdcard_size="256M",
        )
    )
    assert result.name == avd_name
    assert result.replaced_existing is False
    assert result.path.is_dir()
    assert result.config_path.is_file()
    assert (manager.avd_home / f"{avd_name}.ini").is_file()

    config = result.config_path.read_text(encoding="utf-8")
    api, tag, abi = installed_system_image.split(";")[1:]
    assert f"image.sysdir.1={'/'.join(('system-images', api, tag, abi))}" in config


async def test_create_emulator_refuses_to_clobber_an_existing_avd(
    manager: AndroidEmulatorManager, created_avd: str, installed_system_image: str
) -> None:
    with pytest.raises(AvdAlreadyExistsError) as excinfo:
        await manager.create_emulator(
            CreateEmulatorRequest(
                name=created_avd, system_image=installed_system_image, device=DEVICE_PROFILE
            )
        )
    assert excinfo.value.name == created_avd
    assert (manager.avd_home / f"{created_avd}.ini").is_file(), "the original was destroyed"


async def test_create_emulator_with_force_replaces_an_existing_avd(
    manager: AndroidEmulatorManager, created_avd: str, installed_system_image: str
) -> None:
    result = await manager.create_emulator(
        CreateEmulatorRequest(
            name=created_avd,
            system_image=installed_system_image,
            device=DEVICE_PROFILE,
            force=True,
        )
    )
    assert result.replaced_existing is True
    assert result.config_path.is_file()


@pytest.mark.parametrize("name", ["", "has space", "slash/name", "quote'name", "semi;colon"])
async def test_create_emulator_rejects_an_invalid_name(
    manager: AndroidEmulatorManager, installed_system_image: str, name: str
) -> None:
    with pytest.raises(InvalidAvdNameError):
        await manager.create_emulator(
            CreateEmulatorRequest(
                name=name, system_image=installed_system_image, device=DEVICE_PROFILE
            )
        )


async def test_create_emulator_rejects_a_system_image_that_is_not_installed(
    manager: AndroidEmulatorManager, avd_name: str
) -> None:
    with pytest.raises(SystemImageNotInstalledError) as excinfo:
        await manager.create_emulator(
            CreateEmulatorRequest(
                name=avd_name,
                system_image="system-images;android-99;google_apis;x86_64",
                device=DEVICE_PROFILE,
            )
        )
    assert excinfo.value.expected_path is not None
    assert not excinfo.value.expected_path.exists()
    assert not (manager.avd_home / f"{avd_name}.ini").exists()


async def test_create_emulator_rejects_an_unknown_device_profile(
    manager: AndroidEmulatorManager, avd_name: str, installed_system_image: str
) -> None:
    with pytest.raises(module.UnknownDeviceProfileError) as excinfo:
        await manager.create_emulator(
            CreateEmulatorRequest(
                name=avd_name,
                system_image=installed_system_image,
                device="no_such_device_profile_zzz",
            )
        )
    assert excinfo.value.device == "no_such_device_profile_zzz"
    assert not (manager.avd_home / f"{avd_name}.ini").exists()


# ---------------------------------------------------------------------------
# delete_emulator
# ---------------------------------------------------------------------------


async def test_delete_emulator_removes_the_avd_from_disk(
    manager: AndroidEmulatorManager, created_avd: str
) -> None:
    result = await manager.delete_emulator(DeleteEmulatorRequest(name=created_avd))

    assert result.name == created_avd
    assert result.stopped_first is False
    assert not (manager.avd_home / f"{created_avd}.ini").exists()
    assert not (manager.avd_home / f"{created_avd}.avd").exists()

    listed = await manager.list_emulators(ListEmulatorsRequest())
    assert listed.by_name(created_avd) is None


async def test_delete_emulator_raises_for_an_avd_that_is_not_there(
    manager: AndroidEmulatorManager,
) -> None:
    with pytest.raises(AvdNotFoundError) as excinfo:
        await manager.delete_emulator(DeleteEmulatorRequest(name=f"{TEST_AVD_PREFIX}never_created"))
    assert excinfo.value.avd_home == manager.avd_home


# ---------------------------------------------------------------------------
# rename
# ---------------------------------------------------------------------------


async def test_rename_emulator_moves_the_avd_and_its_payload_directory(
    manager: AndroidEmulatorManager, created_avd: str, second_avd_name: str
) -> None:
    """The whole operation, against the real avdmanager.

    Asserted together because they are one atomic outcome: the definition moves,
    the payload directory moves with it, and the AVD answers to exactly one name
    afterwards. A rename that did half of this would leave a working AVD whose
    name and directory disagree.
    """
    before = manager.avd_home / f"{created_avd}.avd"
    assert before.is_dir()

    result = await manager.rename_emulator(
        RenameEmulatorRequest(name=created_avd, new_name=second_avd_name)
    )

    assert result.name == second_avd_name
    assert result.previous_name == created_avd
    assert result.stopped_first is False
    assert result.duration_seconds >= 0

    # the definition
    assert (manager.avd_home / f"{second_avd_name}.ini").exists()
    assert not (manager.avd_home / f"{created_avd}.ini").exists()

    # the payload directory really moved, and the result says where to
    assert result.path.is_dir()
    assert result.path == manager.avd_home / f"{second_avd_name}.avd"
    assert result.previous_path == before
    assert not before.exists()

    # and the AVD answers to the new name only
    listed = await manager.list_emulators(ListEmulatorsRequest())
    assert listed.by_name(second_avd_name) is not None
    assert listed.by_name(created_avd) is None


async def test_rename_emulator_keeps_everything_that_was_on_the_device(
    manager: AndroidEmulatorManager, created_avd: str, second_avd_name: str
) -> None:
    """The claim the tool's docstring makes, checked rather than asserted in prose.

    A file written into the payload directory stands in for the snapshots,
    userdata and installed apps a real AVD carries: if it survives, so do they,
    because the directory is moved rather than rebuilt.
    """
    marker = manager.avd_home / f"{created_avd}.avd" / "at_it_marker.txt"
    marker.write_text("survives the rename", encoding="utf-8")

    result = await manager.rename_emulator(
        RenameEmulatorRequest(name=created_avd, new_name=second_avd_name)
    )

    moved = result.path / "at_it_marker.txt"
    assert moved.is_file()
    assert moved.read_text(encoding="utf-8") == "survives the rename"


async def test_the_renamed_avd_still_records_where_its_payload_lives(
    manager: AndroidEmulatorManager, created_avd: str, second_avd_name: str
) -> None:
    """``AvdStore.directory`` reads ``path=`` from the ``.ini``, so a rename that
    moved the directory without rewriting that line would leave a broken AVD
    that still looked fine in a listing."""
    await manager.rename_emulator(RenameEmulatorRequest(name=created_avd, new_name=second_avd_name))

    ini = (manager.avd_home / f"{second_avd_name}.ini").read_text(encoding="utf-8")
    assert f"{second_avd_name}.avd" in ini
    assert f"{created_avd}.avd" not in ini


async def test_rename_emulator_refuses_to_clobber_another_avd(
    manager: AndroidEmulatorManager,
    created_avd: str,
    second_avd_name: str,
    installed_system_image: str,
) -> None:
    """Unlike create, rename has no force: the other AVD's data is never at risk."""
    await manager.create_emulator(
        CreateEmulatorRequest(
            name=second_avd_name, system_image=installed_system_image, device=DEVICE_PROFILE
        )
    )

    with pytest.raises(AvdAlreadyExistsError) as excinfo:
        await manager.rename_emulator(
            RenameEmulatorRequest(name=created_avd, new_name=second_avd_name)
        )

    assert excinfo.value.name == second_avd_name
    # Both AVDs survive the refusal, untouched.
    assert (manager.avd_home / f"{created_avd}.ini").exists()
    assert (manager.avd_home / f"{second_avd_name}.ini").exists()


async def test_renaming_an_avd_to_its_own_name_is_refused(
    manager: AndroidEmulatorManager, created_avd: str
) -> None:
    """avdmanager treats this as a silent no-op and exits 0, which would have the
    manager report a rename that never happened."""
    with pytest.raises(AvdAlreadyExistsError) as excinfo:
        await manager.rename_emulator(RenameEmulatorRequest(name=created_avd, new_name=created_avd))

    assert excinfo.value.name == created_avd
    assert (manager.avd_home / f"{created_avd}.ini").exists()


async def test_rename_emulator_raises_for_an_avd_that_is_not_there(
    manager: AndroidEmulatorManager, second_avd_name: str
) -> None:
    with pytest.raises(AvdNotFoundError) as excinfo:
        await manager.rename_emulator(
            RenameEmulatorRequest(name=f"{TEST_AVD_PREFIX}never_created", new_name=second_avd_name)
        )

    assert excinfo.value.avd_home == manager.avd_home
    assert not (manager.avd_home / f"{second_avd_name}.ini").exists()


async def test_rename_emulator_rejects_an_invalid_target_name_before_spawning_anything(
    manager: AndroidEmulatorManager, created_avd: str
) -> None:
    """The load-bearing validation on this path.

    Handed a name with a path separator in it, avdmanager prints "Error: Failed
    to move ..." and then **exits 0** -- so the exit code cannot be trusted and
    the in-process check is what actually protects the AVD.
    """
    with pytest.raises(InvalidAvdNameError):
        await manager.rename_emulator(
            RenameEmulatorRequest(name=created_avd, new_name="bad name/../escape")
        )

    assert (manager.avd_home / f"{created_avd}.ini").exists()
    assert (manager.avd_home / f"{created_avd}.avd").is_dir()


async def test_rename_emulator_rejects_an_invalid_source_name(
    manager: AndroidEmulatorManager, second_avd_name: str
) -> None:
    with pytest.raises(InvalidAvdNameError):
        await manager.rename_emulator(
            RenameEmulatorRequest(name="not a valid name", new_name=second_avd_name)
        )


async def test_a_renamed_avd_can_be_renamed_back(
    manager: AndroidEmulatorManager, created_avd: str, second_avd_name: str
) -> None:
    """Nothing about the rename is one-way, and the round trip leaves no debris."""
    await manager.rename_emulator(RenameEmulatorRequest(name=created_avd, new_name=second_avd_name))
    back = await manager.rename_emulator(
        RenameEmulatorRequest(name=second_avd_name, new_name=created_avd)
    )

    assert back.name == created_avd
    assert (manager.avd_home / f"{created_avd}.avd").is_dir()
    assert not (manager.avd_home / f"{second_avd_name}.ini").exists()
    assert not (manager.avd_home / f"{second_avd_name}.avd").exists()


# ---------------------------------------------------------------------------
# start / stop failure paths (no successful boot needed)
# ---------------------------------------------------------------------------


async def test_start_emulator_raises_for_an_avd_that_is_not_there(
    manager: AndroidEmulatorManager,
) -> None:
    with pytest.raises(AvdNotFoundError):
        await manager.start_emulator(StartEmulatorRequest(name=f"{TEST_AVD_PREFIX}never_created"))


async def test_start_emulator_rejects_an_invalid_name(
    manager: AndroidEmulatorManager,
) -> None:
    with pytest.raises(InvalidAvdNameError):
        await manager.start_emulator(StartEmulatorRequest(name="not a valid name"))


async def test_start_emulator_kills_the_process_when_the_boot_deadline_passes(
    manager: AndroidEmulatorManager, created_avd: str
) -> None:
    """A real emulator is launched and then abandoned by the deadline. The
    manager must reap it -- a leaked emulator would hold the AVD lock and break
    every later test."""
    before = await manager.list_devices(ListDevicesRequest(resolve_avd_names=False))

    with pytest.raises(EmulatorBootTimeoutError) as excinfo:
        await manager.start_emulator_headless(
            StartEmulatorRequest(
                name=created_avd, boot_timeout_seconds=1.0, poll_interval_seconds=0.2
            )
        )
    assert excinfo.value.name == created_avd
    assert excinfo.value.timeout_seconds == 1.0

    # Give the killed process a moment to drop off adb, then prove nothing leaked.
    for _ in range(20):
        after = await manager.list_devices(ListDevicesRequest(resolve_avd_names=False))
        if len(after.devices) <= len(before.devices):
            break
        await asyncio.sleep(1.0)
    assert {d.device_id for d in after.devices} <= {d.device_id for d in before.devices}
    # The AVD is not held: it can still be deleted.
    await manager.delete_emulator(DeleteEmulatorRequest(name=created_avd))


async def test_stop_emulator_rejects_a_serial_that_is_not_an_emulator(
    manager: AndroidEmulatorManager,
) -> None:
    with pytest.raises(NotAnEmulatorError) as excinfo:
        await manager.stop_emulator(StopEmulatorRequest(device_id="1234567890abcdef"))
    assert excinfo.value.device_id == "1234567890abcdef"


async def test_stop_emulator_raises_for_a_serial_that_is_not_attached(
    manager: AndroidEmulatorManager,
) -> None:
    with pytest.raises(DeviceNotFoundError) as excinfo:
        await manager.stop_emulator(StopEmulatorRequest(device_id="emulator-5598"))
    assert excinfo.value.device_id == "emulator-5598"


# ---------------------------------------------------------------------------
# the full lifecycle: one real headless boot
# ---------------------------------------------------------------------------


async def test_headless_emulator_boots_serves_adb_and_stops(
    manager: AndroidEmulatorManager, host_can_boot_an_emulator: None, created_avd: str
) -> None:
    """The slow one. Boots a real emulator headless, checks everything that is
    only observable while it runs, then shuts it down.

    These assertions share a single boot deliberately: on this host a boot is
    ~40s, and splitting them across tests would either multiply that or make
    them depend on execution order.

    ``host_can_boot_an_emulator`` comes first in the signature on purpose: it
    decides whether this machine can boot anything at all before the AVD is
    created, so a host that is out of memory or already running an emulator
    skips without paying for an AVD it will never start.
    """
    started = await manager.start_emulator_headless(
        StartEmulatorRequest(
            name=created_avd,
            boot_timeout_seconds=BOOT_TIMEOUT_SECONDS,
            poll_interval_seconds=2.0,
        )
    )

    assert started.name == created_avd
    assert started.booted is True
    assert started.headless is True
    assert started.device_id.startswith("emulator-")
    assert started.pid > 0
    assert "-no-window" in started.launch_argv

    # adb sees it, and the serial maps back to the AVD we asked for.
    devices = await manager.list_devices(ListDevicesRequest())
    running = devices.by_device_id(started.device_id)
    assert running is not None
    assert running.is_emulator
    assert running.is_available
    assert running.avd_name == created_avd
    assert running.model  # -l properties were parsed
    assert devices.by_avd_name(created_avd) is running

    # A second launch of the same AVD is refused rather than racing the first.
    with pytest.raises(EmulatorAlreadyRunningError) as already:
        await manager.start_emulator_headless(StartEmulatorRequest(name=created_avd))
    assert already.value.device_id == started.device_id

    # Deleting a running AVD would corrupt it, so it is refused by default.
    with pytest.raises(AvdInUseError):
        await manager.delete_emulator(DeleteEmulatorRequest(name=created_avd))

    # Renaming it is refused for a stronger reason: the rename moves the payload
    # directory, and moving it out from under a live emulator corrupts it.
    with pytest.raises(AvdInUseError) as in_use:
        await manager.rename_emulator(
            RenameEmulatorRequest(name=created_avd, new_name=f"{created_avd}_x")
        )
    assert in_use.value.device_id == started.device_id

    stopped = await manager.stop_emulator(StopEmulatorRequest(device_id=started.device_id))
    assert stopped.stopped is True
    assert stopped.device_id == started.device_id
    assert stopped.avd_name == created_avd

    after = await manager.list_devices(ListDevicesRequest(resolve_avd_names=False))
    assert after.by_device_id(started.device_id) is None

    # Once stopped it deletes cleanly.
    deleted = await manager.delete_emulator(DeleteEmulatorRequest(name=created_avd))
    assert deleted.stopped_first is False
    assert not (manager.avd_home / f"{created_avd}.ini").exists()
