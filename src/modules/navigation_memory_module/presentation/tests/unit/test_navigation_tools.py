"""The MCP surface, against a mocked service.

Rule 2 §3: the service is a mock, and that is the point of this layer's tests --
what needs asserting is that each tool calls the method it claims to, with the
arguments the caller gave, and renders the payload its docstring advertises.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from modules.navigation_memory_module.application.services import NavigationMemoryService
from modules.navigation_memory_module.domain.errors import (
    DestinationNotFound,
    NoRunForSession,
    ScreenNotRecognised,
)
from modules.navigation_memory_module.domain.models import (
    FindPathResult,
    ForgetResult,
    GetRunResult,
    JournalEntry,
    KnownScreen,
    PathStep,
    RouteOption,
    RunSummary,
    ScreenIdentity,
    SearchScreensResult,
    WhereAmIResult,
)
from modules.navigation_memory_module.presentation import register_navigation_tools

pytestmark = pytest.mark.unit

NOW = dt.datetime(2026, 9, 4, 10, 0, tzinfo=dt.UTC)
PKG = "com.example.shop"

#: Named rather than derived, so growing the MCP surface is a deliberate edit.
TOOL_NAMES = (
    "navigation_memory_where_am_i",
    "navigation_memory_find_path",
    "navigation_memory_search_screens",
    "navigation_memory_get_run",
    "navigation_memory_forget",
)


def screen(screen_id: int = 4, title: str = "Cart") -> KnownScreen:
    return KnownScreen(
        screen_id=screen_id,
        identity=ScreenIdentity(package=PKG, activity=".MainActivity", fingerprint="ab" * 16),
        title=title,
        visit_count=12,
        first_seen_at=NOW,
        last_seen_at=NOW,
    )


def run() -> RunSummary:
    return RunSummary(
        run_id=7,
        device_id="emulator-5554",
        package=PKG,
        started_at=NOW,
        session_id="abc-123",
        step_count=12,
    )


@pytest.fixture
def service() -> AsyncMock:
    """A service that answers every call with a plausible domain result."""
    mock = AsyncMock(spec=NavigationMemoryService)
    mock.where_am_i.return_value = WhereAmIResult(
        run=run(),
        screen=screen(),
        routes=(
            RouteOption(
                action="tap_element",
                target="id=checkout",
                to_screen=screen(9, "Checkout"),
                traversal_count=16,
                success_count=15,
            ),
        ),
    )
    mock.find_path.return_value = FindPathResult(
        origin=screen(1, "Home"),
        destination=screen(9, "Checkout"),
        steps=(PathStep("tap_element", "id=cart", screen(1, "Home"), screen(9, "Checkout"), 0.94),),
    )
    mock.search_screens.return_value = SearchScreensResult(
        package=PKG, screens=(screen(9, "Checkout"),)
    )
    mock.get_run.return_value = GetRunResult(
        run=run(),
        entries=(
            JournalEntry(seq=1, action="observe", target="", at=NOW, to_screen=screen(1, "Home")),
        ),
    )
    mock.forget.return_value = ForgetResult(runs_deleted=3, steps_deleted=412)
    return mock


@pytest.fixture
def mcp(service: AsyncMock) -> FastMCP:
    server = FastMCP("test")
    register_navigation_tools(server, service)
    return server


# -- registration --------------------------------------------------------------


async def test_the_registered_tools_are_exactly_this_surface(mcp: FastMCP) -> None:
    assert {tool.name for tool in await mcp.list_tools()} == set(TOOL_NAMES)


async def test_every_tool_name_carries_the_module_prefix(mcp: FastMCP) -> None:
    """Rule 3 §3: the next module must not be able to collide with this one."""
    for tool in await mcp.list_tools():
        assert tool.name.startswith("navigation_"), tool.name


async def test_every_tool_publishes_an_output_schema(mcp: FastMCP) -> None:
    for tool in await mcp.list_tools():
        assert tool.output_schema, f"{tool.name} returns an undeclared shape"


def test_registering_the_surface_twice_is_free_of_side_effects(service: AsyncMock) -> None:
    """Rule 3 §2: the register function builds nothing and keeps no state."""
    register_navigation_tools(FastMCP("a"), service)
    register_navigation_tools(FastMCP("b"), service)

    assert not service.method_calls


async def test_the_surface_is_read_only_apart_from_forget(mcp: FastMCP) -> None:
    """Recording is pushed by appium_module; no tool here may write to the map."""
    names = {tool.name for tool in await mcp.list_tools()}

    assert not {n for n in names if "record" in n or "start_run" in n}


# -- dispatch --------------------------------------------------------------------


async def test_where_am_i_renders_the_payload_its_docstring_advertises(
    mcp: FastMCP, service: AsyncMock
) -> None:
    result = await mcp.call_tool("navigation_memory_where_am_i", {"session_id": "abc-123"})

    service.where_am_i.assert_awaited_once_with("abc-123", limit=20)
    content = result.structured_content
    assert content["run_id"] == 7
    assert content["package"] == PKG
    assert content["screen"]["label"] == "Cart"
    assert content["routes"][0]["target"] == "id=checkout"
    assert content["routes"][0]["confidence"] == 0.89
    assert content["unexplored"] is False


async def test_where_am_i_forwards_its_limit(mcp: FastMCP, service: AsyncMock) -> None:
    await mcp.call_tool("navigation_memory_where_am_i", {"session_id": "abc-123", "limit": 3})

    service.where_am_i.assert_awaited_once_with("abc-123", limit=3)


async def test_find_path_forwards_every_knob(mcp: FastMCP, service: AsyncMock) -> None:
    await mcp.call_tool(
        "navigation_memory_find_path",
        {
            "session_id": "abc-123",
            "destination": "Checkout",
            "max_steps": 4,
            "min_confidence": 0.7,
        },
    )

    service.find_path.assert_awaited_once_with(
        "abc-123", "Checkout", max_steps=4, min_confidence=0.7
    )


async def test_find_path_returns_actions_in_execution_order(
    mcp: FastMCP, service: AsyncMock
) -> None:
    result = await mcp.call_tool(
        "navigation_memory_find_path", {"session_id": "abc-123", "destination": "Checkout"}
    )

    content = result.structured_content
    assert content["steps"][0]["action"] == "tap_element"
    assert content["steps"][0]["target"] == "id=cart"
    assert content["confidence"] == 0.94
    assert content["already_there"] is False


async def test_search_screens_defaults_to_listing_everything(
    mcp: FastMCP, service: AsyncMock
) -> None:
    result = await mcp.call_tool("navigation_memory_search_screens", {"package": PKG})

    service.search_screens.assert_awaited_once_with(PKG, query="", limit=50)
    assert result.structured_content["count"] == 1


async def test_get_run_renders_the_journal(mcp: FastMCP, service: AsyncMock) -> None:
    result = await mcp.call_tool("navigation_memory_get_run", {"run_id": 7})

    service.get_run.assert_awaited_once_with(7, limit=200)
    content = result.structured_content
    assert content["run"]["open"] is True
    assert content["entries"][0]["action"] == "observe"
    assert content["failure_count"] == 0


# -- forget, the one destructive tool ---------------------------------------------


async def test_forget_passes_a_package_through(mcp: FastMCP, service: AsyncMock) -> None:
    result = await mcp.call_tool("navigation_memory_forget", {"package": PKG})

    service.forget.assert_awaited_once_with(package=PKG, run_id=None, before=None)
    assert result.structured_content["total"] == 415


async def test_forget_parses_an_iso_timestamp(mcp: FastMCP, service: AsyncMock) -> None:
    await mcp.call_tool("navigation_memory_forget", {"before": "2026-01-01T00:00:00+00:00"})

    assert service.forget.await_args.kwargs["before"] == dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


async def test_a_naive_timestamp_is_read_as_utc(mcp: FastMCP, service: AsyncMock) -> None:
    """The memory stores UTC; guessing a local zone here would silently shift the
    cut-off by hours."""
    await mcp.call_tool("navigation_memory_forget", {"before": "2026-01-01T00:00:00"})

    assert service.forget.await_args.kwargs["before"].tzinfo is dt.UTC


async def test_a_malformed_timestamp_is_rejected_before_anything_is_deleted(
    mcp: FastMCP, service: AsyncMock
) -> None:
    with pytest.raises(ToolError) as excinfo:
        await mcp.call_tool("navigation_memory_forget", {"before": "last tuesday"})

    assert "InvalidNavigationRequest" in str(excinfo.value)
    service.forget.assert_not_awaited()


async def test_forget_with_no_selector_still_reaches_the_service_to_be_refused(
    mcp: FastMCP, service: AsyncMock
) -> None:
    """Presentation does not second-guess the rule; the adapter owns it, and this
    is what proves the tool does not quietly fill a selector in."""
    await mcp.call_tool("navigation_memory_forget", {})

    service.forget.assert_awaited_once_with(package=None, run_id=None, before=None)


# -- failures ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ScreenNotRecognised(PKG), "ScreenNotRecognised"),
        (NoRunForSession("abc-123"), "NoRunForSession"),
    ],
)
async def test_a_domain_failure_reaches_the_caller_with_a_remedy(
    mcp: FastMCP, service: AsyncMock, error: Exception, expected: str
) -> None:
    service.where_am_i.side_effect = error

    with pytest.raises(ToolError) as excinfo:
        await mcp.call_tool("navigation_memory_where_am_i", {"session_id": "abc-123"})

    message = str(excinfo.value)
    assert expected in message
    assert len(message) > len(str(error)) + 40, "the remedy is missing"


async def test_a_missing_destination_names_the_tool_that_lists_them(
    mcp: FastMCP, service: AsyncMock
) -> None:
    service.find_path.side_effect = DestinationNotFound("Nowhere", PKG, ("Home", "Cart"))

    with pytest.raises(ToolError) as excinfo:
        await mcp.call_tool(
            "navigation_memory_find_path", {"session_id": "abc-123", "destination": "Nowhere"}
        )

    assert "navigation_memory_search_screens" in str(excinfo.value)


async def test_an_unexpected_exception_is_not_dressed_up_as_a_result(
    mcp: FastMCP, service: AsyncMock
) -> None:
    """A bug in this server is a bug, not a fact about the memory."""
    service.get_run.side_effect = ZeroDivisionError("a real bug")

    with pytest.raises(Exception) as excinfo:
        await mcp.call_tool("navigation_memory_get_run", {"run_id": 7})

    assert "NavigationMemoryError" not in str(excinfo.value)
