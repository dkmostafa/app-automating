"""The component's typed failures. Every one of them is a domain error too.

Rule 1 §3: no foreign exception escapes this package. A ``FileNotFoundError``
from a missing binary, an ``asyncio.TimeoutError``, a WebDriver exception from
the client library, a non-zero exit code -- all of it is translated here and
re-raised ``from`` the original.

Each class inherits from two bases: :class:`AppiumComponentError`, so a caller
inside this package can catch everything the component raises, and one of the
domain's errors (Rule 0 §2), so the application layer never has to name an
infrastructure type in an ``except`` clause. That is the whole trick -- there is
no translation step in the middle waiting to be forgotten, because the object
already *is* both things.

``Exception.__init__`` is called explicitly rather than through ``super()``:
with two bases, a co-operative ``super().__init__`` would dispatch along the MRO
into the sibling's constructor with the wrong arity.
"""

from __future__ import annotations

from ...domain.errors import (
    AppiumModuleError,
    AppNotFound,
    BackendFailure,
    BackendUnavailable,
    DriverInstallFailed,
    DriverNotInstalled,
    ElementNotFound,
    InteractionFailed,
    InvalidCoordinates,
    InvalidDeviceId,
    InvalidKeyName,
    InvalidLocator,
    InvalidScroll,
    ScreenshotFailed,
    ServerStartFailed,
    ServerUnreachable,
    SessionExpired,
    SessionNotFound,
    SessionStartFailed,
)
from .models import CommandResult, display_command

__all__ = [
    "AppiumComponentError",
    # host tools
    "AppiumToolNotFoundError",
    "CommandFailedError",
    "CommandTimeoutError",
    "DriverMissingError",
    "DriverInstallCommandError",
    # the server
    "ServerLaunchError",
    "ServerNotReadyError",
    "ServerNotAnsweringError",
    # rejected input
    "InvalidDeviceIdError",
    "InvalidLocatorError",
    "InvalidCoordinatesError",
    "InvalidKeyNameError",
    "InvalidScrollError",
    "AppFileNotFoundError",
    # sessions
    "SessionCreateError",
    "SessionDeadError",
    "UnknownSessionError",
    # driving the screen
    "ElementLookupError",
    "WebDriverCallError",
    "ScreenshotCaptureError",
]


class AppiumComponentError(AppiumModuleError):
    """Base class for every failure raised inside this component.

    Also an :class:`~....domain.errors.AppiumModuleError`, so catching the
    domain's base catches these without naming them.
    """


class AppiumToolNotFoundError(AppiumComponentError, BackendUnavailable):
    """A host binary this component needs is not where the config points."""

    def __init__(self, tool: str, searched: str) -> None:
        Exception.__init__(self, tool, searched)
        self.tool = tool
        self.searched = searched
        self.what = tool
        self.detail = f"looked for {searched}"

    def __str__(self) -> str:
        return f"{self.tool!r} was not found (looked for {self.searched})"


class CommandFailedError(AppiumComponentError, BackendFailure):
    """A child process exited non-zero in a way no rule classified.

    The honest fallback, and deliberately not the default: a failure that can be
    named should be named (Rule 1 §3).
    """

    def __init__(self, operation: str, result: CommandResult) -> None:
        if not isinstance(result, CommandResult):
            raise TypeError(f"result must be CommandResult, got {type(result).__name__!r}")
        Exception.__init__(self, operation, result)
        self.operation = operation
        self.result = result

    def __str__(self) -> str:
        return (
            f"{self.operation} failed (exit {self.result.returncode}): "
            f"{self.result.command}\n{self.result.tail}"
        )


class CommandTimeoutError(AppiumComponentError, BackendFailure):
    """A child process outlived its configured timeout and was killed."""

    def __init__(self, operation: str, argv: tuple[str, ...], timeout_seconds: float) -> None:
        Exception.__init__(self, operation, argv, timeout_seconds)
        self.operation = operation
        self.argv = argv
        self.timeout_seconds = timeout_seconds
        self.detail = f"timed out after {timeout_seconds}s"

    def __str__(self) -> str:
        return (
            f"{self.operation} did not finish within {self.timeout_seconds}s: "
            f"{display_command(self.argv)}"
        )


class ServerLaunchError(AppiumComponentError, ServerStartFailed):
    """The Appium server process died, or never answered, after being launched."""

    def __init__(self, url: str, detail: str, log_tail: str = "") -> None:
        Exception.__init__(self, url, detail, log_tail)
        ServerStartFailed.__init__(self, url, detail, log_tail)


class ServerNotReadyError(AppiumComponentError, ServerStartFailed):
    """A launched server was still not answering ``/status`` when time ran out."""

    def __init__(self, url: str, timeout_seconds: float, log_tail: str = "") -> None:
        Exception.__init__(self, url, timeout_seconds, log_tail)
        self.url = url
        self.timeout_seconds = timeout_seconds
        self.log_tail = log_tail
        self.detail = f"still not answering after {timeout_seconds}s"


class DriverInstallCommandError(AppiumComponentError, DriverInstallFailed):
    """``appium driver install`` exited non-zero."""

    def __init__(self, driver_name: str, result: CommandResult) -> None:
        if not isinstance(result, CommandResult):
            raise TypeError(f"result must be CommandResult, got {type(result).__name__!r}")
        Exception.__init__(self, driver_name, result)
        self.driver_name = driver_name
        self.result = result

    @property
    def detail(self) -> str:
        return self.result.tail


class SessionCreateError(AppiumComponentError, SessionStartFailed):
    """The server refused to create a session on this device."""

    def __init__(self, device_id: str, detail: str) -> None:
        Exception.__init__(self, device_id, detail)
        SessionStartFailed.__init__(self, device_id, detail)


class SessionDeadError(AppiumComponentError, SessionExpired):
    """A session this process is holding has been dropped by the server."""

    def __init__(self, session_id: str, detail: str = "") -> None:
        Exception.__init__(self, session_id, detail)
        SessionExpired.__init__(self, session_id, detail)


class ElementLookupError(AppiumComponentError, ElementNotFound):
    """The locator matched nothing before the wait ran out."""

    def __init__(self, strategy: str, selector: str, timeout_seconds: float) -> None:
        Exception.__init__(self, strategy, selector, timeout_seconds)
        ElementNotFound.__init__(self, strategy, selector, timeout_seconds)


class WebDriverCallError(AppiumComponentError, InteractionFailed):
    """The device was reached and the command failed there."""

    def __init__(self, action: str, session_id: str, detail: str) -> None:
        Exception.__init__(self, action, session_id, detail)
        InteractionFailed.__init__(self, action, session_id, detail)


class DriverMissingError(AppiumComponentError, DriverNotInstalled):
    """A session was asked for before its driver was installed."""

    def __init__(self, driver_name: str, installed: tuple[str, ...] = ()) -> None:
        Exception.__init__(self, driver_name, installed)
        DriverNotInstalled.__init__(self, driver_name, installed)


class ServerNotAnsweringError(AppiumComponentError, ServerUnreachable):
    """``manage_server`` is off and nothing is listening at the configured URL."""

    def __init__(self, url: str, detail: str = "") -> None:
        Exception.__init__(self, url, detail)
        ServerUnreachable.__init__(self, url, detail)


# -- rejected input, before anything is spawned or sent --------------------
#
# Rule 1 §3: cheap checks that need no subprocess and no round trip belong
# before the subprocess and the round trip. Every one of these is raised by
# :mod:`.parsing`, which is pure, and so runs before the device is touched.


class InvalidDeviceIdError(AppiumComponentError, InvalidDeviceId):
    """A device id that adb could not be holding."""

    def __init__(self, device_id: str, reason: str = "") -> None:
        Exception.__init__(self, device_id, reason)
        InvalidDeviceId.__init__(self, device_id, reason)


class InvalidLocatorError(AppiumComponentError, InvalidLocator):
    def __init__(self, strategy: str, selector: str, supported: tuple[str, ...] = ()) -> None:
        Exception.__init__(self, strategy, selector, supported)
        InvalidLocator.__init__(self, strategy, selector, supported)


class InvalidCoordinatesError(AppiumComponentError, InvalidCoordinates):
    def __init__(self, x: int, y: int, reason: str = "") -> None:
        Exception.__init__(self, x, y, reason)
        InvalidCoordinates.__init__(self, x, y, reason)


class InvalidKeyNameError(AppiumComponentError, InvalidKeyName):
    def __init__(self, key: str, supported: tuple[str, ...] = ()) -> None:
        Exception.__init__(self, key, supported)
        InvalidKeyName.__init__(self, key, supported)


class InvalidScrollError(AppiumComponentError, InvalidScroll):
    def __init__(self, direction: str, reason: str = "", supported: tuple[str, ...] = ()) -> None:
        Exception.__init__(self, direction, reason, supported)
        InvalidScroll.__init__(self, direction, reason, supported)


class AppFileNotFoundError(AppiumComponentError, AppNotFound):
    """The APK named in a session request is not on this host."""

    def __init__(self, app_path: object) -> None:
        Exception.__init__(self, app_path)
        AppNotFound.__init__(self, app_path)  # type: ignore[arg-type]


class UnknownSessionError(AppiumComponentError, SessionNotFound):
    """No session by that id was ever started by this process."""

    def __init__(self, session_id: str, known: tuple[str, ...] = ()) -> None:
        Exception.__init__(self, session_id, known)
        SessionNotFound.__init__(self, session_id, known)


class ScreenshotCaptureError(AppiumComponentError, ScreenshotFailed):
    def __init__(self, session_id: str, detail: str = "") -> None:
        Exception.__init__(self, session_id, detail)
        ScreenshotFailed.__init__(self, session_id, detail)
