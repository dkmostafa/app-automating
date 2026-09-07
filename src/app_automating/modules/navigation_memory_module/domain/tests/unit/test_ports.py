"""The ports: their shape, and the segregation that makes the read tools safe."""

from __future__ import annotations

import inspect
import typing

import pytest

from app_automating.modules.navigation_memory_module.domain import models as domain_models
from app_automating.modules.navigation_memory_module.domain.ports import (
    MemoryMaintenance,
    RouteMemory,
    RouteRecorder,
)

pytestmark = pytest.mark.unit

PORTS = (RouteMemory, RouteRecorder, MemoryMaintenance)


def methods_of(port: type) -> set[str]:
    return {n for n, _ in inspect.getmembers(port, inspect.isfunction) if not n.startswith("_")}


@pytest.mark.parametrize("port", PORTS, ids=lambda p: p.__name__)
def test_every_port_is_a_runtime_checkable_protocol(port: type) -> None:
    assert issubclass(port, typing.Protocol)  # type: ignore[arg-type]
    assert getattr(port, "_is_runtime_protocol", False)


@pytest.mark.parametrize("port", PORTS, ids=lambda p: p.__name__)
def test_every_port_method_is_async_and_takes_one_request(port: type) -> None:
    assert methods_of(port), f"{port.__name__} declares no operations"
    for name, method in inspect.getmembers(port, inspect.isfunction):
        if name.startswith("_"):
            continue
        assert inspect.iscoroutinefunction(method), f"{port.__name__}.{name} must be async"
        assert list(inspect.signature(method).parameters) == ["self", "request"]


@pytest.mark.parametrize("port", PORTS, ids=lambda p: p.__name__)
def test_every_port_signature_speaks_only_domain_types(port: type) -> None:
    exported = set(domain_models.__all__)
    for name, method in inspect.getmembers(port, inspect.isfunction):
        if name.startswith("_"):
            continue
        hints = typing.get_type_hints(method)
        for role in ("request", "return"):
            assert hints[role].__name__ in exported, (
                f"{port.__name__}.{name} {role} is {hints[role]!r}, not a domain model"
            )


def test_reading_the_map_cannot_change_it() -> None:
    """The reason the split is load-bearing rather than decorative: the MCP tools
    hold RouteMemory, so they are structurally incapable of recording."""
    reading = methods_of(RouteMemory)

    assert not reading & methods_of(RouteRecorder)
    assert not reading & methods_of(MemoryMaintenance)
    assert "record_interaction" not in reading
    assert "forget" not in reading


def test_recording_cannot_delete() -> None:
    """RecordingDeviceDriver holds RouteRecorder and nothing else; a bug there
    must not be able to wipe the map."""
    assert "forget" not in methods_of(RouteRecorder)


def test_the_ports_do_not_overlap() -> None:
    seen: set[str] = set()
    for port in PORTS:
        assert not (methods_of(port) & seen), f"{port.__name__} repeats an operation"
        seen |= methods_of(port)
