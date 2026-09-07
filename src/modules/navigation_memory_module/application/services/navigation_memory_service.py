"""Navigation memory, as the rest of the product asks for it.

One method per thing a caller actually wants, named the way they would say it,
taking the arguments they actually have -- a session id, an app package, a
destination someone typed -- and building the domain's ``*Request`` objects
itself.

The service holds three ports and never an adapter (Rule 0 §1). The split is
what makes the read tools safe: the MCP surface is built on
:class:`~...domain.ports.RouteMemory` and
:class:`~...domain.ports.MemoryMaintenance`, while
:class:`~...domain.ports.RouteRecorder` is handed to ``appium_module`` instead.
Nothing that reads the map can write to it, and that is structural rather than
a convention.
"""

from __future__ import annotations

import datetime as dt

from ...domain.models import (
    EndRunRequest,
    EndRunResult,
    FindPathRequest,
    FindPathResult,
    ForgetRequest,
    ForgetResult,
    GetRunRequest,
    GetRunResult,
    RecordInteractionRequest,
    RecordInteractionResult,
    RecordObservationRequest,
    RecordObservationResult,
    ScreenSnapshot,
    SearchScreensRequest,
    SearchScreensResult,
    StartRunRequest,
    StartRunResult,
    WhereAmIRequest,
    WhereAmIResult,
)
from ...domain.ports import MemoryMaintenance, RouteMemory, RouteRecorder

__all__ = ["NavigationMemoryService"]


class NavigationMemoryService:
    """The navigation memory's operations, in the product's words."""

    def __init__(
        self,
        memory: RouteMemory,
        recorder: RouteRecorder,
        maintenance: MemoryMaintenance,
    ) -> None:
        self._memory = memory
        self._recorder = recorder
        self._maintenance = maintenance

    # -- reading: what the MCP tools are built on --------------------------

    async def where_am_i(self, session_id: str, *, limit: int = 20) -> WhereAmIResult:
        """The screen this session is on, and what has worked from it before.

        Answered from what the recorder already wrote, so the caller never has
        to carry a page source back across the boundary to ask.
        """
        return await self._memory.where_am_i(WhereAmIRequest(session_id=session_id, limit=limit))

    async def find_path(
        self,
        session_id: str,
        destination: str,
        *,
        max_steps: int = 12,
        min_confidence: float = 0.0,
    ) -> FindPathResult:
        """The most reliable known route from here to ``destination``.

        ``min_confidence`` is the knob that turns "any route I have ever seen"
        into "only routes I trust"; the default considers everything known,
        because a shaky route is still better information than none.
        """
        return await self._memory.find_path(
            FindPathRequest(
                session_id=session_id,
                destination=destination,
                max_steps=max_steps,
                min_confidence=min_confidence,
            )
        )

    async def search_screens(
        self, package: str, *, query: str = "", limit: int = 50
    ) -> SearchScreensResult:
        """Screens this memory knows for an app, most recently seen first.

        How a caller turns "the checkout page" into something
        :meth:`find_path` can resolve.
        """
        return await self._memory.search_screens(
            SearchScreensRequest(package=package, query=query, limit=limit)
        )

    async def get_run(self, run_id: int, *, limit: int = 200) -> GetRunResult:
        """One run's journal, in order, failures included."""
        return await self._memory.get_run(GetRunRequest(run_id=run_id, limit=limit))

    # -- forgetting --------------------------------------------------------

    async def forget(
        self,
        *,
        package: str | None = None,
        run_id: int | None = None,
        before: dt.datetime | None = None,
    ) -> ForgetResult:
        """Drop learned memory, by app, by run, or by age.

        Every selector is keyword-only and defaults to ``None``, and a call
        that names none of them is refused rather than treated as "everything"
        -- a full wipe should be something a caller asked for, not something it
        arrived at.
        """
        return await self._maintenance.forget(
            ForgetRequest(package=package, run_id=run_id, before=before)
        )

    # -- recording: driven by appium_module, not by a tool -----------------

    async def start_run(
        self,
        device_id: str,
        package: str,
        *,
        app_version: str | None = None,
        session_id: str | None = None,
    ) -> StartRunResult:
        """Begin recording a drive of one app on one device.

        Not on the MCP surface. ``appium_module`` calls this when a session
        opens, which is what makes the map complete without a model having to
        remember anything.
        """
        return await self._recorder.start_run(
            StartRunRequest(
                device_id=device_id,
                package=package,
                app_version=app_version,
                session_id=session_id,
            )
        )

    async def record_observation(
        self, run_id: int, snapshot: ScreenSnapshot
    ) -> RecordObservationResult:
        """Record where a run currently is, without recording an action."""
        return await self._recorder.record_observation(
            RecordObservationRequest(run_id=run_id, snapshot=snapshot)
        )

    async def record_interaction(
        self,
        run_id: int,
        action: str,
        *,
        target: str = "",
        snapshot_after: ScreenSnapshot | None = None,
        ok: bool = True,
        detail: str | None = None,
    ) -> RecordInteractionResult:
        """Record one action and the screen it landed on."""
        return await self._recorder.record_interaction(
            RecordInteractionRequest(
                run_id=run_id,
                action=action,
                target=target,
                snapshot_after=snapshot_after,
                ok=ok,
                detail=detail,
            )
        )

    async def end_run(self, run_id: int) -> EndRunResult:
        """Close a run. Nothing more can be recorded against it."""
        return await self._recorder.end_run(EndRunRequest(run_id=run_id))
