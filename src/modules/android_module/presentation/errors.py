"""Domain failures, translated into something the caller can act on.

Rule 3 §6: no exception escapes a tool untranslated, and the translation is a
table rather than an ``if``-chain (Rule 0 §3, OCP) -- a new domain error adds a
row here and edits no function.

Two things are deliberate about the shape of these messages.

* Each one ends in a **remedy**: what the caller should do next. The client is
  a model choosing its next tool call, and "no emulator named 'pixel'" without
  "list them and use a name from that list" is an invitation to guess a spelling.
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

__all__ = ["REMEDIES", "as_tool_error", "domain_errors_as_tool_errors"]


#: What the caller should do about each failure, most specific first.
#:
#: Order matters: the first row whose type matches wins, so a subclass must
#: appear above its base. :class:`AndroidModuleError` is the last row and the
#: honest fallback -- a new domain error is reported rather than swallowed, and
#: this table is where it gets its advice.
REMEDIES: tuple[tuple[type[AndroidModuleError], str], ...] = (
    # -- rejected input, before anything was attempted ---------------------
    (
        InvalidEmulatorName,
        "Emulator names may hold only letters, digits, dots, underscores and hyphens. "
        "Pick a name in that alphabet, or call android_get_installed_emulators and use "
        "one from the list exactly as it appears.",
    ),
    (
        InvalidDeviceId,
        "A device id looks like 'emulator-5554'. Call android_get_available_devices and "
        "use a device_id from that list; do not pass an AVD name here.",
    ),
    (
        InvalidSystemImage,
        "A system image is a full sdkmanager package id, e.g. "
        "'system-images;android-34;google_apis;x86_64'. Fix the id rather than retrying.",
    ),
    (
        UnknownDeviceProfile,
        "That hardware profile does not exist on this host. Use a common one such as "
        "'pixel_6' or 'pixel_7', and do not invent a profile name.",
    ),
    (
        SystemImageNotInstalled,
        "Install it first with android_download_image, then retry this call. Creating an "
        "AVD cannot download its own image.",
    ),
    # -- the emulator's life -----------------------------------------------
    (
        EmulatorNotFound,
        "No AVD by that name exists on this host. Call android_get_installed_emulators "
        "and retry with a name from that list -- do not guess a close spelling. "
        "android_create_device is what makes a new one.",
    ),
    (
        EmulatorAlreadyExists,
        "Choose a different name, or pass replace_existing=True to overwrite the "
        "existing AVD -- which destroys its data irreversibly.",
    ),
    (
        EmulatorAlreadyRunning,
        "It is already booted, and the serial is in the message. Use that device_id "
        "instead of starting it again; android_get_available_devices confirms it.",
    ),
    (
        EmulatorInUse,
        "Stop it first with android_stop_emulator, or pass stop_if_running=True to have "
        "this call shut it down for you.",
    ),
    (
        EmulatorBootTimeout,
        "The emulator was launched but never finished booting in time. Retry with a "
        "larger boot_timeout_seconds, or with cold_boot=True if a saved snapshot has "
        "left the device wedged.",
    ),
    (
        EmulatorStartFailed,
        "The emulator process refused to start; the detail in this message is the tool's "
        "own output. Check that the AVD is loadable in android_get_installed_emulators "
        "before retrying -- an unchanged retry will fail the same way.",
    ),
    (
        EmulatorStopFailed,
        "The shutdown was requested but the device was still attached when time ran out. "
        "Call android_get_available_devices to see whether it has gone since.",
    ),
    (
        DeviceNotFound,
        "That serial is not attached. Call android_get_all_devices for what is actually "
        "connected; a device that was there earlier may have been stopped.",
    ),
    # -- the backend itself ------------------------------------------------
    (
        BackendUnavailable,
        "This host cannot run Android tooling at all -- a missing SDK or an unset "
        "ANDROID_HOME. No retry will fix it; report it to the user rather than trying "
        "another tool.",
    ),
    (
        BackendFailure,
        "The Android tooling was reached and refused the operation. The detail in this "
        "message is the tool's own output; do not retry unchanged.",
    ),
    (
        AndroidModuleError,
        "The Android module reported a failure it has no specific advice for. Report the "
        "message to the user rather than retrying.",
    ),
)


def as_tool_error(exc: AndroidModuleError) -> ToolError:
    """The MCP-facing form of a domain failure: what happened, then what to do.

    The error's own class name is kept in the message on purpose -- it is the
    stable handle a caller can branch on, where the prose around it is not.
    """
    for error_type, remedy in REMEDIES:
        if isinstance(exc, error_type):
            return ToolError(f"{type(exc).__name__}: {exc}. {remedy}")
    # Unreachable while AndroidModuleError is the last row, and kept so that
    # deleting that row is a visible failure rather than a silent None.
    raise AssertionError(f"no remedy for {type(exc).__name__}; the table lost its fallback row")


@contextmanager
def domain_errors_as_tool_errors() -> Iterator[None]:
    """Wrap the one service call in a tool body.

    Only :class:`AndroidModuleError` is caught. Anything else is a bug in this
    server rather than a fact about the device, and dressing it up as a tool
    result would hide it.
    """
    try:
        yield
    except AndroidModuleError as exc:
        raise as_tool_error(exc) from exc
