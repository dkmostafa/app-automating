"""The abstractions the application layer calls, declared by the caller.

Rule 0 §2: a port is owned by the layer that *needs* it, so these live in the
domain and the adapters under ``infrastructure/`` import them in order to be
satisfied by them. They are ``Protocol``s rather than ``ABC``s so an adapter
never has to inherit from the inner layer -- the arrow stays pointing one way
even in the class statement.

They are split by capability rather than by adapter (Rule 0 §3, ISP): a use case
that only enumerates devices takes :class:`DeviceCatalog` and is then
structurally incapable of deleting one. One class may satisfy all three; that is
the adapter's business, not the caller's.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import (
    CreateEmulatorRequest,
    CreateEmulatorResult,
    DeleteEmulatorRequest,
    DeleteEmulatorResult,
    InstallSystemImageRequest,
    InstallSystemImageResult,
    ListDevicesRequest,
    ListDevicesResult,
    ListEmulatorsRequest,
    ListEmulatorsResult,
    StartEmulatorRequest,
    StartEmulatorResult,
    StopEmulatorRequest,
    StopEmulatorResult,
)

__all__ = ["DeviceCatalog", "EmulatorLifecycle", "SystemImageInstaller"]


@runtime_checkable
class DeviceCatalog(Protocol):
    """Read-only questions about what exists on the host."""

    async def list_devices(self, request: ListDevicesRequest) -> ListDevicesResult: ...

    async def list_emulators(self, request: ListEmulatorsRequest) -> ListEmulatorsResult: ...


@runtime_checkable
class EmulatorLifecycle(Protocol):
    """An emulator from creation, through running, to deletion."""

    async def create_emulator(self, request: CreateEmulatorRequest) -> CreateEmulatorResult: ...

    async def start_emulator(self, request: StartEmulatorRequest) -> StartEmulatorResult: ...

    async def start_emulator_headless(
        self, request: StartEmulatorRequest
    ) -> StartEmulatorResult: ...

    async def stop_emulator(self, request: StopEmulatorRequest) -> StopEmulatorResult: ...

    async def delete_emulator(self, request: DeleteEmulatorRequest) -> DeleteEmulatorResult: ...


@runtime_checkable
class SystemImageInstaller(Protocol):
    """Provisioning the platform packages an emulator needs to exist."""

    async def install_system_image(
        self, request: InstallSystemImageRequest
    ) -> InstallSystemImageResult: ...
