"""The abstractions the application layer calls, declared by the caller.

Rule 0 §2: a port is owned by the layer that *needs* it, so these live in the
domain and the adapters under ``infrastructure/`` import them in order to be
satisfied by them. They are ``Protocol``s rather than ``ABC``s so an adapter
never has to inherit from the inner layer -- the arrow stays pointing one way
even in the class statement.

They are split by capability rather than by adapter (Rule 0 §3, ISP), and the
split here is load-bearing rather than decorative. Four capabilities, in the
order a caller meets them:

* :class:`AppiumEnvironment` -- "can this host automate anything at all", and
  the one operation that changes the host rather than a device.
* :class:`SessionLifecycle` -- opening and closing the expensive, stateful thing
  everything else needs.
* :class:`ScreenInspector` -- reading the screen. Changes nothing.
* :class:`DeviceInteraction` -- driving the screen. Changes everything.

A use case that only wants to read a screen takes :class:`ScreenInspector` and
is then structurally incapable of tapping anything. One class may satisfy all
four; that is the adapter's business, not the caller's.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import (
    CheckEnvironmentRequest,
    ElementInteractionResult,
    EndSessionRequest,
    EndSessionResult,
    EnvironmentStatus,
    GetPageSourceRequest,
    InstallDriverRequest,
    InstallDriverResult,
    InteractionResult,
    ListSessionsRequest,
    ListSessionsResult,
    PageSourceResult,
    PressKeyRequest,
    ScreenshotResult,
    ScrollRequest,
    StartSessionRequest,
    StartSessionResult,
    SwipeRequest,
    TakeScreenshotRequest,
    TapElementRequest,
    TapRequest,
    TypeTextRequest,
)

__all__ = [
    "AppiumEnvironment",
    "SessionLifecycle",
    "ScreenInspector",
    "DeviceInteraction",
]


@runtime_checkable
class AppiumEnvironment(Protocol):
    """The host's automation toolchain: what is installed, and installing it."""

    async def check_environment(self, request: CheckEnvironmentRequest) -> EnvironmentStatus: ...

    async def install_driver(self, request: InstallDriverRequest) -> InstallDriverResult: ...


@runtime_checkable
class SessionLifecycle(Protocol):
    """An automation session, from creation to teardown.

    A session binds one device to one app and costs seconds to create, which is
    why it is a first-class thing with an id rather than something implied by
    each interaction.
    """

    async def start_session(self, request: StartSessionRequest) -> StartSessionResult: ...

    async def end_session(self, request: EndSessionRequest) -> EndSessionResult: ...

    async def list_sessions(self, request: ListSessionsRequest) -> ListSessionsResult: ...


@runtime_checkable
class ScreenInspector(Protocol):
    """Reading what is on screen. Every method here is free of side effects."""

    async def take_screenshot(self, request: TakeScreenshotRequest) -> ScreenshotResult: ...

    async def get_page_source(self, request: GetPageSourceRequest) -> PageSourceResult: ...


@runtime_checkable
class DeviceInteraction(Protocol):
    """Driving the screen: everything here changes the state of the device."""

    async def tap(self, request: TapRequest) -> InteractionResult: ...

    async def tap_element(self, request: TapElementRequest) -> ElementInteractionResult: ...

    async def swipe(self, request: SwipeRequest) -> InteractionResult: ...

    async def scroll(self, request: ScrollRequest) -> InteractionResult: ...

    async def type_text(self, request: TypeTextRequest) -> ElementInteractionResult: ...

    async def press_key(self, request: PressKeyRequest) -> InteractionResult: ...
