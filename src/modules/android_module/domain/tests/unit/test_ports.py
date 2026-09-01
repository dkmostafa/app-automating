"""Unit tests for ``domain/ports.py`` -- the abstractions adapters must satisfy."""

from __future__ import annotations

import inspect
import typing

import pytest

from modules.android_module.domain import models as domain_models
from modules.android_module.domain.ports import (
    DeviceCatalog,
    EmulatorLifecycle,
    SystemImageInstaller,
)

pytestmark = pytest.mark.unit

PORTS = (DeviceCatalog, EmulatorLifecycle, SystemImageInstaller)


@pytest.mark.parametrize("port", PORTS, ids=lambda p: p.__name__)
def test_every_port_is_a_runtime_checkable_protocol(port: type) -> None:
    """Adapters must not have to inherit from the domain to satisfy it."""
    assert issubclass(port, typing.Protocol)  # type: ignore[arg-type]
    assert getattr(port, "_is_runtime_protocol", False), (
        f"{port.__name__} must be runtime_checkable"
    )


@pytest.mark.parametrize("port", PORTS, ids=lambda p: p.__name__)
def test_every_port_method_is_async_and_takes_one_request(port: type) -> None:
    """Rule 1 §2, stated at the boundary rather than only at the adapter."""
    methods = [
        (n, m) for n, m in inspect.getmembers(port, inspect.isfunction) if not n.startswith("_")
    ]
    assert methods, f"{port.__name__} declares no operations"
    for name, method in methods:
        assert inspect.iscoroutinefunction(method), f"{port.__name__}.{name} must be async"
        assert list(inspect.signature(method).parameters) == ["self", "request"], (
            f"{port.__name__}.{name} must take exactly one parameter named 'request'"
        )


@pytest.mark.parametrize("port", PORTS, ids=lambda p: p.__name__)
def test_every_port_signature_speaks_only_domain_types(port: type) -> None:
    """A port that named an infrastructure type would invert the dependency rule."""
    exported = set(domain_models.__all__)
    for name, method in inspect.getmembers(port, inspect.isfunction):
        if name.startswith("_"):
            continue
        hints = typing.get_type_hints(method)
        for role in ("request", "return"):
            assert hints[role].__name__ in exported, (
                f"{port.__name__}.{name} {role} is {hints[role]!r}, not a domain model"
            )


def test_the_ports_are_narrow_enough_to_be_worth_separating() -> None:
    """ISP: a caller that only reads must not receive the ability to delete."""
    catalog = {
        n for n, _ in inspect.getmembers(DeviceCatalog, inspect.isfunction) if not n.startswith("_")
    }
    lifecycle = {
        n
        for n, _ in inspect.getmembers(EmulatorLifecycle, inspect.isfunction)
        if not n.startswith("_")
    }
    assert not catalog & lifecycle, "the ports overlap; they are not segregated"
    assert "delete_emulator" not in catalog
