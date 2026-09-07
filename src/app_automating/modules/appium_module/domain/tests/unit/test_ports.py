"""The ports: that they are narrow, structural, and cover the whole surface.

There is nothing to execute here -- a Protocol has no behaviour. What is worth
asserting is the *shape*, because the shape is the contract Rule 0 §2 hangs on:
segregated by capability, satisfied structurally rather than by inheritance, and
checkable at runtime so `di.py` can be verified.
"""

from __future__ import annotations

import inspect
import typing

import pytest

from app_automating.modules.appium_module.domain import ports as ports_module
from app_automating.modules.appium_module.domain.ports import (
    AppiumEnvironment,
    DeviceInteraction,
    ScreenInspector,
    SessionLifecycle,
)

pytestmark = pytest.mark.unit

ALL_PORTS = (AppiumEnvironment, SessionLifecycle, ScreenInspector, DeviceInteraction)


def _methods(port: type) -> set[str]:
    return {
        name for name, _ in inspect.getmembers(port, inspect.isfunction) if not name.startswith("_")
    }


@pytest.mark.parametrize("port", ALL_PORTS, ids=lambda p: p.__name__)
def test_every_port_is_a_runtime_checkable_protocol(port: type) -> None:
    """`di.py` binds adapters to these, and the architecture test isinstance-checks
    them; both need the decorator to be there."""
    assert issubclass(port, typing.Protocol)  # type: ignore[arg-type]
    assert getattr(port, "_is_runtime_protocol", False), f"{port.__name__} is not runtime_checkable"


@pytest.mark.parametrize("port", ALL_PORTS, ids=lambda p: p.__name__)
def test_every_port_method_is_async_and_takes_one_request(port: type) -> None:
    """Rule 1 §2, stated on the abstraction rather than only on the adapter."""
    for name in _methods(port):
        method = getattr(port, name)
        assert inspect.iscoroutinefunction(method), f"{port.__name__}.{name} is not async"
        parameters = list(inspect.signature(method).parameters)
        assert parameters == ["self", "request"], f"{port.__name__}.{name}{parameters}"


def test_the_ports_are_segregated_rather_than_one_big_interface() -> None:
    """Rule 0 §3 (ISP): a use case that only reads a screen must be incapable of
    tapping. That is only true while these sets stay disjoint."""
    seen: set[str] = set()
    for port in ALL_PORTS:
        methods = _methods(port)
        assert not (methods & seen), f"{port.__name__} repeats {methods & seen}"
        seen |= methods


def test_reading_the_screen_and_changing_it_are_different_ports() -> None:
    """The split that matters most: ScreenInspector is side-effect free, and
    holding it must not let a caller drive the device."""
    assert _methods(ScreenInspector) == {"take_screenshot", "get_page_source"}
    assert "tap" not in _methods(ScreenInspector)


def test_installing_a_driver_is_not_reachable_from_a_session_port() -> None:
    """The only operation that changes the host lives on its own port, so a
    service that merely drives a device cannot mutate the machine."""
    assert "install_driver" in _methods(AppiumEnvironment)
    for port in (SessionLifecycle, ScreenInspector, DeviceInteraction):
        assert "install_driver" not in _methods(port)


def test_the_ports_are_the_modules_whole_declared_surface() -> None:
    """A port added without being exported is a capability nothing can inject."""
    exported = set(ports_module.__all__)
    defined = {p.__name__ for p in ALL_PORTS}
    assert exported == defined


def test_an_object_satisfies_a_port_without_inheriting_from_it() -> None:
    """Rule 0 §2: Protocol not ABC, so the arrow keeps pointing one way even in
    the class statement."""

    class NotADescendant:
        async def take_screenshot(self, request): ...
        async def get_page_source(self, request): ...

    assert isinstance(NotADescendant(), ScreenInspector)
    assert ScreenInspector not in NotADescendant.__mro__


def test_a_partial_implementation_does_not_satisfy_a_port() -> None:
    """Guard the guard: if this passed, the isinstance checks in di.py's test
    would prove nothing."""

    class OnlyHalf:
        async def take_screenshot(self, request): ...

    assert not isinstance(OnlyHalf(), ScreenInspector)
