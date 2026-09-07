"""The composition root: what it builds, what it injects, and what it closes.

Rule 2 §3: wiring is asserted with mocks, because the question is *which object
was passed to which constructor*, and building the real collaborators would drag
a real Appium into a test that is not about Appium. There is no
``application/integration/`` at all, and the architecture test enforces that.

One exception is kept deliberately, at the bottom: a single assertion that what
the composition root really builds satisfies the ports it claims. A wiring test
made entirely of mocks would not notice the adapter drifting from its
abstraction, which is the one failure this file exists to prevent.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, patch

import pytest

from modules.appium_module.application.di import (
    appium_device_ports,
    appium_device_service,
    build_appium_device_manager,
    build_appium_device_service,
)
from modules.appium_module.application.services import AppiumDeviceService
from modules.appium_module.domain.ports import (
    AppiumEnvironment,
    DeviceInteraction,
    ScreenInspector,
    SessionLifecycle,
)
from modules.appium_module.infrastructure.appium_device_manager import (
    AppiumConfig,
    AppiumDeviceManager,
)

pytestmark = pytest.mark.unit

DI = "modules.appium_module.application.di"


# -- what gets built, and with what ---------------------------------------


def test_the_manager_is_assembled_from_its_three_collaborators() -> None:
    config = AppiumConfig()

    with (
        patch(f"{DI}.CommandRunner") as runner_cls,
        patch(f"{DI}.AppiumServer") as server_cls,
        patch(f"{DI}.SessionRegistry") as sessions_cls,
        patch(f"{DI}.AppiumDeviceManager") as manager_cls,
    ):
        build_appium_device_manager(config)

    runner_cls.assert_called_once_with(config)
    server_cls.assert_called_once_with(config, runner_cls.return_value)
    sessions_cls.assert_called_once_with(config)
    manager_cls.assert_called_once_with(
        config=config,
        runner=runner_cls.return_value,
        server=server_cls.return_value,
        sessions=sessions_cls.return_value,
    )


def test_one_runner_is_shared_by_the_server_and_the_manager() -> None:
    """They must not disagree about which `appium` this host has -- a second
    resolver could pick a different binary and make failures irreproducible."""
    with (
        patch(f"{DI}.CommandRunner") as runner_cls,
        patch(f"{DI}.AppiumServer") as server_cls,
        patch(f"{DI}.SessionRegistry"),
        patch(f"{DI}.AppiumDeviceManager") as manager_cls,
    ):
        build_appium_device_manager(AppiumConfig())

    assert runner_cls.call_count == 1
    assert server_cls.call_args.args[1] is runner_cls.return_value
    assert manager_cls.call_args.kwargs["runner"] is runner_cls.return_value


def test_no_config_means_read_the_environment_here_and_only_here() -> None:
    """Rule 0 §4: the composition root is the only place ambient state is
    resolved. Nothing it builds may reach for a default of its own."""
    with (
        patch(f"{DI}.AppiumConfig") as config_cls,
        patch(f"{DI}.CommandRunner"),
        patch(f"{DI}.AppiumServer"),
        patch(f"{DI}.SessionRegistry"),
        patch(f"{DI}.AppiumDeviceManager") as manager_cls,
    ):
        build_appium_device_manager()

    config_cls.from_environment.assert_called_once_with()
    assert manager_cls.call_args.kwargs["config"] is config_cls.from_environment.return_value


def test_an_explicit_config_is_used_untouched() -> None:
    """The other half: a caller that passed a config must not have it overridden
    by the environment behind its back."""
    config = AppiumConfig(server_port=4999)

    with (
        patch(f"{DI}.AppiumConfig") as config_cls,
        patch(f"{DI}.CommandRunner"),
        patch(f"{DI}.AppiumServer"),
        patch(f"{DI}.SessionRegistry"),
        patch(f"{DI}.AppiumDeviceManager") as manager_cls,
    ):
        build_appium_device_manager(config)

    config_cls.from_environment.assert_not_called()
    assert manager_cls.call_args.kwargs["config"] is config


# -- binding the adapter to its ports -------------------------------------


def test_the_ports_helper_returns_the_manager_four_times_over() -> None:
    """One class satisfying four ports is an implementation detail; what the
    services receive is four narrow views of it."""
    manager = object()

    ports = appium_device_ports(manager)  # type: ignore[arg-type]

    assert len(ports) == 4
    assert all(port is manager for port in ports)


def test_the_service_is_built_from_ports_rather_than_from_the_manager() -> None:
    """So a service holds exactly the authority it needs, and the annotations in
    di.py are where a type checker verifies the adapter still matches."""
    with (
        patch(f"{DI}.build_appium_device_manager") as build_manager,
        patch(f"{DI}.AppiumDeviceService") as service_cls,
    ):
        build_appium_device_service(AppiumConfig())

    manager = build_manager.return_value
    service_cls.assert_called_once_with(
        environment=manager, sessions=manager, screen=manager, interaction=manager
    )


def test_a_decorator_replaces_only_the_session_and_gesture_ports() -> None:
    """The generic composition hook this module offers, and nothing more: it
    never sees ``environment``, and it cannot substitute ``screen``."""
    wrapped_sessions, wrapped_interaction = object(), object()

    def decorate(sessions: object, screen: object, interaction: object) -> tuple[object, object]:
        return wrapped_sessions, wrapped_interaction

    with (
        patch(f"{DI}.build_appium_device_manager") as build_manager,
        patch(f"{DI}.AppiumDeviceService") as service_cls,
    ):
        build_appium_device_service(AppiumConfig(), decorate=decorate)

    manager = build_manager.return_value
    service_cls.assert_called_once_with(
        environment=manager,
        sessions=wrapped_sessions,
        screen=manager,
        interaction=wrapped_interaction,
    )


def test_with_no_decorator_the_service_is_unaware_one_could_exist() -> None:
    """The default: `decorate=None` is a no-op, not an error."""
    with (
        patch(f"{DI}.build_appium_device_manager") as build_manager,
        patch(f"{DI}.AppiumDeviceService") as service_cls,
    ):
        build_appium_device_service(AppiumConfig(), decorate=None)

    manager = build_manager.return_value
    service_cls.assert_called_once_with(
        environment=manager, sessions=manager, screen=manager, interaction=manager
    )


# -- lifetimes -------------------------------------------------------------


async def test_the_scoped_form_closes_the_adapter_on_the_way_out() -> None:
    """A session left open holds its device; a managed Appium server left running
    is a stray listener. The scoped form is what a server's lifespan uses."""
    manager = AsyncMock(spec=AppiumDeviceManager)

    with patch(f"{DI}.build_appium_device_manager", return_value=manager):
        async with appium_device_service() as service:
            assert isinstance(service, AppiumDeviceService)
        manager.aclose.assert_awaited_once()


async def test_the_scoped_form_also_accepts_a_decorator() -> None:
    """The hook is threaded through both builders identically."""
    manager = AsyncMock(spec=AppiumDeviceManager)
    wrapped_sessions, wrapped_interaction = object(), object()

    def decorate(sessions: object, screen: object, interaction: object) -> tuple[object, object]:
        return wrapped_sessions, wrapped_interaction

    with (
        patch(f"{DI}.build_appium_device_manager", return_value=manager),
        patch(f"{DI}.AppiumDeviceService") as service_cls,
    ):
        async with appium_device_service(decorate=decorate):
            pass

    service_cls.assert_called_once_with(
        environment=manager,
        sessions=wrapped_sessions,
        screen=manager,
        interaction=wrapped_interaction,
    )


async def test_the_adapter_is_closed_even_when_the_body_raises() -> None:
    """The path that matters: a crash inside the scope must still release the
    device, or the next run cannot claim it."""
    manager = AsyncMock(spec=AppiumDeviceManager)

    with patch(f"{DI}.build_appium_device_manager", return_value=manager):
        with pytest.raises(RuntimeError):
            async with appium_device_service():
                raise RuntimeError("boom")

    manager.aclose.assert_awaited_once()


def test_the_unscoped_form_leaves_the_lifetime_to_the_caller() -> None:
    """Two builders, two honest contracts: this one hands over ownership rather
    than quietly closing what it built."""
    assert not inspect.isasyncgenfunction(build_appium_device_service)
    assert "aclose" not in inspect.getsource(build_appium_device_service)


# -- the one non-mocked assertion -----------------------------------------


@pytest.mark.parametrize(
    "port",
    [AppiumEnvironment, SessionLifecycle, ScreenInspector, DeviceInteraction],
    ids=lambda p: p.__name__,
)
def test_what_the_composition_root_really_builds_satisfies_the_ports(port: type) -> None:
    """Rule 2 §3's exception, and the reason it exists.

    Everything above is mocked, so none of it would notice the adapter drifting
    away from the abstraction it is injected as. This builds the real object --
    which constructs nothing and touches no host tool until a method is awaited
    -- and checks it structurally against each Protocol.
    """
    manager = build_appium_device_manager(AppiumConfig())

    assert isinstance(manager, port)


def test_the_real_service_can_be_built_without_touching_the_host() -> None:
    """Construction is inert: no subprocess, no server, no device. That is what
    lets `index.py` build its object graph before anything is attached."""
    service = build_appium_device_service(AppiumConfig())

    assert isinstance(service, AppiumDeviceService)
