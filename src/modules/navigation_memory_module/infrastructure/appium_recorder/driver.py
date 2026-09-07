"""appium_module's SessionLifecycle and DeviceInteraction, recording as they run.

A decorator and nothing else: every call is delegated to the real port first,
and the outcome is written down second. ``appium_module`` never imports this
class, never names it, and never learns it exists -- the composition root asks
``appium_module``'s own ``build_appium_device_service``/``appium_device_service``
for a generic "wrap my ports" hook (see that module's ``application/di.py``,
``PortDecorator``) and this is what fills it in. Structurally,
:class:`RecordingDeviceDriver` satisfies ``appium_module``'s ``SessionLifecycle``
and ``DeviceInteraction`` Protocols, so the service that ends up holding one
cannot tell it apart from the real adapter.

The contract this class must hold, because it is a side channel and nothing
more:

* **The recording never changes an outcome.** A gesture that reached the
  device succeeded whether or not it got written down, so every failure on the
  recording path is swallowed rather than raised.
* **A failed gesture is still recorded**, with ``ok=False``. "Tapping this here
  does nothing" is knowledge; a memory that only remembers successes cannot
  warn anyone off a dead end.
* **Typed text is never recorded.** It is routinely a password or a card
  number, and the map only needs to know a field was filled, not with what.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import TypeVar

from modules.appium_module.domain.errors import AppiumModuleError
from modules.appium_module.domain.models import (
    ElementInteractionResult,
    EndSessionRequest,
    EndSessionResult,
    GetPageSourceRequest,
    InteractionResult,
    ListSessionsRequest,
    ListSessionsResult,
    PressKeyRequest,
    ScrollRequest,
    StartSessionRequest,
    StartSessionResult,
    SwipeRequest,
    TapElementRequest,
    TapRequest,
    TypeTextRequest,
)
from modules.appium_module.domain.ports import DeviceInteraction, ScreenInspector, SessionLifecycle

from ...domain.errors import NavigationMemoryError
from ...domain.models import (
    EndRunRequest,
    RecordInteractionRequest,
    RecordObservationRequest,
    ScreenSnapshot,
    StartRunRequest,
)
from ...domain.ports import RouteRecorder

__all__ = ["RecordingDeviceDriver"]

_T = TypeVar("_T")


class RecordingDeviceDriver:
    """Wraps a real ``SessionLifecycle`` and ``DeviceInteraction``, recording as it goes.

    Takes a :class:`~...domain.ports.RouteRecorder` and never an adapter, so the
    composition root decides what it actually writes to (Rule 0 §2). Holds the
    one piece of state the recording needs -- which run belongs to which
    session -- because appium's ports speak in session ids and this module's
    recorder speaks in run ids.
    """

    def __init__(
        self,
        sessions: SessionLifecycle,
        screen: ScreenInspector,
        interaction: DeviceInteraction,
        recorder: RouteRecorder,
    ) -> None:
        self._sessions = sessions
        self._screen = screen
        self._interaction = interaction
        self._recorder = recorder
        #: session id -> run id, for sessions this driver has opened.
        self._runs: dict[str, int] = {}

    @property
    def tracked_sessions(self) -> tuple[str, ...]:
        """The sessions currently being recorded. For diagnostics."""
        return tuple(self._runs)

    # -- session lifecycle ---------------------------------------------------

    async def start_session(self, request: StartSessionRequest) -> StartSessionResult:
        result = await self._sessions.start_session(request)
        await self._open_run(result)
        return result

    async def end_session(self, request: EndSessionRequest) -> EndSessionResult:
        result = await self._sessions.end_session(request)
        await self._close_run(result.session_id)
        return result

    async def list_sessions(self, request: ListSessionsRequest) -> ListSessionsResult:
        return await self._sessions.list_sessions(request)

    # -- gestures --------------------------------------------------------------

    async def tap(self, request: TapRequest) -> InteractionResult:
        return await self._recorded(
            request.session_id, "tap", f"{request.x},{request.y}", self._interaction.tap(request)
        )

    async def tap_element(self, request: TapElementRequest) -> ElementInteractionResult:
        return await self._recorded(
            request.session_id,
            "tap_element",
            f"{request.strategy}={request.selector}",
            self._interaction.tap_element(request),
        )

    async def swipe(self, request: SwipeRequest) -> InteractionResult:
        return await self._recorded(
            request.session_id,
            "swipe",
            f"{request.start_x},{request.start_y}->{request.end_x},{request.end_y}",
            self._interaction.swipe(request),
        )

    async def scroll(self, request: ScrollRequest) -> InteractionResult:
        return await self._recorded(
            request.session_id, "scroll", request.direction, self._interaction.scroll(request)
        )

    async def type_text(self, request: TypeTextRequest) -> ElementInteractionResult:
        # The text itself is never recorded -- see the module docstring.
        target = (
            f"{request.strategy}={request.selector}"
            if request.strategy and request.selector
            else ""
        )
        return await self._recorded(
            request.session_id, "type_text", target, self._interaction.type_text(request)
        )

    async def press_key(self, request: PressKeyRequest) -> InteractionResult:
        return await self._recorded(
            request.session_id, "press_key", request.key, self._interaction.press_key(request)
        )

    # -- recording -------------------------------------------------------------

    async def _open_run(self, result: StartSessionResult) -> None:
        """Open a run for the session, and note where it starts.

        A session with no known package is not recorded at all: every screen is
        keyed by its app, and filing an unknown app under the empty string would
        merge unrelated apps into one map.
        """
        session = result.session
        if not session.app_package:
            return
        try:
            started = await self._recorder.start_run(
                StartRunRequest(
                    device_id=session.device_id,
                    package=session.app_package,
                    session_id=session.session_id,
                )
            )
        except NavigationMemoryError:
            return
        self._runs[session.session_id] = started.run.run_id

        # Captured here so there is a screen for the first gesture's edge to
        # come *from*; without it that edge is lost on every short run.
        page_source = await self._capture(session.session_id)
        if page_source is None:
            return
        try:
            await self._recorder.record_observation(
                RecordObservationRequest(
                    run_id=started.run.run_id,
                    snapshot=ScreenSnapshot(package=session.app_package, page_source=page_source),
                )
            )
        except NavigationMemoryError:
            return

    async def _close_run(self, session_id: str) -> None:
        """Close the run, whatever happened to the session.

        Dropped from the map first, so a failure to close cannot leave this
        driver writing into a run the store has already finished.
        """
        run_id = self._runs.pop(session_id, None)
        if run_id is None:
            return
        try:
            await self._recorder.end_run(EndRunRequest(run_id=run_id))
        except NavigationMemoryError:
            return

    async def _recorded(self, session_id: str, action: str, target: str, call: Awaitable[_T]) -> _T:
        """Perform a gesture and write down what it did, whichever way it went."""
        try:
            result = await call
        except AppiumModuleError as exc:
            await self._record(session_id, action, target, ok=False, detail=str(exc))
            raise
        await self._record(session_id, action, target, ok=True)
        return result

    async def _record(
        self, session_id: str, action: str, target: str, *, ok: bool, detail: str | None = None
    ) -> None:
        run_id = self._runs.get(session_id)
        if run_id is None:
            return
        try:
            await self._recorder.record_interaction(
                RecordInteractionRequest(
                    run_id=run_id,
                    action=action,
                    target=target,
                    snapshot_after=await self._snapshot(session_id),
                    ok=ok,
                    detail=detail,
                )
            )
        except NavigationMemoryError:
            return

    async def _snapshot(self, session_id: str) -> ScreenSnapshot | None:
        """The screen after a gesture, as this module's value object.

        ``None`` when it could not be captured: the step is still recorded, and
        the store simply learns no edge from it.
        """
        page_source = await self._capture(session_id)
        if page_source is None:
            return None
        # The store fills the package in from the run, so an empty one here is
        # correct rather than a gap -- appium's result does not carry it.
        return ScreenSnapshot(package="", page_source=page_source)

    async def _capture(self, session_id: str) -> str | None:
        try:
            return (
                await self._screen.get_page_source(GetPageSourceRequest(session_id=session_id))
            ).source
        except AppiumModuleError:
            return None

    def __repr__(self) -> str:
        return f"RecordingDeviceDriver(sessions={len(self._runs)})"
