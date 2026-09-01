"""Pure functions over the text the Android host tools emit.

Nothing here spawns a process, touches the filesystem or reads the clock -- it
turns tool output and caller input into models, or raises. Keeping it free of
side effects is what lets the cheap checks (an AVD name, a package id, a serial)
run *before* a subprocess is ever started.

Failure classification lives here for the same reason: mapping a tool's stderr
onto a typed error is a pure function of two values, so it belongs beside the
other pure functions rather than as a private method on the manager. It is
written as a table (Rule 0 §3, OCP) -- a newly discovered failure mode adds a
row, it does not edit a function.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from pathlib import Path

from ...domain.models import AndroidDevice, AvdInfo, CreateEmulatorRequest
from .errors import (
    AndroidEmulatorError,
    AvdAlreadyExistsError,
    CommandFailedError,
    InvalidAvdNameError,
    InvalidSystemImageError,
    SystemImageNotInstalledError,
    UnknownDeviceProfileError,
)
from .models import CommandResult

__all__ = [
    "is_emulator_serial",
    "validate_avd_name",
    "validate_system_image_id",
    "system_image_path",
    "parse_adb_devices",
    "parse_avd_list",
    "classify_create_failure",
    "classify_install_failure",
    "CREATE_FAILURES",
    "INSTALL_FAILURES",
]

AVD_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
EMULATOR_SERIAL_RE = re.compile(r"^emulator-\d+$")

#: Keys ``adb devices -l`` appends after the state. Anything else is state text.
DEVICE_PROPERTY_KEYS = frozenset({"product", "model", "device", "transport_id", "usb", "features"})


def is_emulator_serial(device_id: str) -> bool:
    """True for the ``emulator-<port>`` serials that accept ``adb emu`` commands."""
    return bool(EMULATOR_SERIAL_RE.match(device_id))


def validate_avd_name(name: str) -> None:
    if not name or not AVD_NAME_RE.match(name):
        raise InvalidAvdNameError(name)


def validate_system_image_id(system_image: str) -> None:
    parts = system_image.split(";")
    if len(parts) != 4 or not all(part.strip() for part in parts):
        raise InvalidSystemImageError(
            system_image, "expected 'system-images;android-<api>;<tag>;<abi>'"
        )
    if parts[0] != "system-images":
        raise InvalidSystemImageError(system_image, "package id must start with 'system-images'")


def system_image_path(sdk_root: Path, system_image: str) -> Path:
    """Where sdkmanager unpacks a system image, e.g.
    ``<sdk>/system-images/android-34/google_apis/x86_64``."""
    return sdk_root.joinpath("system-images", *system_image.split(";")[1:])


def parse_adb_devices(stdout: str) -> list[AndroidDevice]:
    """Parse ``adb devices -l``.

    ``* daemon not running; starting now ...`` noise precedes the header, and a
    state may itself contain spaces (``no permissions; see [http://...]``), so
    the state is whatever is left once the known ``key:value`` pairs are taken.
    """
    devices: list[AndroidDevice] = []
    seen_header = False
    for raw_line in stdout.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if line.lower().startswith("list of devices attached"):
            seen_header = True
            continue
        if not seen_header or line.startswith("*"):
            continue
        tokens = line.split()
        serial = tokens[0]
        properties: dict[str, str] = {}
        state_tokens: list[str] = []
        for token in tokens[1:]:
            key, sep, value = token.partition(":")
            if sep and key in DEVICE_PROPERTY_KEYS:
                properties[key] = value
            else:
                state_tokens.append(token)
        devices.append(
            AndroidDevice(
                device_id=serial,
                state=" ".join(state_tokens) or "unknown",
                is_emulator=bool(EMULATOR_SERIAL_RE.match(serial)),
                product=properties.get("product"),
                model=properties.get("model"),
                device=properties.get("device"),
                transport_id=properties.get("transport_id"),
            )
        )
    return devices


AVD_FIELD_RE = re.compile(
    r"^\s*(Name|Device|Path|Target|Sdcard|Error|Based on|Tag/ABI|Skin)\s*:\s*(.*)$"
)
UNLOADABLE_HEADER = "could not be loaded"
AVAILABLE_HEADER = "available android virtual devices"


def parse_avd_list(output: str) -> list[AvdInfo]:
    """Parse ``avdmanager list avd``.

    The command prints two sections -- usable AVDs, then AVDs that exist on disk
    but cannot be loaded -- with entries separated by a dashed line. ``Target``
    is followed by an indented continuation line that packs two fields into one:
    ``Based on: Android API 35 Tag/ABI: google_apis/x86_64``.
    """
    avds: list[AvdInfo] = []
    current: dict[str, str] = {}
    loadable = True

    def flush() -> None:
        nonlocal current
        name = current.get("Name")
        if name:
            path = current.get("Path")
            avds.append(
                AvdInfo(
                    name=name,
                    path=Path(path) if path else None,
                    device=current.get("Device"),
                    target=current.get("Target"),
                    based_on=current.get("Based on"),
                    tag_abi=current.get("Tag/ABI"),
                    sdcard=current.get("Sdcard"),
                    loadable=current.get("_loadable") == "1",
                    error=current.get("Error"),
                )
            )
        current = {}

    for line in output.splitlines():
        lowered = line.strip().lower()
        if UNLOADABLE_HEADER in lowered:
            flush()
            loadable = False
            continue
        if lowered.startswith(AVAILABLE_HEADER):
            flush()
            loadable = True
            continue
        if lowered.startswith("---"):
            flush()
            continue
        match = AVD_FIELD_RE.match(line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if key == "Name":
            flush()
        elif not current:
            # A field before any Name: not part of an entry.
            continue
        if key == "Based on" and "Tag/ABI:" in value:
            based_on, _, tag_abi = value.partition("Tag/ABI:")
            current["Based on"] = based_on.strip()
            current["Tag/ABI"] = tag_abi.strip()
        else:
            current[key] = value
        current["_loadable"] = "1" if loadable else "0"

    flush()
    return avds


# --------------------------------------------------------------------------
# failure classification
# --------------------------------------------------------------------------

#: One row is ``(markers, needs_device_name, build)``. A row matches when any of
#: its markers appears in the tool's combined output and, when
#: ``needs_device_name`` is set, the requested device profile is named there too
#: -- avdmanager reports an unknown profile as a bare "not found", which is far
#: too broad a marker to match on its own.
CreateFailureRow = tuple[tuple[str, ...], bool, Callable[[CreateEmulatorRequest], Exception]]

CREATE_FAILURES: tuple[CreateFailureRow, ...] = (
    (("not found",), True, lambda r: UnknownDeviceProfileError(r.device)),
    (("no device found", "device profile"), False, lambda r: UnknownDeviceProfileError(r.device)),
    (
        ("package path is not valid", "invalid --package"),
        False,
        lambda r: SystemImageNotInstalledError(r.system_image),
    ),
    (("already exists",), False, lambda r: AvdAlreadyExistsError(r.name)),
)

#: ``(markers, build)``; ``build`` takes the requested package id.
InstallFailureRow = tuple[tuple[str, ...], Callable[[str], Exception]]

INSTALL_FAILURES: tuple[InstallFailureRow, ...] = (
    (
        ("failed to find package", "could not find package"),
        lambda image: InvalidSystemImageError(image, "sdkmanager does not publish this package"),
    ),
)


def _matches(lowered: str, markers: Sequence[str]) -> bool:
    return any(marker in lowered for marker in markers)


def classify_create_failure(
    request: CreateEmulatorRequest, result: CommandResult
) -> AndroidEmulatorError:
    """Map a failed ``avdmanager create avd`` onto a typed error.

    Returns the error rather than raising it, so the caller's ``raise`` keeps
    the traceback anchored at the call site.
    """
    lowered = result.output.lower()
    for markers, needs_device_name, build in CREATE_FAILURES:
        if not _matches(lowered, markers):
            continue
        if needs_device_name and request.device.lower() not in lowered:
            continue
        error = build(request)
        assert isinstance(error, AndroidEmulatorError)
        return error
    # The honest fallback, not the default (Rule 1 §3).
    return CommandFailedError(result)


def classify_install_failure(system_image: str, result: CommandResult) -> AndroidEmulatorError:
    """Map a failed ``sdkmanager --install`` onto a typed error."""
    lowered = result.output.lower()
    for markers, build in INSTALL_FAILURES:
        if _matches(lowered, markers):
            error = build(system_image)
            assert isinstance(error, AndroidEmulatorError)
            return error
    return CommandFailedError(result)
