"""The typed payloads the Appium tools return.

Every tool returns one of these rather than a bare ``dict``, so FastMCP
publishes a real output schema next to each tool description and the client sees
field names and types instead of inferring them from prose. They are the
presentation layer's own vocabulary: JSON-shaped, flat, and free of
:class:`pathlib.Path`, tuples-of-dataclasses and properties -- everything the
domain expresses in Python and JSON cannot.

This file is the bottom of the presentation package's dependency order
(``schemas -> rendering -> errors -> device_tools``). It imports nothing from
the module: a payload knows nothing about the domain result it was built from,
which is what keeps the conversion in one place (:mod:`.rendering`) instead of
spread across thirteen tool bodies.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ToolStatusPayload",
    "EnvironmentPayload",
    "InstalledDriverPayload",
    "SessionPayload",
    "StartedSessionPayload",
    "EndedSessionPayload",
    "SessionListPayload",
    "ScreenshotPayload",
    "PageSourcePayload",
    "InteractionPayload",
    "ElementInteractionPayload",
]


class _Payload(BaseModel):
    """Shared configuration: frozen, and no field the schema did not declare."""

    model_config = ConfigDict(frozen=True, extra="forbid")


# --------------------------------------------------------------------------
# what this host can do
# --------------------------------------------------------------------------


class ToolStatusPayload(_Payload):
    """One host tool the Appium module depends on."""

    name: str = Field(description="Tool name: 'node' or 'appium'.")
    present: bool = Field(
        description="True when the tool was found and reported a version. False means broken."
    )
    version: str | None = Field(default=None, description="Version string, when it reported one.")
    path: str | None = Field(default=None, description="Absolute path it resolved to.")
    detail: str | None = Field(
        default=None, description="Why it is unusable. Null when present is true."
    )


class EnvironmentPayload(_Payload):
    """The answer to "can this machine automate a device"."""

    ready: bool = Field(
        description=(
            "True when every tool is present and at least one driver is installed, i.e. a "
            "session could be started right now without installing anything."
        )
    )
    tools: tuple[ToolStatusPayload, ...] = Field(description="One entry per required host tool.")
    missing: tuple[str, ...] = Field(
        description="Names of the unusable tools. Empty when nothing is missing."
    )
    drivers: tuple[str, ...] = Field(
        description="Installed Appium driver names, e.g. ['uiautomator2']. Android needs that one."
    )
    server_url: str = Field(description="Where this module will look for an Appium server.")
    server_running: bool = Field(
        description=(
            "True when a server is already answering at server_url. False is normal -- "
            "appium_start_session launches one on demand."
        )
    )


class InstalledDriverPayload(_Payload):
    """The result of provisioning an Appium driver on this host."""

    driver_name: str = Field(description="The driver that is now installed, e.g. 'uiautomator2'.")
    version: str | None = Field(default=None, description="Installed version, when reported.")
    already_installed: bool = Field(
        description="True when it was already present and nothing was downloaded."
    )
    duration_seconds: float = Field(
        description="Wall-clock time this call took. Minutes on a real download."
    )


# --------------------------------------------------------------------------
# sessions
# --------------------------------------------------------------------------


class SessionPayload(_Payload):
    """One open automation session."""

    session_id: str = Field(
        description="The handle every interaction tool takes. Valid until the session is ended."
    )
    device_id: str = Field(description="The adb serial this session is driving.")
    platform_name: str = Field(description="Platform being automated, e.g. 'Android'.")
    automation_name: str = Field(description="Appium driver in use, e.g. 'UiAutomator2'.")
    app_package: str | None = Field(
        default=None, description="Package under automation, when one was named or detected."
    )
    app_activity: str | None = Field(default=None, description="Activity, when known.")
    screen_width: int | None = Field(
        default=None, description="Screen width in pixels, for choosing tap coordinates."
    )
    screen_height: int | None = Field(default=None, description="Screen height in pixels.")


class StartedSessionPayload(_Payload):
    """The result of opening a session. It stays open after the tool returns."""

    session: SessionPayload = Field(description="The session that is now open.")
    session_id: str = Field(
        description="Lifted out of 'session' so the next call can read it without nesting."
    )
    server_started: bool = Field(
        description=(
            "True when this call had to launch an Appium server first. That server is owned "
            "by this MCP server and is shut down with it."
        )
    )
    duration_seconds: float = Field(description="Wall-clock time this call took.")


class EndedSessionPayload(_Payload):
    """The result of closing a session. The device is released."""

    session_id: str = Field(description="The session that was closed. It is no longer usable.")
    device_id: str = Field(description="The serial it was driving, now free for a new session.")
    duration_seconds: float = Field(description="Wall-clock time this call took.")


class SessionListPayload(_Payload):
    """The answer to "what am I currently driving"."""

    sessions: tuple[SessionPayload, ...] = Field(description="One entry per open session.")
    session_ids: tuple[str, ...] = Field(
        description="Just the ids, in the same order -- the usual next argument."
    )
    count: int = Field(description="len(sessions). Zero is a normal answer, not an error.")


# --------------------------------------------------------------------------
# reading the screen
# --------------------------------------------------------------------------


class ScreenshotPayload(_Payload):
    """A captured screen. Exactly one of 'path' and 'image_base64' is set."""

    session_id: str = Field(description="The session that was captured.")
    path: str | None = Field(
        default=None, description="Where the PNG was written, when save_path was given."
    )
    image_base64: str | None = Field(
        default=None,
        description="The PNG as base64, when no save_path was given. Large -- prefer a file.",
    )
    width: int | None = Field(default=None, description="Image width in pixels.")
    height: int | None = Field(default=None, description="Image height in pixels.")
    size_bytes: int = Field(description="Size of the PNG in bytes.")


class PageSourcePayload(_Payload):
    """The screen's UI hierarchy as XML."""

    session_id: str = Field(description="The session that was inspected.")
    source: str = Field(
        description=(
            "The hierarchy as XML. Element attributes here -- resource-id, content-desc, "
            "text, class -- are what the locator strategies match on."
        )
    )
    truncated: bool = Field(
        description=(
            "True when max_characters cut it short. The tail is missing, so an element you "
            "cannot find may simply be past the cut."
        )
    )
    total_characters: int = Field(
        description="Length before truncation, so you can tell a short screen from a cut one."
    )


# --------------------------------------------------------------------------
# driving the screen
# --------------------------------------------------------------------------


class InteractionPayload(_Payload):
    """The result of a gesture that addressed the screen by coordinates."""

    session_id: str = Field(description="The session the gesture was sent to.")
    action: str = Field(description="Which gesture: 'tap', 'swipe', 'scroll', 'press_key'.")
    detail: str = Field(
        description="What was actually sent, including the derived coordinates for a scroll."
    )
    duration_seconds: float = Field(description="Wall-clock time this call took.")


class ElementInteractionPayload(_Payload):
    """The result of a gesture that had to find an element first."""

    session_id: str = Field(description="The session the gesture was sent to.")
    action: str = Field(description="Which gesture: 'tap_element' or 'type_text'.")
    strategy: str = Field(
        description="Locator strategy used. 'focused' when text went to the focused field."
    )
    selector: str = Field(description="The selector that matched. Empty for 'focused'.")
    element_text: str | None = Field(
        default=None,
        description=(
            "The element's visible text when it was found, captured before the gesture. "
            "The cheapest confirmation that the right thing was hit."
        ),
    )
    duration_seconds: float = Field(description="Wall-clock time this call took.")
