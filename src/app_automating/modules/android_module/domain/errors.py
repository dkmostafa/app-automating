"""The failure vocabulary of the Android module.

A use case may catch only these. An adapter raises its own richer subclass
(Rule 1 §3) which *is-a* one of these, so the application layer never has to
name an infrastructure type in an ``except`` clause and no translation step sits
in the middle waiting to be forgotten.

Two conventions hold for every error in the module, here and in every adapter:

* **The constructor arguments are the state.** Each ``__init__`` passes its real
  arguments to ``Exception.__init__`` and stores them, so
  ``type(exc)(*exc.args)`` reconstructs the exception. That is what makes these
  copyable, picklable and able to cross a process boundary. Formatting lives in
  ``__str__``, never in the arguments handed to ``super()``.
* **``Exception.__init__`` is called explicitly**, not through ``super()``.
  Adapter errors inherit from both their component's base and one of these, and
  a co-operative ``super().__init__`` would dispatch along the MRO into a
  sibling's constructor with the wrong arity.
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "AndroidModuleError",
    "BackendUnavailable",
    "BackendFailure",
    "InvalidEmulatorName",
    "InvalidDeviceId",
    "InvalidSystemImage",
    "SystemImageNotInstalled",
    "UnknownDeviceProfile",
    "EmulatorNotFound",
    "EmulatorAlreadyExists",
    "EmulatorInUse",
    "EmulatorAlreadyRunning",
    "EmulatorStartFailed",
    "EmulatorBootTimeout",
    "EmulatorStopFailed",
    "DeviceNotFound",
]


class AndroidModuleError(Exception):
    """Base class for every failure the Android module reports."""


# -- the backend itself ----------------------------------------------------


class BackendUnavailable(AndroidModuleError):
    """The backend cannot be reached at all: a missing tool, an unset SDK root.

    Distinct from :class:`BackendFailure` on purpose -- this one is a
    provisioning problem on the host and no retry will fix it.
    """

    def __init__(self, what: str, detail: str = "") -> None:
        Exception.__init__(self, what, detail)
        self.what = what
        self.detail = detail

    def __str__(self) -> str:
        suffix = f": {self.detail}" if self.detail else ""
        return f"Android backend unavailable -- {self.what}{suffix}"


class BackendFailure(AndroidModuleError):
    """The backend was reached and refused, in a way the domain cannot name."""

    def __init__(self, operation: str, detail: str = "") -> None:
        Exception.__init__(self, operation, detail)
        self.operation = operation
        self.detail = detail

    def __str__(self) -> str:
        suffix = f": {self.detail}" if self.detail else ""
        return f"{self.operation} failed{suffix}"


# -- rejected input --------------------------------------------------------


class InvalidEmulatorName(AndroidModuleError):
    def __init__(self, name: str) -> None:
        Exception.__init__(self, name)
        self.name = name

    def __str__(self) -> str:
        return f"invalid emulator name {self.name!r}"


class InvalidDeviceId(AndroidModuleError):
    def __init__(self, device_id: str) -> None:
        Exception.__init__(self, device_id)
        self.device_id = device_id

    def __str__(self) -> str:
        return f"{self.device_id!r} is not a valid emulator device id"


class InvalidSystemImage(AndroidModuleError):
    def __init__(self, system_image: str, reason: str = "") -> None:
        Exception.__init__(self, system_image, reason)
        self.system_image = system_image
        self.reason = reason

    def __str__(self) -> str:
        suffix = f": {self.reason}" if self.reason else ""
        return f"invalid system image {self.system_image!r}{suffix}"


class UnknownDeviceProfile(AndroidModuleError):
    def __init__(self, device: str) -> None:
        Exception.__init__(self, device)
        self.device = device

    def __str__(self) -> str:
        return f"unknown device profile {self.device!r}"


class SystemImageNotInstalled(AndroidModuleError):
    def __init__(self, system_image: str, expected_path: Path | None = None) -> None:
        Exception.__init__(self, system_image, expected_path)
        self.system_image = system_image
        self.expected_path = expected_path

    def __str__(self) -> str:
        where = f" (expected {self.expected_path})" if self.expected_path else ""
        return f"system image {self.system_image!r} is not installed{where}"


# -- the emulator's life ---------------------------------------------------


class EmulatorNotFound(AndroidModuleError):
    def __init__(self, name: str) -> None:
        Exception.__init__(self, name)
        self.name = name

    def __str__(self) -> str:
        return f"no emulator named {self.name!r}"


class EmulatorAlreadyExists(AndroidModuleError):
    def __init__(self, name: str) -> None:
        Exception.__init__(self, name)
        self.name = name

    def __str__(self) -> str:
        return f"emulator {self.name!r} already exists"


class EmulatorInUse(AndroidModuleError):
    """The emulator is running, so the operation would corrupt it."""

    def __init__(self, name: str, device_id: str | None = None) -> None:
        Exception.__init__(self, name, device_id)
        self.name = name
        self.device_id = device_id

    def __str__(self) -> str:
        running_as = f" as {self.device_id}" if self.device_id else ""
        return f"emulator {self.name!r} is running{running_as}; stop it first"


class EmulatorAlreadyRunning(AndroidModuleError):
    def __init__(self, name: str, device_id: str) -> None:
        Exception.__init__(self, name, device_id)
        self.name = name
        self.device_id = device_id

    def __str__(self) -> str:
        return f"emulator {self.name!r} is already running as {self.device_id}"


class EmulatorStartFailed(AndroidModuleError):
    def __init__(self, name: str, detail: str = "") -> None:
        Exception.__init__(self, name, detail)
        self.name = name
        self.detail = detail

    def __str__(self) -> str:
        suffix = f": {self.detail}" if self.detail else ""
        return f"emulator {self.name!r} failed to start{suffix}"


class EmulatorBootTimeout(AndroidModuleError):
    def __init__(self, name: str, device_id: str | None, timeout_seconds: float) -> None:
        Exception.__init__(self, name, device_id, timeout_seconds)
        self.name = name
        self.device_id = device_id
        self.timeout_seconds = timeout_seconds

    def __str__(self) -> str:
        attached = f" (attached as {self.device_id})" if self.device_id else " (never attached)"
        return f"emulator {self.name!r} did not boot within {self.timeout_seconds}s{attached}"


class EmulatorStopFailed(AndroidModuleError):
    def __init__(self, device_id: str, timeout_seconds: float) -> None:
        Exception.__init__(self, device_id, timeout_seconds)
        self.device_id = device_id
        self.timeout_seconds = timeout_seconds

    def __str__(self) -> str:
        return f"{self.device_id} was still attached {self.timeout_seconds}s after being stopped"


class DeviceNotFound(AndroidModuleError):
    def __init__(self, device_id: str) -> None:
        Exception.__init__(self, device_id)
        self.device_id = device_id

    def __str__(self) -> str:
        return f"device {self.device_id!r} is not attached"
