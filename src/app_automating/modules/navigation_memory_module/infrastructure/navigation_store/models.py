"""Value objects that describe the database itself, and never cross a port.

Rule 0 §2 draws the line at the port signature: a type that a caller above this
layer would name belongs in ``domain/``, and a type that only describes *how*
this component stores things stays here. Everything in this file is the second
kind. A screen and a route are domain facts; "which file are we open on", "was
the schema just created" and "what version does this file claim" are facts about
SQLite, and the layers above have no business knowing that navigation memory is
a file at all.

This file is the bottom of the package's dependency order and imports nothing
from it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ["DatabaseLocation", "SchemaState", "MEMORY_URL"]

#: The URL of a database that lives only for the life of the process. Used by
#: tests and by any caller that wants the module's behaviour without its
#: persistence -- ``StaticPool`` in ``engine.py`` is what makes every session
#: reach the same one, instead of each connection getting a private empty file.
MEMORY_URL = "sqlite+aiosqlite://"


@dataclass(frozen=True, slots=True)
class DatabaseLocation:
    """Where the navigation memory actually is, once the config is resolved.

    Carried on errors so a failure says *which* file it was about -- a config
    pointing at an unwritable directory and one pointing at a corrupt file both
    surface as "could not open the database" without it.
    """

    #: The SQLAlchemy URL the engine was created from.
    url: str
    #: The file on disk, or ``None`` for an in-memory database.
    path: Path | None

    @property
    def in_memory(self) -> bool:
        """True when nothing will survive this process."""
        return self.path is None

    @property
    def directory(self) -> Path | None:
        """The directory that must exist and be writable, if any."""
        return None if self.path is None else self.path.parent

    def __str__(self) -> str:
        return "an in-memory database" if self.path is None else str(self.path)


@dataclass(frozen=True, slots=True)
class SchemaState:
    """What ``connect`` found and what it did about it.

    Returned rather than logged: the caller is the only one that can decide
    whether a freshly created database is a normal first run or the sign that
    someone deleted the file everything had been learned into.
    """

    location: DatabaseLocation
    #: The version this file claims, or ``None`` when it carried no version row
    #: -- which for a file that already had tables means it predates the
    #: bookkeeping and cannot be trusted.
    found_version: int | None
    #: The version this build of the code writes.
    expected_version: int
    #: True when the tables did not exist and were created by this call.
    created: bool
    #: The table names present after the call, sorted.
    tables: tuple[str, ...] = ()

    @property
    def matches(self) -> bool:
        """True when the file on disk is one this build can safely use."""
        return self.found_version == self.expected_version

    @property
    def empty(self) -> bool:
        """True when this is a brand-new memory with nothing learned in it yet."""
        return self.created
