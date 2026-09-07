"""The entities and boundary DTOs of the Android module.

Every type here appears in a port signature (:mod:`..domain.ports`), which is
why it lives in the domain rather than in the adapter that happens to produce
it today. A second adapter -- a remote device farm, a different SDK layout --
would speak these same dataclasses.

This module imports nothing but the standard library. It is the bottom of the
module's dependency graph, and it must stay importable on a host with no
Android SDK installed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = [
    # requests
    "ListDevicesRequest",
    "ListEmulatorsRequest",
    "InstallSystemImageRequest",
    "CreateEmulatorRequest",
    "StartEmulatorRequest",
    "StopEmulatorRequest",
    "DeleteEmulatorRequest",
    "RenameEmulatorRequest",
    # results and value objects
    "AndroidDevice",
    "AvdInfo",
    "ListDevicesResult",
    "ListEmulatorsResult",
    "InstallSystemImageResult",
    "CreateEmulatorResult",
    "StartEmulatorResult",
    "StopEmulatorResult",
    "DeleteEmulatorResult",
    "RenameEmulatorResult",
]


# --------------------------------------------------------------------------
# requests
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ListDevicesRequest:
    """Input for :meth:`DeviceCatalog.list_devices`."""

    #: Ask each emulator for the AVD it is running. One extra adb call per
    #: emulator; the only way to map a serial back to an AVD name.
    resolve_avd_names: bool = True
    #: Keep devices whose state is not ``device`` (offline, unauthorized, ...).
    include_unavailable: bool = True


@dataclass(frozen=True, slots=True)
class ListEmulatorsRequest:
    """Input for :meth:`DeviceCatalog.list_emulators`."""

    #: Keep AVDs avdmanager reported under "could not be loaded". They exist on
    #: disk but are unusable -- a missing system image, a retired device profile.
    include_unloadable: bool = True


@dataclass(frozen=True, slots=True)
class InstallSystemImageRequest:
    """Input for :meth:`SystemImageInstaller.install_system_image`."""

    #: Full sdkmanager package id, e.g. ``system-images;android-34;google_apis;x86_64``.
    system_image: str
    #: Feed "y" to sdkmanager's licence prompts. Without it the install blocks.
    accept_licenses: bool = True
    #: Run sdkmanager even when the image is already unpacked under the SDK root.
    reinstall: bool = False


@dataclass(frozen=True, slots=True)
class CreateEmulatorRequest:
    """Input for :meth:`EmulatorLifecycle.create_emulator`."""

    name: str
    system_image: str
    device: str
    #: e.g. ``"512M"``. ``None`` leaves avdmanager's default.
    sdcard_size: str | None = None
    #: Overwrite an existing AVD of the same name instead of raising.
    force: bool = False
    #: Only needed when the image ships more than one ABI.
    abi: str | None = None


@dataclass(frozen=True, slots=True)
class StartEmulatorRequest:
    """Input for :meth:`EmulatorLifecycle.start_emulator`."""

    name: str
    #: Ignored by :meth:`start_emulator_headless`, which always forces it on.
    headless: bool = False
    #: Block until ``sys.boot_completed=1``. When false the call returns as soon
    #: as the serial attaches to adb and ``booted`` is reported as False.
    wait_for_boot: bool = True
    #: ``None`` falls back to the adapter's configured value.
    boot_timeout_seconds: float | None = None
    poll_interval_seconds: float | None = None
    #: Discard the saved snapshot and boot from scratch.
    cold_boot: bool = False
    #: Reset userdata before booting.
    wipe_data: bool = False
    #: Appended after the adapter's own launch arguments.
    extra_args: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StopEmulatorRequest:
    """Input for :meth:`EmulatorLifecycle.stop_emulator`."""

    device_id: str
    #: Poll until the serial leaves the device list.
    wait_for_exit: bool = True
    timeout_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class DeleteEmulatorRequest:
    """Input for :meth:`EmulatorLifecycle.delete_emulator`."""

    name: str
    #: Stop the AVD first if it is running. Without it a running AVD raises
    #: :class:`~.errors.EmulatorInUse` rather than being deleted out from
    #: under itself.
    stop_if_running: bool = False


@dataclass(frozen=True, slots=True)
class RenameEmulatorRequest:
    """Input for :meth:`EmulatorLifecycle.rename_emulator`.

    There is deliberately no "move the payload directory too" flag. The backend
    moves ``<name>.avd`` to ``<new_name>.avd`` and rewrites the ``.ini``'s
    ``path=`` as part of the rename, so a flag would only offer callers the
    chance to leave the two disagreeing.
    """

    name: str
    #: The name the AVD should have afterwards. Same alphabet as
    #: :attr:`CreateEmulatorRequest.name`, and it must not already be taken --
    #: including by this AVD, so renaming something to its current name is
    #: :class:`~.errors.EmulatorAlreadyExists` rather than a silent no-op.
    new_name: str
    #: Stop the AVD first if it is running. Without it a running AVD raises
    #: :class:`~.errors.EmulatorInUse`: a rename moves the payload directory,
    #: and moving it out from under a live emulator corrupts the device.
    stop_if_running: bool = False


# --------------------------------------------------------------------------
# results and value objects
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AndroidDevice:
    """One device attached to the host."""

    device_id: str
    state: str
    is_emulator: bool
    avd_name: str | None = None
    product: str | None = None
    model: str | None = None
    device: str | None = None
    transport_id: str | None = None

    @property
    def is_available(self) -> bool:
        """True when the backend will actually accept commands for this serial."""
        return self.state == "device"


@dataclass(frozen=True, slots=True)
class AvdInfo:
    """One Android Virtual Device known to the host."""

    name: str
    path: Path | None = None
    device: str | None = None
    target: str | None = None
    based_on: str | None = None
    tag_abi: str | None = None
    sdcard: str | None = None
    #: False for AVDs the backend listed as unloadable; :attr:`error` says why.
    loadable: bool = True
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ListDevicesResult:
    devices: tuple[AndroidDevice, ...]

    @property
    def emulators(self) -> tuple[AndroidDevice, ...]:
        return tuple(d for d in self.devices if d.is_emulator)

    def by_avd_name(self, name: str) -> AndroidDevice | None:
        for device in self.devices:
            if device.avd_name == name:
                return device
        return None

    def by_device_id(self, device_id: str) -> AndroidDevice | None:
        for device in self.devices:
            if device.device_id == device_id:
                return device
        return None


@dataclass(frozen=True, slots=True)
class ListEmulatorsResult:
    emulators: tuple[AvdInfo, ...]

    @property
    def loadable(self) -> tuple[AvdInfo, ...]:
        return tuple(a for a in self.emulators if a.loadable)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(a.name for a in self.emulators)

    def by_name(self, name: str) -> AvdInfo | None:
        for avd in self.emulators:
            if avd.name == name:
                return avd
        return None


@dataclass(frozen=True, slots=True)
class InstallSystemImageResult:
    system_image: str
    path: Path
    #: True when the image was already unpacked and no install was run.
    already_installed: bool
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class CreateEmulatorResult:
    name: str
    path: Path
    config_path: Path
    system_image: str
    device: str
    #: True when an existing AVD of the same name was overwritten.
    replaced_existing: bool
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class StartEmulatorResult:
    name: str
    device_id: str
    pid: int
    headless: bool
    #: False only when ``wait_for_boot`` was not requested.
    booted: bool
    startup_duration_seconds: float
    launch_argv: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StopEmulatorResult:
    device_id: str
    avd_name: str | None
    #: False only when ``wait_for_exit`` was not requested.
    stopped: bool
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class DeleteEmulatorResult:
    name: str
    #: The payload directory that was removed.
    deleted_path: Path | None
    #: True when the AVD had to be stopped first.
    stopped_first: bool
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class RenameEmulatorResult:
    #: What the AVD is called now. ``name`` means the same thing on every result
    #: in this file, which is why the old one is the field that got a prefix.
    name: str
    previous_name: str
    #: The payload directory now. The backend moves it as part of the rename, so
    #: this is read back from disk rather than assumed from the new name.
    path: Path
    previous_path: Path | None
    #: True when the AVD was running and had to be shut down first.
    stopped_first: bool
    duration_seconds: float
