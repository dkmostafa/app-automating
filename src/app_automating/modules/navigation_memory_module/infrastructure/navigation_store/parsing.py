"""Pure functions: caller input and driver output become models or errors.

Rule 1 §1: this file stays pure. No engine, no connection, no filesystem, no
clock. That purity is what lets the interesting decisions in this component --
which PRAGMAs a connection gets, and what a raw SQLite message actually means --
be unit-tested on a host with no database file in existence, and it is why every
function below takes plain scalars rather than a
:class:`~.config.NavigationStoreConfig`: ``parsing`` sits on the ``models ->
errors -> parsing`` branch of the dependency order and never reaches across to
``config``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .errors import (
    CONSTRAINT_KINDS,
    ConstraintViolated,
    DatabaseLocked,
    DatabaseOperationFailed,
    DatabaseUnavailable,
    InvalidDatabasePath,
    NavigationStoreError,
)

__all__ = [
    "FAILURE_SIGNATURES",
    "constraint_kind",
    "JOURNAL_MODES",
    "SYNCHRONOUS_MODES",
    "classify_database_failure",
    "pragma_statements",
    "parse_schema_version",
    "validate_database_path",
]

#: The journal modes SQLite accepts. Validated rather than interpolated blind:
#: a PRAGMA value cannot be a bound parameter, so it goes into the statement as
#: text and an unchecked config value would be a SQL injection with extra steps.
JOURNAL_MODES = ("DELETE", "TRUNCATE", "PERSIST", "MEMORY", "WAL", "OFF")

#: Likewise for ``PRAGMA synchronous``.
SYNCHRONOUS_MODES = ("OFF", "NORMAL", "FULL", "EXTRA")


def constraint_kind(detail: str) -> str:
    """Which kind of constraint a SQLite integrity message is about.

    SQLite spells these as ``UNIQUE constraint failed: screen.package, ...`` and
    ``FOREIGN KEY constraint failed``. The kind is the part a caller branches
    on, so it is lifted out of the message here rather than left for every call
    site to re-discover with its own substring check.
    """
    haystack = detail.lower()
    for kind in CONSTRAINT_KINDS:
        if kind == "unknown":
            continue
        if f"{kind} constraint" in haystack:
            return kind
    return "unknown"


def _locked(location: str, detail: str, busy_timeout_seconds: float) -> NavigationStoreError:
    return DatabaseLocked(location, busy_timeout_seconds, detail)


def _constraint(location: str, detail: str, busy_timeout_seconds: float) -> NavigationStoreError:
    return ConstraintViolated(constraint_kind(detail), detail, location)


def _unavailable(location: str, detail: str, busy_timeout_seconds: float) -> NavigationStoreError:
    return DatabaseUnavailable(location, detail)


#: Rule 0 §3 (O): the raw-message-to-typed-error mapping is a table, so a newly
#: recognised failure is a row here rather than another branch in a method.
#: Order matters -- the first matching row wins, so the specific and retryable
#: lock case is checked before the general "cannot open" family.
FAILURE_SIGNATURES: tuple[
    tuple[tuple[str, ...], Callable[[str, str, float], NavigationStoreError]], ...
] = (
    (
        ("database is locked", "database table is locked", "database schema is locked"),
        _locked,
    ),
    (
        ("constraint failed",),
        _constraint,
    ),
    (
        (
            "unable to open database file",
            "no such file or directory",
            "file is not a database",
            "file is encrypted",
            "attempt to write a readonly database",
            "readonly database",
            "disk i/o error",
            "database or disk is full",
            "permission denied",
        ),
        _unavailable,
    ),
)


def classify_database_failure(
    operation: str,
    location: str,
    detail: str,
    busy_timeout_seconds: float,
) -> NavigationStoreError:
    """Turn a driver's message into the most specific error that fits.

    Falls back to :class:`~.errors.DatabaseOperationFailed`, which is the honest
    answer for an unclassified failure rather than the default one: a message
    that keeps landing here and has a remedy of its own has earned a row in
    :data:`FAILURE_SIGNATURES`.
    """
    haystack = detail.lower()
    for needles, build in FAILURE_SIGNATURES:
        if any(needle in haystack for needle in needles):
            return build(location, detail, busy_timeout_seconds)
    return DatabaseOperationFailed(operation, location, detail)


def pragma_statements(
    *,
    enforce_foreign_keys: bool,
    journal_mode: str,
    synchronous: str,
    busy_timeout_milliseconds: int,
    in_memory: bool,
) -> tuple[str, ...]:
    """The PRAGMAs every new connection runs, in the order they must run.

    SQLite applies most of these **per connection**, not per file, which is the
    trap this function exists to avoid: ``foreign_keys`` defaults to *off* on
    every fresh connection, so without this the ``ON DELETE CASCADE`` clauses in
    the schema are decoration and orphan rows accumulate silently.

    ``journal_mode`` is skipped for an in-memory database, where SQLite ignores
    it and reports back ``memory`` regardless -- issuing it would just produce a
    result row nobody reads. An unrecognised mode falls back to the safe default
    rather than being interpolated into SQL, since a PRAGMA value cannot be
    bound as a parameter.
    """
    statements: list[str] = [f"PRAGMA busy_timeout = {max(0, int(busy_timeout_milliseconds))}"]
    statements.append(f"PRAGMA foreign_keys = {'ON' if enforce_foreign_keys else 'OFF'}")

    if not in_memory:
        mode = journal_mode.strip().upper()
        statements.append(f"PRAGMA journal_mode = {mode if mode in JOURNAL_MODES else 'WAL'}")

    level = synchronous.strip().upper()
    statements.append(f"PRAGMA synchronous = {level if level in SYNCHRONOUS_MODES else 'NORMAL'}")
    return tuple(statements)


def parse_schema_version(raw: str | None) -> int | None:
    """The version a file claims, or ``None`` when it claims nothing usable.

    Garbage is deliberately flattened to ``None`` rather than raised on: the
    caller's next move is the same either way -- refuse to run against a file
    this build cannot vouch for -- and a :class:`ValueError` escaping from here
    would be a foreign exception crossing the component boundary (Rule 1 §3).
    """
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except (ValueError, AttributeError):
        return None


def validate_database_path(path: Path) -> None:
    """Reject a path that cannot be a database file, before an engine exists.

    Rule 1 §3, "validate before you spawn": these are the checks that need no
    I/O, so they happen in-process and cost nothing. Whether the path is
    *already* a directory is a filesystem question and belongs to the caller
    that is about to touch the disk anyway.
    """
    text = str(path).strip()
    if not text or text in (".", ".."):
        raise InvalidDatabasePath(str(path), "the path is empty")
    if not path.name:
        raise InvalidDatabasePath(str(path), "the path names a directory, not a file")
    if path.name in (".", ".."):
        raise InvalidDatabasePath(str(path), "the path names a directory, not a file")
