"""Dependency injection for the navigation-memory module: the one wiring place.

Rule 0 §4: every class in this module takes its collaborators as required
constructor arguments, which is only tolerable because one file assembles them.
This is that file, and there is not a second one.

What happens here, and nowhere else:

* the environment is read (:meth:`NavigationStoreConfig.from_environment`);
* a concrete adapter is named -- importing ``..infrastructure`` from this file
  is the application layer doing its job, not a violation of the dependency
  rule. It is what keeps that knowledge out of ``..domain``, out of the service
  and out of ``..presentation``;
* the adapter is bound to the ports it satisfies, and the ports are injected.

The recorder is handed out separately from the service on purpose. It is the
one port that leaves this module: ``src/app_automating/server.py`` uses it to build the
``decorate`` hook it hands to ``appium_module``'s own composition root, so
interactions record themselves, while the MCP tools built here get only the
read and maintenance ports. Nothing that reads the map can write to it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from ..domain.ports import MemoryMaintenance, RouteMemory, RouteRecorder
from ..infrastructure.appium_recorder import RecordingDeviceDriver
from ..infrastructure.navigation_store import (
    NavigationDatabase,
    NavigationStore,
    NavigationStoreConfig,
)
from .services import NavigationMemoryService

__all__ = [
    "build_navigation_store",
    "navigation_ports",
    "build_appium_port_decorator",
    "build_navigation_memory_service",
    "navigation_memory_service",
]


def build_navigation_store(config: NavigationStoreConfig | None = None) -> NavigationStore:
    """Assemble the navigation adapter over its database.

    ``config=None`` means "read the host environment", which is a decision only
    this layer is allowed to make. Nothing here touches the disk: the database
    connects on first use, so this is safe to call on a machine whose data
    directory does not exist yet.
    """
    resolved = NavigationStoreConfig.from_environment() if config is None else config
    return NavigationStore(NavigationDatabase(resolved), resolved)


def navigation_ports(
    store: NavigationStore,
) -> tuple[RouteMemory, RouteRecorder, MemoryMaintenance]:
    """The store viewed as the three ports it satisfies.

    The annotations are the point: this is where a type checker verifies the
    adapter still matches the domain's abstractions.
    """
    memory: RouteMemory = store
    recorder: RouteRecorder = store
    maintenance: MemoryMaintenance = store
    return memory, recorder, maintenance


#: What ``appium_module``'s own ``PortDecorator`` hook expects: given its three
#: ports, hand back the (possibly substituted) session and gesture ones. Typed
#: loosely on purpose -- this file constructs :class:`RecordingDeviceDriver`, a
#: class of this module's own, and never needs to name ``appium_module``'s
#: Protocols to do it. Only ``infrastructure/appium_recorder/driver.py`` does
#: that (Rule 0 §2 across the module boundary): keeping the exact appium types
#: out of this signature is what keeps this the sole exception file's business.
AppiumPortDecorator = Callable[[object, object, object], tuple[object, object]]


def build_appium_port_decorator(recorder: RouteRecorder) -> AppiumPortDecorator:
    """A hook that wraps appium's session and gesture ports so they record.

    Naming :class:`~..infrastructure.appium_recorder.RecordingDeviceDriver` is
    the composition root's job and nobody else's -- ``src/app_automating/server.py`` may not
    import an ``infrastructure`` package (Rule 3 §1), so the one function that
    knows the two modules can be connected lives here. ``src/app_automating/server.py`` passes
    what this returns straight through to ``appium_module``'s own
    ``appium_device_service(decorate=...)``.
    """

    def decorate(sessions: object, screen: object, interaction: object) -> tuple[object, object]:
        driver = RecordingDeviceDriver(
            sessions=sessions, screen=screen, interaction=interaction, recorder=recorder
        )
        return driver, driver

    return decorate


def build_navigation_memory_service(
    config: NavigationStoreConfig | None = None,
) -> NavigationMemoryService:
    """The navigation memory service, wired to this host's database.

    The caller owns the lifetime of what this builds, including the connection
    pool. A scope that should close it wants :func:`navigation_memory_service`.
    """
    memory, recorder, maintenance = navigation_ports(build_navigation_store(config))
    return NavigationMemoryService(memory=memory, recorder=recorder, maintenance=maintenance)


@asynccontextmanager
async def navigation_memory_service(
    config: NavigationStoreConfig | None = None,
) -> AsyncIterator[tuple[NavigationMemoryService, AppiumPortDecorator | None]]:
    """The service and the appium port decorator, scoped: the pool is closed on exit.

    Yields both because they go to different places. The service backs this
    module's MCP tools; the decorator is handed straight to ``appium_module``'s
    own ``appium_device_service(decorate=...)`` so that every gesture records
    itself. They are two views of one store, so there is one connection pool and
    one lifetime -- which is exactly why the composition root gets them out
    together rather than building the store twice.

    ``config.record_interactions`` is read here, and only here: ``False`` yields
    ``None`` for the decorator, so the composition root wires ``appium_module``
    with no recording at all rather than one that quietly does nothing.
    """
    resolved = NavigationStoreConfig.from_environment() if config is None else config
    store = build_navigation_store(resolved)
    memory, recorder, maintenance = navigation_ports(store)
    decorate = build_appium_port_decorator(recorder) if resolved.record_interactions else None
    try:
        yield (
            NavigationMemoryService(memory=memory, recorder=recorder, maintenance=maintenance),
            decorate,
        )
    finally:
        await store.aclose()
