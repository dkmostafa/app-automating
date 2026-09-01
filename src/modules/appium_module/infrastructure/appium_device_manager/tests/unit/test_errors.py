"""The component's typed failures: each is a domain error, and each round-trips.

The double inheritance is the point of this file. Every class here inherits from
:class:`AppiumComponentError` *and* from one of the domain's errors, which is
what lets the application layer catch a domain type without ever naming an
infrastructure one. It is also what makes ``Exception.__init__`` have to be
called explicitly -- a co-operative ``super().__init__`` would dispatch along
the MRO into the sibling constructor with the wrong arity, and the round-trip
assertion below is what catches that if anyone changes it.

No mocks, per Rule 1 §6. Unit tests because exceptions are pure.
"""

from __future__ import annotations

import copy
import inspect
import pickle

import pytest

from modules.appium_module.domain.errors import (
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
from modules.appium_module.infrastructure.appium_device_manager import errors as errors_module
from modules.appium_module.infrastructure.appium_device_manager.errors import (
    AppiumComponentError,
    AppiumToolNotFoundError,
    CommandFailedError,
    CommandTimeoutError,
    DriverMissingError,
    ElementLookupError,
    ServerNotReadyError,
    SessionDeadError,
    UnknownSessionError,
)
from modules.appium_module.infrastructure.appium_device_manager.models import CommandResult

pytestmark = pytest.mark.unit

RESULT = CommandResult(
    argv=("appium", "driver", "install", "uiautomator2"),
    returncode=1,
    stdout="",
    stderr="npm ERR! network timeout",
    duration_seconds=2.0,
)

COMPONENT_ERRORS = sorted(
    (
        value
        for value in vars(errors_module).values()
        if inspect.isclass(value)
        and issubclass(value, AppiumComponentError)
        and value is not AppiumComponentError
    ),
    key=lambda cls: cls.__name__,
)

SAMPLES: dict[str, BaseException] = {
    "AppiumToolNotFoundError": AppiumToolNotFoundError("appium", "'appium' on PATH"),
    "CommandFailedError": CommandFailedError("appium driver install", RESULT),
    "CommandTimeoutError": CommandTimeoutError("appium -v", ("appium", "-v"), 60.0),
    "DriverMissingError": DriverMissingError("uiautomator2", ()),
    "DriverInstallCommandError": errors_module.DriverInstallCommandError("uiautomator2", RESULT),
    "ServerLaunchError": errors_module.ServerLaunchError("http://x:4723", "died", "tail"),
    "ServerNotReadyError": ServerNotReadyError("http://x:4723", 60.0, "tail"),
    "ServerNotAnsweringError": errors_module.ServerNotAnsweringError("http://x:4723", "off"),
    "InvalidDeviceIdError": errors_module.InvalidDeviceIdError("bad id", "has a space"),
    "InvalidLocatorError": errors_module.InvalidLocatorError("css", "div", ("xpath",)),
    "InvalidCoordinatesError": errors_module.InvalidCoordinatesError(-1, 2, "negative"),
    "InvalidKeyNameError": errors_module.InvalidKeyNameError("wat", ("back",)),
    "InvalidScrollError": errors_module.InvalidScrollError("sideways", "unknown", ("up",)),
    "AppFileNotFoundError": errors_module.AppFileNotFoundError("/tmp/missing.apk"),
    "SessionCreateError": errors_module.SessionCreateError("emulator-5554", "refused"),
    "SessionDeadError": SessionDeadError("s1", "dropped"),
    "UnknownSessionError": UnknownSessionError("s1", ("s2",)),
    "ElementLookupError": ElementLookupError("text", "Submit", 10.0),
    "WebDriverCallError": errors_module.WebDriverCallError("tap", "s1", "not clickable"),
    "ScreenshotCaptureError": errors_module.ScreenshotCaptureError("s1", "empty"),
}

#: Which domain error each component error must also be. This table is the
#: contract Rule 0 §2 describes: the adapter's vocabulary is richer, and the
#: domain's is what the layer above is allowed to catch.
DOMAIN_BASES: dict[str, type[AppiumModuleError]] = {
    "AppiumToolNotFoundError": BackendUnavailable,
    "CommandFailedError": BackendFailure,
    "CommandTimeoutError": BackendFailure,
    "DriverMissingError": DriverNotInstalled,
    "DriverInstallCommandError": DriverInstallFailed,
    "ServerLaunchError": ServerStartFailed,
    "ServerNotReadyError": ServerStartFailed,
    "ServerNotAnsweringError": ServerUnreachable,
    "InvalidDeviceIdError": InvalidDeviceId,
    "InvalidLocatorError": InvalidLocator,
    "InvalidCoordinatesError": InvalidCoordinates,
    "InvalidKeyNameError": InvalidKeyName,
    "InvalidScrollError": InvalidScroll,
    "AppFileNotFoundError": AppNotFound,
    "SessionCreateError": SessionStartFailed,
    "SessionDeadError": SessionExpired,
    "UnknownSessionError": SessionNotFound,
    "ElementLookupError": ElementNotFound,
    "WebDriverCallError": InteractionFailed,
    "ScreenshotCaptureError": ScreenshotFailed,
}


def test_every_component_error_has_a_sample_and_a_declared_domain_base() -> None:
    """Guard the guard: a new error must be added here or these checks skip it."""
    names = {cls.__name__ for cls in COMPONENT_ERRORS}
    assert names - set(SAMPLES) == set(), f"add samples for {sorted(names - set(SAMPLES))}"
    undeclared = sorted(names - set(DOMAIN_BASES))
    assert not undeclared, f"declare domain bases for {undeclared}"


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_every_component_error_is_also_a_domain_error(name: str) -> None:
    """Rule 0 §2: no translation step in the middle -- the object *is* both."""
    exc = SAMPLES[name]

    assert isinstance(exc, AppiumComponentError)
    assert isinstance(exc, AppiumModuleError)
    assert isinstance(exc, DOMAIN_BASES[name]), f"{name} must also be {DOMAIN_BASES[name].__name__}"


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_component_errors_round_trip_through_their_own_arguments(name: str) -> None:
    """Rule 0 §3 (L). This is the check that catches a `super().__init__` creeping
    back in: with two bases, the co-operative call reaches the sibling with the
    wrong arity and the rebuild fails here rather than in production."""
    exc = SAMPLES[name]

    rebuilt = type(exc)(*exc.args)

    assert type(rebuilt) is type(exc)
    assert str(rebuilt) == str(exc)


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_component_errors_survive_pickling_and_copying(name: str) -> None:
    exc = SAMPLES[name]

    assert str(pickle.loads(pickle.dumps(exc))) == str(exc)
    assert str(copy.copy(exc)) == str(exc)


# -- the evidence these carry ---------------------------------------------


def test_a_missing_tool_says_where_it_looked() -> None:
    """ "appium not found" is useless; "looked for 'appium' on PATH" is actionable."""
    exc = AppiumToolNotFoundError("appium", "'appium' on PATH")

    assert exc.tool == "appium"
    assert exc.searched == "'appium' on PATH"
    assert "on PATH" in str(exc)


def test_a_failed_command_carries_the_whole_result_not_just_a_string() -> None:
    """Rule 1 §3: evidence as attributes, so a caller can act without parsing."""
    exc = CommandFailedError("appium driver install", RESULT)

    assert exc.result.returncode == 1
    assert exc.result.argv == RESULT.argv
    assert "npm ERR! network timeout" in str(exc)
    assert "exit 1" in str(exc)


def test_a_timeout_names_the_command_and_the_deadline_it_missed() -> None:
    exc = CommandTimeoutError("appium -v", ("appium", "-v"), 60.0)

    assert exc.timeout_seconds == 60.0
    assert "appium -v" in str(exc)


def test_a_server_that_never_answered_carries_its_log_tail() -> None:
    """The log is the only evidence of why: the process is gone by then."""
    exc = ServerNotReadyError("http://127.0.0.1:4723", 60.0, "EADDRINUSE :::4723")

    assert exc.log_tail == "EADDRINUSE :::4723"
    assert "EADDRINUSE" in str(exc)


def test_an_unknown_session_lists_the_open_ones() -> None:
    exc = UnknownSessionError("gone", ("live-1",))

    assert exc.known == ("live-1",)
    assert "live-1" in str(exc)


def test_a_dead_session_is_an_expired_one_not_an_unknown_one() -> None:
    """The remedies differ, so the types must not be interchangeable."""
    assert isinstance(SessionDeadError("s1"), SessionExpired)
    assert not isinstance(SessionDeadError("s1"), SessionNotFound)
