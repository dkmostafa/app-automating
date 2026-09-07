"""The abstractions the application layer calls, declared by the caller.

Rule 0 §2: a port is owned by the layer that *needs* it, so these live in the
domain and the adapters under ``infrastructure/`` import them in order to be
satisfied by them. They are ``Protocol``s rather than ``ABC``s so an adapter
never has to inherit from the inner layer.

Split by capability (Rule 0 §3, ISP), and the split here is load-bearing rather
than decorative -- these three are used by three different callers:

* :class:`RouteRecorder` -- the **write** path. Satisfied by this module and
  consumed by this module's own ``infrastructure/appium_recorder``, which
  wraps ``appium_module``'s session and gesture ports so every interaction
  records itself as it happens. A caller holding only this can record and
  cannot read.
* :class:`RouteMemory` -- the **read** path, and what the MCP tools are built
  on. Changes nothing, so a tool holding only this is structurally incapable of
  corrupting the map it is reading.
* :class:`MemoryMaintenance` -- the **destructive** path. On its own port
  precisely so that neither of the other two can delete anything.

One class may satisfy all three; that is the adapter's business, not the
caller's.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import (
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
    SearchScreensRequest,
    SearchScreensResult,
    StartRunRequest,
    StartRunResult,
    WhereAmIRequest,
    WhereAmIResult,
)

__all__ = ["RouteRecorder", "RouteMemory", "MemoryMaintenance"]


@runtime_checkable
class RouteRecorder(Protocol):
    """Writing down where the device went, as it goes.

    The port ``appium_module`` is handed. Everything here is best-effort from
    the caller's point of view: a device interaction that succeeded must not be
    reported as failed because the memory could not be written, and the
    adapter's own errors are what let the caller make that distinction.
    """

    async def start_run(self, request: StartRunRequest) -> StartRunResult: ...

    async def record_observation(
        self, request: RecordObservationRequest
    ) -> RecordObservationResult: ...

    async def record_interaction(
        self, request: RecordInteractionRequest
    ) -> RecordInteractionResult: ...

    async def end_run(self, request: EndRunRequest) -> EndRunResult: ...


@runtime_checkable
class RouteMemory(Protocol):
    """Reading the map. Every method here is free of side effects."""

    async def where_am_i(self, request: WhereAmIRequest) -> WhereAmIResult: ...

    async def find_path(self, request: FindPathRequest) -> FindPathResult: ...

    async def search_screens(self, request: SearchScreensRequest) -> SearchScreensResult: ...

    async def get_run(self, request: GetRunRequest) -> GetRunResult: ...


@runtime_checkable
class MemoryMaintenance(Protocol):
    """Forgetting. The one capability that destroys what was learned."""

    async def forget(self, request: ForgetRequest) -> ForgetResult: ...
