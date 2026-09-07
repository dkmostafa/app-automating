"""The composition root: what it builds, and that what it builds fits the ports."""

from __future__ import annotations

import inspect

import pytest

from app_automating.modules.appium_module.domain.ports import DeviceInteraction, SessionLifecycle
from app_automating.modules.navigation_memory_module.application.di import (
    build_appium_port_decorator,
    build_navigation_memory_service,
    build_navigation_store,
    navigation_memory_service,
    navigation_ports,
)
from app_automating.modules.navigation_memory_module.application.services import (
    NavigationMemoryService,
)
from app_automating.modules.navigation_memory_module.domain.ports import (
    MemoryMaintenance,
    RouteMemory,
    RouteRecorder,
)
from app_automating.modules.navigation_memory_module.infrastructure.appium_recorder import (
    RecordingDeviceDriver,
)
from app_automating.modules.navigation_memory_module.infrastructure.navigation_store import (
    NavigationStore,
    NavigationStoreConfig,
)

pytestmark = pytest.mark.unit

ALL_PORTS = (RouteMemory, RouteRecorder, MemoryMaintenance)


def test_building_the_store_touches_nothing() -> None:
    """The database connects lazily, so the object graph can be assembled on a
    host whose data directory does not exist yet."""
    store = build_navigation_store(NavigationStoreConfig())

    assert isinstance(store, NavigationStore)
    assert not store.database.connected


@pytest.mark.parametrize("port", ALL_PORTS, ids=lambda p: p.__name__)
def test_what_the_root_builds_satisfies_the_ports_it_claims(port: type) -> None:
    """Rule 2 §3's one exception: a wiring test made entirely of mocks would not
    notice the adapter drifting from its abstraction."""
    assert isinstance(build_navigation_store(NavigationStoreConfig()), port)


def test_the_ports_helper_returns_them_in_the_documented_order() -> None:
    ports = navigation_ports(build_navigation_store(NavigationStoreConfig()))

    assert len(ports) == len(ALL_PORTS)
    for built, expected in zip(ports, ALL_PORTS, strict=True):
        assert isinstance(built, expected)


def test_the_service_is_built_from_ports_rather_than_the_adapter() -> None:
    service = build_navigation_memory_service(NavigationStoreConfig())

    assert isinstance(service, NavigationMemoryService)


def test_the_port_decorator_is_built_here_and_not_by_index() -> None:
    """Rule 3 §1: ``src/app_automating/server.py`` may not name an adapter, so the one function
    that connects the two modules lives in this file."""
    decorate = build_appium_port_decorator(build_navigation_store(NavigationStoreConfig()))

    sessions, interaction = decorate(object(), object(), object())  # type: ignore[arg-type]

    assert isinstance(sessions, RecordingDeviceDriver)
    assert isinstance(sessions, SessionLifecycle)
    assert isinstance(interaction, DeviceInteraction)
    assert sessions is interaction


async def test_the_scoped_form_yields_the_service_and_a_decorator() -> None:
    """They are two views of one store, which is why they are handed out together
    rather than the store being built twice."""
    async with navigation_memory_service(NavigationStoreConfig()) as (service, decorate):
        assert isinstance(service, NavigationMemoryService)
        assert decorate is not None
        sessions, _ = decorate(object(), object(), object())  # type: ignore[arg-type]
        assert isinstance(sessions, RecordingDeviceDriver)


async def test_record_interactions_off_yields_no_decorator() -> None:
    """``src/app_automating/server.py`` wires ``appium_module`` with no recording at all rather
    than a decorator that quietly does nothing."""
    config = NavigationStoreConfig(record_interactions=False)

    async with navigation_memory_service(config) as (_, decorate):
        assert decorate is None


async def test_the_scoped_form_closes_the_store_even_when_the_body_raises() -> None:
    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        async with navigation_memory_service(NavigationStoreConfig()):
            raise Boom


def test_only_the_composition_root_may_default_config_to_none() -> None:
    """``config=None`` means "read the environment", and this layer is the only
    one allowed to make that decision."""
    for builder in (build_navigation_store, build_navigation_memory_service):
        assert inspect.signature(builder).parameters["config"].default is None
