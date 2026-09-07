"""The entities and boundary DTOs of the navigation-memory module.

Every type here appears in a port signature (:mod:`.ports`), which is why it
lives in the domain rather than in the adapter that happens to produce it today.
A second adapter -- Postgres, a remote memory service, a different device
backend -- would speak these same dataclasses.

This module imports nothing but the standard library. It is the bottom of the
module's dependency graph and must stay importable on a host with no database,
no Android SDK and no Appium.

The vocabulary divides the way the schema does. A :class:`ScreenSnapshot` is
what was *observed* -- raw, from a device, not yet stored. A
:class:`KnownScreen` is what is *remembered* -- deduplicated, counted, with an
id. A :class:`RouteOption` is what was *learned*: an edge that has been walked,
with the numbers that say whether it can be trusted.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

__all__ = [
    # value objects
    "ScreenIdentity",
    "ScreenSnapshot",
    "KnownScreen",
    "RouteOption",
    "PathStep",
    "RunSummary",
    "JournalEntry",
    # requests
    "StartRunRequest",
    "RecordObservationRequest",
    "RecordInteractionRequest",
    "EndRunRequest",
    "WhereAmIRequest",
    "FindPathRequest",
    "SearchScreensRequest",
    "GetRunRequest",
    "ForgetRequest",
    # results
    "StartRunResult",
    "RecordObservationResult",
    "RecordInteractionResult",
    "EndRunResult",
    "WhereAmIResult",
    "FindPathResult",
    "SearchScreensResult",
    "GetRunResult",
    "ForgetResult",
    # the vocabulary of an action
    "OBSERVE_ACTION",
]

#: The action recorded for the first observation of a run, before anything has
#: been done. It is a real journal entry rather than a special case on ``Run``:
#: the schema already allows a step with no ``from_screen``, and encoding "where
#: we started" as data keeps "where is this session now" a single query over
#: ``step`` instead of two lookups against two different shapes.
OBSERVE_ACTION = "observe"


# --------------------------------------------------------------------------
# value objects
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScreenIdentity:
    """What makes two observations the same place.

    The package and activity come from the device; the fingerprint is computed
    from the UI hierarchy by :mod:`.fingerprinting`. All three are part of the
    identity, and none of them is ever ``None`` -- the store keeps them in a
    UNIQUE constraint, where a NULL would silently stop deduplicating.
    """

    package: str
    activity: str = ""
    fingerprint: str = ""

    def __str__(self) -> str:
        return f"{self.package}/{self.activity or '?'}#{self.fingerprint[:8] or '?'}"


@dataclass(frozen=True, slots=True)
class ScreenSnapshot:
    """One raw observation of a device's screen, before it is stored.

    This is what an adapter hands in. It carries the page source because the
    fingerprint is derived from it, and the derivation is a domain rule rather
    than the caller's business.
    """

    package: str
    activity: str = ""
    page_source: str = ""
    #: A human label -- a toolbar title, a heading. Best effort, never matched on.
    title: str | None = None
    screenshot_path: str | None = None

    def identify(self, fingerprint: str) -> ScreenIdentity:
        """Pair this observation with the fingerprint computed from it."""
        return ScreenIdentity(package=self.package, activity=self.activity, fingerprint=fingerprint)


@dataclass(frozen=True, slots=True)
class KnownScreen:
    """A screen the memory has seen before, with its id and its history."""

    screen_id: int
    identity: ScreenIdentity
    title: str | None = None
    visit_count: int = 0
    first_seen_at: dt.datetime | None = None
    last_seen_at: dt.datetime | None = None

    @property
    def label(self) -> str:
        """The best name to show a caller: the title if there is one, else the
        activity, else the truncated fingerprint. Never empty."""
        if self.title:
            return self.title
        if self.identity.activity:
            return self.identity.activity
        return f"screen {self.screen_id}"


@dataclass(frozen=True, slots=True)
class RouteOption:
    """One learned edge out of a screen: do this, arrive there.

    :attr:`confidence` is the derived read a caller would otherwise compute by
    hand, and getting it wrong is easy -- one success out of one traversal is
    not the same evidence as forty out of forty.
    """

    action: str
    target: str
    to_screen: KnownScreen
    traversal_count: int
    success_count: int
    last_seen_at: dt.datetime | None = None
    last_app_version: str | None = None

    @property
    def confidence(self) -> float:
        """Success rate, smoothed so a single lucky traversal is not certainty.

        Laplace smoothing: one success in one try reads as 0.67 rather than
        1.0, and forty in forty as 0.98. That ordering is the whole point --
        a planner must prefer the well-trodden route over the lucky one.
        """
        if self.traversal_count <= 0:
            return 0.0
        return (self.success_count + 1) / (self.traversal_count + 2)

    @property
    def reliable(self) -> bool:
        """Walked more than once, and never seen to fail."""
        return self.traversal_count > 1 and self.success_count == self.traversal_count


@dataclass(frozen=True, slots=True)
class PathStep:
    """One hop of a planned route."""

    action: str
    target: str
    from_screen: KnownScreen
    to_screen: KnownScreen
    confidence: float


@dataclass(frozen=True, slots=True)
class RunSummary:
    """One drive of one app on one device."""

    run_id: int
    device_id: str
    package: str
    started_at: dt.datetime
    app_version: str | None = None
    session_id: str | None = None
    ended_at: dt.datetime | None = None
    step_count: int = 0

    @property
    def open(self) -> bool:
        """True while the run is still in flight."""
        return self.ended_at is None


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """One action inside a run, exactly as it happened -- failures included."""

    seq: int
    action: str
    target: str
    at: dt.datetime
    ok: bool = True
    from_screen: KnownScreen | None = None
    to_screen: KnownScreen | None = None
    detail: str | None = None

    @property
    def moved(self) -> bool:
        """True when the action actually changed screen.

        An action that left the device where it was is the most useful negative
        result the memory holds, and it is not the same thing as a failure.
        """
        if self.from_screen is None or self.to_screen is None:
            return False
        return self.from_screen.screen_id != self.to_screen.screen_id


# --------------------------------------------------------------------------
# requests
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StartRunRequest:
    """Input for :meth:`RouteRecorder.start_run`."""

    device_id: str
    package: str
    app_version: str | None = None
    #: The Appium session this run is recorded through. It is how
    #: :class:`WhereAmIRequest` finds the run again, so a caller that wants the
    #: read tools to work must supply it.
    session_id: str | None = None


@dataclass(frozen=True, slots=True)
class RecordObservationRequest:
    """Input for :meth:`RouteRecorder.record_observation` -- where a run starts.

    Seeds the run's position without recording an action. Every later
    interaction is an edge *from* wherever this left the cursor.
    """

    run_id: int
    snapshot: ScreenSnapshot


@dataclass(frozen=True, slots=True)
class RecordInteractionRequest:
    """Input for :meth:`RouteRecorder.record_interaction` -- one action taken.

    The "from" screen is wherever the run already was; only the screen
    *afterwards* is supplied, because that is the only one the caller has just
    observed.
    """

    run_id: int
    action: str
    target: str = ""
    #: The screen after the action. ``None`` when it could not be captured --
    #: the step is still journalled, but no edge can be learned from it.
    snapshot_after: ScreenSnapshot | None = None
    #: Whether the interaction itself reported success. A failed action that
    #: still changed screen is a real traversal and a real non-success.
    ok: bool = True
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class EndRunRequest:
    """Input for :meth:`RouteRecorder.end_run`."""

    run_id: int


@dataclass(frozen=True, slots=True)
class WhereAmIRequest:
    """Input for :meth:`RouteMemory.where_am_i`.

    Identified by session rather than by screen: the recorder already knows
    where the session is, so the caller does not have to carry a page source
    back across the boundary to ask.
    """

    session_id: str
    #: Cap on how many outgoing routes to return, best first.
    limit: int = 20


@dataclass(frozen=True, slots=True)
class FindPathRequest:
    """Input for :meth:`RouteMemory.find_path`."""

    session_id: str
    #: Where to go. Matched against a screen's title, then its activity, then
    #: its id as text -- so a caller can say "Checkout" without a prior lookup.
    destination: str
    #: Refuse to plan a route longer than this. A path of twenty blind taps is
    #: not a plan, it is a guess with extra steps.
    max_steps: int = 12
    #: Ignore edges below this confidence. ``0.0`` considers everything known.
    min_confidence: float = 0.0


@dataclass(frozen=True, slots=True)
class SearchScreensRequest:
    """Input for :meth:`RouteMemory.search_screens`."""

    package: str
    #: Substring matched against title and activity. Empty lists everything.
    query: str = ""
    limit: int = 50


@dataclass(frozen=True, slots=True)
class GetRunRequest:
    """Input for :meth:`RouteMemory.get_run`."""

    run_id: int
    #: Cap on journal entries returned, from the start of the run.
    limit: int = 200


@dataclass(frozen=True, slots=True)
class ForgetRequest:
    """Input for :meth:`MemoryMaintenance.forget`.

    At least one selector must be set; a request that selected nothing would
    otherwise mean "delete everything", which is not a thing to arrive at by
    omission.
    """

    package: str | None = None
    run_id: int | None = None
    #: Drop runs that started before this. Screens and edges are untouched --
    #: the journal ages out, the map does not.
    before: dt.datetime | None = None

    @property
    def selects_nothing(self) -> bool:
        return self.package is None and self.run_id is None and self.before is None


# --------------------------------------------------------------------------
# results
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StartRunResult:
    run: RunSummary


@dataclass(frozen=True, slots=True)
class RecordObservationResult:
    screen: KnownScreen
    #: True when this screen had never been seen before, in any run.
    first_visit: bool


@dataclass(frozen=True, slots=True)
class RecordInteractionResult:
    seq: int
    from_screen: KnownScreen | None
    to_screen: KnownScreen | None
    #: True when the edge was recorded for the first time.
    new_route: bool
    #: True when the action left the device on the same screen.
    stayed_put: bool


@dataclass(frozen=True, slots=True)
class EndRunResult:
    run: RunSummary


@dataclass(frozen=True, slots=True)
class WhereAmIResult:
    run: RunSummary
    screen: KnownScreen
    routes: tuple[RouteOption, ...] = ()

    @property
    def unexplored(self) -> bool:
        """True when nothing is known to lead anywhere from here."""
        return not self.routes


@dataclass(frozen=True, slots=True)
class FindPathResult:
    origin: KnownScreen
    destination: KnownScreen
    steps: tuple[PathStep, ...] = ()

    @property
    def confidence(self) -> float:
        """The path is only as good as its weakest hop."""
        return min((step.confidence for step in self.steps), default=0.0)

    @property
    def already_there(self) -> bool:
        return not self.steps


@dataclass(frozen=True, slots=True)
class SearchScreensResult:
    package: str
    screens: tuple[KnownScreen, ...] = ()


@dataclass(frozen=True, slots=True)
class GetRunResult:
    run: RunSummary
    entries: tuple[JournalEntry, ...] = field(default=())

    @property
    def failures(self) -> tuple[JournalEntry, ...]:
        """The entries worth looking at first when a run went wrong."""
        return tuple(entry for entry in self.entries if not entry.ok)


@dataclass(frozen=True, slots=True)
class ForgetResult:
    runs_deleted: int = 0
    steps_deleted: int = 0
    screens_deleted: int = 0
    transitions_deleted: int = 0

    @property
    def total(self) -> int:
        return (
            self.runs_deleted + self.steps_deleted + self.screens_deleted + self.transitions_deleted
        )
