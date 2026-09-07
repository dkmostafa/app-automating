"""The database against a real SQLite file.

Integration by Rule 2 §2: every test here opens a real database, most of them on
a real path under ``tmp_path``. Rule 1 §6's ban applies in full -- nothing is
patched, stubbed or faked. A test that replaced the driver would be testing the
replacement, and the failures worth catching here (foreign keys defaulting off,
an in-memory pool handing out private databases, a PRAGMA that never reached the
connection) are precisely the ones a fake would paper over.

``tmp_path`` gives every test its own directory, which pytest removes
afterwards, so nothing here can touch the developer's real navigation memory.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app_automating.modules.navigation_memory_module.infrastructure.navigation_store.config import (
    NavigationStoreConfig,
)
from app_automating.modules.navigation_memory_module.infrastructure.navigation_store.engine import (
    NavigationDatabase,
)
from app_automating.modules.navigation_memory_module.infrastructure.navigation_store.errors import (
    ConstraintViolated,
    DatabaseUnavailable,
    InvalidDatabasePath,
    SchemaVersionMismatch,
)
from app_automating.modules.navigation_memory_module.infrastructure.navigation_store.tables import (
    SCHEMA_VERSION,
    SCHEMA_VERSION_KEY,
    Run,
    Screen,
    Step,
    Transition,
)

pytestmark = pytest.mark.integration

EXPECTED_TABLES = ("run", "schema_meta", "screen", "step", "transition")


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def file_config(tmp_path: Path, **overrides: object) -> NavigationStoreConfig:
    return dataclasses.replace(
        NavigationStoreConfig(database_path=tmp_path / "nav.db"),
        **overrides,  # type: ignore[arg-type]
    )


def a_screen(fingerprint: str = "a" * 64, **overrides: object) -> Screen:
    fields: dict[str, object] = {
        "package": "com.example.shop",
        "activity": ".MainActivity",
        "fingerprint": fingerprint,
        "first_seen_at": now(),
        "last_seen_at": now(),
        "visit_count": 1,
    }
    return Screen(**{**fields, **overrides})  # type: ignore[arg-type]


@pytest.fixture
async def database(tmp_path: Path):
    """A real database on a real path, closed however the test ends."""
    db = NavigationDatabase(file_config(tmp_path))
    try:
        await db.connect()
        yield db
    finally:
        await db.aclose()


# ---------------------------------------------------------------------------
# opening it
# ---------------------------------------------------------------------------


async def test_connecting_creates_the_file_and_the_whole_schema(tmp_path: Path) -> None:
    db = NavigationDatabase(file_config(tmp_path))
    try:
        state = await db.connect()
    finally:
        await db.aclose()

    assert (tmp_path / "nav.db").is_file()
    assert state.created is True
    assert state.tables == EXPECTED_TABLES
    assert state.found_version == SCHEMA_VERSION
    assert state.matches is True


async def test_a_missing_parent_directory_is_created(tmp_path: Path) -> None:
    """A fresh machine has no ~/.local/share/app-automating, and must not need one."""
    nested = tmp_path / "deeply" / "nested" / "nav.db"
    db = NavigationDatabase(NavigationStoreConfig(database_path=nested))
    try:
        await db.connect()
    finally:
        await db.aclose()

    assert nested.is_file()


async def test_a_missing_directory_is_an_error_when_creation_is_off(tmp_path: Path) -> None:
    """What a deployment wants when the path is supposed to be a mounted volume."""
    config = NavigationStoreConfig(
        database_path=tmp_path / "absent" / "nav.db", create_directories=False
    )
    db = NavigationDatabase(config)

    with pytest.raises(DatabaseUnavailable) as excinfo:
        await db.connect()

    assert "absent" in excinfo.value.detail
    assert not (tmp_path / "absent").exists()


async def test_a_path_that_is_a_directory_is_rejected(tmp_path: Path) -> None:
    db = NavigationDatabase(NavigationStoreConfig(database_path=tmp_path))

    with pytest.raises(InvalidDatabasePath) as excinfo:
        await db.connect()

    assert excinfo.value.reason == "the path is an existing directory"


async def test_building_the_object_touches_nothing(tmp_path: Path) -> None:
    """Construction is cheap and lazy, so the composition root can run anywhere."""
    target = tmp_path / "never" / "nav.db"

    db = NavigationDatabase(NavigationStoreConfig(database_path=target))

    assert db.connected is False
    assert db.state is None
    assert not target.parent.exists()


async def test_connect_is_idempotent(database: NavigationDatabase) -> None:
    """Every entry point can insist on a connection without coordinating."""
    first = database.state
    second = await database.connect()

    assert second is first
    assert second.created is True


async def test_reopening_an_existing_database_does_not_recreate_it(tmp_path: Path) -> None:
    config = file_config(tmp_path)
    async with NavigationDatabase(config) as db:
        assert db.state is not None and db.state.created is True

    async with NavigationDatabase(config) as db:
        assert db.state is not None
        assert db.state.created is False
        assert db.state.matches is True


async def test_a_failed_connect_leaves_nothing_behind(tmp_path: Path) -> None:
    """Rule 1 §5: the pool the failed attempt opened is disposed before it raises."""
    db = NavigationDatabase(NavigationStoreConfig(database_path=tmp_path))

    with pytest.raises(InvalidDatabasePath):
        await db.connect()

    assert db.connected is False
    assert db.state is None


# ---------------------------------------------------------------------------
# the version check
# ---------------------------------------------------------------------------


async def test_a_file_from_a_different_build_is_refused(tmp_path: Path) -> None:
    """Fail once at startup with both numbers, not with `no such column` later."""
    config = file_config(tmp_path)
    async with NavigationDatabase(config):
        pass

    raw = sqlite3.connect(config.database_path)
    raw.execute("UPDATE schema_meta SET value = '99' WHERE key = ?", (SCHEMA_VERSION_KEY,))
    raw.commit()
    raw.close()

    with pytest.raises(SchemaVersionMismatch) as excinfo:
        await NavigationDatabase(config).connect()

    assert excinfo.value.found == 99
    assert excinfo.value.expected == SCHEMA_VERSION


async def test_a_file_with_tables_but_no_version_row_is_refused(tmp_path: Path) -> None:
    """It predates the bookkeeping, so this build cannot vouch for its columns."""
    config = file_config(tmp_path)
    async with NavigationDatabase(config):
        pass

    raw = sqlite3.connect(config.database_path)
    raw.execute("DELETE FROM schema_meta")
    raw.commit()
    raw.close()

    with pytest.raises(SchemaVersionMismatch) as excinfo:
        await NavigationDatabase(config).connect()

    assert excinfo.value.found is None


async def test_a_garbled_version_row_is_refused_rather_than_guessed_at(tmp_path: Path) -> None:
    config = file_config(tmp_path)
    async with NavigationDatabase(config):
        pass

    raw = sqlite3.connect(config.database_path)
    raw.execute("UPDATE schema_meta SET value = 'v2-beta' WHERE key = ?", (SCHEMA_VERSION_KEY,))
    raw.commit()
    raw.close()

    with pytest.raises(SchemaVersionMismatch):
        await NavigationDatabase(config).connect()


async def test_a_refused_file_is_left_exactly_as_it_was_found(tmp_path: Path) -> None:
    """The version is checked before create_all, so nothing is written at all.

    Not a rollback: SQLite's driver does not undo DDL with the transaction, so
    refusing the file has to happen before a single table is touched.
    """
    config = file_config(tmp_path)
    async with NavigationDatabase(config):
        pass

    raw = sqlite3.connect(config.database_path)
    raw.execute("UPDATE schema_meta SET value = '99' WHERE key = ?", (SCHEMA_VERSION_KEY,))
    raw.execute("DROP TABLE step")
    raw.commit()
    raw.close()

    with pytest.raises(SchemaVersionMismatch):
        await NavigationDatabase(config).connect()

    raw = sqlite3.connect(config.database_path)
    tables = {row[0] for row in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    raw.close()
    assert "step" not in tables, "a refused file must not be half-migrated"


# ---------------------------------------------------------------------------
# the PRAGMAs, checked on the real connection
# ---------------------------------------------------------------------------


async def test_write_ahead_logging_is_actually_on_the_file(tmp_path: Path) -> None:
    """WAL is what lets a run be inspected while it is still going."""
    config = file_config(tmp_path)
    async with NavigationDatabase(config):
        pass

    raw = sqlite3.connect(config.database_path)
    mode = raw.execute("PRAGMA journal_mode").fetchone()[0]
    raw.close()

    assert mode.lower() == "wal"


async def test_a_configured_journal_mode_reaches_the_file(tmp_path: Path) -> None:
    config = file_config(tmp_path, journal_mode="DELETE")
    async with NavigationDatabase(config):
        pass

    raw = sqlite3.connect(config.database_path)
    mode = raw.execute("PRAGMA journal_mode").fetchone()[0]
    raw.close()

    assert mode.lower() == "delete"


async def test_foreign_keys_are_enforced_on_every_connection(database: NavigationDatabase) -> None:
    """SQLite defaults them OFF per connection; off makes the cascades decoration."""
    with pytest.raises(ConstraintViolated) as excinfo:
        async with database.session() as session:
            session.add(Step(run_id=999_999, seq=1, action="tap", target="", ok=True, at=now()))

    assert excinfo.value.kind == "foreign key"


async def test_foreign_keys_hold_on_a_connection_the_pool_opened_later(
    database: NavigationDatabase,
) -> None:
    """The reason the PRAGMAs hang off the connect event and not off startup."""
    for _ in range(4):
        async with database.session() as session:
            await session.execute(select(func.count()).select_from(Screen))

    with pytest.raises(ConstraintViolated) as excinfo:
        async with database.session() as session:
            session.add(Step(run_id=888_888, seq=1, action="tap", target="", ok=True, at=now()))

    assert excinfo.value.kind == "foreign key"


async def test_foreign_keys_can_be_switched_off_deliberately(tmp_path: Path) -> None:
    """Proves the setting is wired through, not that anyone should use it."""
    async with NavigationDatabase(file_config(tmp_path, enforce_foreign_keys=False)) as db:
        async with db.session() as session:
            session.add(Step(run_id=777_777, seq=1, action="tap", target="", ok=True, at=now()))

        async with db.session() as session:
            assert (await session.execute(select(func.count()).select_from(Step))).scalar() == 1


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------


async def test_a_session_commits_when_the_block_exits_cleanly(tmp_path: Path) -> None:
    config = file_config(tmp_path)
    async with NavigationDatabase(config) as db:
        async with db.session() as session:
            session.add(a_screen())

    async with NavigationDatabase(config) as db:
        async with db.session() as session:
            assert (await session.execute(select(func.count()).select_from(Screen))).scalar() == 1


async def test_a_session_rolls_back_when_the_block_raises(database: NavigationDatabase) -> None:
    """A half-written run is worse than no run: the caller's error wins."""

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        async with database.session() as session:
            session.add(a_screen())
            await session.flush()
            raise Boom

    async with database.session() as session:
        assert (await session.execute(select(func.count()).select_from(Screen))).scalar() == 0


async def test_a_session_connects_on_demand(tmp_path: Path) -> None:
    """A caller never has to remember to connect first."""
    db = NavigationDatabase(file_config(tmp_path))
    try:
        async with db.session() as session:
            session.add(a_screen())
        assert db.connected is True
    finally:
        await db.aclose()


async def test_no_sqlalchemy_error_escapes_the_component(database: NavigationDatabase) -> None:
    """Rule 1 §3: a foreign exception crossing the boundary is the bug this catches."""
    from sqlalchemy.exc import SQLAlchemyError

    with pytest.raises(ConstraintViolated) as excinfo:
        async with database.session() as session:
            session.add(a_screen())
            session.add(a_screen())

    assert not isinstance(excinfo.value, SQLAlchemyError)
    assert excinfo.value.kind == "unique"


# ---------------------------------------------------------------------------
# the schema, exercised
# ---------------------------------------------------------------------------


async def test_the_same_screen_seen_twice_is_one_row(database: NavigationDatabase) -> None:
    """The deduplication the whole map half of the schema depends on."""
    async with database.session() as session:
        session.add(a_screen())

    with pytest.raises(ConstraintViolated) as excinfo:
        async with database.session() as session:
            session.add(a_screen())

    assert excinfo.value.kind == "unique"


async def test_two_screens_in_one_activity_are_two_rows(database: NavigationDatabase) -> None:
    """A Compose app runs its whole UI in one activity; the fingerprint separates them."""
    async with database.session() as session:
        session.add_all([a_screen("a" * 64), a_screen("b" * 64)])

    async with database.session() as session:
        assert (await session.execute(select(func.count()).select_from(Screen))).scalar() == 2


async def test_the_same_edge_walked_twice_cannot_be_inserted_twice(
    database: NavigationDatabase,
) -> None:
    """Without this, traversal_count would mean nothing."""
    async with database.session() as session:
        session.add_all([a_screen("a" * 64), a_screen("b" * 64)])
        await session.flush()
        first, second = (await session.execute(select(Screen.id).order_by(Screen.id))).scalars()
        session.add(
            Transition(
                from_screen_id=first,
                to_screen_id=second,
                action="tap_element",
                target="id=cart",
                traversal_count=1,
                success_count=1,
                first_seen_at=now(),
                last_seen_at=now(),
            )
        )

    with pytest.raises(ConstraintViolated) as excinfo:
        async with database.session() as session:
            session.add(
                Transition(
                    from_screen_id=first,
                    to_screen_id=second,
                    action="tap_element",
                    target="id=cart",
                    traversal_count=1,
                    success_count=1,
                    first_seen_at=now(),
                    last_seen_at=now(),
                )
            )

    assert excinfo.value.kind == "unique"


async def test_a_success_count_above_the_traversal_count_is_refused(
    database: NavigationDatabase,
) -> None:
    """Nonsense the database itself will not hold."""
    async with database.session() as session:
        session.add_all([a_screen("a" * 64), a_screen("b" * 64)])
        await session.flush()
        first, second = (await session.execute(select(Screen.id).order_by(Screen.id))).scalars()

    with pytest.raises(ConstraintViolated) as excinfo:
        async with database.session() as session:
            session.add(
                Transition(
                    from_screen_id=first,
                    to_screen_id=second,
                    action="tap",
                    target="",
                    traversal_count=1,
                    success_count=5,
                    first_seen_at=now(),
                    last_seen_at=now(),
                )
            )

    assert excinfo.value.kind == "check"


async def test_deleting_a_run_deletes_its_journal(database: NavigationDatabase) -> None:
    async with database.session() as session:
        run = Run(device_id="emulator-5554", package="com.example.shop", started_at=now())
        session.add(run)
        await session.flush()
        session.add_all(
            [Step(run_id=run.id, seq=n, action="tap", target="", ok=True, at=now()) for n in (1, 2)]
        )

    async with database.session() as session:
        await session.delete((await session.execute(select(Run))).scalar_one())

    async with database.session() as session:
        assert (await session.execute(select(func.count()).select_from(Step))).scalar() == 0


async def test_a_timestamp_comes_back_as_aware_utc(database: NavigationDatabase) -> None:
    """The bug UtcDateTime exists to prevent, asserted through a real round trip."""
    written = dt.datetime(2026, 9, 2, 10, 32, 5, 123456, tzinfo=dt.UTC)
    async with database.session() as session:
        session.add(a_screen(first_seen_at=written, last_seen_at=written))

    async with database.session() as session:
        screen = (await session.execute(select(Screen))).scalar_one()

    assert screen.first_seen_at == written
    assert screen.first_seen_at.tzinfo is not None


async def test_a_run_in_flight_has_no_end_time(database: NavigationDatabase) -> None:
    async with database.session() as session:
        session.add(Run(device_id="emulator-5554", package="com.example.shop", started_at=now()))

    async with database.session() as session:
        assert (await session.execute(select(Run))).scalar_one().ended_at is None


# ---------------------------------------------------------------------------
# an in-memory database
# ---------------------------------------------------------------------------


async def test_an_in_memory_database_is_shared_across_sessions() -> None:
    """Without StaticPool each connection gets a private, empty database.

    The failure looks exactly like data loss, which is why it is asserted.
    """
    async with NavigationDatabase(NavigationStoreConfig()) as db:
        async with db.session() as session:
            session.add(a_screen())

        async with db.session() as session:
            assert (await session.execute(select(func.count()).select_from(Screen))).scalar() == 1


async def test_an_in_memory_database_writes_no_file(tmp_path: Path) -> None:
    """Nothing to create and nothing to clean up -- not even a journal beside it."""
    async with NavigationDatabase(NavigationStoreConfig()) as db:
        assert db.location.path is None
        assert db.location.in_memory is True
        async with db.session() as session:
            session.add(a_screen())

    assert list(tmp_path.iterdir()) == []
    assert not list(Path.cwd().glob("*.db"))


async def test_an_in_memory_database_still_enforces_foreign_keys() -> None:
    """The pragmas are applied there too, minus the one SQLite ignores."""
    async with NavigationDatabase(NavigationStoreConfig()) as db:
        with pytest.raises(ConstraintViolated) as excinfo:
            async with db.session() as session:
                session.add(Step(run_id=1, seq=1, action="tap", target="", ok=True, at=now()))

    assert excinfo.value.kind == "foreign key"


# ---------------------------------------------------------------------------
# closing it
# ---------------------------------------------------------------------------


async def test_closing_releases_the_engine(tmp_path: Path) -> None:
    db = NavigationDatabase(file_config(tmp_path))
    await db.connect()

    await db.aclose()

    assert db.connected is False
    assert db.state is None


async def test_closing_twice_is_safe(tmp_path: Path) -> None:
    """Teardown runs on every exit path, including after another teardown."""
    db = NavigationDatabase(file_config(tmp_path))
    await db.connect()

    await db.aclose()
    await db.aclose()

    assert db.connected is False


async def test_closing_a_database_that_never_connected_is_safe(tmp_path: Path) -> None:
    await NavigationDatabase(file_config(tmp_path)).aclose()


async def test_a_closed_database_can_be_opened_again(tmp_path: Path) -> None:
    """What was written before the close is still there afterwards."""
    db = NavigationDatabase(file_config(tmp_path))
    async with db.session() as session:
        session.add(a_screen())
    await db.aclose()

    try:
        async with db.session() as session:
            assert (await session.execute(select(func.count()).select_from(Screen))).scalar() == 1
    finally:
        await db.aclose()


async def test_the_context_manager_closes_on_the_way_out(tmp_path: Path) -> None:
    db = NavigationDatabase(file_config(tmp_path))

    async with db as opened:
        assert opened is db
        assert db.connected is True

    assert db.connected is False


async def test_the_context_manager_closes_when_the_body_raises(tmp_path: Path) -> None:
    class Boom(Exception):
        pass

    db = NavigationDatabase(file_config(tmp_path))

    with pytest.raises(Boom):
        async with db:
            raise Boom

    assert db.connected is False


async def test_the_repr_says_where_it_is_and_whether_it_is_open(tmp_path: Path) -> None:
    """A failing assertion should name the file, not an address."""
    db = NavigationDatabase(file_config(tmp_path))

    assert "nav.db" in repr(db)
    assert "connected=False" in repr(db)
