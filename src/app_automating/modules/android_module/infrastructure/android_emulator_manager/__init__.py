"""Android emulator control: the infrastructure adapter for the local SDK.

Split by kind -- :mod:`config`, :mod:`models`, :mod:`errors`, :mod:`parsing`,
:mod:`process`, :mod:`filesystem`, :mod:`manager` -- and re-exported here so
callers import from the component, not from its internals::

    from app_automating.modules.android_module.infrastructure.android_emulator_manager import (
        AndroidEmulatorManager,
        AndroidSdkConfig,
    )

What this package exports is the *adapter's* surface: its configuration, its
collaborators, its typed failures, and the manager. The request and result
dataclasses are deliberately **not** here -- they cross the port boundary, so
Rule 0 §2 puts them in ``android_module.domain`` and a caller imports them from
there. That split is the whole point: it is what stops the application layer
from depending on this package.

Dependency order inside the package, no cycles:
``models -> errors -> parsing`` and ``config -> process``, with ``filesystem``
beside ``process`` and ``manager`` on top.
"""

from .config import (
    DEFAULT_HEADLESS_ARGS,
    DISPLAY_VARS,
    AndroidSdkConfig,
    avd_home_from_env,
    child_env_from_env,
    display_env_from_env,
    sdk_root_from_env,
)
from .errors import (
    AndroidEmulatorError,
    AndroidToolNotFoundError,
    AvdAlreadyExistsError,
    AvdInUseError,
    AvdNotFoundError,
    CommandError,
    CommandFailedError,
    CommandTimeoutError,
    DeviceNotFoundError,
    EmulatorAlreadyRunningError,
    EmulatorBootTimeoutError,
    EmulatorStartError,
    EmulatorStopTimeoutError,
    InvalidAvdNameError,
    InvalidSystemImageError,
    NotAnEmulatorError,
    SdkRootNotConfiguredError,
    SystemImageNotInstalledError,
    UnknownDeviceProfileError,
)
from .filesystem import AvdStore
from .manager import AndroidEmulatorManager
from .models import CommandResult
from .process import CommandRunner

__all__ = [
    # configuration
    "AndroidSdkConfig",
    "DEFAULT_HEADLESS_ARGS",
    "DISPLAY_VARS",
    "avd_home_from_env",
    "child_env_from_env",
    "display_env_from_env",
    "sdk_root_from_env",
    # collaborators
    "CommandRunner",
    "AvdStore",
    # the adapter's own value objects
    "CommandResult",
    # errors
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
    # manager
    "AndroidEmulatorManager",
]
