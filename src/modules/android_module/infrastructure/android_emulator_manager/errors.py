"""Typed failures raised by the Android emulator infrastructure component.

Every error this component reports is an :class:`AndroidEmulatorError`. Nothing
foreign -- no ``OSError``, no ``TimeoutError``, no ``CalledProcessError`` -- is
allowed to escape past the manager's public methods, so a caller can branch on
what went wrong without reading message strings.

Each class also inherits the matching *domain* error from
``android_module.domain.errors``. That is the whole trick behind Rule 0 §2: a
use case catches :class:`~...domain.errors.EmulatorNotFound` and this component
raises :class:`AvdNotFoundError`, which is one, so the application layer never
names an Android SDK concept and no translation shim sits between the two.
Domain errors carry the fact; these subclasses add the evidence -- the argv, the
exit code, the tail of the child's output.

Two conventions, both enforced by the architecture test:

* ``type(exc)(*exc.args)`` must reconstruct the exception, so ``__init__``
  passes its real arguments to ``Exception.__init__`` and ``__str__`` does the
  formatting. An exception that hands ``super()`` a pre-rendered string is not
  copyable or picklable and will not survive a process boundary.
* ``Exception.__init__`` is called explicitly rather than through ``super()``:
  these classes inherit from two branches, and a co-operative ``super()`` would
  dispatch into the domain sibling's constructor with the wrong arity.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ...domain.errors import (
    AndroidModuleError,
    BackendFailure,
    BackendUnavailable,
    DeviceNotFound,
    EmulatorAlreadyExists,
    EmulatorAlreadyRunning,
    EmulatorBootTimeout,
    EmulatorInUse,
    EmulatorNotFound,
    EmulatorStartFailed,
    EmulatorStopFailed,
    InvalidDeviceId,
    InvalidEmulatorName,
    InvalidSystemImage,
    SystemImageNotInstalled,
    UnknownDeviceProfile,
)
from .models import CommandResult, display_command

__all__ = [
    "AndroidEmulatorError",
    "AndroidToolNotFoundError",
    "SdkRootNotConfiguredError",
    "CommandError",
    "CommandFailedError",
    "CommandTimeoutError",
    "InvalidAvdNameError",
    "AvdNotFoundError",
    "AvdAlreadyExistsError",
    "AvdInUseError",
    "UnknownDeviceProfileError",
    "InvalidSystemImageError",
    "SystemImageNotInstalledError",
    "EmulatorStartError",
    "EmulatorAlreadyRunningError",
    "EmulatorBootTimeoutError",
    "EmulatorStopTimeoutError",
    "DeviceNotFoundError",
    "NotAnEmulatorError",
]


class AndroidEmulatorError(AndroidModuleError):
    """Base class for every failure this component reports."""


class AndroidToolNotFoundError(AndroidEmulatorError, BackendUnavailable):
    """A host tool (adb/emulator/avdmanager/sdkmanager) could not be resolved."""

    def __init__(self, tool: str, configured: str, searched: Sequence[str] = ()) -> None:
        Exception.__init__(self, tool, configured, tuple(searched))
        self.tool = tool
        self.what = tool
        self.configured = configured
        self.searched = tuple(searched)
        self.detail = f"not found as {configured!r}"

    def __str__(self) -> str:
        where = f" (looked in: {', '.join(self.searched)})" if self.searched else ""
        return f"Android tool {self.tool!r} not found as {self.configured!r}{where}"


class SdkRootNotConfiguredError(AndroidEmulatorError, BackendUnavailable):
    """An operation needed the SDK root and neither config nor environment had one."""

    def __init__(self, operation: str) -> None:
        Exception.__init__(self, operation)
        self.operation = operation
        self.what = "the Android SDK root is not configured"
        self.detail = operation

    def __str__(self) -> str:
        return (
            f"{self.operation} needs the Android SDK root; set AndroidSdkConfig.sdk_root "
            f"or ANDROID_SDK_ROOT/ANDROID_HOME"
        )


class CommandError(AndroidEmulatorError, BackendFailure):
    """Base class for failures that carry the command that produced them."""

    def __init__(self, argv: Sequence[str]) -> None:
        Exception.__init__(self, tuple(argv))
        self.argv = tuple(argv)
        self.operation = display_command(argv)
        self.detail = ""

    def __str__(self) -> str:
        return f"{display_command(self.argv)} failed"


class CommandFailedError(CommandError):
    """A host tool exited non-zero and the reason could not be classified further."""

    def __init__(self, result: CommandResult) -> None:
        Exception.__init__(self, result)
        self.result = result
        self.argv = result.argv
        self.operation = result.display_command
        self.detail = result.first_error_line()

    def __str__(self) -> str:
        return (
            f"{self.result.display_command} exited {self.result.returncode}: "
            f"{self.result.first_error_line()}"
        )


class CommandTimeoutError(CommandError):
    """A host tool did not finish inside its configured timeout and was killed."""

    def __init__(self, argv: Sequence[str], timeout_seconds: float) -> None:
        Exception.__init__(self, tuple(argv), timeout_seconds)
        self.argv = tuple(argv)
        self.timeout_seconds = timeout_seconds
        self.operation = display_command(argv)
        self.detail = f"timed out after {timeout_seconds}s"

    def __str__(self) -> str:
        return (
            f"{display_command(self.argv)} did not finish within "
            f"{self.timeout_seconds}s and was killed"
        )


class InvalidAvdNameError(AndroidEmulatorError, InvalidEmulatorName):
    """An AVD name contains characters avdmanager will not accept."""

    def __init__(self, name: str) -> None:
        Exception.__init__(self, name)
        self.name = name

    def __str__(self) -> str:
        return f"invalid AVD name {self.name!r}: use only letters, digits, '.', '_' and '-'"


class AvdNotFoundError(AndroidEmulatorError, EmulatorNotFound):
    """The named AVD does not exist on this host."""

    def __init__(self, name: str, avd_home: Path) -> None:
        Exception.__init__(self, name, avd_home)
        self.name = name
        self.avd_home = avd_home

    def __str__(self) -> str:
        return f"no AVD named {self.name!r} under {self.avd_home}"


class AvdAlreadyExistsError(AndroidEmulatorError, EmulatorAlreadyExists):
    """An AVD with that name already exists and ``force`` was not set."""

    def __init__(self, name: str) -> None:
        Exception.__init__(self, name)
        self.name = name

    def __str__(self) -> str:
        return f"AVD {self.name!r} already exists; pass force=True to overwrite it"


class AvdInUseError(AndroidEmulatorError, EmulatorInUse):
    """The AVD is currently running, so the operation would corrupt it."""

    def __init__(self, name: str, device_id: str | None) -> None:
        Exception.__init__(self, name, device_id)
        self.name = name
        self.device_id = device_id

    def __str__(self) -> str:
        running_as = f" as {self.device_id}" if self.device_id else ""
        return f"AVD {self.name!r} is running{running_as}; stop it first"


class UnknownDeviceProfileError(AndroidEmulatorError, UnknownDeviceProfile):
    """avdmanager does not know the requested hardware profile."""

    def __init__(self, device: str) -> None:
        Exception.__init__(self, device)
        self.device = device

    def __str__(self) -> str:
        return f"unknown device profile {self.device!r}; see `avdmanager list device`"


class InvalidSystemImageError(AndroidEmulatorError, InvalidSystemImage):
    """A string was passed where an sdkmanager system-image package id was expected."""

    def __init__(self, system_image: str, reason: str) -> None:
        Exception.__init__(self, system_image, reason)
        self.system_image = system_image
        self.reason = reason

    def __str__(self) -> str:
        return f"invalid system image {self.system_image!r}: {self.reason}"


class SystemImageNotInstalledError(AndroidEmulatorError, SystemImageNotInstalled):
    """The system image is not present under the SDK root."""

    def __init__(self, system_image: str, expected_path: Path | None = None) -> None:
        Exception.__init__(self, system_image, expected_path)
        self.system_image = system_image
        self.expected_path = expected_path

    def __str__(self) -> str:
        where = f" (expected {self.expected_path})" if self.expected_path else ""
        return (
            f"system image {self.system_image!r} is not installed{where}; "
            f"install it with install_system_image first"
        )


class EmulatorStartError(AndroidEmulatorError, EmulatorStartFailed):
    """The emulator process refused to start or exited during startup."""

    def __init__(self, name: str, returncode: int | None, log_tail: Sequence[str]) -> None:
        Exception.__init__(self, name, returncode, tuple(log_tail))
        self.name = name
        self.returncode = returncode
        self.log_tail = tuple(log_tail)
        self.detail = f"exited {returncode}"

    def __str__(self) -> str:
        detail = "\n".join(self.log_tail[-20:]) or "(no output)"
        return (
            f"emulator for AVD {self.name!r} exited ({self.returncode}) during startup:\n{detail}"
        )


class EmulatorAlreadyRunningError(AndroidEmulatorError, EmulatorAlreadyRunning):
    """The AVD is already attached to adb."""

    def __init__(self, name: str, device_id: str) -> None:
        Exception.__init__(self, name, device_id)
        self.name = name
        self.device_id = device_id

    def __str__(self) -> str:
        return f"AVD {self.name!r} is already running as {self.device_id}"


class EmulatorBootTimeoutError(AndroidEmulatorError, EmulatorBootTimeout):
    """The emulator started but never reported ``sys.boot_completed=1`` in time."""

    def __init__(
        self,
        name: str,
        device_id: str | None,
        timeout_seconds: float,
        log_tail: Sequence[str] = (),
    ) -> None:
        Exception.__init__(self, name, device_id, timeout_seconds, tuple(log_tail))
        self.name = name
        self.device_id = device_id
        self.timeout_seconds = timeout_seconds
        self.log_tail = tuple(log_tail)

    def __str__(self) -> str:
        attached = (
            f" (attached as {self.device_id})" if self.device_id else " (never attached to adb)"
        )
        return f"AVD {self.name!r} did not finish booting within {self.timeout_seconds}s{attached}"


class EmulatorStopTimeoutError(AndroidEmulatorError, EmulatorStopFailed):
    """``adb emu kill`` was accepted but the device never left the device list."""

    def __init__(self, device_id: str, timeout_seconds: float) -> None:
        Exception.__init__(self, device_id, timeout_seconds)
        self.device_id = device_id
        self.timeout_seconds = timeout_seconds

    def __str__(self) -> str:
        return f"{self.device_id} was still attached {self.timeout_seconds}s after emu kill"


class DeviceNotFoundError(AndroidEmulatorError, DeviceNotFound):
    """No device with that serial is attached to adb."""

    def __init__(self, device_id: str, attached: Sequence[str] = ()) -> None:
        Exception.__init__(self, device_id, tuple(attached))
        self.device_id = device_id
        self.attached = tuple(attached)

    def __str__(self) -> str:
        seen = ", ".join(self.attached) if self.attached else "none"
        return f"device {self.device_id!r} is not attached (attached: {seen})"


class NotAnEmulatorError(AndroidEmulatorError, InvalidDeviceId):
    """The serial belongs to a physical device; emulator control does not apply."""

    def __init__(self, device_id: str) -> None:
        Exception.__init__(self, device_id)
        self.device_id = device_id

    def __str__(self) -> str:
        return f"{self.device_id!r} is not an emulator serial (expected 'emulator-<port>')"
