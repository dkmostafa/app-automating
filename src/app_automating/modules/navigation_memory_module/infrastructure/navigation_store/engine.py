"""The database itself: the engine, its connections, its schema, its sessions.

This is the component's resource-owning file -- the analogue of a ``process.py``
in a component that spawns child processes (Rule 1 §1). A pooled database
connection is a live thing with a lifetime, and this is the one place that
opens, configures, hands out and closes them. It sits at the top of the
dependency order, importing ``config``, ``models``, ``errors``, ``parsing`` and
``tables``, and is imported by none of them.

Three jobs, in order:

1. **Resolve and open.** Validate the path, create the directory if the config
   allows it, and build the async engine. An in-memory database is given a
   ``StaticPool`` so every session reaches the same one -- with the default pool
   each connection would get a private, empty database and nothing would appear
   to persist even within the process.
2. **Configure every connection.** SQLite applies ``foreign_keys`` and
   ``busy_timeout`` per connection, not per file, and the pool opens new ones as
   it grows. The pragmas therefore hang off the ``connect`` event rather than
   being run once at startup, which is the difference between cascades that work
   and cascades that work until the pool grows a second connection.
3. **Create the schema and vouch for it.** ``create_all`` is idempotent, so it
   also heals a half-created file; the version row is what turns "the columns
   are different from what this code expects" into one clear failure at startup
   instead of a confusing ``no such column`` in the middle of a run.

Nothing here knows what a screen or a route *is*. That is ``tables.py``'s
business, and reading and writing them is the business of the store that will
sit on top of this.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import TracebackType
from typing import Any

from sqlalchemy import event, insert, inspect, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from .config import NavigationStoreConfig
from .errors import (
    DatabaseUnavailable,
    InvalidDatabasePath,
    SchemaCreateFailed,
    SchemaVersionMismatch,
)
from .models import DatabaseLocation, SchemaState
from .parsing import (
    classify_database_failure,
    parse_schema_version,
    pragma_statements,
    validate_database_path,
)
from .tables import SCHEMA_VERSION, SCHEMA_VERSION_KEY, Base, SchemaMeta

__all__ = ["NavigationDatabase"]


class NavigationDatabase:
    """The navigation memory's SQLite database, and everything that owns it.

    Constructed cheaply and connected lazily: building one touches no disk, so
    the composition root can assemble the object graph on a host where the data
    directory does not exist yet and nothing fails until something is actually
    stored. :meth:`connect` is what creates the file, and it is idempotent.

    Owns a resource, so it offers ``aclose()`` and works as an async context
    manager (Rule 1 §5).
    """

    def __init__(self, config: NavigationStoreConfig) -> None:
        self._config = config
        self._location = config.location
        self._engine: AsyncEngine | None = None
        self._sessions: async_sessionmaker[AsyncSession] | None = None
        self._state: SchemaState | None = None

    # -- what this database is ---------------------------------------------

    @property
    def config(self) -> NavigationStoreConfig:
        return self._config

    @property
    def location(self) -> DatabaseLocation:
        """Where the memory is, resolved. Safe to read before connecting."""
        return self._location

    @property
    def state(self) -> SchemaState | None:
        """What :meth:`connect` found, or ``None`` if it has not run."""
        return self._state

    @property
    def connected(self) -> bool:
        return self._engine is not None

    # -- opening it --------------------------------------------------------

    async def connect(self) -> SchemaState:
        """Open the database, apply the schema, and check its version.

        Idempotent: calling it again returns the state the first call
        established without touching the disk, so every entry point can insist
        on a connected database without coordinating with the others.

        Raises :class:`~.errors.InvalidDatabasePath` for a path that cannot be a
        file, :class:`~.errors.DatabaseUnavailable` when the directory or file
        cannot be used, :class:`~.errors.SchemaCreateFailed` when the tables
        cannot be written, and :class:`~.errors.SchemaVersionMismatch` when the
        file was written by a different build of the schema.
        """
        if self._state is not None:
            return self._state

        self._prepare_filesystem()
        engine = self._build_engine()
        try:
            state = await self._apply_schema(engine)
        except BaseException:
            # A failed connect leaves nothing behind (Rule 1 §5): the pool this
            # engine already opened is closed before the error propagates.
            await engine.dispose()
            raise

        self._engine = engine
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)
        self._state = state
        return state

    def _prepare_filesystem(self) -> None:
        """Make sure the path can hold a database, before an engine exists."""
        path = self._location.path
        if path is None:
            return

        validate_database_path(path)
        directory = path.parent
        try:
            if self._config.create_directories:
                directory.mkdir(parents=True, exist_ok=True)
            elif not directory.is_dir():
                raise DatabaseUnavailable(
                    str(self._location),
                    f"{directory} does not exist and create_directories is off",
                )
            if path.is_dir():
                raise InvalidDatabasePath(str(path), "the path is an existing directory")
        except OSError as exc:
            # Never let a foreign exception escape the component (Rule 1 §3).
            raise DatabaseUnavailable(str(self._location), str(exc)) from exc

    def _build_engine(self) -> AsyncEngine:
        """Create the engine and attach the per-connection PRAGMAs."""
        options: dict[str, Any] = {
            "echo": self._config.echo_sql,
            # aiosqlite passes this straight to sqlite3.connect.
            "connect_args": {"timeout": self._config.connect_timeout_seconds},
        }
        if self._location.in_memory:
            # Without StaticPool each connection gets its own private in-memory
            # database, and nothing written through one session is visible to
            # the next -- the failure looks like data loss, not configuration.
            options["poolclass"] = StaticPool

        engine = create_async_engine(self._location.url, **options)

        statements = pragma_statements(
            enforce_foreign_keys=self._config.enforce_foreign_keys,
            journal_mode=self._config.journal_mode,
            synchronous=self._config.synchronous,
            busy_timeout_milliseconds=self._config.busy_timeout_milliseconds,
            in_memory=self._location.in_memory,
        )

        @event.listens_for(engine.sync_engine, "connect")
        def _apply_pragmas(dbapi_connection: Any, _record: Any) -> None:
            # Runs on the DBAPI connection, in the pool's thread, for every
            # connection the pool ever opens -- including the ones it opens
            # later, which is exactly why this is an event and not a one-off.
            cursor = dbapi_connection.cursor()
            try:
                for statement in statements:
                    cursor.execute(statement)
            finally:
                cursor.close()

        return engine

    async def _apply_schema(self, engine: AsyncEngine) -> SchemaState:
        """Create the tables if they are absent, then vouch for the version."""
        try:
            async with engine.begin() as connection:
                before = set(
                    await connection.run_sync(lambda sync: inspect(sync).get_table_names())
                )
                fresh = SchemaMeta.__tablename__ not in before

                # The version is read *before* create_all, and this order is the
                # whole guarantee that a refused file is left as it was found.
                # SQLite's driver does not roll DDL back with the enclosing
                # transaction, so a mismatch raised after create_all would leave
                # the file half-migrated -- refused by this build and altered by
                # it in the same breath.
                found: int | None = None
                if not fresh:
                    raw = await connection.scalar(
                        select(SchemaMeta.value).where(SchemaMeta.key == SCHEMA_VERSION_KEY)
                    )
                    found = parse_schema_version(raw)
                    if found != SCHEMA_VERSION:
                        raise SchemaVersionMismatch(str(self._location), found, SCHEMA_VERSION)

                # Idempotent, so this still heals a file that is missing tables
                # -- including one an older build of this method half-migrated.
                await connection.run_sync(Base.metadata.create_all)

                if fresh:
                    await connection.execute(
                        insert(SchemaMeta).values(key=SCHEMA_VERSION_KEY, value=str(SCHEMA_VERSION))
                    )
                    found = SCHEMA_VERSION

                after = tuple(
                    sorted(await connection.run_sync(lambda sync: inspect(sync).get_table_names()))
                )
        except SQLAlchemyError as exc:
            raise self._translate("creating the navigation schema", exc) from exc
        except OSError as exc:
            raise SchemaCreateFailed(str(self._location), str(exc)) from exc

        return SchemaState(
            location=self._location,
            found_version=found,
            expected_version=SCHEMA_VERSION,
            created=fresh,
            tables=after,
        )

    # -- using it ----------------------------------------------------------

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """A transactional session: committed on a clean exit, rolled back otherwise.

        Connects first if it has to, so a caller never has to remember the
        order. Every SQLAlchemy failure is translated on the way out, so nothing
        above this layer ever sees a ``SQLAlchemyError``.
        """
        if self._sessions is None:
            await self.connect()
        assert self._sessions is not None  # connect() sets it or raises

        session = self._sessions()
        try:
            yield session
        except SQLAlchemyError as exc:
            await session.rollback()
            raise self._translate("a navigation database transaction", exc) from exc
        except BaseException:
            await session.rollback()
            raise
        else:
            try:
                await session.commit()
            except SQLAlchemyError as exc:
                await session.rollback()
                raise self._translate("committing to the navigation database", exc) from exc
        finally:
            await session.close()

    # -- closing it --------------------------------------------------------

    async def aclose(self) -> None:
        """Close every pooled connection. Safe to call more than once."""
        engine, self._engine = self._engine, None
        self._sessions = None
        self._state = None
        if engine is not None:
            await engine.dispose()

    async def __aenter__(self) -> NavigationDatabase:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    # -- failures ----------------------------------------------------------

    def _translate(self, operation: str, exc: SQLAlchemyError) -> Exception:
        """A SQLAlchemy failure as one of this component's own errors.

        The driver's own message is what carries the distinguishing detail --
        "database is locked" and "unable to open database file" arrive as the
        same ``OperationalError`` -- so classification reads it, from the table
        in ``parsing.py`` rather than an if-chain here.
        """
        detail = str(getattr(exc, "orig", None) or exc)
        return classify_database_failure(
            operation, str(self._location), detail, self._config.busy_timeout_seconds
        )

    def __repr__(self) -> str:
        return f"NavigationDatabase(location={self._location!s}, connected={self.connected})"
