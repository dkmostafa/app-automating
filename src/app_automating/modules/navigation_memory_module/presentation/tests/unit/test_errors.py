"""The domain-error to ToolError translation table."""

from __future__ import annotations

import inspect

import pytest
from fastmcp.exceptions import ToolError

from app_automating.modules.navigation_memory_module.domain import errors as domain_errors
from app_automating.modules.navigation_memory_module.domain.errors import (
    DestinationNotFound,
    NavigationMemoryError,
    NoKnownRoute,
    NoRunForSession,
    RunNotFound,
    ScreenNotRecognised,
)
from app_automating.modules.navigation_memory_module.presentation.errors import (
    REMEDIES,
    as_tool_error,
    domain_errors_as_tool_errors,
)

pytestmark = pytest.mark.unit


def test_every_domain_error_has_a_row() -> None:
    """A new domain error must not fall off the table into the fallback silently."""
    tabled = {error_type for error_type, _ in REMEDIES}
    defined = {
        value
        for value in vars(domain_errors).values()
        if inspect.isclass(value)
        and issubclass(value, NavigationMemoryError)
        and value.__module__ == domain_errors.__name__
    }
    missing = {error for error in defined if not any(issubclass(error, t) for t in tabled)}
    assert not missing, f"no remedy covers {sorted(e.__name__ for e in missing)}"


def test_the_fallback_is_last_and_appears_once() -> None:
    assert REMEDIES[-1][0] is NavigationMemoryError
    assert [t for t, _ in REMEDIES[:-1] if t is NavigationMemoryError] == []


def test_a_subclass_never_sits_below_its_base() -> None:
    """Order is what makes the first-match-wins lookup correct.

    A base listed above one of its own subclasses would swallow it: the lookup
    returns the first row that matches by ``isinstance``, so the specific
    remedy below it could never be reached. The reverse -- a subclass above its
    base -- is the arrangement this table is built on, ``NavigationMemoryError``
    last of all.
    """
    for index, (error_type, _) in enumerate(REMEDIES):
        for later, _ in REMEDIES[index + 1 :]:
            assert not issubclass(later, error_type) or error_type is later, (
                f"{error_type.__name__} is a base of {later.__name__} but appears above it, "
                f"so {later.__name__}'s remedy is unreachable"
            )


@pytest.mark.parametrize("error_type, remedy", REMEDIES, ids=lambda v: getattr(v, "__name__", ""))
def test_every_remedy_tells_the_caller_what_to_do(error_type: type, remedy: str) -> None:
    assert len(remedy) > 40, f"{error_type.__name__}'s remedy is too thin to act on"
    assert remedy.strip().endswith(".")


def test_the_message_names_the_error_and_carries_its_evidence() -> None:
    """The class name is the stable handle a caller branches on; the prose is not."""
    error = as_tool_error(RunNotFound(7))

    assert "RunNotFound" in str(error)
    assert "7" in str(error)


def test_a_new_screen_is_not_reported_as_a_dead_end() -> None:
    """The failure mode this row exists to prevent: a caller reading "not
    recognised" as an error and giving up instead of exploring."""
    message = str(as_tool_error(ScreenNotRecognised("com.example.shop")))

    assert "Explore" in message or "explore" in message
    assert "not an error" in message


def test_no_known_route_points_at_the_knobs_that_might_help() -> None:
    message = str(as_tool_error(NoKnownRoute("Home", "Checkout", 12)))

    assert "max_steps" in message
    assert "min_confidence" in message


def test_a_missing_destination_names_the_tool_that_lists_them() -> None:
    message = str(as_tool_error(DestinationNotFound("Nowhere", "com.example.shop")))

    assert "navigation_memory_search_screens" in message


def test_a_missing_session_names_the_tool_that_lists_them() -> None:
    assert "appium_get_open_sessions" in str(as_tool_error(NoRunForSession("s-1")))


def test_the_context_manager_translates_a_domain_error() -> None:
    with pytest.raises(ToolError):
        with domain_errors_as_tool_errors():
            raise RunNotFound(7)


def test_the_context_manager_lets_a_real_bug_through() -> None:
    """An unexpected exception is a bug in this server, not a fact about the
    memory, and dressing it up as a tool result would hide it."""
    with pytest.raises(ZeroDivisionError):
        with domain_errors_as_tool_errors():
            raise ZeroDivisionError

    with domain_errors_as_tool_errors():
        pass


def test_no_message_is_a_bare_str_of_the_exception() -> None:
    """Rule 3 §6: every message ends in a remedy."""
    error = as_tool_error(RunNotFound(7))

    assert str(error) != str(RunNotFound(7))
    assert len(str(error)) > len(str(RunNotFound(7))) + 40
