"""The typed payloads the Android tools return.

Every tool returns one of these rather than a bare ``dict``, so FastMCP
publishes a real output schema next to each tool description and the client sees
field names and types instead of inferring them from prose. They are the
presentation layer's own vocabulary: JSON-shaped, flat, and free of
:class:`pathlib.Path`, tuples-of-dataclasses and properties -- everything the
domain expresses in Python and JSON cannot.

This file is the bottom of the presentation package's dependency order
(``schemas -> rendering -> errors -> emulator_tools``). It imports nothing from
the module: a payload knows nothing about the domain result it was built from,
which is what keeps the conversion in one place (:mod:`.rendering`) instead of
spread across nine tool bodies.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DevicePayload",
    "DeviceListPayload",
    "AvdPayload",
    "AvdListPayload",
    "StartedEmulatorPayload",
    "StoppedEmulatorPayload",
    "CreatedEmulatorPayload",
    "DeletedEmulatorPayload",
    "InstalledImagePayload",
]


class _Payload(BaseModel):
    """Shared configuration: frozen, and no field the schema did not declare."""

    model_config = ConfigDict(frozen=True, extra="forbid")


# --------------------------------------------------------------------------
# what is attached to the host
# --------------------------------------------------------------------------


class DevicePayload(_Payload):
    """One device attached to the host right now."""

    device_id: str = Field(
        description="The adb serial, e.g. 'emulator-5554'. Every device-scoped tool takes this."
    )
    state: str = Field(
        description="Raw adb state: 'device' (usable), 'offline', 'unauthorized', 'booting'."
    )
    available: bool = Field(
        description="True when the device will accept commands right now, i.e. state == 'device'."
    )
    is_emulator: bool = Field(description="True for an emulator, False for physical hardware.")
    avd_name: str | None = Field(
        default=None,
        description=(
            "The AVD this emulator is running, when it was resolved. None for physical "
            "devices, and None for emulators when resolve_avd_names was turned off."
        ),
    )
    model: str | None = Field(default=None, description="Device model as adb reports it.")
    product: str | None = Field(default=None, description="Product name as adb reports it.")
    transport_id: str | None = Field(default=None, description="adb transport id, when reported.")


class DeviceListPayload(_Payload):
    """The answer to "what is attached to this host"."""

    devices: tuple[DevicePayload, ...] = Field(description="One entry per attached device.")
    count: int = Field(description="len(devices), so a caller need not count to know it is empty.")
    emulator_count: int = Field(description="How many of them are emulators rather than hardware.")


# --------------------------------------------------------------------------
# what exists on disk
# --------------------------------------------------------------------------


class AvdPayload(_Payload):
    """One Android Virtual Device defined on this host, running or not."""

    name: str = Field(description="The AVD name, exactly as the start/delete tools want it.")
    path: str | None = Field(default=None, description="Directory holding the AVD's data.")
    device: str | None = Field(
        default=None, description="Hardware profile it was created from, e.g. 'pixel_6'."
    )
    target: str | None = Field(default=None, description="Android platform target, when reported.")
    based_on: str | None = Field(default=None, description="System image the AVD was built on.")
    tag_abi: str | None = Field(default=None, description="Tag and ABI, e.g. 'google_apis/x86_64'.")
    sdcard: str | None = Field(default=None, description="SD card size, when the AVD has one.")
    loadable: bool = Field(
        description=(
            "False when the backend could not read this AVD: it exists on disk and cannot be "
            "booted. Starting it will fail until whatever 'error' names is fixed."
        )
    )
    error: str | None = Field(
        default=None, description="Why an unloadable AVD could not be read. None when loadable."
    )


class AvdListPayload(_Payload):
    """The answer to "what could I start"."""

    emulators: tuple[AvdPayload, ...] = Field(description="One entry per AVD defined on the host.")
    names: tuple[str, ...] = Field(
        description="Just the names, in the same order -- the usual next argument."
    )
    count: int = Field(description="len(emulators).")
    unloadable_count: int = Field(
        description="How many are broken. Non-zero means some names in this list cannot boot."
    )


# --------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------


class StartedEmulatorPayload(_Payload):
    """The result of booting an emulator. It keeps running after the tool returns."""

    name: str = Field(description="The AVD that was started.")
    device_id: str = Field(
        description="The adb serial it attached as. This is what every other device tool takes."
    )
    pid: int = Field(description="Host process id of the emulator, for diagnosis only.")
    headless: bool = Field(description="True when it was started without a window.")
    booted: bool = Field(
        description=(
            "True when the device finished booting and accepts input. False only when "
            "wait_for_boot was turned off -- the device is then still coming up."
        )
    )
    startup_duration_seconds: float = Field(description="Wall-clock time this call spent booting.")


class StoppedEmulatorPayload(_Payload):
    """The result of shutting an emulator down. The AVD stays on disk."""

    device_id: str = Field(description="The serial that was stopped.")
    avd_name: str | None = Field(
        default=None, description="The AVD it was running, when that was known."
    )
    stopped: bool = Field(
        description=(
            "True when the serial left the device list. False only when wait_for_exit was "
            "turned off -- the device is then still going down."
        )
    )
    duration_seconds: float = Field(description="Wall-clock time this call took.")


class CreatedEmulatorPayload(_Payload):
    """The result of defining a new AVD. It is not running."""

    name: str = Field(description="The new AVD's name; pass it to android_run_emulator to boot it.")
    path: str = Field(description="Directory now holding the AVD's data.")
    config_path: str = Field(description="The AVD's config.ini.")
    system_image: str = Field(description="System image package id it was built on.")
    device: str = Field(description="Hardware profile it was built from.")
    replaced_existing: bool = Field(
        description="True when an AVD of the same name was overwritten and its data destroyed."
    )
    duration_seconds: float = Field(description="Wall-clock time this call took.")


class DeletedEmulatorPayload(_Payload):
    """The result of deleting an AVD. Nothing here can be undone."""

    name: str = Field(description="The AVD that was deleted.")
    deleted_path: str | None = Field(
        default=None, description="The directory that was removed, when there was one."
    )
    stopped_first: bool = Field(
        description="True when the AVD was running and had to be shut down before deletion."
    )
    duration_seconds: float = Field(description="Wall-clock time this call took.")


class InstalledImagePayload(_Payload):
    """The result of provisioning a system image."""

    system_image: str = Field(description="The sdkmanager package id that is now installed.")
    path: str = Field(description="Where it was unpacked under the SDK root.")
    already_installed: bool = Field(
        description="True when it was already on disk and nothing was downloaded."
    )
    duration_seconds: float = Field(
        description="Wall-clock time this call took. Large on a real download."
    )
