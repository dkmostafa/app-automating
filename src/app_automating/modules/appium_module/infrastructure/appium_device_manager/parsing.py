"""Pure text and input handling: caller input and tool output become models or
errors, and nothing here spawns, waits or touches a disk.

That purity is the point rather than a nicety. Every check in this file runs
*before* a subprocess is started or an HTTP round trip is made, so a malformed
locator costs microseconds instead of a session round trip, and the unit tests
for all of it run on a host with no Appium at all (Rule 1 §3, Rule 2 §2).

The classification tables at the bottom are Rule 0 §3's OCP in the form this
codebase uses: a newly recognised failure adds a row, and edits no function.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from pathlib import Path

from appium.webdriver.common.appiumby import AppiumBy

from ...domain.errors import AppiumModuleError
from ...domain.models import LOCATOR_STRATEGIES, SCROLL_DIRECTIONS
from .errors import (
    AppFileNotFoundError,
    CommandFailedError,
    InvalidCoordinatesError,
    InvalidDeviceIdError,
    InvalidKeyNameError,
    InvalidLocatorError,
    InvalidScrollError,
    SessionCreateError,
    SessionDeadError,
    WebDriverCallError,
)
from .models import CommandResult

__all__ = [
    "ANDROID_KEYCODES",
    "LOCATOR_BY",
    "SESSION_FAILURES",
    "INTERACTION_FAILURES",
    "validate_device_id",
    "validate_locator",
    "validate_coordinates",
    "validate_key",
    "validate_scroll",
    "validate_app_path",
    "resolve_locator",
    "parse_version_output",
    "parse_installed_drivers",
    "parse_server_status",
    "truncate",
    "png_dimensions",
    "classify_session_failure",
    "classify_interaction_failure",
]

#: An adb serial: an emulator's ``emulator-<port>`` or a hardware serial. The
#: alphabet is deliberately narrow -- a serial goes onto a command line and into
#: a URL, and a space or a quote in one is a bug rather than a device.
DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]+$")

#: Appium prints its version and nothing else for ``-v``, but a wrapper script
#: or an nvm shim can prepend a line, so the version is matched rather than
#: assumed to be the whole output.
VERSION_RE = re.compile(r"(\d+\.\d+\.\d+(?:-[A-Za-z0-9.]+)?)")


# --------------------------------------------------------------------------
# the vocabularies a caller may use
# --------------------------------------------------------------------------

#: Friendly key name -> Android ``KeyEvent`` keycode.
#:
#: Names rather than numbers, because the client is a model writing a tool call
#: and ``press_key("back")`` is checkable where ``press_keycode(4)`` is a magic
#: number nobody can review. Unknown names are rejected in-process.
ANDROID_KEYCODES: dict[str, int] = {
    "home": 3,
    "back": 4,
    "call": 5,
    "endcall": 6,
    "dpad_up": 19,
    "dpad_down": 20,
    "dpad_left": 21,
    "dpad_right": 22,
    "dpad_center": 23,
    "volume_up": 24,
    "volume_down": 25,
    "power": 26,
    "camera": 27,
    "clear": 28,
    "tab": 61,
    "space": 62,
    "enter": 66,
    "delete": 67,
    "backspace": 67,
    "menu": 82,
    "notification": 83,
    "search": 84,
    "media_play_pause": 85,
    "media_stop": 86,
    "media_next": 87,
    "media_previous": 88,
    "page_up": 92,
    "page_down": 93,
    "escape": 111,
    "forward_delete": 112,
    "move_home": 122,
    "move_end": 123,
    "app_switch": 187,
    "brightness_down": 220,
    "brightness_up": 221,
}

#: This module's strategy names -> the locator strategy the client speaks.
#:
#: ``text`` and ``id`` are the two that are not a straight rename: Android has
#: no "find by visible text" strategy, so it is expressed as a UiSelector, and
#: ``id`` means a resource id, which the client spells ``id`` but which callers
#: routinely write in full (``com.app:id/button``) -- both forms work.
LOCATOR_BY: dict[str, str] = {
    "accessibility_id": AppiumBy.ACCESSIBILITY_ID,
    "id": AppiumBy.ID,
    "class_name": AppiumBy.CLASS_NAME,
    "xpath": AppiumBy.XPATH,
    "uiautomator": AppiumBy.ANDROID_UIAUTOMATOR,
    "text": AppiumBy.ANDROID_UIAUTOMATOR,
}


def _ui_selector_string(value: str) -> str:
    """Quote a value for a Java UiSelector expression.

    A selector is Java source that the driver compiles on the device, so a
    quote or a backslash in the text has to be escaped or the expression stops
    parsing -- and an app label containing an apostrophe is entirely ordinary.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


# --------------------------------------------------------------------------
# validation: everything here runs before the device is touched
# --------------------------------------------------------------------------


def validate_device_id(device_id: str) -> str:
    """Reject a serial that could not name a real device. Returns it stripped."""
    candidate = device_id.strip()
    if not candidate:
        raise InvalidDeviceIdError(device_id, "empty")
    if not DEVICE_ID_RE.match(candidate):
        raise InvalidDeviceIdError(
            device_id, "expected an adb serial such as 'emulator-5554' or 'R58M12ABCDE'"
        )
    return candidate


def validate_locator(strategy: str, selector: str) -> tuple[str, str]:
    """Reject an unknown strategy or an empty selector. Returns them normalised."""
    normalised = strategy.strip().lower()
    if normalised not in LOCATOR_BY:
        raise InvalidLocatorError(strategy, selector, LOCATOR_STRATEGIES)
    if not selector.strip():
        raise InvalidLocatorError(strategy, selector, LOCATOR_STRATEGIES)
    return normalised, selector


def resolve_locator(strategy: str, selector: str) -> tuple[str, str]:
    """A validated locator, as the client's ``(by, value)`` pair.

    ``text`` is rewritten into a UiSelector here rather than at the call site,
    which is what keeps "find by text" a first-class strategy for the caller
    without leaking UiAutomator syntax into the tool surface.
    """
    normalised, raw = validate_locator(strategy, selector)
    if normalised == "text":
        return AppiumBy.ANDROID_UIAUTOMATOR, f"new UiSelector().text({_ui_selector_string(raw)})"
    return LOCATOR_BY[normalised], raw


def validate_coordinates(x: int, y: int) -> tuple[int, int]:
    """Reject coordinates off the screen's near edge.

    The far edge cannot be checked without knowing the device's resolution, and
    this function must not ask a device anything. Negative is unambiguous, and
    is the mistake that actually happens -- an offset subtracted twice.
    """
    if x < 0 or y < 0:
        raise InvalidCoordinatesError(
            x, y, "coordinates are pixels from the top-left, never negative"
        )
    return x, y


def validate_key(key: str) -> int:
    """Map a friendly key name to an Android keycode, or reject it."""
    normalised = key.strip().lower().replace("-", "_").replace(" ", "_")
    if normalised not in ANDROID_KEYCODES:
        raise InvalidKeyNameError(key, tuple(sorted(ANDROID_KEYCODES)))
    return ANDROID_KEYCODES[normalised]


def validate_scroll(direction: str, distance: float) -> tuple[str, float]:
    """Reject an unknown direction or a distance outside ``0 < d <= 1``.

    A distance of 0 would be a gesture that goes nowhere and a distance above 1
    would ask the finger to leave the screen; both are caller mistakes worth
    naming before a device is asked to attempt them.
    """
    normalised = direction.strip().lower()
    if normalised not in SCROLL_DIRECTIONS:
        raise InvalidScrollError(direction, "unknown direction", SCROLL_DIRECTIONS)
    if not 0 < distance <= 1:
        raise InvalidScrollError(
            direction,
            f"distance {distance} must be greater than 0 and at most 1.0",
            SCROLL_DIRECTIONS,
        )
    return normalised, distance


def validate_app_path(app_path: Path | None) -> Path | None:
    """An APK path that is not on disk is a caller error, not a session failure.

    This is the one validator that looks at the filesystem, which is why the
    manager calls it and the pure unit tests pass it a ``tmp_path``.
    """
    if app_path is None:
        return None
    resolved = Path(app_path).expanduser()
    if not resolved.is_file():
        raise AppFileNotFoundError(resolved)
    return resolved


# --------------------------------------------------------------------------
# reading what the tools said
# --------------------------------------------------------------------------


def parse_version_output(stdout: str) -> str | None:
    """The first semver-looking token in a tool's ``--version`` output."""
    match = VERSION_RE.search(stdout)
    return match.group(1) if match else None


def parse_installed_drivers(stdout: str) -> tuple[str, ...]:
    """Driver names from ``appium driver list --installed --json``.

    The JSON form is used rather than the human one because the human one is
    ANSI-coloured progress output, and stripping escape codes to find a name is
    a parser waiting to break on the next release.
    """
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return ()
    if not isinstance(payload, dict):
        return ()
    return tuple(sorted(str(name) for name in payload))


def parse_driver_version(stdout: str, driver_name: str) -> str | None:
    """One driver's version from the same JSON payload."""
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return None
    entry = payload.get(driver_name) if isinstance(payload, dict) else None
    if isinstance(entry, dict) and entry.get("version"):
        return str(entry["version"])
    return None


def parse_server_status(body: str) -> str | None:
    """The server version from an Appium ``/status`` body, when it says.

    Returns ``None`` for a body that parses but carries no version -- which is
    still a live server, so the caller must not read ``None`` as "down".
    """
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return None
    value = payload.get("value") if isinstance(payload, dict) else None
    if isinstance(value, dict):
        build = value.get("build")
        if isinstance(build, dict) and build.get("version"):
            return str(build["version"])
    return None


def png_dimensions(data: bytes) -> tuple[int, int] | None:
    """Width and height straight out of a PNG's IHDR chunk.

    A screenshot's real size is not always the window size the session reported
    -- a status bar, a scaled emulator window, a rotated device -- so it is read
    from the image rather than assumed. Sixteen bytes of header beats a decoding
    dependency for a number this small.
    """
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def truncate(source: str, max_characters: int | None) -> tuple[str, bool, int]:
    """Cut a string to length. Returns ``(text, was_truncated, original_length)``.

    The original length travels with the text so a caller can tell "the screen
    is empty" from "you are looking at the first 2000 characters of it".
    """
    total = len(source)
    if max_characters is None or max_characters <= 0 or total <= max_characters:
        return source, False, total
    return source[:max_characters], True, total


# --------------------------------------------------------------------------
# classification: tool output and client exceptions become typed errors
# --------------------------------------------------------------------------

#: ``(markers, build)``; the first row whose markers all appear wins.
SessionFailureRow = tuple[tuple[str, ...], Callable[[str, str], AppiumModuleError]]

SESSION_FAILURES: tuple[SessionFailureRow, ...] = (
    (
        ("could not find a connected android device",),
        lambda device_id, detail: SessionCreateError(
            device_id, "no such device is attached; check the serial with the Android module"
        ),
    ),
    (
        ("device", "not found"),
        lambda device_id, detail: SessionCreateError(device_id, "the device is not attached"),
    ),
    (
        ("unable to find an active device or emulator",),
        lambda device_id, detail: SessionCreateError(device_id, "no device matched that serial"),
    ),
    (
        ("does not exist or is not accessible",),
        lambda device_id, detail: SessionCreateError(device_id, "the app file is not readable"),
    ),
    (
        ("not implemented", "automationname"),
        lambda device_id, detail: SessionCreateError(
            device_id, "the requested automation driver is not installed"
        ),
    ),
)

#: ``(markers, build)`` for a failure while driving an existing session.
InteractionFailureRow = tuple[tuple[str, ...], Callable[[str, str, str], AppiumModuleError]]

INTERACTION_FAILURES: tuple[InteractionFailureRow, ...] = (
    (
        ("invalid session id",),
        lambda action, session_id, detail: SessionDeadError(session_id, "the server dropped it"),
    ),
    (
        ("session is either terminated or not started",),
        lambda action, session_id, detail: SessionDeadError(session_id, "the server dropped it"),
    ),
    (
        ("a session is either terminated",),
        lambda action, session_id, detail: SessionDeadError(session_id, "the server dropped it"),
    ),
)


def _matches(lowered: str, markers: Sequence[str]) -> bool:
    return all(marker in lowered for marker in markers)


def classify_session_failure(device_id: str, detail: str) -> AppiumModuleError:
    """Map a session-creation failure onto the most specific error that fits."""
    lowered = detail.lower()
    for markers, build in SESSION_FAILURES:
        if _matches(lowered, markers):
            return build(device_id, detail)
    return SessionCreateError(device_id, detail)


def classify_interaction_failure(action: str, session_id: str, detail: str) -> AppiumModuleError:
    """Map a failure during a gesture onto a typed error.

    A dead session is the one worth naming: the remedy is "start a new session",
    where every other interaction failure means "the screen is not what you
    thought" and the caller should look at it again.
    """
    lowered = detail.lower()
    for markers, build in INTERACTION_FAILURES:
        if _matches(lowered, markers):
            return build(action, session_id, detail)
    return WebDriverCallError(action, session_id, detail)


def classify_command_failure(operation: str, result: CommandResult) -> AppiumModuleError:
    """The fallback for a CLI call that exited non-zero with nothing to say."""
    return CommandFailedError(operation, result)
