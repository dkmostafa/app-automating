"""The schema, asserted against itself.

Unit tests because ``tables.py`` is pure (Rule 2 §2): a declarative mapping is
data, and ``Base.metadata`` can be walked without a database file existing
anywhere. Nothing here is mocked or faked -- these are unit tests because the
code under test touches nothing, not because anything was replaced.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint

from modules.navigation_memory_module.infrastructure.navigation_store.tables import (
    SCHEMA_VERSION,
    SCHEMA_VERSION_KEY,
    UNKNOWN,
    Base,
    Run,
    Screen,
    Step,
    Transition,
    UtcDateTime,
)

pytestmark = pytest.mark.unit

EXPECTED_TABLES = frozenset({"schema_meta", "screen", "transition", "run", "step"})


def test_the_schema_is_exactly_these_five_tables() -> None:
    """A table added or removed without a version bump is the failure this catches."""
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_the_schema_version_is_a_positive_integer() -> None:
    assert isinstance(SCHEMA_VERSION, int)
    assert SCHEMA_VERSION >= 1
    assert SCHEMA_VERSION_KEY == "schema_version"


# ---------------------------------------------------------------------------
# the load-bearing constraint rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "table", sorted(Base.metadata.tables.values(), key=lambda t: t.name), ids=lambda t: t.name
)
def test_every_uniquely_constrained_column_is_not_null(table) -> None:
    """The rule the whole deduplicating half of the schema rests on.

    SQL treats two NULLs as distinct inside a unique index, so one nullable
    column in a UNIQUE constraint silently turns "one row per edge" into
    "unlimited rows per edge". ``UNKNOWN`` is the stand-in instead.
    """
    offenders = [
        f"{table.name}.{column.name} in {constraint.name}"
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
        for column in constraint.columns
        if column.nullable
    ]
    assert not offenders, "a NULL here defeats the constraint entirely: " + ", ".join(offenders)


def test_the_unknown_placeholder_is_the_empty_string() -> None:
    """Not None, and not a sentinel object -- it goes into a NOT NULL column."""
    assert UNKNOWN == ""
    assert isinstance(UNKNOWN, str)


def test_screen_is_identified_by_package_activity_and_fingerprint() -> None:
    """Both halves of the identity are stored, so lookup can degrade (see §Identity)."""
    constraint = next(
        c
        for c in Screen.__table__.constraints
        if isinstance(c, UniqueConstraint) and c.name == "uq_screen_identity"
    )
    assert [column.name for column in constraint.columns] == ["package", "activity", "fingerprint"]


def test_a_transition_is_unique_per_from_action_target_and_to() -> None:
    """Deduplication is what makes traversal_count mean anything."""
    constraint = next(
        c
        for c in Transition.__table__.constraints
        if isinstance(c, UniqueConstraint) and c.name == "uq_transition_edge"
    )
    assert [column.name for column in constraint.columns] == [
        "from_screen_id",
        "action",
        "target",
        "to_screen_id",
    ]


def test_a_step_is_unique_per_run_and_sequence() -> None:
    """A double-write of the same step is rejected rather than reordering the journal."""
    constraint = next(
        c
        for c in Step.__table__.constraints
        if isinstance(c, UniqueConstraint) and c.name == "uq_step_sequence"
    )
    assert [column.name for column in constraint.columns] == ["run_id", "seq"]


@pytest.mark.parametrize(
    ("table", "name"),
    [
        (Screen, "ck_screen_visit_count"),
        (Transition, "ck_transition_traversals"),
        (Transition, "ck_transition_successes"),
        (Step, "ck_step_seq"),
    ],
)
def test_the_counting_invariants_are_enforced_by_the_database(table, name: str) -> None:
    """A success count above the traversal count is nonsense; the file refuses it."""
    names = {c.name for c in table.__table__.constraints if isinstance(c, CheckConstraint)}
    assert name in names


# ---------------------------------------------------------------------------
# the indexes the documented lookups need
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("table", "name", "columns"),
    [
        (Screen, "ix_screen_fingerprint", ["package", "fingerprint"]),
        (Screen, "ix_screen_activity", ["package", "activity"]),
        (Transition, "ix_transition_from", ["from_screen_id"]),
        (Transition, "ix_transition_to", ["to_screen_id"]),
        (Run, "ix_run_package_started", ["package", "started_at"]),
        (Step, "ix_step_run_seq", ["run_id", "seq"]),
    ],
)
def test_the_lookups_the_module_exists_for_are_indexed(
    table, name: str, columns: list[str]
) -> None:
    """The two queries the map exists for: what can I do here, how do I get there."""
    index = next(i for i in table.__table__.indexes if i.name == name)
    assert [column.name for column in index.columns] == columns


# ---------------------------------------------------------------------------
# cascades
# ---------------------------------------------------------------------------


def test_deleting_a_run_takes_its_steps_with_it() -> None:
    """The journal belongs to the run; an orphan step is meaningless."""
    foreign_key = next(iter(Step.__table__.c.run_id.foreign_keys))
    assert foreign_key.ondelete == "CASCADE"


def test_deleting_a_screen_takes_its_edges_but_spares_the_journal() -> None:
    """A transition without both endpoints is not a route. A step still happened."""
    for column in (Transition.__table__.c.from_screen_id, Transition.__table__.c.to_screen_id):
        assert next(iter(column.foreign_keys)).ondelete == "CASCADE"
    for column in (Step.__table__.c.from_screen_id, Step.__table__.c.to_screen_id):
        assert next(iter(column.foreign_keys)).ondelete == "SET NULL"


# ---------------------------------------------------------------------------
# UtcDateTime -- the type that exists because SQLite has none
# ---------------------------------------------------------------------------


def test_an_aware_datetime_round_trips_unchanged() -> None:
    kind = UtcDateTime()
    moment = dt.datetime(2026, 9, 2, 10, 32, 5, 123456, tzinfo=dt.UTC)

    stored = kind.process_bind_param(moment, None)

    assert kind.process_result_value(stored, None) == moment


def test_a_non_utc_datetime_is_normalised_to_utc() -> None:
    """The same instant, stored one way, however the caller spelled it."""
    kind = UtcDateTime()
    plus_two = dt.timezone(dt.timedelta(hours=2))
    moment = dt.datetime(2026, 9, 2, 12, 32, 5, 123456, tzinfo=plus_two)

    stored = kind.process_bind_param(moment, None)

    assert stored == "2026-09-02T10:32:05.123456+00:00"
    assert kind.process_result_value(stored, None) == moment


def test_a_naive_datetime_is_read_as_utc_rather_than_local_time() -> None:
    """The product writes UTC; a naive value is a caller that forgot, not a hint."""
    kind = UtcDateTime()

    stored = kind.process_bind_param(dt.datetime(2026, 9, 2, 10, 32, 5, 123456), None)

    assert stored == "2026-09-02T10:32:05.123456+00:00"


def test_none_survives_in_both_directions() -> None:
    """`Run.ended_at` is NULL for the whole life of a run in flight."""
    kind = UtcDateTime()

    assert kind.process_bind_param(None, None) is None
    assert kind.process_result_value(None, None) is None


def test_what_comes_back_is_always_timezone_aware() -> None:
    """The bug this type exists to prevent: SQLAlchemy's DateTime returns naive."""
    kind = UtcDateTime()

    assert kind.process_result_value("2026-09-02T10:32:05.123456+00:00", None).tzinfo is not None
    # A value written before this type existed, or by hand through the CLI.
    assert kind.process_result_value("2026-09-02T10:32:05.123456", None).tzinfo == dt.UTC


def test_text_order_is_time_order() -> None:
    """`ORDER BY last_seen_at` is correct on the raw string only if this holds."""
    kind = UtcDateTime()
    moments = [
        dt.datetime(2026, 1, 2, 3, 4, 5, 6, tzinfo=dt.UTC),
        dt.datetime(2026, 1, 2, 3, 4, 5, 7, tzinfo=dt.UTC),
        dt.datetime(2026, 1, 2, 3, 4, 6, 0, tzinfo=dt.UTC),
        dt.datetime(2026, 12, 31, 23, 59, 59, 999999, tzinfo=dt.UTC),
        dt.datetime(2027, 1, 1, 0, 0, 0, 0, tzinfo=dt.UTC),
    ]

    stored = [kind.process_bind_param(m, None) for m in moments]

    assert stored == sorted(stored)
    assert len({len(s) for s in stored}) == 1, "fixed width is what makes the order hold"


def test_the_stored_width_fits_the_column() -> None:
    """32 characters, and an ISO-8601 UTC microsecond timestamp is exactly 32."""
    kind = UtcDateTime()
    stored = kind.process_bind_param(dt.datetime(2026, 9, 2, 10, 32, 5, 1, tzinfo=dt.UTC), None)

    assert len(stored) <= kind.impl.length


def test_every_table_has_a_repr_that_names_its_row() -> None:
    """A failing assertion should say which row, not `<object at 0x...>`."""
    for model in (Screen, Transition, Run, Step):
        assert "__repr__" in vars(model)
