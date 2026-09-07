"""Every way the navigation store can fail, as a typed exception.

Rule 1 §3: no foreign exception escapes this component. ``OSError``,
``PermissionError`` and every ``sqlalchemy.exc.SQLAlchemyError`` are caught at
the boundary and re-raised as one of the classes below, with the evidence on the
exception as attributes rather than buried in a message a caller would have to
parse.

Two conventions hold for every error here, matching the rest of the product:

* **The constructor arguments are the state.** Each ``__init__`` passes its real
  arguments to ``Exception.__init__`` and stores them, so ``type(exc)(*exc.args)``
  reconstructs the exception (Rule 0 §3, L). Formatting lives in ``__str__``,
  never in what is handed to ``super()``.
* **``Exception.__init__`` is called explicitly**, not through ``super()``. These
  classes gain a second base -- a domain error -- when the domain layer lands,
  and a co-operative ``super().__init__`` would then dispatch along the MRO into
  the sibling constructor with the wrong arity.

The failures divide by what the caller should do about them:

* the file cannot be opened at all -- :class:`DatabaseUnavailable`. A host or
  permissions problem; no retry will fix it.
* the file is the wrong shape -- :class:`SchemaVersionMismatch`,
  :class:`SchemaCreateFailed`. Recoverable, but only by a human deciding whether
  the learned history is worth keeping.
* the write contradicted the schema -- :class:`ConstraintViolated`. The most
  common failure on the write path, and the one a caller most often handles
  rather than propagates: a screen that already exists is a *normal* outcome of
  recording navigation, not an error to report to a user.
* the database is busy -- :class:`DatabaseLocked`. Worth retrying, and the only
  other one that is.
* a statement failed -- :class:`DatabaseOperationFailed`. The honest fallback
  for a genuinely unclassified SQL failure, not the default.
"""

from __future__ import annotations

from ...domain.errors import (
    InvalidNavigationRequest,
    MemoryFailure,
    MemoryUnavailable,
    NavigationMemoryError,
)

__all__ = [
    "NavigationStoreError",
    "DatabaseUnavailable",
    "InvalidDatabasePath",
    "SchemaVersionMismatch",
    "SchemaCreateFailed",
    "DatabaseLocked",
    "ConstraintViolated",
    "DatabaseOperationFailed",
    "CONSTRAINT_KINDS",
]

#: The constraint kinds SQLite distinguishes, as :class:`ConstraintViolated`
#: reports them. ``"unknown"`` covers a constraint failure whose message this
#: build does not recognise -- still a constraint failure, still not a crash.
CONSTRAINT_KINDS = ("unique", "foreign key", "check", "not null", "primary key", "unknown")


class NavigationStoreError(NavigationMemoryError):
    """Base class for every failure this component reports.

    Every subclass below also inherits a *domain* error, so an adapter failure
    already **is** the domain fact it represents (Rule 0 §2). Nothing between
    this component and the service has to remember to translate, and a service
    that catches :class:`~...domain.errors.MemoryUnavailable` catches a
    corrupt file, an unwritable directory and a version mismatch alike.
    """


class DatabaseUnavailable(NavigationStoreError, MemoryUnavailable):
    """The database could not be opened, created or reached.

    An unwritable directory, a path that is a directory, a corrupt file, a
    filesystem that is full. The location is carried as a string rather than a
    :class:`~.models.DatabaseLocation` so the exception stays reconstructable
    from its own ``args`` without dragging a second type through pickling.
    """

    def __init__(self, location: str, detail: str = "") -> None:
        Exception.__init__(self, location, detail)
        self.location = location
        self.detail = detail

    def __str__(self) -> str:
        suffix = f": {self.detail}" if self.detail else ""
        return f"the navigation database at {self.location} could not be opened{suffix}"


class InvalidDatabasePath(NavigationStoreError, InvalidNavigationRequest):
    """The configured path cannot be a database file.

    Rejected in-process before an engine is built (Rule 1 §3, "validate before
    you spawn"): an empty path, or one that already exists as a directory.
    """

    def __init__(self, path: str, reason: str = "") -> None:
        Exception.__init__(self, path, reason)
        self.path = path
        self.reason = reason

    def __str__(self) -> str:
        suffix = f": {self.reason}" if self.reason else ""
        return f"{self.path!r} is not a usable database path{suffix}"


class SchemaVersionMismatch(NavigationStoreError, MemoryUnavailable):
    """The file on disk was written by a different build of this schema.

    Raised rather than migrated on purpose. This build creates its tables and
    checks a version; it does not know how to upgrade one. Failing here with
    both numbers in hand is what turns a confusing "no such column" three calls
    later into a decision the developer can actually make: keep the history and
    add migrations, or delete the file and re-learn.
    """

    def __init__(self, location: str, found: int | None, expected: int) -> None:
        Exception.__init__(self, location, found, expected)
        self.location = location
        self.found = found
        self.expected = expected

    def __str__(self) -> str:
        found = "no version at all" if self.found is None else f"version {self.found}"
        return (
            f"the navigation database at {self.location} reports {found}, but this "
            f"build writes version {self.expected}; delete the file to start a fresh "
            f"memory, or migrate it if the learned history is worth keeping"
        )


class SchemaCreateFailed(NavigationStoreError, MemoryUnavailable):
    """The tables could not be created in a database that did open."""

    def __init__(self, location: str, detail: str = "") -> None:
        Exception.__init__(self, location, detail)
        self.location = location
        self.detail = detail

    def __str__(self) -> str:
        suffix = f": {self.detail}" if self.detail else ""
        return f"could not create the navigation schema in {self.location}{suffix}"


class DatabaseLocked(NavigationStoreError, MemoryFailure):
    """Another writer held the database for longer than the busy timeout.

    The one failure here worth retrying. SQLite serialises writers, so this
    means a concurrent run was mid-write, not that anything is broken.
    """

    def __init__(self, location: str, timeout_seconds: float, detail: str = "") -> None:
        Exception.__init__(self, location, timeout_seconds, detail)
        self.location = location
        self.timeout_seconds = timeout_seconds
        self.detail = detail

    def __str__(self) -> str:
        suffix = f": {self.detail}" if self.detail else ""
        return (
            f"the navigation database at {self.location} stayed locked for more than "
            f"{self.timeout_seconds}s{suffix}"
        )


class ConstraintViolated(NavigationStoreError, MemoryFailure):
    """A write contradicted the schema, and the database refused it.

    Separated from :class:`DatabaseOperationFailed` because it is the one
    failure on the write path a caller routinely *handles* instead of
    propagating. Recording navigation means inserting screens and edges that
    have very often been seen before, and ``uq_screen_identity`` rejecting a
    duplicate is the schema doing its job -- the remedy is to read the existing
    row, not to report a fault.

    ``kind`` is what a caller branches on and is one of
    :data:`CONSTRAINT_KINDS`; ``detail`` carries the driver's own message, which
    names the exact columns.
    """

    def __init__(self, kind: str, detail: str = "", location: str = "") -> None:
        Exception.__init__(self, kind, detail, location)
        self.kind = kind
        self.detail = detail
        self.location = location

    def __str__(self) -> str:
        where = f" in {self.location}" if self.location else ""
        suffix = f": {self.detail}" if self.detail else ""
        return f"a {self.kind} constraint rejected the write{where}{suffix}"


class DatabaseOperationFailed(NavigationStoreError, MemoryFailure):
    """A statement failed for a reason this component does not classify.

    The honest fallback (Rule 1 §3). A failure that keeps arriving here and has
    a remedy of its own has earned a class above, and adding one is a row in
    this file rather than a branch in a method.
    """

    def __init__(self, operation: str, location: str = "", detail: str = "") -> None:
        Exception.__init__(self, operation, location, detail)
        self.operation = operation
        self.location = location
        self.detail = detail

    def __str__(self) -> str:
        where = f" on {self.location}" if self.location else ""
        suffix = f": {self.detail}" if self.detail else ""
        return f"{self.operation} failed{where}{suffix}"
