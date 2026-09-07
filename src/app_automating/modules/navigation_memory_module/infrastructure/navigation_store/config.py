"""The injected configuration of the navigation store, and the one place it is
resolved from the environment.

Rule 1 §4: the component takes this dataclass and nothing else. Nothing below
reads ``os.environ``, and no path, pragma or timeout is hardcoded inside a
method -- which is what makes ``dataclasses.replace(config,
busy_timeout_seconds=0.001)`` a real lock test instead of a mock.

:meth:`NavigationStoreConfig.from_environment` is the only reader, it is called
by the composition root, and every value it produces is handed downwards
explicitly. Settings answer to the ``AT_NAVIGATION_MEMORY_`` prefix, matching
the product's ``AT_`` namespace.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .models import MEMORY_URL, DatabaseLocation

__all__ = [
    "NavigationStoreConfig",
    "ENV_PREFIX",
    "DEFAULT_DATA_DIRNAME",
    "DEFAULT_DATABASE_FILENAME",
    "DEFAULT_SCREENSHOT_DIRNAME",
]

ENV_PREFIX = "AT_NAVIGATION_MEMORY_"

#: The product's own directory under the XDG data root. Data, not cache: a
#: learned navigation map is expensive to rebuild and must not sit somewhere a
#: cleaner is entitled to delete.
DEFAULT_DATA_DIRNAME = "app-automating"
DEFAULT_DATABASE_FILENAME = "navigation_memory.db"
DEFAULT_SCREENSHOT_DIRNAME = "screenshots"


def _env(source: Mapping[str, str], name: str) -> str | None:
    value = source.get(f"{ENV_PREFIX}{name}")
    return value.strip() if value and value.strip() else None


def _float(source: Mapping[str, str], name: str, fallback: float) -> float:
    value = _env(source, name)
    if value is None:
        return fallback
    try:
        return float(value)
    except ValueError:
        # A malformed timeout is a typo in a .env file, not a reason to refuse
        # to start; the documented default is the safer reading.
        return fallback


def _int(source: Mapping[str, str], name: str, fallback: int) -> int:
    value = _env(source, name)
    if value is None:
        return fallback
    try:
        return int(value)
    except ValueError:
        return fallback


def _bool(source: Mapping[str, str], name: str, fallback: bool) -> bool:
    value = _env(source, name)
    if value is None:
        return fallback
    return value.lower() in ("1", "true", "yes", "on")


def default_data_directory(source: Mapping[str, str]) -> Path:
    """Where this host keeps application data, per the XDG base-directory spec.

    ``XDG_DATA_HOME`` is honoured when it is set to an absolute path, which is
    the spec's own condition -- a relative value is required to be ignored.
    """
    raw = source.get("XDG_DATA_HOME", "").strip()
    root = Path(raw) if raw and Path(raw).is_absolute() else Path.home() / ".local" / "share"
    return root / DEFAULT_DATA_DIRNAME


@dataclass(frozen=True, slots=True)
class NavigationStoreConfig:
    """Everything the navigation store needs to know about this host."""

    # -- where the memory lives -------------------------------------------
    #: The SQLite file. ``None`` means an in-memory database that dies with the
    #: process -- useful for a test or a caller that wants the behaviour without
    #: the persistence, and never the default.
    database_path: Path | None = None
    #: Where screenshots are written. Only the path is stored in a row; the
    #: bytes stay on the filesystem (see ``tables.Screen.screenshot_path``).
    screenshot_directory: Path | None = None
    #: Create the parent directory when it is missing. False makes an absent
    #: directory a :class:`~.errors.DatabaseUnavailable` instead -- what a
    #: deployment wants when the path is supposed to be a mounted volume.
    create_directories: bool = True

    # -- connection behaviour ---------------------------------------------
    #: SQLite has foreign keys **off** by default, per connection. Off means the
    #: ``ON DELETE CASCADE`` clauses in the schema are decoration, so this is on
    #: and there is no real reason to turn it off outside a repair session.
    enforce_foreign_keys: bool = True
    #: WAL lets readers run while a writer holds the file, which is the whole
    #: difference between one run at a time and a run that can be inspected
    #: while it is going. Persistent on the file once set.
    journal_mode: str = "WAL"
    #: ``NORMAL`` under WAL syncs at checkpoints rather than at every commit.
    #: The exposure is losing the last few writes if the machine loses power --
    #: acceptable for a memory that is re-learnable, and several times faster.
    synchronous: str = "NORMAL"
    #: How long SQLite waits on a locked database before giving up. Beyond this
    #: the call raises :class:`~.errors.DatabaseLocked`, which is the one
    #: failure here worth retrying.
    busy_timeout_seconds: float = 10.0
    #: How long the driver waits to establish a connection at all.
    connect_timeout_seconds: float = 15.0

    # -- what gets stored --------------------------------------------------
    #: Cap on the page source kept per screen. A pathological hierarchy can run
    #: to megabytes, and the tail of one is never what identifies a screen. Rows
    #: are deduplicated, so this bounds the table by *screens*, not by actions.
    max_page_source_bytes: int = 256 * 1024
    #: Store the page source at all. False keeps the map and drops the labels,
    #: for a caller that wants the smallest possible file.
    store_page_source: bool = True

    #: Whether the composition root wraps ``appium_module``'s session and
    #: gesture ports so every interaction records itself. False leaves those
    #: ports untouched: the read tools still work against whatever was learned
    #: before, and every gesture gets its latency back -- recording costs one
    #: extra page-source capture per interaction, which is the price of a map
    #: with no holes in it.
    record_interactions: bool = True

    # -- diagnostics -------------------------------------------------------
    #: Echo every statement to stderr. Debugging only: it is loud, and on a
    #: STDIO MCP server anything on stdout would corrupt the protocol.
    echo_sql: bool = False

    @property
    def location(self) -> DatabaseLocation:
        """The resolved location, as the engine and every error will name it."""
        if self.database_path is None:
            return DatabaseLocation(url=MEMORY_URL, path=None)
        resolved = self.database_path.expanduser()
        return DatabaseLocation(url=f"sqlite+aiosqlite:///{resolved}", path=resolved)

    @property
    def busy_timeout_milliseconds(self) -> int:
        """The busy timeout as SQLite's own pragma wants it."""
        return max(0, int(self.busy_timeout_seconds * 1000))

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> NavigationStoreConfig:
        """Read the host environment. The only place in the module that does.

        Takes an explicit mapping so the resolution rules are testable without
        touching the real environment -- the rules are pure, and only the
        default argument is not.

        ``AT_NAVIGATION_MEMORY_DATABASE_PATH`` set to ``:memory:`` selects an
        in-memory database; unset falls back to the XDG data directory, so the
        product works on a fresh machine with no configuration at all.
        """
        source = os.environ if environ is None else environ
        data_directory = default_data_directory(source)

        raw_database = _env(source, "DATABASE_PATH")
        if raw_database == ":memory:":
            database_path = None
        elif raw_database:
            database_path = Path(raw_database).expanduser()
        else:
            database_path = data_directory / DEFAULT_DATABASE_FILENAME

        raw_screenshots = _env(source, "SCREENSHOT_DIRECTORY")
        screenshot_directory = (
            Path(raw_screenshots).expanduser()
            if raw_screenshots
            else data_directory / DEFAULT_SCREENSHOT_DIRNAME
        )

        return cls(
            database_path=database_path,
            screenshot_directory=screenshot_directory,
            create_directories=_bool(source, "CREATE_DIRECTORIES", True),
            enforce_foreign_keys=_bool(source, "ENFORCE_FOREIGN_KEYS", True),
            journal_mode=_env(source, "JOURNAL_MODE") or "WAL",
            synchronous=_env(source, "SYNCHRONOUS") or "NORMAL",
            busy_timeout_seconds=_float(source, "BUSY_TIMEOUT_SECONDS", 10.0),
            connect_timeout_seconds=_float(source, "CONNECT_TIMEOUT_SECONDS", 15.0),
            max_page_source_bytes=_int(source, "MAX_PAGE_SOURCE_BYTES", 256 * 1024),
            store_page_source=_bool(source, "STORE_PAGE_SOURCE", True),
            record_interactions=_bool(source, "RECORD_INTERACTIONS", True),
            echo_sql=_bool(source, "ECHO_SQL", False),
        )
