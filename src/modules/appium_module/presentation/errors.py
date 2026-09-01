"""Domain failures, translated into something the caller can act on.

Rule 3 §6: no exception escapes a tool untranslated, and the translation is a
table rather than an ``if``-chain (Rule 0 §3, OCP) -- a new domain error adds a
row here and edits no function.

Two things are deliberate about the shape of these messages.

* Each one ends in a **remedy**: what the caller should do next. The client is
  a model choosing its next tool call, and "no element matching text='Submit'"
  without "read the page source and use a selector from it" is an invitation to
  guess a second spelling and fail the same way.
* The evidence comes from ``str(exc)``, which every domain error renders from
  the attributes it carries (see ``domain/errors.py``). No stack trace, no
  adapter vocabulary, and never a bare ``repr``.

This file may name only the domain's failure vocabulary. The infrastructure's
richer subclasses *are* domain errors, so they arrive here already translated,
and presentation could not import them anyway.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastmcp.exceptions import ToolError

from ..domain.errors import (
    AppiumModuleError,
    AppNotFound,
    BackendFailure,
    BackendUnavailable,
    DeviceNotFound,
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

__all__ = ["REMEDIES", "as_tool_error", "domain_errors_as_tool_errors"]


#: What the caller should do about each failure, most specific first.
#:
#: Order matters: the first row whose type matches wins, so a subclass must
#: appear above its base. :class:`AppiumModuleError` is the last row and the
#: honest fallback -- a new domain error is reported rather than swallowed, and
#: this table is where it gets its advice.
REMEDIES: tuple[tuple[type[AppiumModuleError], str], ...] = (
    # -- rejected input, before anything was attempted ---------------------
    (
        InvalidDeviceId,
        "A device id is an adb serial such as 'emulator-5554' or 'R58M12ABCDE'. Call "
        "android_get_available_devices and use a device_id from that list; do not pass "
        "an AVD name or a session_id here.",
    ),
    (
        InvalidLocator,
        "Use one of the supported strategies named in this message. If you do not know "
        "what is on screen, call appium_get_page_source first and take a resource-id, "
        "content-desc or text value from it rather than inventing a selector.",
    ),
    (
        InvalidCoordinates,
        "Coordinates are pixels from the top-left corner and are never negative. The "
        "screen size is on the session returned by appium_start_session; a tap outside "
        "it does nothing even when it is accepted.",
    ),
    (
        InvalidKeyName,
        "Use one of the key names listed in this message. Do not pass a raw Android "
        "keycode number and do not pass a character to type -- that is appium_type_text.",
    ),
    (
        InvalidScroll,
        "Direction must be up, down, left or right, and distance must be greater than 0 "
        "and at most 1.0. For a gesture that needs exact endpoints, use appium_swipe.",
    ),
    (
        AppNotFound,
        "The APK path does not exist on the machine running this server. Check the path, "
        "or omit it and pass app_package to launch something already installed.",
    ),
    # -- the host and its toolchain ----------------------------------------
    (
        DriverNotInstalled,
        "Install it with appium_install_driver(driver_name='uiautomator2'), then retry. "
        "appium_check_environment lists what this host actually has.",
    ),
    (
        DriverInstallFailed,
        "The installer ran and failed; its own output is in this message. This usually "
        "means no network or an npm permissions problem, neither of which an unchanged "
        "retry will fix. Report it to the user.",
    ),
    # -- the server --------------------------------------------------------
    (
        ServerStartFailed,
        "The Appium server was launched and did not come up; the tail of its log is in "
        "this message. A port already in use is the common cause. Call "
        "appium_check_environment to see whether something is already listening.",
    ),
    (
        ServerUnreachable,
        "No Appium server is answering and this server is configured not to start one. "
        "Ask the user to run 'appium' on that host, or to unset AT_APPIUM_MANAGE_SERVER.",
    ),
    # -- sessions ----------------------------------------------------------
    (
        SessionNotFound,
        "That session id is not open here; the ones that are open are in this message. "
        "Call appium_get_open_sessions, or appium_start_session to open a new one. Do "
        "not pass a device_id where a session_id is wanted.",
    ),
    (
        SessionExpired,
        "The session timed out or the server dropped it. Start a new one with "
        "appium_start_session and use the new session_id -- this one will never work "
        "again.",
    ),
    (
        SessionStartFailed,
        "The session could not be created; the reason is in this message. Confirm the "
        "device is attached and booted with android_get_available_devices, and that the "
        "driver is installed with appium_check_environment.",
    ),
    (
        DeviceNotFound,
        "That device is not attached. Call android_get_available_devices for what is "
        "actually connected, and android_run_emulator if the emulator is not booted yet.",
    ),
    # -- driving the screen ------------------------------------------------
    (
        ElementNotFound,
        "Nothing matched before the wait ran out. Call appium_get_page_source and pick a "
        "selector that appears in it, rather than retrying the same one with a longer "
        "timeout. The screen may also simply not be the one you expect -- "
        "appium_take_screenshot will say.",
    ),
    (
        InteractionFailed,
        "The device rejected the gesture; its own reason is in this message. Look at the "
        "screen with appium_take_screenshot before retrying -- an unchanged retry against "
        "an unchanged screen will fail the same way.",
    ),
    (
        ScreenshotFailed,
        "The screen could not be captured. If the device is mid-transition, retry once; "
        "if it keeps failing, the session is probably wedged and wants restarting.",
    ),
    # -- the backend itself ------------------------------------------------
    (
        BackendUnavailable,
        "This host cannot run Appium at all -- a missing Node or a missing Appium CLI. "
        "Call appium_check_environment for the specifics. No retry will fix it; report "
        "it to the user.",
    ),
    (
        BackendFailure,
        "The Appium toolchain was reached and refused the operation. The detail in this "
        "message is the tool's own output; do not retry unchanged.",
    ),
    (
        AppiumModuleError,
        "The Appium module reported a failure it has no specific advice for. Report the "
        "message to the user rather than retrying.",
    ),
)


def as_tool_error(exc: AppiumModuleError) -> ToolError:
    """The MCP-facing form of a domain failure: what happened, then what to do.

    The error's own class name is kept in the message on purpose -- it is the
    stable handle a caller can branch on, where the prose around it is not.
    """
    for error_type, remedy in REMEDIES:
        if isinstance(exc, error_type):
            return ToolError(f"{type(exc).__name__}: {exc}. {remedy}")
    # Unreachable while AppiumModuleError is the last row, and kept so that
    # deleting that row is a visible failure rather than a silent None.
    raise AssertionError(f"no remedy for {type(exc).__name__}; the table lost its fallback row")


@contextmanager
def domain_errors_as_tool_errors() -> Iterator[None]:
    """Wrap the one service call in a tool body.

    Only :class:`AppiumModuleError` is caught. Anything else is a bug in this
    server rather than a fact about the device, and dressing it up as a tool
    result would hide it.
    """
    try:
        yield
    except AppiumModuleError as exc:
        raise as_tool_error(exc) from exc
