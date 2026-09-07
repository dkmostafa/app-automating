"""The navigation memory's operations: reading and writing the learned map.

The component's manager (Rule 1 §1). It satisfies all three of the module's
ports -- :class:`~...domain.ports.RouteRecorder`,
:class:`~...domain.ports.RouteMemory` and
:class:`~...domain.ports.MemoryMaintenance` -- and owns no I/O of its own:
everything it touches arrives through the constructor, which is the
:class:`~.engine.NavigationDatabase` that holds the sessions.

Sitting at the top of the package's dependency order, it is the only file here
that knows both the schema (:mod:`.tables`) and the domain. That is the whole
translation layer: rows in, dataclasses out.

Two decisions worth reading before the code
-------------------------------------------

**A run's position is derived, not stored.** "Which screen is this session on"
is the last step's landing screen, found with one indexed query over ``step``.
There is no ``current_screen_id`` column to drift out of sync with the journal,
and the first observation of a run is recorded as a real step with
``action='observe'`` and no ``from_screen`` -- which the schema already allowed
for -- so the query has exactly one shape to handle.

**Failures with no storage detail are raised as domain errors directly.** Rule 1
§3 wants a typed exception per distinguishable failure, and Rule 0 §2 wants
adapter failures translated at the boundary. Where a failure carries real
storage evidence -- an exit status, a constraint name -- it gets an adapter
class in :mod:`.errors` that inherits the domain error too. Where it carries
none, ``RunNotFound(run_id)`` is already complete, and wrapping it in an
identical subclass would add a name and no information.
"""

from __future__ import annotations

import datetime as dt
from types import TracebackType

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.errors import (
    DestinationNotFound,
    InvalidNavigationRequest,
    NoKnownRoute,
    NoRunForSession,
    RunAlreadyEnded,
    RunNotFound,
    ScreenNotRecognised,
)
from ...domain.fingerprinting import extract_title, fingerprint_source
from ...domain.models import (
    OBSERVE_ACTION,
    EndRunRequest,
    EndRunResult,
    FindPathRequest,
    FindPathResult,
    ForgetRequest,
    ForgetResult,
    GetRunRequest,
    GetRunResult,
    JournalEntry,
    KnownScreen,
    PathStep,
    RecordInteractionRequest,
    RecordInteractionResult,
    RecordObservationRequest,
    RecordObservationResult,
    RouteOption,
    RunSummary,
    ScreenIdentity,
    ScreenSnapshot,
    SearchScreensRequest,
    SearchScreensResult,
    StartRunRequest,
    StartRunResult,
    WhereAmIRequest,
    WhereAmIResult,
)
from ...domain.routing import RouteEdge, plan_route
from .config import NavigationStoreConfig
from .engine import NavigationDatabase
from .tables import UNKNOWN, Run, Screen, Step, Transition

__all__ = ["NavigationStore"]


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _confidence(traversals: int, successes: int) -> float:
    """The same smoothing :class:`~...domain.models.RouteOption` applies.

    Duplicated here rather than imported because the search needs it over raw
    integers from a query, before any dataclass exists; the two must agree, and
    a test asserts that they do.
    """
    if traversals <= 0:
        return 0.0
    return (successes + 1) / (traversals + 2)


class NavigationStore:
    """The learned navigation map, over a real database.

    Built cheaply: the database connects lazily, so assembling the object graph
    on a host whose data directory does not exist yet touches nothing.
    """

    def __init__(self, database: NavigationDatabase, config: NavigationStoreConfig) -> None:
        self._db = database
        self._config = config

    @property
    def config(self) -> NavigationStoreConfig:
        return self._config

    @property
    def database(self) -> NavigationDatabase:
        return self._db

    # ----------------------------------------------------------------------
    # RouteRecorder -- the write path
    # ----------------------------------------------------------------------

    async def start_run(self, request: StartRunRequest) -> StartRunResult:
        """Open a run. Every later write is recorded against it."""
        if not request.device_id.strip() or not request.package.strip():
            raise InvalidNavigationRequest("start_run", "device_id and package are both required")

        started = _now()
        async with self._db.session() as session:
            # A session drives one app at a time, so an already-open run for
            # this session is a previous one that nobody closed -- a crash, or
            # a caller that skipped end_run. Close it rather than leaving two
            # open runs claiming the same session and making where_am_i
            # ambiguous.
            if request.session_id:
                await self._close_open_runs(session, request.session_id, started)

            run = Run(
                device_id=request.device_id,
                package=request.package,
                app_version=request.app_version,
                session_id=request.session_id,
                started_at=started,
            )
            session.add(run)
            await session.flush()
            summary = RunSummary(
                run_id=run.id,
                device_id=run.device_id,
                package=run.package,
                started_at=started,
                app_version=run.app_version,
                session_id=run.session_id,
                step_count=0,
            )
        return StartRunResult(run=summary)

    async def record_observation(
        self, request: RecordObservationRequest
    ) -> RecordObservationResult:
        """Record where a run is, without recording an action.

        Written as a real journal step so that "where is this session" stays one
        query. Every later interaction is an edge out of whatever this left.
        """
        now = _now()
        async with self._db.session() as session:
            run = await self._require_open_run(session, request.run_id)
            screen, created = await self._upsert_screen(session, request.snapshot, run, now)
            await self._append_step(
                session,
                run_id=run.id,
                action=OBSERVE_ACTION,
                target=UNKNOWN,
                from_screen_id=None,
                to_screen_id=screen.id,
                ok=True,
                detail=None,
                at=now,
            )
            known = _as_known(screen)
        return RecordObservationResult(screen=known, first_visit=created)

    async def record_interaction(
        self, request: RecordInteractionRequest
    ) -> RecordInteractionResult:
        """Record one action and where it landed.

        Journals the step whether or not the action succeeded, and folds the
        edge only when both endpoints are known. A failed action that still
        moved the device is a real traversal and a real non-success -- that is
        the distinction ``traversal_count`` and ``success_count`` exist for.
        """
        if not request.action.strip():
            raise InvalidNavigationRequest("record_interaction", "action is required")

        now = _now()
        async with self._db.session() as session:
            run = await self._require_open_run(session, request.run_id)

            from_id = await self._current_screen_id(session, run.id)
            to_screen = None
            if request.snapshot_after is not None:
                to_screen, _ = await self._upsert_screen(session, request.snapshot_after, run, now)

            to_id = to_screen.id if to_screen is not None else None
            target = request.target or UNKNOWN

            await self._append_step(
                session,
                run_id=run.id,
                action=request.action,
                target=target,
                from_screen_id=from_id,
                to_screen_id=to_id,
                ok=request.ok,
                detail=request.detail,
                at=now,
            )

            new_route = False
            if from_id is not None and to_id is not None:
                new_route = await self._fold_transition(
                    session,
                    from_screen_id=from_id,
                    to_screen_id=to_id,
                    action=request.action,
                    target=target,
                    ok=request.ok,
                    app_version=run.app_version,
                    now=now,
                )

            from_known = await self._known_by_id(session, from_id)
            to_known = _as_known(to_screen) if to_screen is not None else None
            seq = await self._last_seq(session, run.id)

        return RecordInteractionResult(
            seq=seq,
            from_screen=from_known,
            to_screen=to_known,
            new_route=new_route,
            stayed_put=from_id is not None and from_id == to_id,
        )

    async def end_run(self, request: EndRunRequest) -> EndRunResult:
        """Close a run. Recording anything against it afterwards is an error."""
        now = _now()
        async with self._db.session() as session:
            run = await self._require_run(session, request.run_id)
            if run.ended_at is not None:
                raise RunAlreadyEnded(request.run_id)
            run.ended_at = now
            await session.flush()
            summary = await self._summarise(session, run)
        return EndRunResult(run=summary)

    # ----------------------------------------------------------------------
    # RouteMemory -- the read path
    # ----------------------------------------------------------------------

    async def where_am_i(self, request: WhereAmIRequest) -> WhereAmIResult:
        """Which screen this session is on, and what has worked from here."""
        async with self._db.session() as session:
            run = await self._require_run_for_session(session, request.session_id)
            screen_id = await self._current_screen_id(session, run.id)
            if screen_id is None:
                raise ScreenNotRecognised(run.package)

            screen = await session.get(Screen, screen_id)
            if screen is None:
                raise ScreenNotRecognised(run.package)

            routes = await self._routes_from(session, screen_id, limit=request.limit)
            summary = await self._summarise(session, run)
            known = _as_known(screen)
        return WhereAmIResult(run=summary, screen=known, routes=routes)

    async def find_path(self, request: FindPathRequest) -> FindPathResult:
        """The most reliable known route from here to a named destination."""
        if not request.destination.strip():
            raise InvalidNavigationRequest("find_path", "destination is required")

        async with self._db.session() as session:
            run = await self._require_run_for_session(session, request.session_id)
            origin_id = await self._current_screen_id(session, run.id)
            if origin_id is None:
                raise ScreenNotRecognised(run.package)
            origin = await session.get(Screen, origin_id)
            if origin is None:
                raise ScreenNotRecognised(run.package)

            destination = await self._resolve_destination(session, run.package, request.destination)

            edges = await self._edges_for_package(session, run.package)
            hops = plan_route(
                edges,
                origin_id,
                destination.id,
                max_steps=request.max_steps,
                min_confidence=request.min_confidence,
            )
            if not hops and origin_id != destination.id:
                raise NoKnownRoute(
                    _as_known(origin).label, _as_known(destination).label, request.max_steps
                )

            steps = await self._as_path_steps(session, hops)
            result = FindPathResult(
                origin=_as_known(origin),
                destination=_as_known(destination),
                steps=steps,
            )
        return result

    async def search_screens(self, request: SearchScreensRequest) -> SearchScreensResult:
        """Known screens for an app, most recently seen first."""
        async with self._db.session() as session:
            statement = select(Screen).where(Screen.package == request.package)
            if request.query.strip():
                pattern = f"%{request.query.strip()}%"
                statement = statement.where(
                    or_(Screen.title.ilike(pattern), Screen.activity.ilike(pattern))
                )
            statement = statement.order_by(Screen.last_seen_at.desc()).limit(max(1, request.limit))
            rows = (await session.execute(statement)).scalars().all()
            screens = tuple(_as_known(row) for row in rows)
        return SearchScreensResult(package=request.package, screens=screens)

    async def get_run(self, request: GetRunRequest) -> GetRunResult:
        """A run's journal, in order, failures included."""
        async with self._db.session() as session:
            run = await self._require_run(session, request.run_id)
            statement = (
                select(Step)
                .where(Step.run_id == run.id)
                .order_by(Step.seq)
                .limit(max(1, request.limit))
            )
            rows = (await session.execute(statement)).scalars().all()

            wanted = {row.from_screen_id for row in rows} | {row.to_screen_id for row in rows}
            screens = await self._known_by_ids(session, {i for i in wanted if i is not None})

            entries = tuple(
                JournalEntry(
                    seq=row.seq,
                    action=row.action,
                    target=row.target,
                    at=row.at,
                    ok=bool(row.ok),
                    from_screen=screens.get(row.from_screen_id),
                    to_screen=screens.get(row.to_screen_id),
                    detail=row.detail,
                )
                for row in rows
            )
            summary = await self._summarise(session, run)
        return GetRunResult(run=summary, entries=entries)

    # ----------------------------------------------------------------------
    # MemoryMaintenance
    # ----------------------------------------------------------------------

    async def forget(self, request: ForgetRequest) -> ForgetResult:
        """Drop what was learned, by app, by run, or by age.

        A request that selected nothing is refused rather than treated as
        "everything": arriving at a full wipe by omission is not a thing this
        should make easy.
        """
        if request.selects_nothing:
            raise InvalidNavigationRequest(
                "forget", "set package, run_id or before; refusing to delete everything"
            )

        async with self._db.session() as session:
            runs = 0
            steps = 0
            screens = 0
            transitions = 0

            run_filter = []
            if request.run_id is not None:
                run_filter.append(Run.id == request.run_id)
            if request.package is not None:
                run_filter.append(Run.package == request.package)
            if request.before is not None:
                run_filter.append(Run.started_at < request.before)

            doomed = (await session.execute(select(Run.id).where(*run_filter))).scalars().all()
            if doomed:
                steps = int(
                    (
                        await session.execute(
                            select(func.count()).select_from(Step).where(Step.run_id.in_(doomed))
                        )
                    ).scalar()
                    or 0
                )
                # ON DELETE CASCADE takes the steps; the count is read first so
                # the caller learns how much journal went with them.
                runs = int(
                    (await session.execute(delete(Run).where(Run.id.in_(doomed)))).rowcount or 0
                )

            # The map is only dropped for a whole package. Ageing out a run
            # retires its journal and deliberately keeps what it taught.
            if request.package is not None and request.before is None and request.run_id is None:
                doomed_screens = (
                    (
                        await session.execute(
                            select(Screen.id).where(Screen.package == request.package)
                        )
                    )
                    .scalars()
                    .all()
                )
                if doomed_screens:
                    transitions = int(
                        (
                            await session.execute(
                                select(func.count())
                                .select_from(Transition)
                                .where(
                                    or_(
                                        Transition.from_screen_id.in_(doomed_screens),
                                        Transition.to_screen_id.in_(doomed_screens),
                                    )
                                )
                            )
                        ).scalar()
                        or 0
                    )
                    screens = int(
                        (
                            await session.execute(
                                delete(Screen).where(Screen.id.in_(doomed_screens))
                            )
                        ).rowcount
                        or 0
                    )

        return ForgetResult(
            runs_deleted=runs,
            steps_deleted=steps,
            screens_deleted=screens,
            transitions_deleted=transitions,
        )

    # ----------------------------------------------------------------------
    # lifecycle
    # ----------------------------------------------------------------------

    async def aclose(self) -> None:
        await self._db.aclose()

    async def __aenter__(self) -> NavigationStore:
        await self._db.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    def __repr__(self) -> str:
        return f"NavigationStore(location={self._db.location!s})"

    # ----------------------------------------------------------------------
    # the queries everything above is built from
    # ----------------------------------------------------------------------

    async def _require_run(self, session: AsyncSession, run_id: int) -> Run:
        run = await session.get(Run, run_id)
        if run is None:
            raise RunNotFound(run_id)
        return run

    async def _require_open_run(self, session: AsyncSession, run_id: int) -> Run:
        run = await self._require_run(session, run_id)
        if run.ended_at is not None:
            raise RunAlreadyEnded(run_id)
        return run

    async def _require_run_for_session(self, session: AsyncSession, session_id: str) -> Run:
        statement = (
            select(Run)
            .where(Run.session_id == session_id, Run.ended_at.is_(None))
            .order_by(Run.started_at.desc())
            .limit(1)
        )
        run = (await session.execute(statement)).scalar_one_or_none()
        if run is None:
            raise NoRunForSession(session_id)
        return run

    async def _close_open_runs(
        self, session: AsyncSession, session_id: str, now: dt.datetime
    ) -> None:
        statement = select(Run).where(Run.session_id == session_id, Run.ended_at.is_(None))
        for stale in (await session.execute(statement)).scalars().all():
            stale.ended_at = now
        await session.flush()

    async def _upsert_screen(
        self, session: AsyncSession, snapshot: ScreenSnapshot, run: Run, now: dt.datetime
    ) -> tuple[Screen, bool]:
        """Find this screen or create it, and count the visit either way.

        Read-then-write rather than an upsert statement: SQLite serialises
        writers and this runs inside one transaction, so nothing can interleave,
        and knowing whether the row was created is what
        ``first_visit`` reports.
        """
        computed = fingerprint_source(snapshot.page_source)
        identity = ScreenIdentity(
            package=snapshot.package or run.package,
            activity=snapshot.activity or UNKNOWN,
            fingerprint=computed.value,
        )

        statement = select(Screen).where(
            Screen.package == identity.package,
            Screen.activity == identity.activity,
            Screen.fingerprint == identity.fingerprint,
        )
        screen = (await session.execute(statement)).scalar_one_or_none()

        title = snapshot.title or extract_title(snapshot.page_source)
        page_source = self._page_source_to_store(snapshot.page_source)

        if screen is None:
            screen = Screen(
                package=identity.package,
                activity=identity.activity,
                fingerprint=identity.fingerprint,
                title=title,
                page_source=page_source,
                screenshot_path=snapshot.screenshot_path,
                first_seen_at=now,
                last_seen_at=now,
                visit_count=1,
            )
            session.add(screen)
            await session.flush()
            return screen, True

        screen.last_seen_at = now
        screen.visit_count += 1
        # Only fill gaps. A title that was good enough the first time is not
        # improved by a later capture that happened to catch a spinner.
        if title and not screen.title:
            screen.title = title
        if page_source and not screen.page_source:
            screen.page_source = page_source
        if snapshot.screenshot_path and not screen.screenshot_path:
            screen.screenshot_path = snapshot.screenshot_path
        await session.flush()
        return screen, False

    def _page_source_to_store(self, page_source: str) -> str | None:
        if not self._config.store_page_source or not page_source:
            return None
        cap = self._config.max_page_source_bytes
        if cap > 0 and len(page_source.encode("utf-8")) > cap:
            # Truncate on a character boundary; the tail of a hierarchy is
            # never what identifies a screen.
            return page_source.encode("utf-8")[:cap].decode("utf-8", errors="ignore")
        return page_source

    async def _fold_transition(
        self,
        session: AsyncSession,
        *,
        from_screen_id: int,
        to_screen_id: int,
        action: str,
        target: str,
        ok: bool,
        app_version: str | None,
        now: dt.datetime,
    ) -> bool:
        statement = select(Transition).where(
            Transition.from_screen_id == from_screen_id,
            Transition.to_screen_id == to_screen_id,
            Transition.action == action,
            Transition.target == target,
        )
        edge = (await session.execute(statement)).scalar_one_or_none()
        if edge is None:
            session.add(
                Transition(
                    from_screen_id=from_screen_id,
                    to_screen_id=to_screen_id,
                    action=action,
                    target=target,
                    traversal_count=1,
                    success_count=1 if ok else 0,
                    first_seen_at=now,
                    last_seen_at=now,
                    last_app_version=app_version,
                )
            )
            await session.flush()
            return True

        edge.traversal_count += 1
        if ok:
            edge.success_count += 1
        edge.last_seen_at = now
        if app_version:
            edge.last_app_version = app_version
        await session.flush()
        return False

    async def _append_step(
        self,
        session: AsyncSession,
        *,
        run_id: int,
        action: str,
        target: str,
        from_screen_id: int | None,
        to_screen_id: int | None,
        ok: bool,
        detail: str | None,
        at: dt.datetime,
    ) -> int:
        seq = await self._last_seq(session, run_id) + 1
        session.add(
            Step(
                run_id=run_id,
                seq=seq,
                from_screen_id=from_screen_id,
                to_screen_id=to_screen_id,
                action=action,
                target=target,
                ok=ok,
                detail=detail,
                at=at,
            )
        )
        await session.flush()
        return seq

    async def _last_seq(self, session: AsyncSession, run_id: int) -> int:
        found = (
            await session.execute(select(func.max(Step.seq)).where(Step.run_id == run_id))
        ).scalar()
        return int(found or 0)

    async def _current_screen_id(self, session: AsyncSession, run_id: int) -> int | None:
        """Where the run is: the landing screen of its most recent step.

        Falls back to that step's origin, which is what a failed interaction
        with no observation afterwards leaves behind -- the device is still
        wherever it was.
        """
        statement = (
            select(Step.to_screen_id, Step.from_screen_id)
            .where(Step.run_id == run_id)
            .order_by(Step.seq.desc())
            .limit(1)
        )
        row = (await session.execute(statement)).first()
        if row is None:
            return None
        landed, came_from = row
        return landed if landed is not None else came_from

    async def _routes_from(
        self, session: AsyncSession, screen_id: int, *, limit: int
    ) -> tuple[RouteOption, ...]:
        statement = (
            select(Transition)
            .where(Transition.from_screen_id == screen_id)
            .order_by(Transition.traversal_count.desc())
        )
        edges = (await session.execute(statement)).scalars().all()
        targets = await self._known_by_ids(session, {edge.to_screen_id for edge in edges})

        options = [
            RouteOption(
                action=edge.action,
                target=edge.target,
                to_screen=known,
                traversal_count=edge.traversal_count,
                success_count=edge.success_count,
                last_seen_at=edge.last_seen_at,
                last_app_version=edge.last_app_version,
            )
            for edge in edges
            if (known := targets.get(edge.to_screen_id)) is not None
        ]
        # Best first: a caller that reads only the top option should get the
        # one most likely to work, not the one inserted first.
        options.sort(key=lambda option: (option.confidence, option.traversal_count), reverse=True)
        return tuple(options[: max(1, limit)])

    async def _edges_for_package(
        self, session: AsyncSession, package: str
    ) -> tuple[RouteEdge, ...]:
        """Every edge inside one app, as the search wants them.

        Scoped to the package so a path can never route through another app's
        screens, and loaded in one query because the search needs the whole
        graph rather than a neighbourhood at a time.
        """
        origin = Screen.__table__.alias("origin")
        statement = (
            select(
                Transition.from_screen_id,
                Transition.to_screen_id,
                Transition.action,
                Transition.target,
                Transition.traversal_count,
                Transition.success_count,
            )
            .join(origin, origin.c.id == Transition.from_screen_id)
            .where(origin.c.package == package)
        )
        rows = (await session.execute(statement)).all()
        return tuple(
            RouteEdge(
                from_screen_id=row[0],
                to_screen_id=row[1],
                action=row[2],
                target=row[3],
                confidence=_confidence(row[4], row[5]),
            )
            for row in rows
        )

    async def _resolve_destination(
        self, session: AsyncSession, package: str, destination: str
    ) -> Screen:
        """Turn what the caller typed into a screen.

        Tried in order of how specific the caller was being: an exact title,
        then an exact activity, then a substring of either. A numeric string is
        taken as a screen id first, because that is unambiguous and is what a
        previous tool call handed back.
        """
        wanted = destination.strip()

        if wanted.isdigit():
            by_id = await session.get(Screen, int(wanted))
            if by_id is not None and by_id.package == package:
                return by_id

        for clause in (
            Screen.title == wanted,
            Screen.activity == wanted,
        ):
            statement = (
                select(Screen)
                .where(Screen.package == package, clause)
                .order_by(Screen.visit_count.desc())
                .limit(1)
            )
            found = (await session.execute(statement)).scalar_one_or_none()
            if found is not None:
                return found

        pattern = f"%{wanted}%"
        statement = (
            select(Screen)
            .where(
                Screen.package == package,
                or_(Screen.title.ilike(pattern), Screen.activity.ilike(pattern)),
            )
            .order_by(Screen.visit_count.desc())
            .limit(1)
        )
        found = (await session.execute(statement)).scalar_one_or_none()
        if found is not None:
            return found

        known = (
            await session.execute(
                select(Screen.title, Screen.activity)
                .where(Screen.package == package)
                .order_by(Screen.visit_count.desc())
                .limit(8)
            )
        ).all()
        labels = tuple(str(title or activity) for title, activity in known if title or activity)
        raise DestinationNotFound(wanted, package, labels)

    async def _as_path_steps(
        self, session: AsyncSession, hops: tuple[RouteEdge, ...]
    ) -> tuple[PathStep, ...]:
        wanted = {hop.from_screen_id for hop in hops} | {hop.to_screen_id for hop in hops}
        screens = await self._known_by_ids(session, wanted)
        return tuple(
            PathStep(
                action=hop.action,
                target=hop.target,
                from_screen=screens[hop.from_screen_id],
                to_screen=screens[hop.to_screen_id],
                confidence=hop.confidence,
            )
            for hop in hops
            if hop.from_screen_id in screens and hop.to_screen_id in screens
        )

    async def _known_by_id(
        self, session: AsyncSession, screen_id: int | None
    ) -> KnownScreen | None:
        if screen_id is None:
            return None
        row = await session.get(Screen, screen_id)
        return None if row is None else _as_known(row)

    async def _known_by_ids(
        self, session: AsyncSession, screen_ids: set[int]
    ) -> dict[int, KnownScreen]:
        """Every screen named by a batch of rows, in one query rather than N."""
        if not screen_ids:
            return {}
        rows = (
            (await session.execute(select(Screen).where(Screen.id.in_(screen_ids)))).scalars().all()
        )
        return {row.id: _as_known(row) for row in rows}

    async def _summarise(self, session: AsyncSession, run: Run) -> RunSummary:
        steps = int(
            (
                await session.execute(
                    select(func.count()).select_from(Step).where(Step.run_id == run.id)
                )
            ).scalar()
            or 0
        )
        return RunSummary(
            run_id=run.id,
            device_id=run.device_id,
            package=run.package,
            started_at=run.started_at,
            app_version=run.app_version,
            session_id=run.session_id,
            ended_at=run.ended_at,
            step_count=steps,
        )


def _as_known(screen: Screen) -> KnownScreen:
    """A row as the domain's screen. The one place that conversion happens."""
    return KnownScreen(
        screen_id=screen.id,
        identity=ScreenIdentity(
            package=screen.package,
            activity=screen.activity,
            fingerprint=screen.fingerprint,
        ),
        title=screen.title,
        visit_count=screen.visit_count,
        first_seen_at=screen.first_seen_at,
        last_seen_at=screen.last_seen_at,
    )
