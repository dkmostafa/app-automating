"""Domain results in, JSON-shaped payloads out. Pure, and the only converter.

The domain speaks :class:`pathlib.Path`, tuples of frozen dataclasses and
derived properties; MCP speaks JSON. This file is where one becomes the other,
and it is the only place that knows both -- a tool body calls a render function,
never a payload constructor, so a change to what a result looks like on the wire
happens once here rather than nine times.

Nothing in here awaits, spawns, reads the clock or touches the filesystem. It
imports the domain's models (Rule 0 §1 permits it: presentation may name the
domain) and this package's :mod:`.schemas`, and nothing else.
"""

from __future__ import annotations

from pathlib import Path

from ..domain.models import (
    AndroidDevice,
    AvdInfo,
    CreateEmulatorResult,
    DeleteEmulatorResult,
    InstallSystemImageResult,
    ListDevicesResult,
    ListEmulatorsResult,
    StartEmulatorResult,
    StopEmulatorResult,
)
from .schemas import (
    AvdListPayload,
    AvdPayload,
    CreatedEmulatorPayload,
    DeletedEmulatorPayload,
    DeviceListPayload,
    DevicePayload,
    InstalledImagePayload,
    StartedEmulatorPayload,
    StoppedEmulatorPayload,
)

__all__ = [
    "render_device",
    "render_device_list",
    "render_avd",
    "render_avd_list",
    "render_started_emulator",
    "render_stopped_emulator",
    "render_created_emulator",
    "render_deleted_emulator",
    "render_installed_image",
]


def _path(value: Path | None) -> str | None:
    """A Path is not JSON. Rendering one is the single reason this helper exists."""
    return None if value is None else str(value)


def render_device(device: AndroidDevice) -> DevicePayload:
    """One attached device. ``available`` is the domain's property, not a re-derivation."""
    return DevicePayload(
        device_id=device.device_id,
        state=device.state,
        available=device.is_available,
        is_emulator=device.is_emulator,
        avd_name=device.avd_name,
        model=device.model,
        product=device.product,
        transport_id=device.transport_id,
    )


def render_device_list(result: ListDevicesResult) -> DeviceListPayload:
    """The device list, with the two counts a caller would otherwise compute itself."""
    return DeviceListPayload(
        devices=tuple(render_device(device) for device in result.devices),
        count=len(result.devices),
        emulator_count=len(result.emulators),
    )


def render_avd(avd: AvdInfo) -> AvdPayload:
    """One AVD defined on the host."""
    return AvdPayload(
        name=avd.name,
        path=_path(avd.path),
        device=avd.device,
        target=avd.target,
        based_on=avd.based_on,
        tag_abi=avd.tag_abi,
        sdcard=avd.sdcard,
        loadable=avd.loadable,
        error=avd.error,
    )


def render_avd_list(result: ListEmulatorsResult) -> AvdListPayload:
    """The AVD catalogue.

    ``unloadable_count`` is surfaced rather than left to be counted because it
    changes what the list means: a name in ``names`` that is not loadable will
    fail to boot, and a caller that never looks would read the failure as a bug.
    """
    return AvdListPayload(
        emulators=tuple(render_avd(avd) for avd in result.emulators),
        names=result.names,
        count=len(result.emulators),
        unloadable_count=len(result.emulators) - len(result.loadable),
    )


def render_started_emulator(result: StartEmulatorResult) -> StartedEmulatorPayload:
    """A booted emulator.

    ``launch_argv`` is deliberately dropped: it is the adapter's command line,
    and putting an SDK invocation in front of the client would leak exactly the
    detail the layering exists to hide.
    """
    return StartedEmulatorPayload(
        name=result.name,
        device_id=result.device_id,
        pid=result.pid,
        headless=result.headless,
        booted=result.booted,
        startup_duration_seconds=result.startup_duration_seconds,
    )


def render_stopped_emulator(result: StopEmulatorResult) -> StoppedEmulatorPayload:
    """A stopped emulator."""
    return StoppedEmulatorPayload(
        device_id=result.device_id,
        avd_name=result.avd_name,
        stopped=result.stopped,
        duration_seconds=result.duration_seconds,
    )


def render_created_emulator(result: CreateEmulatorResult) -> CreatedEmulatorPayload:
    """A newly defined AVD."""
    return CreatedEmulatorPayload(
        name=result.name,
        path=str(result.path),
        config_path=str(result.config_path),
        system_image=result.system_image,
        device=result.device,
        replaced_existing=result.replaced_existing,
        duration_seconds=result.duration_seconds,
    )


def render_deleted_emulator(result: DeleteEmulatorResult) -> DeletedEmulatorPayload:
    """A deleted AVD."""
    return DeletedEmulatorPayload(
        name=result.name,
        deleted_path=_path(result.deleted_path),
        stopped_first=result.stopped_first,
        duration_seconds=result.duration_seconds,
    )


def render_installed_image(result: InstallSystemImageResult) -> InstalledImagePayload:
    """An installed system image."""
    return InstalledImagePayload(
        system_image=result.system_image,
        path=str(result.path),
        already_installed=result.already_installed,
        duration_seconds=result.duration_seconds,
    )
