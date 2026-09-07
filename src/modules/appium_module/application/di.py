"""Dependency injection for the Appium module: the one place things are wired.

Rule 0 §4: every class in this module takes its collaborators as required
constructor arguments, which is only tolerable because one file assembles them.
This is that file, and there is not a second one.

What happens here, and nowhere else:

* the environment is read (:meth:`AppiumConfig.from_environment`);
* a concrete adapter is named -- importing ``..infrastructure`` from this file
  is the application layer doing its job, not a violation of the dependency
  rule. It is what keeps that knowledge out of ``..domain``, out of the
  services, and out of ``..presentation``;
* an adapter is bound to the ports it satisfies, and the ports are injected into
  a service.

``decorate`` is the one optional hook this file offers a caller. It is a plain
function -- typed as :data:`PortDecorator`, naming nothing but this module's own
ports -- that gets a chance to wrap ``sessions`` and ``interaction`` before they
reach the service. This module builds it, calls it, and forgets it; it never
learns what the returned objects actually do or who supplied the hook. That is
what keeps this module ignorant of ``navigation_memory_module``, which is the
composition root's caller for this hook today: it wraps these same ports to
record every gesture into a navigation map. Leave ``decorate`` unset and this
module behaves exactly as if the hook did not exist.

Everything this file builds is handed its settings explicitly. Nothing below it
reaches for an ambient default.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from ..domain.ports import (
    AppiumEnvironment,
    DeviceInteraction,
    ScreenInspector,
    SessionLifecycle,
)
from ..infrastructure.appium_device_manager import (
    AppiumConfig,
    AppiumDeviceManager,
    AppiumServer,
    CommandRunner,
    SessionRegistry,
)
from .services import AppiumDeviceService

__all__ = [
    "PortDecorator",
    "build_appium_device_manager",
    "appium_device_ports",
    "build_appium_device_service",
    "appium_device_service",
]

#: A hook that wraps the session and gesture ports before they reach the
#: service, given the three ports a wrapper could plausibly need. Returns the
#: (possibly substituted) ``sessions`` and ``interaction`` ports; ``screen`` is
#: never replaced; reading is unaffected by whatever the hook does.
PortDecorator = Callable[
    [SessionLifecycle, ScreenInspector, DeviceInteraction],
    tuple[SessionLifecycle, DeviceInteraction],
]


# --------------------------------------------------------------------------
# adapters
# --------------------------------------------------------------------------


def build_appium_device_manager(config: AppiumConfig | None = None) -> AppiumDeviceManager:
    """Assemble the Appium adapter from its three collaborators.

    ``config=None`` means "read the host environment", which is a decision only
    this layer is allowed to make. The runner is built first and shared: the
    server process and every version probe go through the same resolved
    toolchain, so they cannot disagree about which ``appium`` this host has.
    """
    resolved = AppiumConfig.from_environment() if config is None else config
    runner = CommandRunner(resolved)
    return AppiumDeviceManager(
        config=resolved,
        runner=runner,
        server=AppiumServer(resolved, runner),
        sessions=SessionRegistry(resolved),
    )


def appium_device_ports(
    manager: AppiumDeviceManager,
) -> tuple[AppiumEnvironment, SessionLifecycle, ScreenInspector, DeviceInteraction]:
    """The manager viewed as the four ports it satisfies.

    Services are constructed from these rather than from the manager, so a
    service holds exactly the authority it needs. The annotations are the point:
    this is where a type checker verifies the adapter still matches the domain's
    abstractions.
    """
    environment: AppiumEnvironment = manager
    sessions: SessionLifecycle = manager
    screen: ScreenInspector = manager
    interaction: DeviceInteraction = manager
    return environment, sessions, screen, interaction


# --------------------------------------------------------------------------
# services
# --------------------------------------------------------------------------


def build_appium_device_service(
    config: AppiumConfig | None = None, decorate: PortDecorator | None = None
) -> AppiumDeviceService:
    """The Appium device service, wired to this host's toolchain.

    The caller owns the lifetime of what this builds -- including any Appium
    server it launches and any session it opens. A scope that *should* take
    those down with it wants :func:`appium_device_service` instead.
    """
    environment, sessions, screen, interaction = appium_device_ports(
        build_appium_device_manager(config)
    )
    if decorate is not None:
        sessions, interaction = decorate(sessions, screen, interaction)
    return AppiumDeviceService(
        environment=environment,
        sessions=sessions,
        screen=screen,
        interaction=interaction,
    )


@asynccontextmanager
async def appium_device_service(
    config: AppiumConfig | None = None, decorate: PortDecorator | None = None
) -> AsyncIterator[AppiumDeviceService]:
    """The same service, scoped: sessions closed and a managed server killed on exit.

    This is the form a server's lifespan should use. A session left open holds
    its device against the next caller, and an Appium server this process
    started with nobody holding the handle is a stray listener on a port.
    """
    manager = build_appium_device_manager(config)
    environment, sessions, screen, interaction = appium_device_ports(manager)
    if decorate is not None:
        sessions, interaction = decorate(sessions, screen, interaction)
    try:
        yield AppiumDeviceService(
            environment=environment,
            sessions=sessions,
            screen=screen,
            interaction=interaction,
        )
    finally:
        await manager.aclose()
