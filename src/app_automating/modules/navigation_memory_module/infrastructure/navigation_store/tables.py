"""The database structure: every table, every column, every index.

This file is the schema and nothing else -- no engine, no session, no I/O. It
imports nothing from the rest of the package, which makes it the bottom of the
dependency order alongside ``models.py`` and lets the whole schema be inspected,
diffed and unit-tested on a host with no database file anywhere.

The shape, and why it is two halves
-----------------------------------

Four tables in two pairs, plus one for bookkeeping::

    the journal -- what happened          the map -- what was learned
    ------------------------------        ---------------------------
    Run    one drive of one app           Screen      a place, deduplicated
    Step   one action, in order           Transition  an edge, with counts

:class:`Step` is append-only and lossless: it records every action in sequence,
including the ones that failed, so a run can be replayed and debugged long after
it finished. :class:`Transition` is the fold of those steps into knowledge:
one row per distinct ``(from, action, target, to)`` edge, however many times it
was walked, carrying the counts that tell a planner whether the edge is
reliable.

Keeping both costs one extra write per action and buys the two questions that
are actually asked -- "what did this run do?" and "what usually works from
here?" -- without either answer being derived at read time.

Identity, and the empty string
------------------------------

A screen is identified by ``(package, activity, fingerprint)``: the app, the
Android activity, and a hash of the visible resource-ids. Both halves are
stored deliberately. Activity alone is too coarse -- a Compose or React Native
app runs its whole UI inside one activity, and matching on activity alone would
collapse the entire graph to a single node. Fingerprint alone is too brittle --
a cart badge going ``2`` to ``3`` would mint a new screen forever. Storing both
lets a lookup prefer the fingerprint and degrade to the activity without a
migration.

Every column inside a ``UNIQUE`` constraint is ``NOT NULL`` with ``""`` as the
"unknown" value, never ``NULL``. This is not tidiness: SQL treats two ``NULL``s
as distinct inside a unique index, so a nullable ``target`` would let the same
edge be inserted without limit and quietly break the deduplication this whole
half of the schema exists to provide.

Time
----

Timestamps go through :class:`UtcDateTime`, which stores a fixed-width ISO-8601
UTC string. SQLite has no native datetime type and SQLAlchemy's own ``DateTime``
silently drops the offset on the way in and hands back a naive value on the way
out -- so a UTC-aware write becomes an ambiguous read. The fixed width matters
too: it makes lexicographic order and chronological order the same order, so
``ORDER BY last_seen_at`` is correct on the raw text and the file stays readable
under the ``sqlite3`` CLI, which is where a developer will actually go looking.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

__all__ = [
    "SCHEMA_VERSION",
    "SCHEMA_VERSION_KEY",
    "UtcDateTime",
    "Base",
    "SchemaMeta",
    "Screen",
    "Transition",
    "Run",
    "Step",
    "UNKNOWN",
]

#: Bumped whenever a table, column or constraint below changes in a way an
#: existing file cannot satisfy. ``NavigationDatabase.connect`` compares it
#: against what the file claims and refuses to run against a mismatch rather
#: than failing later with a confusing SQL error.
SCHEMA_VERSION = 1

#: The row in :class:`SchemaMeta` that carries it.
SCHEMA_VERSION_KEY = "schema_version"

#: The stand-in for "we could not determine this", used wherever a column takes
#: part in a UNIQUE constraint. See the module docstring: ``NULL`` there would
#: defeat the constraint entirely.
UNKNOWN = ""


class UtcDateTime(TypeDecorator[dt.datetime]):
    """A timestamp that survives the round trip through SQLite as UTC.

    Accepts an aware datetime in any zone and a naive one read as UTC; always
    stores ``YYYY-MM-DDTHH:MM:SS.ffffff+00:00`` and always returns an aware UTC
    datetime. The width is fixed, so text order is time order.
    """

    impl = String(32)
    cache_ok = True

    def process_bind_param(self, value: dt.datetime | None, dialect: Any) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            # A naive value here is a caller that forgot, not a local time we
            # should guess at: the whole product writes UTC.
            value = value.replace(tzinfo=dt.UTC)
        return value.astimezone(dt.UTC).isoformat(timespec="microseconds")

    def process_result_value(self, value: str | None, dialect: Any) -> dt.datetime | None:
        if value is None:
            return None
        parsed = dt.datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


class Base(DeclarativeBase):
    """The declarative base every table below hangs off.

    ``Base.metadata`` is what creates the schema and what a test introspects; it
    is the single object that means "the structure of the navigation database".
    """


class SchemaMeta(Base):
    """Bookkeeping: a tiny key/value table holding the schema version.

    Deliberately a table rather than SQLite's ``PRAGMA user_version``. A pragma
    is invisible to a plain ``.dump`` and to anyone reading the file with a
    generic tool, and it has room for exactly one integer -- this has room for
    whatever the next fact about the file turns out to be.
    """

    __tablename__ = "schema_meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255), nullable=False)


class Screen(Base):
    """One place in one app, recognised again the next time it is reached.

    A row is written the first time a screen is observed and updated on every
    later visit. ``page_source`` is held here rather than on :class:`Step`
    precisely because this table is deduplicated: a run that bounces between
    two screens forty times stores two XML documents, not forty.
    """

    __tablename__ = "screen"
    __table_args__ = (
        UniqueConstraint("package", "activity", "fingerprint", name="uq_screen_identity"),
        # The intended lookup: "which screen am I on", answered by fingerprint.
        Index("ix_screen_fingerprint", "package", "fingerprint"),
        # The degraded lookup, for an app whose fingerprint churns.
        Index("ix_screen_activity", "package", "activity"),
        CheckConstraint("visit_count >= 0", name="ck_screen_visit_count"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    #: The app this screen belongs to, e.g. ``com.example.shop``.
    package: Mapped[str] = mapped_column(String(255), nullable=False)
    #: The Android activity, e.g. ``.MainActivity``. ``UNKNOWN`` when the driver
    #: could not report one -- never NULL, see the module docstring.
    activity: Mapped[str] = mapped_column(String(255), nullable=False, default=UNKNOWN)
    #: A hash of the screen's stable structure -- the sorted resource-ids of the
    #: visible elements. Computed by the caller: this layer stores it and never
    #: decides what goes into it, so the recipe can change without a migration.
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, default=UNKNOWN)

    #: A human label, best effort: a toolbar title, a heading, an activity name.
    #: For a person reading the map, not for matching.
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: The UI hierarchy XML as captured. Truncated by the caller to the
    #: configured cap; ``NULL`` when it was not captured at all.
    page_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Where the screenshot was written. A path, never the bytes -- pixels in
    #: SQLite turn a queryable megabyte-scale file into an unqueryable
    #: gigabyte-scale one.
    screenshot_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    first_seen_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False)
    last_seen_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False)
    #: How many times this screen has been observed, across every run.
    visit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    outgoing: Mapped[list[Transition]] = relationship(
        back_populates="from_screen",
        foreign_keys="Transition.from_screen_id",
        cascade="all, delete-orphan",
    )
    incoming: Mapped[list[Transition]] = relationship(
        back_populates="to_screen",
        foreign_keys="Transition.to_screen_id",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"Screen(id={self.id!r}, package={self.package!r}, "
            f"activity={self.activity!r}, fingerprint={self.fingerprint[:12]!r})"
        )


class Transition(Base):
    """A learned edge: doing ``action`` on ``from_screen`` arrives at ``to_screen``.

    This is the table a planner reads. One row per distinct edge no matter how
    often it is walked, so ``traversal_count`` and ``success_count`` are what
    separate a route that works from one that worked once by accident.

    A self-edge (``from_screen_id == to_screen_id``) is legal and meaningful: it
    records an action that changed nothing, which is exactly the knowledge that
    stops a model retrying it.
    """

    __tablename__ = "transition"
    __table_args__ = (
        UniqueConstraint(
            "from_screen_id", "action", "target", "to_screen_id", name="uq_transition_edge"
        ),
        # "What can I do from here" -- the forward plan.
        Index("ix_transition_from", "from_screen_id"),
        # "How do I get there" -- the same graph searched backwards from a goal.
        Index("ix_transition_to", "to_screen_id"),
        CheckConstraint("traversal_count >= 0", name="ck_transition_traversals"),
        CheckConstraint(
            "success_count >= 0 AND success_count <= traversal_count",
            name="ck_transition_successes",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    from_screen_id: Mapped[int] = mapped_column(
        ForeignKey("screen.id", ondelete="CASCADE"), nullable=False
    )
    to_screen_id: Mapped[int] = mapped_column(
        ForeignKey("screen.id", ondelete="CASCADE"), nullable=False
    )

    #: What was done: ``tap``, ``tap_element``, ``swipe``, ``scroll``,
    #: ``type_text``, ``press_key``. Free text on purpose -- the vocabulary
    #: belongs to the domain layer, and pinning it here as an enum would make
    #: every new gesture a schema change.
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    #: What it was done to: a locator, a key name, a direction, coordinates.
    #: ``UNKNOWN`` for an action that needs no target -- never NULL, or the
    #: unique constraint above stops deduplicating.
    target: Mapped[str] = mapped_column(String(512), nullable=False, default=UNKNOWN)

    #: Times this edge has been walked, and how many of those were reported ok.
    #: The ratio is the edge's reliability; the invariant is enforced above.
    traversal_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    first_seen_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False)
    last_seen_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False)
    #: The app version this edge was last confirmed on. A route learned against
    #: an old build may no longer exist, and this is what lets a caller notice
    #: without re-learning the whole graph on every version bump.
    last_app_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    from_screen: Mapped[Screen] = relationship(
        back_populates="outgoing", foreign_keys=[from_screen_id]
    )
    to_screen: Mapped[Screen] = relationship(back_populates="incoming", foreign_keys=[to_screen_id])

    def __repr__(self) -> str:
        return (
            f"Transition(id={self.id!r}, {self.from_screen_id!r} -{self.action}-> "
            f"{self.to_screen_id!r}, {self.success_count}/{self.traversal_count})"
        )


class Run(Base):
    """One continuous drive of one app on one device.

    The unit a journal is grouped by, and the only place ``device_id`` and
    ``app_version`` are recorded -- neither belongs on a screen or an edge,
    because the point of the map is that a route learned on an emulator is
    usable on a phone.
    """

    __tablename__ = "run"
    __table_args__ = (
        Index("ix_run_package_started", "package", "started_at"),
        Index("ix_run_open", "ended_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    #: The adb serial the run was driven on, e.g. ``emulator-5554``.
    device_id: Mapped[str] = mapped_column(String(128), nullable=False)
    package: Mapped[str] = mapped_column(String(255), nullable=False)
    #: ``versionName`` if it could be read. Nullable, because it often cannot.
    app_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The Appium session this run was recorded through, when there was one.
    #: Kept for correlating a run against that module's logs, nothing more.
    session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    started_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False)
    #: ``NULL`` while the run is still in flight. A file full of runs with no
    #: end time is the signature of a process that kept crashing.
    ended_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime, nullable=True)

    steps: Mapped[list[Step]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="Step.seq",
    )

    def __repr__(self) -> str:
        return (
            f"Run(id={self.id!r}, device_id={self.device_id!r}, "
            f"package={self.package!r}, ended={self.ended_at is not None})"
        )


class Step(Base):
    """One action inside a run, in the order it happened. Append-only.

    The journal half of the schema: written whether the action succeeded or
    not, which is what separates it from :class:`Transition`. A failed step is
    a fact about the run; it is not knowledge about the app, so it is recorded
    here and not folded into the map.
    """

    __tablename__ = "step"
    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_step_sequence"),
        Index("ix_step_run_seq", "run_id", "seq"),
        CheckConstraint("seq >= 1", name="ck_step_seq"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"), nullable=False)
    #: Position within the run, 1-based and gapless. Unique per run, so a
    #: double-write of the same step is rejected by the database rather than
    #: silently corrupting the order.
    seq: Mapped[int] = mapped_column(Integer, nullable=False)

    #: Where the action was taken from. ``NULL`` only for the first observation
    #: of a run, before anything had been done.
    from_screen_id: Mapped[int | None] = mapped_column(
        ForeignKey("screen.id", ondelete="SET NULL"), nullable=True
    )
    #: Where it landed. ``NULL`` when the action failed, or when the screen
    #: afterwards was never observed.
    to_screen_id: Mapped[int | None] = mapped_column(
        ForeignKey("screen.id", ondelete="SET NULL"), nullable=True
    )

    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str] = mapped_column(String(512), nullable=False, default=UNKNOWN)

    #: Whether the action was reported successful. Stored as an integer because
    #: SQLite has no boolean and an explicit 0/1 reads the same from every tool.
    ok: Mapped[bool] = mapped_column(Integer, nullable=False, default=1)
    #: The failure message when ``ok`` is false. The reason a journal is worth
    #: keeping at all.
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False)

    run: Mapped[Run] = relationship(back_populates="steps")
    from_screen: Mapped[Screen | None] = relationship(foreign_keys=[from_screen_id])
    to_screen: Mapped[Screen | None] = relationship(foreign_keys=[to_screen_id])

    def __repr__(self) -> str:
        return (
            f"Step(id={self.id!r}, run_id={self.run_id!r}, seq={self.seq!r}, "
            f"{self.from_screen_id!r} -{self.action}-> {self.to_screen_id!r}, ok={self.ok!r})"
        )
