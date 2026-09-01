"""Domain results in, JSON-shaped payloads out. Pure, and the only converter.

The domain speaks :class:`pathlib.Path`, tuples of frozen dataclasses and
derived properties; MCP speaks JSON. This file is where one becomes the other,
and it is the only place that knows both -- a tool body calls a render function,
never a payload constructor, so a change to what a result looks like on the wire
happens once here rather than thirteen times.

Nothing in here awaits, spawns, reads the clock or touches the filesystem. It
imports the domain's models (Rule 0 §1 permits it: presentation may name the
domain) and this package's :mod:`.schemas`, and nothing else.
"""

from __future__ import annotations

from pathlib import Path

from ..domain.models import (
    AppiumSession,
    ElementInteractionResult,
    EndSessionResult,
    EnvironmentStatus,
    InstallDriverResult,
    InteractionResult,
    ListSessionsResult,
    PageSourceResult,
    ScreenshotResult,
    StartSessionResult,
    ToolStatus,
)
from .schemas import (
    ElementInteractionPayload,
    EndedSessionPayload,
    EnvironmentPayload,
    InstalledDriverPayload,
    InteractionPayload,
    PageSourcePayload,
    ScreenshotPayload,
    SessionListPayload,
    SessionPayload,
    StartedSessionPayload,
    ToolStatusPayload,
)

__all__ = [
    "render_tool_status",
    "render_environment",
    "render_installed_driver",
    "render_session",
    "render_started_session",
    "render_ended_session",
    "render_session_list",
    "render_screenshot",
    "render_page_source",
    "render_interaction",
    "render_element_interaction",
]


def _path(value: Path | None) -> str | None:
    """A Path is not JSON. Rendering one is the single reason this helper exists."""
    return None if value is None else str(value)


def render_tool_status(status: ToolStatus) -> ToolStatusPayload:
    """One host tool's state."""
    return ToolStatusPayload(
        name=status.name,
        present=status.present,
        version=status.version,
        path=_path(status.path),
        detail=status.detail,
    )


def render_environment(status: EnvironmentStatus) -> EnvironmentPayload:
    """The host report.

    ``ready`` and ``missing`` are the domain's own properties rather than a
    re-derivation here: "can I start a session" is a business question, and the
    presentation layer must not be the second place that answers it.
    """
    return EnvironmentPayload(
        ready=status.ready,
        tools=tuple(render_tool_status(tool) for tool in status.tools),
        missing=status.missing,
        drivers=status.drivers,
        server_url=status.server_url,
        server_running=status.server_running,
    )


def render_installed_driver(result: InstallDriverResult) -> InstalledDriverPayload:
    """An installed Appium driver."""
    return InstalledDriverPayload(
        driver_name=result.driver_name,
        version=result.version,
        already_installed=result.already_installed,
        duration_seconds=result.duration_seconds,
    )


def render_session(session: AppiumSession) -> SessionPayload:
    """One open session."""
    return SessionPayload(
        session_id=session.session_id,
        device_id=session.device_id,
        platform_name=session.platform_name,
        automation_name=session.automation_name,
        app_package=session.app_package,
        app_activity=session.app_activity,
        screen_width=session.screen_width,
        screen_height=session.screen_height,
    )


def render_started_session(result: StartSessionResult) -> StartedSessionPayload:
    """A newly opened session.

    ``session_id`` is repeated at the top level on purpose: it is the argument
    of the next twelve calls, and a client that has to reach into a nested
    object for it will eventually reach into the wrong one.
    """
    return StartedSessionPayload(
        session=render_session(result.session),
        session_id=result.session.session_id,
        server_started=result.server_started,
        duration_seconds=result.duration_seconds,
    )


def render_ended_session(result: EndSessionResult) -> EndedSessionPayload:
    """A closed session."""
    return EndedSessionPayload(
        session_id=result.session_id,
        device_id=result.device_id,
        duration_seconds=result.duration_seconds,
    )


def render_session_list(result: ListSessionsResult) -> SessionListPayload:
    """The open-session list, with the ids lifted out for the next call."""
    return SessionListPayload(
        sessions=tuple(render_session(session) for session in result.sessions),
        session_ids=result.session_ids,
        count=len(result.sessions),
    )


def render_screenshot(result: ScreenshotResult) -> ScreenshotPayload:
    """A captured screen."""
    return ScreenshotPayload(
        session_id=result.session_id,
        path=_path(result.path),
        image_base64=result.image_base64,
        width=result.width,
        height=result.height,
        size_bytes=result.size_bytes,
    )


def render_page_source(result: PageSourceResult) -> PageSourcePayload:
    """A UI hierarchy."""
    return PageSourcePayload(
        session_id=result.session_id,
        source=result.source,
        truncated=result.truncated,
        total_characters=result.total_characters,
    )


def render_interaction(result: InteractionResult) -> InteractionPayload:
    """A coordinate gesture."""
    return InteractionPayload(
        session_id=result.session_id,
        action=result.action,
        detail=result.detail,
        duration_seconds=result.duration_seconds,
    )


def render_element_interaction(result: ElementInteractionResult) -> ElementInteractionPayload:
    """An element gesture."""
    return ElementInteractionPayload(
        session_id=result.session_id,
        action=result.action,
        strategy=result.strategy,
        selector=result.selector,
        element_text=result.element_text,
        duration_seconds=result.duration_seconds,
    )
