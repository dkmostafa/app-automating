"""The Android module's domain: entities, boundary DTOs and ports.

Imports nothing from ``infrastructure``, ``application`` or ``presentation``, and
nothing from a third-party I/O library. It is importable on a host with no
Android SDK, which the architecture test asserts.
"""

from .errors import (
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
from .models import (
    AndroidDevice,
    AvdInfo,
    CreateEmulatorRequest,
    CreateEmulatorResult,
    DeleteEmulatorRequest,
    DeleteEmulatorResult,
    InstallSystemImageRequest,
    InstallSystemImageResult,
    ListDevicesRequest,
    ListDevicesResult,
    ListEmulatorsRequest,
    ListEmulatorsResult,
    RenameEmulatorRequest,
    RenameEmulatorResult,
    StartEmulatorRequest,
    StartEmulatorResult,
    StopEmulatorRequest,
    StopEmulatorResult,
)
from .ports import DeviceCatalog, EmulatorLifecycle, SystemImageInstaller

__all__ = [
    # ports
    "DeviceCatalog",
    "EmulatorLifecycle",
    "SystemImageInstaller",
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
    # errors
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
