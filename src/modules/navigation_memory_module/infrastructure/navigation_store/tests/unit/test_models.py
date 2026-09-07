"""The value objects that describe the database, not what is stored in it."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from modules.navigation_memory_module.infrastructure.navigation_store.models import (
    MEMORY_URL,
    DatabaseLocation,
    SchemaState,
)

pytestmark = pytest.mark.unit

FILE = DatabaseLocation(url="sqlite+aiosqlite:////data/nav.db", path=Path("/data/nav.db"))
MEMORY = DatabaseLocation(url=MEMORY_URL, path=None)


def test_a_file_location_knows_it_will_survive_the_process() -> None:
    assert FILE.in_memory is False
    assert FILE.directory == Path("/data")


def test_an_in_memory_location_has_no_file_and_no_directory() -> None:
    """Nothing to create, nothing to check permissions on -- the engine skips both."""
    assert MEMORY.in_memory is True
    assert MEMORY.directory is None
    assert MEMORY.path is None


def test_a_location_says_where_it_is_when_an_error_names_it() -> None:
    """These strings end up in exception messages, so they are asserted here."""
    assert str(FILE) == "/data/nav.db"
    assert str(MEMORY) == "an in-memory database"


@pytest.mark.parametrize("location", [FILE, MEMORY])
def test_a_location_is_frozen(location: DatabaseLocation) -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        location.url = "sqlite://"  # type: ignore[misc]


def _state(**overrides: object) -> SchemaState:
    base = {
        "location": FILE,
        "found_version": 1,
        "expected_version": 1,
        "created": False,
        "tables": ("run", "schema_meta", "screen", "step", "transition"),
    }
    return SchemaState(**{**base, **overrides})  # type: ignore[arg-type]


def test_a_matching_version_is_a_file_this_build_can_use() -> None:
    assert _state().matches is True


@pytest.mark.parametrize("found", [None, 0, 2, 99])
def test_any_other_version_does_not_match(found: int | None) -> None:
    """`None` included: a file with tables but no version row cannot be vouched for."""
    assert _state(found_version=found).matches is False


def test_a_freshly_created_database_reports_itself_as_empty() -> None:
    """The caller decides whether that is a first run or a deleted memory."""
    assert _state(created=True).empty is True
    assert _state(created=False).empty is False


def test_the_state_carries_the_tables_it_found() -> None:
    """So a half-created file is visible in the return value, not just in a failure."""
    assert "screen" in _state().tables
    assert isinstance(_state().tables, tuple)
