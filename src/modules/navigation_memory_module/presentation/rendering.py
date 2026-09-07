"""Domain results in, JSON-shaped payloads out. Pure, and the only converter.

The domain speaks :class:`datetime`, tuples of frozen dataclasses and derived
properties; MCP speaks JSON. This file is where one becomes the other, and it is
the only place that knows both -- a tool body calls a render function, never a
payload constructor, so a change to what a result looks like on the wire happens
once here rather than in five tool bodies.

Nothing in here awaits, spawns, reads the clock or touches the filesystem. It
imports the domain's models (Rule 0 §1 permits it) and this package's
:mod:`.schemas`, and nothing else.
"""

from __future__ import annotations

import datetime as dt

from ..domain.models import (
    FindPathResult,
    ForgetResult,
    GetRunResult,
    JournalEntry,
    KnownScreen,
    PathStep,
    RouteOption,
    RunSummary,
    SearchScreensResult,
    WhereAmIResult,
)
from .schemas import (
    ForgetPayload,
    JournalEntryPayload,
    PathPayload,
    PathStepPayload,
    RoutePayload,
    RunJournalPayload,
    RunPayload,
    ScreenListPayload,
    ScreenPayload,
    WhereAmIPayload,
)

__all__ = [
    "render_screen",
    "render_route",
    "render_where_am_i",
    "render_path",
    "render_screen_list",
    "render_run",
    "render_run_journal",
    "render_forget",
]

#: Confidence is a smoothed ratio and carries no meaningful precision past two
#: places; rounding here keeps a payload readable rather than showing a model
#: 0.7222222222222222 and inviting it to treat the tail as signal.
CONFIDENCE_PLACES = 2


def _moment(value: dt.datetime | None) -> str | None:
    """A datetime is not JSON. Rendering one is the reason this helper exists."""
    return None if value is None else value.isoformat()


def render_screen(screen: KnownScreen) -> ScreenPayload:
    """One known screen. ``label`` is the domain's property, not a re-derivation."""
    return ScreenPayload(
        screen_id=screen.screen_id,
        label=screen.label,
        package=screen.identity.package,
        activity=screen.identity.activity or None,
        title=screen.title,
        visit_count=screen.visit_count,
        last_seen_at=_moment(screen.last_seen_at),
    )


def render_route(option: RouteOption) -> RoutePayload:
    """One learned way out of a screen.

    Both the raw counts and the smoothed confidence are surfaced: the ratio is
    what to sort by, and the counts are what say whether the ratio means
    anything yet.
    """
    return RoutePayload(
        action=option.action,
        target=option.target,
        leads_to=render_screen(option.to_screen),
        confidence=round(option.confidence, CONFIDENCE_PLACES),
        traversals=option.traversal_count,
        successes=option.success_count,
        last_app_version=option.last_app_version,
    )


def render_where_am_i(result: WhereAmIResult) -> WhereAmIPayload:
    """Where a session is, with the run id a caller needs to look up its journal."""
    return WhereAmIPayload(
        session_id=result.run.session_id or "",
        run_id=result.run.run_id,
        package=result.run.package,
        screen=render_screen(result.screen),
        routes=tuple(render_route(option) for option in result.routes),
        unexplored=result.unexplored,
    )


def render_path_step(step: PathStep) -> PathStepPayload:
    return PathStepPayload(
        action=step.action,
        target=step.target,
        leads_to=render_screen(step.to_screen),
        confidence=round(step.confidence, CONFIDENCE_PLACES),
    )


def render_path(result: FindPathResult) -> PathPayload:
    """A planned route. ``confidence`` is the weakest hop, not the average."""
    return PathPayload(
        origin=render_screen(result.origin),
        destination=render_screen(result.destination),
        steps=tuple(render_path_step(step) for step in result.steps),
        confidence=round(result.confidence, CONFIDENCE_PLACES),
        already_there=result.already_there,
    )


def render_screen_list(result: SearchScreensResult) -> ScreenListPayload:
    """The screens known for one app, with the count a caller would else compute."""
    return ScreenListPayload(
        package=result.package,
        screens=tuple(render_screen(screen) for screen in result.screens),
        count=len(result.screens),
    )


def render_run(run: RunSummary) -> RunPayload:
    return RunPayload(
        run_id=run.run_id,
        device_id=run.device_id,
        package=run.package,
        started_at=run.started_at.isoformat(),
        ended_at=_moment(run.ended_at),
        app_version=run.app_version,
        session_id=run.session_id,
        step_count=run.step_count,
        open=run.open,
    )


def render_journal_entry(entry: JournalEntry) -> JournalEntryPayload:
    """One recorded action. Screens are rendered as labels rather than nested
    objects: a journal is read top to bottom, and full screen records at every
    line would bury the sequence that is the point of reading it."""
    return JournalEntryPayload(
        seq=entry.seq,
        action=entry.action,
        target=entry.target,
        ok=entry.ok,
        moved=entry.moved,
        at=entry.at.isoformat(),
        from_label=entry.from_screen.label if entry.from_screen else None,
        to_label=entry.to_screen.label if entry.to_screen else None,
        detail=entry.detail,
    )


def render_run_journal(result: GetRunResult) -> RunJournalPayload:
    """A run and its journal.

    ``failure_count`` is surfaced rather than left to be counted because it
    changes what the journal means: a run of forty steps with nine failures is
    a different thing from a clean one, and that should be visible before
    reading every line.
    """
    return RunJournalPayload(
        run=render_run(result.run),
        entries=tuple(render_journal_entry(entry) for entry in result.entries),
        failure_count=len(result.failures),
    )


def render_forget(result: ForgetResult) -> ForgetPayload:
    """What was deleted."""
    return ForgetPayload(
        runs_deleted=result.runs_deleted,
        steps_deleted=result.steps_deleted,
        screens_deleted=result.screens_deleted,
        transitions_deleted=result.transitions_deleted,
        total=result.total,
    )
