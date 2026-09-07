"""The failure vocabulary: its attributes, its messages, and its round trip.

Rule 0 §3 (L) is the load-bearing test here. These classes gain a second base --
a domain error -- when the domain layer lands, and an error that formats its
message into `super().__init__` instead of passing its real arguments stops
being reconstructable at exactly that moment.
"""

from __future__ import annotations

import copy
import inspect
import pickle

import pytest

from app_automating.modules.navigation_memory_module.infrastructure.navigation_store import (
    errors as module,
)
from app_automating.modules.navigation_memory_module.infrastructure.navigation_store.errors import (
    ConstraintViolated,
    DatabaseLocked,
    DatabaseOperationFailed,
    DatabaseUnavailable,
    InvalidDatabasePath,
    NavigationStoreError,
    SchemaCreateFailed,
    SchemaVersionMismatch,
)

pytestmark = pytest.mark.unit

ALL_ERRORS = (
    DatabaseUnavailable,
    InvalidDatabasePath,
    SchemaVersionMismatch,
    SchemaCreateFailed,
    DatabaseLocked,
    ConstraintViolated,
    DatabaseOperationFailed,
)

SAMPLES = (
    DatabaseUnavailable("/data/nav.db", "permission denied"),
    InvalidDatabasePath("/data", "the path is an existing directory"),
    SchemaVersionMismatch("/data/nav.db", 99, 1),
    SchemaVersionMismatch("/data/nav.db", None, 1),
    SchemaCreateFailed("/data/nav.db", "disk I/O error"),
    DatabaseLocked("/data/nav.db", 10.0, "database is locked"),
    ConstraintViolated("unique", "UNIQUE constraint failed: screen.package", "/data/nav.db"),
    DatabaseOperationFailed("writing a step", "/data/nav.db", "no such column"),
)


@pytest.mark.parametrize("error_type", ALL_ERRORS, ids=lambda c: c.__name__)
def test_every_error_is_one_of_this_components_errors(error_type: type) -> None:
    """A caller catches the base and is guaranteed to have caught everything."""
    assert issubclass(error_type, NavigationStoreError)


def test_the_exported_surface_is_the_whole_hierarchy() -> None:
    """An error class that exists but is not exported cannot be caught by name."""
    defined = {
        value.__name__
        for value in vars(module).values()
        if inspect.isclass(value)
        and issubclass(value, BaseException)
        and value.__module__ == module.__name__
    }
    assert defined == set(module.__all__) - {"CONSTRAINT_KINDS"}


@pytest.mark.parametrize("error", SAMPLES, ids=lambda e: type(e).__name__)
def test_an_error_reconstructs_from_its_own_arguments(error: Exception) -> None:
    """Rule 0 §3 (L): `type(exc)(*exc.args)` must rebuild it, identically."""
    rebuilt = type(error)(*error.args)

    assert rebuilt.args == error.args
    assert str(rebuilt) == str(error)


@pytest.mark.parametrize("error", SAMPLES, ids=lambda e: type(e).__name__)
def test_an_error_survives_pickling_and_copying(error: Exception) -> None:
    """What "crosses a process boundary" actually means in practice."""
    assert str(pickle.loads(pickle.dumps(error))) == str(error)
    assert str(copy.copy(error)) == str(error)
    assert str(copy.deepcopy(error)) == str(error)


@pytest.mark.parametrize("error", SAMPLES, ids=lambda e: type(e).__name__)
def test_the_evidence_is_on_the_exception_not_only_in_the_message(error: Exception) -> None:
    """Rule 1 §3: a caller branches on attributes, never on `str(exc)`."""
    assert vars(error), f"{type(error).__name__} carries nothing a caller can read"


def test_a_version_mismatch_reports_both_numbers_and_the_remedy() -> None:
    """The whole point of failing at startup: the developer can act on it."""
    error = SchemaVersionMismatch("/data/nav.db", 99, 1)

    assert error.found == 99
    assert error.expected == 1
    assert "99" in str(error) and "version 1" in str(error)
    assert "delete the file" in str(error)


def test_a_file_with_no_version_row_says_so_rather_than_showing_none() -> None:
    assert "no version at all" in str(SchemaVersionMismatch("/data/nav.db", None, 1))


def test_a_lock_reports_how_long_it_waited() -> None:
    """So a caller can tell "raise the timeout" from "something is deadlocked"."""
    error = DatabaseLocked("/data/nav.db", 10.0, "database is locked")

    assert error.timeout_seconds == 10.0
    assert "10.0s" in str(error)


def test_a_constraint_violation_names_the_kind_and_the_columns() -> None:
    error = ConstraintViolated("unique", "UNIQUE constraint failed: screen.package", "/data/nav.db")

    assert error.kind == "unique"
    assert "unique constraint" in str(error)
    assert "screen.package" in str(error)


@pytest.mark.parametrize("kind", module.CONSTRAINT_KINDS)
def test_every_constraint_kind_produces_a_readable_message(kind: str) -> None:
    assert kind in str(ConstraintViolated(kind))


@pytest.mark.parametrize("error", SAMPLES, ids=lambda e: type(e).__name__)
def test_no_message_is_a_bare_repr_or_a_stack_trace(error: Exception) -> None:
    text = str(error)

    assert text and not text.startswith("<")
    assert "Traceback" not in text
    assert "\n" not in text, "a one-line message; the evidence is on the attributes"


@pytest.mark.parametrize(
    "error_type", [DatabaseUnavailable, SchemaCreateFailed, ConstraintViolated, InvalidDatabasePath]
)
def test_the_optional_detail_may_be_omitted(error_type: type) -> None:
    """A failure with no extra detail must still format cleanly, with no dangling colon."""
    text = str(error_type("something"))

    assert not text.rstrip().endswith(":")
