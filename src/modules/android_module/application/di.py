"""Dependency injection for the Android module: the one place things are wired.

Rule 0 §4: every class in this module takes its collaborators as required
constructor arguments, which is only tolerable because one file assembles them.
This is that file, and there is not a second one.

What happens here, and nowhere else:

* the environment is read (:meth:`AndroidSdkConfig.from_environment`, and
  :func:`avd_home_from_env` on the fallback path);
* a concrete adapter is named -- importing ``..infrastructure`` from this file
  is the application layer doing its job, not a violation of the dependency
  rule. It is what keeps that knowledge out of ``..domain``, out of the
  services, and out of ``..presentation``;
* an adapter is bound to the ports it satisfies, and the ports are injected into
  a service.

Everything this file builds is handed its settings explicitly. Nothing below it
reaches for an ambient default.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from ..domain.ports import DeviceCatalog, EmulatorLifecycle, SystemImageInstaller
from ..infrastructure.android_emulator_manager import (
    AndroidEmulatorManager,
    AndroidSdkConfig,
    AvdStore,
    CommandRunner,
    avd_home_from_env,
)
from .services import AndroidEmulatorService

__all__ = [
    "build_android_emulator_manager",
    "android_emulator_ports",
    "build_android_emulator_service",
    "android_emulator_service",
]


# --------------------------------------------------------------------------
# adapters
# --------------------------------------------------------------------------


def build_android_emulator_manager(
    config: AndroidSdkConfig | None = None,
) -> AndroidEmulatorManager:
    """Assemble the Android emulator adapter from its three collaborators.

    ``config=None`` means "read the host environment", which is a decision only
    this layer is allowed to make.
    """
    resolved = AndroidSdkConfig.from_environment() if config is None else config
    # A hand-written config may leave avd_home unset; the fallback belongs here
    # rather than in the manager, which must never consult the environment.
    avd_home = resolved.avd_home if resolved.avd_home is not None else avd_home_from_env()
    return AndroidEmulatorManager(
        config=resolved,
        runner=CommandRunner(resolved),
        avds=AvdStore(avd_home),
    )


def android_emulator_ports(
    manager: AndroidEmulatorManager,
) -> tuple[DeviceCatalog, EmulatorLifecycle, SystemImageInstaller]:
    """The manager viewed as the three ports it satisfies.

    Services are constructed from these rather than from the manager, so a
    service holds exactly the authority it needs. The annotations are the point:
    this is where a type checker verifies the adapter still matches the domain's
    abstractions.
    """
    catalog: DeviceCatalog = manager
    lifecycle: EmulatorLifecycle = manager
    installer: SystemImageInstaller = manager
    return catalog, lifecycle, installer


# --------------------------------------------------------------------------
# services
# --------------------------------------------------------------------------


def build_android_emulator_service(
    config: AndroidSdkConfig | None = None,
) -> AndroidEmulatorService:
    """The Android emulator service, wired to the local SDK.

    The caller owns the lifetime of what this builds. That is deliberate for a
    long-running server: the emulators a service started should outlive the
    request that started them. A scope that *should* take its emulators down
    with it wants :func:`android_emulator_service` instead.
    """
    catalog, lifecycle, installer = android_emulator_ports(build_android_emulator_manager(config))
    return AndroidEmulatorService(devices=catalog, emulators=lifecycle, images=installer)


@asynccontextmanager
async def android_emulator_service(
    config: AndroidSdkConfig | None = None,
) -> AsyncIterator[AndroidEmulatorService]:
    """The same service, scoped: every emulator it launched is killed on exit.

    For scripts and one-shot runs. Do not wrap a server's lifetime in this
    unless you mean for shutdown to take the devices with it.
    """
    manager = build_android_emulator_manager(config)
    catalog, lifecycle, installer = android_emulator_ports(manager)
    try:
        yield AndroidEmulatorService(devices=catalog, emulators=lifecycle, images=installer)
    finally:
        await manager.aclose()
