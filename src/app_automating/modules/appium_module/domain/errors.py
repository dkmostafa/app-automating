"""The failure vocabulary of the Appium module.

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

The failures divide into four groups, and the group is what tells a caller
whether to retry: the host is not provisioned, the server is not answering, the
session is gone, or the input was wrong. Only the third is worth retrying after
a corrective call, and only the fourth is worth retrying with different
arguments.
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "AppiumModuleError",
    # the host and its toolchain
    "BackendUnavailable",
    "BackendFailure",
    "DriverNotInstalled",
    "DriverInstallFailed",
    # the server process
    "ServerStartFailed",
    "ServerUnreachable",
    # rejected input
    "InvalidDeviceId",
    "InvalidLocator",
    "InvalidCoordinates",
    "InvalidKeyName",
    "InvalidScroll",
    "AppNotFound",
    # sessions
    "SessionNotFound",
    "SessionStartFailed",
    "SessionExpired",
    "DeviceNotFound",
    # driving the screen
    "ElementNotFound",
    "InteractionFailed",
    "ScreenshotFailed",
]


class AppiumModuleError(Exception):
    """Base class for every failure the Appium module reports."""


# -- the host and its toolchain --------------------------------------------


class BackendUnavailable(AppiumModuleError):
    """The automation backend cannot be reached at all: no Appium, no Node.

    Distinct from :class:`BackendFailure` on purpose -- this one is a
    provisioning problem on the host and no retry will fix it.
    """

    def __init__(self, what: str, detail: str = "") -> None:
        Exception.__init__(self, what, detail)
        self.what = what
        self.detail = detail

    def __str__(self) -> str:
        suffix = f": {self.detail}" if self.detail else ""
        return f"Appium backend unavailable -- {self.what}{suffix}"


class BackendFailure(AppiumModuleError):
    """The backend was reached and refused, in a way the domain cannot name."""

    def __init__(self, operation: str, detail: str = "") -> None:
        Exception.__init__(self, operation, detail)
        self.operation = operation
        self.detail = detail

    def __str__(self) -> str:
        suffix = f": {self.detail}" if self.detail else ""
        return f"{self.operation} failed{suffix}"


class DriverNotInstalled(AppiumModuleError):
    """The Appium driver a session needs is not installed on this host."""

    def __init__(self, driver_name: str, installed: tuple[str, ...] = ()) -> None:
        Exception.__init__(self, driver_name, installed)
        self.driver_name = driver_name
        self.installed = installed

    def __str__(self) -> str:
        have = ", ".join(self.installed) if self.installed else "none"
        return f"Appium driver {self.driver_name!r} is not installed (installed: {have})"


class DriverInstallFailed(AppiumModuleError):
    def __init__(self, driver_name: str, detail: str = "") -> None:
        Exception.__init__(self, driver_name, detail)
        self.driver_name = driver_name
        self.detail = detail

    def __str__(self) -> str:
        suffix = f": {self.detail}" if self.detail else ""
        return f"installing Appium driver {self.driver_name!r} failed{suffix}"


# -- the server process ----------------------------------------------------


class ServerStartFailed(AppiumModuleError):
    """The module tried to launch an Appium server and it did not come up."""

    def __init__(self, url: str, detail: str = "", log_tail: str = "") -> None:
        Exception.__init__(self, url, detail, log_tail)
        self.url = url
        self.detail = detail
        self.log_tail = log_tail

    def __str__(self) -> str:
        suffix = f": {self.detail}" if self.detail else ""
        tail = f"\n{self.log_tail}" if self.log_tail else ""
        return f"the Appium server at {self.url} failed to start{suffix}{tail}"


class ServerUnreachable(AppiumModuleError):
    """A server was configured but nothing is answering at that address."""

    def __init__(self, url: str, detail: str = "") -> None:
        Exception.__init__(self, url, detail)
        self.url = url
        self.detail = detail

    def __str__(self) -> str:
        suffix = f": {self.detail}" if self.detail else ""
        return f"no Appium server is answering at {self.url}{suffix}"


# -- rejected input, before anything was attempted -------------------------


class InvalidDeviceId(AppiumModuleError):
    def __init__(self, device_id: str, reason: str = "") -> None:
        Exception.__init__(self, device_id, reason)
        self.device_id = device_id
        self.reason = reason

    def __str__(self) -> str:
        suffix = f": {self.reason}" if self.reason else ""
        return f"{self.device_id!r} is not a valid device id{suffix}"


class InvalidLocator(AppiumModuleError):
    """The strategy is not one this module knows, or the selector is empty."""

    def __init__(self, strategy: str, selector: str, supported: tuple[str, ...] = ()) -> None:
        Exception.__init__(self, strategy, selector, supported)
        self.strategy = strategy
        self.selector = selector
        self.supported = supported

    def __str__(self) -> str:
        known = f" (supported: {', '.join(self.supported)})" if self.supported else ""
        return f"invalid locator {self.strategy!r}={self.selector!r}{known}"


class InvalidCoordinates(AppiumModuleError):
    def __init__(self, x: int, y: int, reason: str = "") -> None:
        Exception.__init__(self, x, y, reason)
        self.x = x
        self.y = y
        self.reason = reason

    def __str__(self) -> str:
        suffix = f": {self.reason}" if self.reason else ""
        return f"invalid coordinates ({self.x}, {self.y}){suffix}"


class InvalidKeyName(AppiumModuleError):
    def __init__(self, key: str, supported: tuple[str, ...] = ()) -> None:
        Exception.__init__(self, key, supported)
        self.key = key
        self.supported = supported

    def __str__(self) -> str:
        known = f"; known keys: {', '.join(self.supported)}" if self.supported else ""
        return f"unknown key {self.key!r}{known}"


class InvalidScroll(AppiumModuleError):
    """A scroll request that cannot be carried out as written.

    Covers both halves of the request -- an unknown direction and a distance
    outside ``0 < d <= 1`` -- because they are the same mistake from the
    caller's side and carry the same remedy: fix the argument and call again.
    """

    def __init__(self, direction: str, reason: str = "", supported: tuple[str, ...] = ()) -> None:
        Exception.__init__(self, direction, reason, supported)
        self.direction = direction
        self.reason = reason
        self.supported = supported

    def __str__(self) -> str:
        known = f" (supported: {', '.join(self.supported)})" if self.supported else ""
        detail = f": {self.reason}" if self.reason else ""
        return f"invalid scroll {self.direction!r}{detail}{known}"


class AppNotFound(AppiumModuleError):
    """The APK a session was asked to install is not on disk."""

    def __init__(self, app_path: Path | str) -> None:
        Exception.__init__(self, app_path)
        self.app_path = app_path

    def __str__(self) -> str:
        return f"no application file at {self.app_path}"


# -- the session's life ----------------------------------------------------


class SessionNotFound(AppiumModuleError):
    """No session by that id was started by this process."""

    def __init__(self, session_id: str, known: tuple[str, ...] = ()) -> None:
        Exception.__init__(self, session_id, known)
        self.session_id = session_id
        self.known = known

    def __str__(self) -> str:
        have = ", ".join(self.known) if self.known else "none"
        return f"no session {self.session_id!r} (open sessions: {have})"


class SessionStartFailed(AppiumModuleError):
    def __init__(self, device_id: str, detail: str = "") -> None:
        Exception.__init__(self, device_id, detail)
        self.device_id = device_id
        self.detail = detail

    def __str__(self) -> str:
        suffix = f": {self.detail}" if self.detail else ""
        return f"could not start a session on {self.device_id!r}{suffix}"


class SessionExpired(AppiumModuleError):
    """The session existed but the server has since dropped it.

    Separate from :class:`SessionNotFound` because the remedy differs: this one
    means "start a new session", not "check the id you passed".
    """

    def __init__(self, session_id: str, detail: str = "") -> None:
        Exception.__init__(self, session_id, detail)
        self.session_id = session_id
        self.detail = detail

    def __str__(self) -> str:
        suffix = f": {self.detail}" if self.detail else ""
        return f"session {self.session_id!r} is no longer alive{suffix}"


class DeviceNotFound(AppiumModuleError):
    def __init__(self, device_id: str) -> None:
        Exception.__init__(self, device_id)
        self.device_id = device_id

    def __str__(self) -> str:
        return f"device {self.device_id!r} is not attached"


# -- driving the screen ----------------------------------------------------


class ElementNotFound(AppiumModuleError):
    def __init__(self, strategy: str, selector: str, timeout_seconds: float = 0.0) -> None:
        Exception.__init__(self, strategy, selector, timeout_seconds)
        self.strategy = strategy
        self.selector = selector
        self.timeout_seconds = timeout_seconds

    def __str__(self) -> str:
        waited = f" after {self.timeout_seconds}s" if self.timeout_seconds else ""
        return f"no element matching {self.strategy}={self.selector!r}{waited}"


class InteractionFailed(AppiumModuleError):
    """The gesture reached the device and the device refused it."""

    def __init__(self, action: str, session_id: str, detail: str = "") -> None:
        Exception.__init__(self, action, session_id, detail)
        self.action = action
        self.session_id = session_id
        self.detail = detail

    def __str__(self) -> str:
        suffix = f": {self.detail}" if self.detail else ""
        return f"{self.action} failed on session {self.session_id!r}{suffix}"


class ScreenshotFailed(AppiumModuleError):
    def __init__(self, session_id: str, detail: str = "") -> None:
        Exception.__init__(self, session_id, detail)
        self.session_id = session_id
        self.detail = detail

    def __str__(self) -> str:
        suffix = f": {self.detail}" if self.detail else ""
        return f"could not capture a screenshot of session {self.session_id!r}{suffix}"
