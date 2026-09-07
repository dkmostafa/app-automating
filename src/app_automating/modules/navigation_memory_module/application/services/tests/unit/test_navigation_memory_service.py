"""The service, against mocked ports.

Its whole job is turning an intent into the right domain request and handing it
to the right port, so that is what is asserted. Mocking the ports is required
here (Rule 2 §3) -- a real database has no business in a test about argument
construction, and the ports are exactly the seam that keeps it out.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock

import pytest

from app_automating.modules.navigation_memory_module.application.services import (
    NavigationMemoryService,
)
from app_automating.modules.navigation_memory_module.domain.errors import ScreenNotRecognised
from app_automating.modules.navigation_memory_module.domain.models import ScreenSnapshot
from app_automating.modules.navigation_memory_module.domain.ports import (
    MemoryMaintenance,
    RouteMemory,
    RouteRecorder,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def memory() -> AsyncMock:
    return AsyncMock(spec=RouteMemory)


@pytest.fixture
def recorder() -> AsyncMock:
    return AsyncMock(spec=RouteRecorder)


@pytest.fixture
def maintenance() -> AsyncMock:
    return AsyncMock(spec=MemoryMaintenance)


@pytest.fixture
def service(
    memory: AsyncMock, recorder: AsyncMock, maintenance: AsyncMock
) -> NavigationMemoryService:
    return NavigationMemoryService(memory=memory, recorder=recorder, maintenance=maintenance)


def request_of(mock: AsyncMock):
    mock.assert_awaited_once()
    (request,), _ = mock.call_args
    return request


# -- reading ------------------------------------------------------------------


async def test_where_am_i_passes_the_session_and_the_limit(
    service: NavigationMemoryService, memory: AsyncMock
) -> None:
    result = await service.where_am_i("s-1", limit=5)

    request = request_of(memory.where_am_i)
    assert (request.session_id, request.limit) == ("s-1", 5)
    assert result is memory.where_am_i.return_value


async def test_find_path_forwards_every_knob(
    service: NavigationMemoryService, memory: AsyncMock
) -> None:
    await service.find_path("s-1", "Checkout", max_steps=4, min_confidence=0.7)

    request = request_of(memory.find_path)
    assert (request.destination, request.max_steps, request.min_confidence) == (
        "Checkout",
        4,
        0.7,
    )


async def test_find_path_considers_everything_known_by_default(
    service: NavigationMemoryService, memory: AsyncMock
) -> None:
    """A shaky route is still better information than none."""
    await service.find_path("s-1", "Checkout")

    assert request_of(memory.find_path).min_confidence == 0.0


async def test_search_screens_defaults_to_listing_everything(
    service: NavigationMemoryService, memory: AsyncMock
) -> None:
    await service.search_screens("com.example.shop")

    assert request_of(memory.search_screens).query == ""


async def test_get_run_passes_its_limit(
    service: NavigationMemoryService, memory: AsyncMock
) -> None:
    await service.get_run(7, limit=10)

    request = request_of(memory.get_run)
    assert (request.run_id, request.limit) == (7, 10)


# -- forgetting ----------------------------------------------------------------


async def test_forget_forwards_each_selector(
    service: NavigationMemoryService, maintenance: AsyncMock
) -> None:
    moment = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)

    await service.forget(package="com.example.shop", run_id=3, before=moment)

    request = request_of(maintenance.forget)
    assert (request.package, request.run_id, request.before) == ("com.example.shop", 3, moment)


async def test_forget_selects_nothing_by_default(
    service: NavigationMemoryService, maintenance: AsyncMock
) -> None:
    """The service builds the request; refusing it is the adapter's job, and this
    is what proves the service does not quietly fill a selector in."""
    await service.forget()

    assert request_of(maintenance.forget).selects_nothing


# -- recording -----------------------------------------------------------------


async def test_start_run_builds_the_request_from_what_a_caller_has(
    service: NavigationMemoryService, recorder: AsyncMock
) -> None:
    await service.start_run(
        "emulator-5554", "com.example.shop", app_version="1.2.0", session_id="s-1"
    )

    request = request_of(recorder.start_run)
    assert (request.device_id, request.package, request.session_id) == (
        "emulator-5554",
        "com.example.shop",
        "s-1",
    )


async def test_record_interaction_defaults_to_success_with_no_capture(
    service: NavigationMemoryService, recorder: AsyncMock
) -> None:
    await service.record_interaction(7, "tap")

    request = request_of(recorder.record_interaction)
    assert request.ok is True
    assert request.snapshot_after is None


async def test_record_interaction_forwards_a_failure(
    service: NavigationMemoryService, recorder: AsyncMock
) -> None:
    snapshot = ScreenSnapshot(package="com.example.shop")

    await service.record_interaction(
        7, "tap", target="id=x", snapshot_after=snapshot, ok=False, detail="gone"
    )

    request = request_of(recorder.record_interaction)
    assert request.ok is False
    assert request.detail == "gone"
    assert request.snapshot_after is snapshot


async def test_end_run_passes_the_id(service: NavigationMemoryService, recorder: AsyncMock) -> None:
    await service.end_run(7)

    assert request_of(recorder.end_run).run_id == 7


# -- the layering itself --------------------------------------------------------


async def test_the_service_holds_only_the_authority_each_port_grants(
    service: NavigationMemoryService, memory: AsyncMock, maintenance: AsyncMock
) -> None:
    """ISP: reading the map must not be able to write to it or delete it.

    ``spec=RouteMemory`` is the assertion -- if the service ever reached for
    ``forget`` on the read port, the mock would raise.
    """
    await service.where_am_i("s-1")

    assert not hasattr(memory, "forget")
    assert not hasattr(memory, "record_interaction")
    assert hasattr(maintenance, "forget")


async def test_domain_errors_propagate_untouched(
    service: NavigationMemoryService, memory: AsyncMock
) -> None:
    """The service adds no error handling of its own; a port's failure is the
    product's failure, already in the domain's vocabulary."""
    memory.where_am_i.side_effect = ScreenNotRecognised("com.example.shop")

    with pytest.raises(ScreenNotRecognised):
        await service.where_am_i("s-1")


async def test_every_operation_awaits_its_port_exactly_once(
    service: NavigationMemoryService, memory: AsyncMock
) -> None:
    """No read-back, no verification pass: one intent, one port call."""
    await service.where_am_i("s-1")

    memory.where_am_i.assert_awaited_once()
    memory.find_path.assert_not_awaited()
