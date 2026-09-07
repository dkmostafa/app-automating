"""The pure decisions: which PRAGMAs, and what a raw SQLite message means.

Unit tests because ``parsing.py`` is pure (Rule 2 §2) -- no engine, no file, no
clock. Nothing is replaced or patched; there is simply nothing here to replace.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from modules.navigation_memory_module.infrastructure.navigation_store.errors import (
    CONSTRAINT_KINDS,
    ConstraintViolated,
    DatabaseLocked,
    DatabaseOperationFailed,
    DatabaseUnavailable,
    InvalidDatabasePath,
)
from modules.navigation_memory_module.infrastructure.navigation_store.parsing import (
    JOURNAL_MODES,
    SYNCHRONOUS_MODES,
    classify_database_failure,
    constraint_kind,
    parse_schema_version,
    pragma_statements,
    validate_database_path,
)

pytestmark = pytest.mark.unit

DEFAULTS = {
    "enforce_foreign_keys": True,
    "journal_mode": "WAL",
    "synchronous": "NORMAL",
    "busy_timeout_milliseconds": 10_000,
    "in_memory": False,
}


def _pragmas(**overrides: object) -> tuple[str, ...]:
    return pragma_statements(**{**DEFAULTS, **overrides})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# pragma_statements
# ---------------------------------------------------------------------------


def test_foreign_keys_are_turned_on_for_every_connection() -> None:
    """The trap this whole function exists for: SQLite defaults them to OFF.

    Off means every ON DELETE CASCADE in the schema is decoration.
    """
    assert "PRAGMA foreign_keys = ON" in _pragmas()


def test_foreign_keys_can_be_turned_off_deliberately() -> None:
    assert "PRAGMA foreign_keys = OFF" in _pragmas(enforce_foreign_keys=False)


def test_the_busy_timeout_is_set_before_anything_else() -> None:
    """A statement that blocks before the timeout is set would use SQLite's own 0."""
    assert _pragmas()[0] == "PRAGMA busy_timeout = 10000"


def test_a_negative_busy_timeout_becomes_zero_rather_than_nonsense() -> None:
    assert "PRAGMA busy_timeout = 0" in _pragmas(busy_timeout_milliseconds=-5)


def test_a_file_database_gets_wal() -> None:
    assert "PRAGMA journal_mode = WAL" in _pragmas()


def test_an_in_memory_database_is_not_told_about_journal_mode() -> None:
    """SQLite ignores it there and answers `memory`; issuing it is a wasted round trip."""
    assert not any("journal_mode" in statement for statement in _pragmas(in_memory=True))


def test_an_in_memory_database_still_gets_foreign_keys_and_a_timeout() -> None:
    statements = _pragmas(in_memory=True)

    assert any("foreign_keys" in s for s in statements)
    assert any("busy_timeout" in s for s in statements)


@pytest.mark.parametrize("mode", JOURNAL_MODES)
def test_every_recognised_journal_mode_is_passed_through(mode: str) -> None:
    assert f"PRAGMA journal_mode = {mode}" in _pragmas(journal_mode=mode)


@pytest.mark.parametrize("mode", SYNCHRONOUS_MODES)
def test_every_recognised_synchronous_mode_is_passed_through(mode: str) -> None:
    assert f"PRAGMA synchronous = {mode}" in _pragmas(synchronous=mode)


def test_modes_are_case_insensitive_and_trimmed() -> None:
    """A `.env` file is written by a human, and `wal` is what a human writes."""
    statements = _pragmas(journal_mode="  wal  ", synchronous="normal")

    assert "PRAGMA journal_mode = WAL" in statements
    assert "PRAGMA synchronous = NORMAL" in statements


@pytest.mark.parametrize(
    "hostile",
    ["DELETE; DROP TABLE screen", "WAL; ATTACH DATABASE '/etc/passwd' AS x", "'; --", "nonsense"],
)
def test_an_unrecognised_mode_falls_back_instead_of_reaching_the_statement(hostile: str) -> None:
    """A PRAGMA value cannot be a bound parameter, so it is validated instead.

    Without the allow-list this is a config value interpolated straight into SQL.
    """
    statements = _pragmas(journal_mode=hostile, synchronous=hostile)

    assert "PRAGMA journal_mode = WAL" in statements
    assert "PRAGMA synchronous = NORMAL" in statements
    assert not any("DROP" in s or "ATTACH" in s or "--" in s for s in statements)


# ---------------------------------------------------------------------------
# classify_database_failure
# ---------------------------------------------------------------------------


def _classify(detail: str):
    return classify_database_failure("writing a step", "/tmp/nav.db", detail, 10.0)


@pytest.mark.parametrize(
    "detail",
    ["database is locked", "database table is locked", "OperationalError: database is locked"],
)
def test_a_locked_database_is_the_one_failure_worth_retrying(detail: str) -> None:
    error = _classify(detail)

    assert isinstance(error, DatabaseLocked)
    assert error.timeout_seconds == 10.0
    assert error.location == "/tmp/nav.db"


@pytest.mark.parametrize(
    ("detail", "kind"),
    [
        ("UNIQUE constraint failed: screen.package, screen.activity", "unique"),
        ("FOREIGN KEY constraint failed", "foreign key"),
        ("CHECK constraint failed: ck_transition_successes", "check"),
        ("NOT NULL constraint failed: screen.package", "not null"),
        ("PRIMARY KEY constraint failed", "primary key"),
    ],
)
def test_a_rejected_write_is_classified_by_which_constraint_refused_it(
    detail: str, kind: str
) -> None:
    """The caller branches on the kind -- a duplicate screen is a normal outcome."""
    error = _classify(detail)

    assert isinstance(error, ConstraintViolated)
    assert error.kind == kind
    assert error.detail == detail


def test_an_unrecognised_constraint_message_is_still_a_constraint_violation() -> None:
    """Degrading to `unknown` beats degrading to "some SQL thing went wrong"."""
    error = _classify("SOMETHING constraint failed: whatever")

    assert isinstance(error, ConstraintViolated)
    assert error.kind == "unknown"


@pytest.mark.parametrize(
    "detail",
    [
        "unable to open database file",
        "no such file or directory",
        "file is not a database",
        "attempt to write a readonly database",
        "disk I/O error",
        "database or disk is full",
        "permission denied",
    ],
)
def test_a_host_problem_is_reported_as_unavailable(detail: str) -> None:
    error = _classify(detail)

    assert isinstance(error, DatabaseUnavailable)
    assert error.location == "/tmp/nav.db"


def test_an_unclassified_failure_falls_back_honestly() -> None:
    """The fallback is the honest answer, not the default one (Rule 1 §3)."""
    error = _classify("interface error: something entirely new")

    assert isinstance(error, DatabaseOperationFailed)
    assert error.operation == "writing a step"


def test_classification_is_case_insensitive() -> None:
    """Drivers do not agree on case, and neither do SQLite versions."""
    assert isinstance(_classify("DATABASE IS LOCKED"), DatabaseLocked)
    assert isinstance(_classify("Unique Constraint Failed: x.y"), ConstraintViolated)


def test_a_lock_is_matched_before_the_general_family() -> None:
    """Order in the table is load-bearing: only the lock case is worth retrying."""
    assert isinstance(_classify("database is locked; unable to open database file"), DatabaseLocked)


@pytest.mark.parametrize("kind", CONSTRAINT_KINDS)
def test_every_declared_constraint_kind_is_reachable(kind: str) -> None:
    """A kind in the table that nothing can produce is a lie in the vocabulary."""
    if kind == "unknown":
        assert constraint_kind("mystery constraint failed") == "unknown"
    else:
        assert constraint_kind(f"{kind.upper()} constraint failed: x.y") == kind


# ---------------------------------------------------------------------------
# parse_schema_version
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("raw", "expected"), [("1", 1), ("  7  ", 7), ("0", 0), ("-3", -3)])
def test_a_version_row_is_read_as_an_integer(raw: str, expected: int) -> None:
    assert parse_schema_version(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "one", "1.5", "v1", "1;DROP TABLE run"])
def test_an_unusable_version_flattens_to_none_rather_than_raising(raw: str | None) -> None:
    """A ValueError escaping here would be a foreign exception crossing the boundary.

    The caller's next move is the same either way: refuse the file.
    """
    assert parse_schema_version(raw) is None


# ---------------------------------------------------------------------------
# validate_database_path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["/var/lib/nav.db", "nav.db", "~/.local/share/agentic-testing/navigation_memory.db"]
)
def test_a_plausible_file_path_is_accepted(path: str) -> None:
    validate_database_path(Path(path))


@pytest.mark.parametrize("path", ["", ".", "..", "/", "  "])
def test_a_path_that_cannot_name_a_file_is_rejected_before_any_io(path: str) -> None:
    """Rule 1 §3: the checks that need no I/O happen before an engine exists."""
    with pytest.raises(InvalidDatabasePath) as excinfo:
        validate_database_path(Path(path))

    assert excinfo.value.reason
    assert isinstance(excinfo.value.path, str)
