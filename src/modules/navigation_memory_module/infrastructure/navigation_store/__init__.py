"""The navigation store: the SQLite database behind the module's memory.

Rule 1 §1: callers import from here, never from ``.engine`` or ``.tables``. The
split inside is by *kind of thing*, in a fixed acyclic order::

    models -> errors -> parsing  \\
    tables                        >-- engine -- store
    config                       /

* ``models.py``  -- value objects describing the database itself, never a route.
                    Imports nothing here; it is the bottom.
* ``errors.py``  -- every typed failure this component can raise.
* ``parsing.py`` -- pure: PRAGMA sets, failure classification, path validation.
* ``tables.py``  -- the schema. Every table, column, index and constraint, and
                    nothing else. Also imports nothing here.
* ``config.py``  -- the injected settings, and the only reader of the environment.
* ``engine.py``  -- the engine, the per-connection PRAGMAs, schema creation and
                    the session lifecycle.
* ``store.py``   -- the manager: rows in, domain dataclasses out. Satisfies all
                    three of the module's ports and is imported by none of the
                    files above it.

The names shared across these files are public within the package
(``pragma_statements``, ``classify_database_failure``) rather than
underscore-prefixed; a leading underscore here means "private to this one file".

SQLAlchemy stops at this boundary. Nothing this package exports is a SQLAlchemy
type except the ORM classes in ``tables.py``, and those are the store's own
business -- the layers above will speak in domain objects and will never learn
that navigation memory is a file with columns in it.
"""

from .config import (
    DEFAULT_DATA_DIRNAME,
    DEFAULT_DATABASE_FILENAME,
    DEFAULT_SCREENSHOT_DIRNAME,
    ENV_PREFIX,
    NavigationStoreConfig,
    default_data_directory,
)
from .engine import NavigationDatabase
from .errors import (
    CONSTRAINT_KINDS,
    ConstraintViolated,
    DatabaseLocked,
    DatabaseOperationFailed,
    DatabaseUnavailable,
    InvalidDatabasePath,
    NavigationStoreError,
    SchemaCreateFailed,
    SchemaVersionMismatch,
)
from .models import MEMORY_URL, DatabaseLocation, SchemaState
from .parsing import (
    FAILURE_SIGNATURES,
    JOURNAL_MODES,
    SYNCHRONOUS_MODES,
    classify_database_failure,
    constraint_kind,
    parse_schema_version,
    pragma_statements,
    validate_database_path,
)
from .store import NavigationStore
from .tables import (
    SCHEMA_VERSION,
    SCHEMA_VERSION_KEY,
    UNKNOWN,
    Base,
    Run,
    SchemaMeta,
    Screen,
    Step,
    Transition,
    UtcDateTime,
)

__all__ = [
    # config
    "NavigationStoreConfig",
    "ENV_PREFIX",
    "DEFAULT_DATA_DIRNAME",
    "DEFAULT_DATABASE_FILENAME",
    "DEFAULT_SCREENSHOT_DIRNAME",
    "default_data_directory",
    # the database
    "NavigationDatabase",
    "NavigationStore",
    # value objects
    "DatabaseLocation",
    "SchemaState",
    "MEMORY_URL",
    # errors
    "NavigationStoreError",
    "DatabaseUnavailable",
    "InvalidDatabasePath",
    "SchemaVersionMismatch",
    "SchemaCreateFailed",
    "DatabaseLocked",
    "ConstraintViolated",
    "CONSTRAINT_KINDS",
    "DatabaseOperationFailed",
    # pure helpers
    "classify_database_failure",
    "constraint_kind",
    "pragma_statements",
    "parse_schema_version",
    "validate_database_path",
    "FAILURE_SIGNATURES",
    "JOURNAL_MODES",
    "SYNCHRONOUS_MODES",
    # the schema
    "Base",
    "SchemaMeta",
    "Screen",
    "Transition",
    "Run",
    "Step",
    "UtcDateTime",
    "SCHEMA_VERSION",
    "SCHEMA_VERSION_KEY",
    "UNKNOWN",
]
