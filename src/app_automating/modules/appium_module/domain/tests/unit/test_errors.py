"""The domain's failure vocabulary: reconstructable, evidence-carrying, layered.

Two properties matter enough to be asserted mechanically over every class.

* **Round-tripping** (Rule 0 §3, LSP): ``type(exc)(*exc.args)`` must rebuild the
  exception. An error that stores evidence on ``self`` but hands only a
  formatted string to ``super().__init__`` is not copyable, not picklable, and
  will not survive crossing a process boundary.
* **Evidence on the attributes** (Rule 1 §3): a caller branches on
  ``exc.session_id``, never on the text of the message. The message is for a
  human; the attributes are the API.
"""

from __future__ import annotations

import copy
import inspect
import pickle
from pathlib import Path

import pytest

from app_automating.modules.appium_module.domain import errors as errors_module
from app_automating.modules.appium_module.domain.errors import (
    AppiumModuleError,
    BackendUnavailable,
    DriverNotInstalled,
    ElementNotFound,
    InvalidKeyName,
    InvalidLocator,
    InvalidScroll,
    SessionExpired,
    SessionNotFound,
)

pytestmark = pytest.mark.unit

#: Every error class the module publishes, discovered rather than listed: a new
#: error is covered the moment it is exported, and cannot be forgotten here.
ERROR_TYPES = sorted(
    (
        value
        for value in vars(errors_module).values()
        if inspect.isclass(value) and issubclass(value, AppiumModuleError)
    ),
    key=lambda cls: cls.__name__,
)

#: One representative instance per class, so the mechanical checks below have
#: something real to work on.
SAMPLES: dict[str, BaseException] = {
    "AppiumModuleError": AppiumModuleError("something"),
    "BackendUnavailable": BackendUnavailable("appium", "not on PATH"),
    "BackendFailure": errors_module.BackendFailure("driver list", "exit 1"),
    "DriverNotInstalled": DriverNotInstalled("uiautomator2", ("xcuitest",)),
    "DriverInstallFailed": errors_module.DriverInstallFailed("uiautomator2", "no network"),
    "ServerStartFailed": errors_module.ServerStartFailed("http://x:4723", "died", "log"),
    "ServerUnreachable": errors_module.ServerUnreachable("http://x:4723", "refused"),
    "InvalidDeviceId": errors_module.InvalidDeviceId("bad id", "has a space"),
    "InvalidLocator": InvalidLocator("css", "div", ("xpath",)),
    "InvalidCoordinates": errors_module.InvalidCoordinates(-1, 5, "negative"),
    "InvalidKeyName": InvalidKeyName("wat", ("back", "home")),
    "InvalidScroll": InvalidScroll("sideways", "unknown direction", ("up", "down")),
    "AppNotFound": errors_module.AppNotFound(Path("/tmp/missing.apk")),
    "SessionNotFound": SessionNotFound("s1", ("s2", "s3")),
    "SessionStartFailed": errors_module.SessionStartFailed("emulator-5554", "refused"),
    "SessionExpired": SessionExpired("s1", "dropped"),
    "DeviceNotFound": errors_module.DeviceNotFound("emulator-9999"),
    "ElementNotFound": ElementNotFound("text", "Submit", 10.0),
    "InteractionFailed": errors_module.InteractionFailed("tap", "s1", "not clickable"),
    "ScreenshotFailed": errors_module.ScreenshotFailed("s1", "empty image"),
}


def test_every_error_class_has_a_sample() -> None:
    """Guard the guard: the checks below only cover what SAMPLES names."""
    missing = {cls.__name__ for cls in ERROR_TYPES} - set(SAMPLES)
    assert not missing, f"add a sample for: {sorted(missing)}"


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_errors_round_trip_through_their_own_arguments(name: str) -> None:
    """Rule 0 §3 (L): `type(exc)(*exc.args)` must reconstruct the exception."""
    exc = SAMPLES[name]

    rebuilt = type(exc)(*exc.args)

    assert type(rebuilt) is type(exc)
    assert rebuilt.args == exc.args
    assert str(rebuilt) == str(exc)


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_errors_survive_pickling_and_copying(name: str) -> None:
    """The practical consequence of round-tripping: these cross process
    boundaries and get copied by test frameworks and task runners."""
    exc = SAMPLES[name]

    assert str(pickle.loads(pickle.dumps(exc))) == str(exc)
    assert str(copy.copy(exc)) == str(exc)


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_every_error_is_an_appium_module_error(name: str) -> None:
    """The application layer catches one base class. Anything outside it escapes."""
    assert isinstance(SAMPLES[name], AppiumModuleError)


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_no_message_is_empty_or_a_bare_repr(name: str) -> None:
    exc = SAMPLES[name]

    rendered = str(exc)

    assert rendered.strip()
    assert not rendered.startswith("(")
    assert "object at 0x" not in rendered


# -- the evidence a caller actually branches on ----------------------------


def test_a_missing_session_names_the_ones_that_do_exist() -> None:
    """So the caller can pick a real id instead of guessing a second wrong one."""
    exc = SessionNotFound("gone", ("live-1", "live-2"))

    assert exc.session_id == "gone"
    assert exc.known == ("live-1", "live-2")
    assert "live-1" in str(exc)


def test_an_expired_session_is_not_a_missing_one() -> None:
    """Different remedies -- "start a new session" versus "check the id you
    passed" -- so they must not be the same type."""
    assert not isinstance(SessionExpired("s1"), SessionNotFound)
    assert not isinstance(SessionNotFound("s1"), SessionExpired)


def test_a_missing_element_carries_the_locator_and_the_wait() -> None:
    exc = ElementNotFound("accessibility_id", "Wi-Fi", 10.0)

    assert (exc.strategy, exc.selector, exc.timeout_seconds) == ("accessibility_id", "Wi-Fi", 10.0)
    assert "10.0" in str(exc)


def test_an_invalid_locator_lists_what_would_have_worked() -> None:
    exc = InvalidLocator("css_selector", "#ok", ("accessibility_id", "id", "xpath"))

    assert "accessibility_id" in str(exc)


def test_an_unknown_key_lists_the_known_ones() -> None:
    """The remedy has to be in the error: the caller cannot see the keycode table."""
    exc = InvalidKeyName("goback", ("back", "home", "enter"))

    assert exc.key == "goback"
    assert "back" in str(exc)


def test_an_invalid_scroll_covers_both_direction_and_distance() -> None:
    """One class, two mistakes, because the remedy is the same: fix the argument."""
    direction = InvalidScroll("sideways", "unknown direction", ("up", "down"))
    distance = InvalidScroll("down", "distance 0.0 must be greater than 0", ("up", "down"))

    assert "unknown direction" in str(direction)
    assert "must be greater than 0" in str(distance)


def test_a_missing_driver_says_what_is_installed_instead() -> None:
    exc = DriverNotInstalled("uiautomator2", ())

    assert exc.driver_name == "uiautomator2"
    assert "none" in str(exc)


def test_an_unavailable_backend_is_not_a_backend_failure() -> None:
    """One is a provisioning problem no retry fixes; the other is a refused
    operation. Collapsing them would lose the only useful distinction."""
    assert not isinstance(BackendUnavailable("appium"), errors_module.BackendFailure)


def test_errors_are_exported_so_presentation_can_name_them() -> None:
    """The remedy table imports these by name; an unexported error has no remedy."""
    exported = set(errors_module.__all__)
    defined = {cls.__name__ for cls in ERROR_TYPES}
    assert defined == exported
