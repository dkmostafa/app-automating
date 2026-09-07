"""The failure vocabulary: attributes, messages, and the round trip."""

from __future__ import annotations

import copy
import inspect
import pickle

import pytest

from app_automating.modules.navigation_memory_module.domain import errors as module
from app_automating.modules.navigation_memory_module.domain.errors import (
    DestinationNotFound,
    InvalidNavigationRequest,
    MemoryFailure,
    MemoryUnavailable,
    NavigationMemoryError,
    NoKnownRoute,
    NoRunForSession,
    RunAlreadyEnded,
    RunNotFound,
    ScreenNotFound,
    ScreenNotRecognised,
)

pytestmark = pytest.mark.unit

SAMPLES = (
    MemoryUnavailable("the data directory", "permission denied"),
    MemoryFailure("recording a step", "database is locked"),
    InvalidNavigationRequest("forget", "no selector given"),
    RunNotFound(7),
    RunAlreadyEnded(7),
    NoRunForSession("sess-1"),
    ScreenNotFound(4),
    ScreenNotRecognised("com.example.shop", "ab" * 16),
    NoKnownRoute("Home", "Checkout", 12),
    DestinationNotFound("Nowhere", "com.example.shop", ("Home", "Cart")),
)


@pytest.mark.parametrize("error", SAMPLES, ids=lambda e: type(e).__name__)
def test_every_error_is_one_of_this_modules_errors(error: Exception) -> None:
    """A caller catches the base and is guaranteed to have caught everything."""
    assert isinstance(error, NavigationMemoryError)


def test_the_exported_surface_is_the_whole_hierarchy() -> None:
    defined = {
        value.__name__
        for value in vars(module).values()
        if inspect.isclass(value)
        and issubclass(value, BaseException)
        and value.__module__ == module.__name__
    }
    assert defined == set(module.__all__)


@pytest.mark.parametrize("error", SAMPLES, ids=lambda e: type(e).__name__)
def test_an_error_reconstructs_from_its_own_arguments(error: Exception) -> None:
    """Rule 0 §3 (L). These gain a second base in the adapter, which is exactly
    where a co-operative ``super().__init__`` creeping back in would break."""
    rebuilt = type(error)(*error.args)

    assert rebuilt.args == error.args
    assert str(rebuilt) == str(error)


@pytest.mark.parametrize("error", SAMPLES, ids=lambda e: type(e).__name__)
def test_an_error_survives_pickling_and_copying(error: Exception) -> None:
    assert str(pickle.loads(pickle.dumps(error))) == str(error)
    assert str(copy.deepcopy(error)) == str(error)


@pytest.mark.parametrize("error", SAMPLES, ids=lambda e: type(e).__name__)
def test_the_evidence_is_on_the_exception_not_only_in_the_message(error: Exception) -> None:
    assert vars(error), f"{type(error).__name__} carries nothing a caller can read"


@pytest.mark.parametrize("error", SAMPLES, ids=lambda e: type(e).__name__)
def test_no_message_is_a_bare_repr_or_a_stack_trace(error: Exception) -> None:
    text = str(error)

    assert text and not text.startswith("<")
    assert "Traceback" not in text
    assert "\n" not in text


def test_a_new_screen_is_reported_as_a_fact_rather_than_a_fault() -> None:
    """The wording matters: a caller that reads this as an error gives up, and
    exploring is the only way the map ever gets built."""
    message = str(ScreenNotRecognised("com.example.shop", "ab" * 16))

    assert "has not been seen before" in message


def test_a_missing_destination_offers_what_is_known() -> None:
    """So the caller's next call can succeed rather than guess again."""
    message = str(DestinationNotFound("Nowhere", "com.example.shop", ("Home", "Cart")))

    assert "Home" in message and "Cart" in message


def test_no_known_route_says_how_far_it_looked() -> None:
    assert "12" in str(NoKnownRoute("Home", "Checkout", 12))


@pytest.mark.parametrize("error_type", [MemoryUnavailable, MemoryFailure, InvalidNavigationRequest])
def test_the_optional_detail_may_be_omitted(error_type: type) -> None:
    """A failure with no extra detail must still read cleanly, with no dangling colon."""
    assert not str(error_type("something")).rstrip().endswith(":")
