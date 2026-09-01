"""The entities and boundary DTOs of the Appium module.

Every type here appears in a port signature (:mod:`..domain.ports`), which is
why it lives in the domain rather than in the adapter that happens to produce it
today. A second adapter -- a remote device cloud, a different automation
backend -- would speak these same dataclasses.

This module imports nothing but the standard library. It is the bottom of the
module's dependency graph, and it must stay importable on a host with no Appium,
no Node and no Android SDK installed.

Two vocabulary notes, because the module is about two different handles:

* a **device_id** is an adb serial (``emulator-5554``, or a hardware serial like
  ``R58M12ABCDE``). It identifies a device attached to the host, and it is what
  :class:`StartSessionRequest` takes. The Android module's device listing is
  where one comes from.
* a **session_id** identifies a live automation session bound to one device and
  one app. Every interaction takes it. It exists only between
  :meth:`SessionLifecycle.start_session` and
  :meth:`SessionLifecycle.end_session`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    # requests
    "CheckEnvironmentRequest",
    "InstallDriverRequest",
    "StartSessionRequest",
    "EndSessionRequest",
    "ListSessionsRequest",
    "TakeScreenshotRequest",
    "GetPageSourceRequest",
    "TapRequest",
    "TapElementRequest",
    "SwipeRequest",
    "ScrollRequest",
    "TypeTextRequest",
    "PressKeyRequest",
    # results and value objects
    "ToolStatus",
    "EnvironmentStatus",
    "AppiumSession",
    "StartSessionResult",
    "EndSessionResult",
    "ListSessionsResult",
    "InstallDriverResult",
    "ScreenshotResult",
    "PageSourceResult",
    "InteractionResult",
    "ElementInteractionResult",
]


#: The locator strategies this module accepts, in the order a caller should
#: prefer them. ``accessibility_id`` is stable across layout changes and is what
#: a well-built app exposes on purpose; ``xpath`` is the slow last resort that
#: breaks whenever the hierarchy moves.
LOCATOR_STRATEGIES = ("accessibility_id", "id", "text", "class_name", "xpath", "uiautomator")

#: Directions :class:`ScrollRequest` understands. The name says which way the
#: *content* moves, matching how a person describes scrolling a page.
SCROLL_DIRECTIONS = ("up", "down", "left", "right")


# --------------------------------------------------------------------------
# requests
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CheckEnvironmentRequest:
    """Input for :meth:`AppiumEnvironment.check_environment`."""

    #: Also ask the Appium server for ``/status``. One HTTP call; the only way
    #: to tell "installed" from "installed and answering".
    probe_server: bool = True


@dataclass(frozen=True, slots=True)
class InstallDriverRequest:
    """Input for :meth:`AppiumEnvironment.install_driver`."""

    #: An Appium driver name, e.g. ``uiautomator2``.
    driver_name: str
    #: Run the installer even when the driver is already present.
    reinstall: bool = False


@dataclass(frozen=True, slots=True)
class StartSessionRequest:
    """Input for :meth:`SessionLifecycle.start_session`.

    The three app fields are all optional and mean three different things:
    ``app_path`` installs and launches an APK, ``app_package`` with
    ``app_activity`` launches something already installed, and all three unset
    attaches to whatever the device is currently showing.
    """

    device_id: str
    #: Absolute path to an ``.apk`` to install and launch.
    app_path: Path | None = None
    #: Package id of an already-installed app, e.g. ``com.android.settings``.
    app_package: str | None = None
    #: Activity to launch within ``app_package``. Requires ``app_package``.
    app_activity: str | None = None
    #: Keep app data between sessions instead of clearing it on start.
    no_reset: bool = True
    #: Seconds the server keeps the session alive with no incoming command.
    #: ``None`` falls back to the adapter's configured value.
    new_command_timeout_seconds: float | None = None
    #: Seconds to wait for the session to be created. Real devices and cold
    #: emulators are slow; ``None`` uses the adapter's configured value.
    startup_timeout_seconds: float | None = None
    #: Raw Appium capabilities merged last, overriding everything above. The
    #: escape hatch for a capability this module has no field for.
    extra_capabilities: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class EndSessionRequest:
    """Input for :meth:`SessionLifecycle.end_session`."""

    session_id: str


@dataclass(frozen=True, slots=True)
class ListSessionsRequest:
    """Input for :meth:`SessionLifecycle.list_sessions`."""

    #: Drop sessions this process started but whose server has since dropped
    #: them. Costs one round trip per session.
    verify_alive: bool = False


@dataclass(frozen=True, slots=True)
class TakeScreenshotRequest:
    """Input for :meth:`ScreenInspector.take_screenshot`."""

    session_id: str
    #: Where to write the PNG. ``None`` returns the image inline as base64 and
    #: writes nothing.
    save_path: Path | None = None


@dataclass(frozen=True, slots=True)
class GetPageSourceRequest:
    """Input for :meth:`ScreenInspector.get_page_source`."""

    session_id: str
    #: Cap the returned XML. The hierarchy of a busy screen runs to hundreds of
    #: kilobytes, which is not something to hand a model whole.
    max_characters: int | None = None


@dataclass(frozen=True, slots=True)
class TapRequest:
    """Input for :meth:`DeviceInteraction.tap`."""

    session_id: str
    #: Pixels from the left edge of the screen.
    x: int
    #: Pixels from the top edge of the screen.
    y: int
    #: Hold before releasing. 0 is a plain tap; ~1000 is a long press.
    duration_ms: int = 0


@dataclass(frozen=True, slots=True)
class TapElementRequest:
    """Input for :meth:`DeviceInteraction.tap_element`."""

    session_id: str
    #: One of :data:`LOCATOR_STRATEGIES`.
    strategy: str
    selector: str
    #: Seconds to keep retrying the lookup before giving up. 0 checks once.
    timeout_seconds: float = 10.0
    duration_ms: int = 0


@dataclass(frozen=True, slots=True)
class SwipeRequest:
    """Input for :meth:`DeviceInteraction.swipe`."""

    session_id: str
    start_x: int
    start_y: int
    end_x: int
    end_y: int
    #: How long the finger travels. Too fast reads as a fling, too slow as a
    #: drag; a few hundred milliseconds is an ordinary swipe.
    duration_ms: int = 300


@dataclass(frozen=True, slots=True)
class ScrollRequest:
    """Input for :meth:`DeviceInteraction.scroll`.

    The coordinate-free form of :class:`SwipeRequest`: the adapter reads the
    screen size and derives a swipe across the middle of it, so a caller that
    just wants "further down the list" does not have to know the resolution.
    """

    session_id: str
    #: One of :data:`SCROLL_DIRECTIONS`.
    direction: str
    #: Fraction of the screen to travel, 0 < distance <= 1.0.
    distance: float = 0.5
    duration_ms: int = 300


@dataclass(frozen=True, slots=True)
class TypeTextRequest:
    """Input for :meth:`DeviceInteraction.type_text`.

    With ``strategy``/``selector`` the text goes into that element; without
    them it goes to whatever currently holds focus.
    """

    session_id: str
    text: str
    strategy: str | None = None
    selector: str | None = None
    #: Empty the field before typing. Without it the text is appended.
    clear_first: bool = False
    timeout_seconds: float = 10.0


@dataclass(frozen=True, slots=True)
class PressKeyRequest:
    """Input for :meth:`DeviceInteraction.press_key`."""

    session_id: str
    #: A key name such as ``back``, ``home``, ``enter``. The adapter maps it to
    #: an Android keycode; unknown names are rejected before anything is sent.
    key: str


# --------------------------------------------------------------------------
# results and value objects
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolStatus:
    """One host tool the module depends on: whether it is there, and which one."""

    name: str
    #: False when the tool could not be found or would not report a version.
    present: bool
    version: str | None = None
    path: Path | None = None
    #: Why it is not usable. None when :attr:`present`.
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class EnvironmentStatus:
    """What this host can actually do, tool by tool.

    Deliberately a report rather than a raised error: "is my machine set up"
    is a question with a useful answer even -- especially -- when the answer is
    no, and a caller needs the whole picture to know what to fix first.
    """

    tools: tuple[ToolStatus, ...]
    #: Appium driver names installed on this host, e.g. ``("uiautomator2",)``.
    drivers: tuple[str, ...]
    #: URL the module would use for a session.
    server_url: str
    #: True when that URL answered ``/status``. False when ``probe_server`` was
    #: off, or when nothing was listening.
    server_running: bool

    @property
    def ready(self) -> bool:
        """True when a session could be started right now without installing anything."""
        return all(tool.present for tool in self.tools) and bool(self.drivers)

    @property
    def missing(self) -> tuple[str, ...]:
        """Names of the tools that are not usable, in declaration order."""
        return tuple(tool.name for tool in self.tools if not tool.present)

    def tool(self, name: str) -> ToolStatus | None:
        for status in self.tools:
            if status.name == name:
                return status
        return None


@dataclass(frozen=True, slots=True)
class InstallDriverResult:
    driver_name: str
    version: str | None
    #: True when the driver was already there and no installer was run.
    already_installed: bool
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class AppiumSession:
    """One live automation session."""

    session_id: str
    device_id: str
    platform_name: str
    automation_name: str
    app_package: str | None = None
    app_activity: str | None = None
    #: Screen size in pixels, as the device reported it at session start.
    screen_width: int | None = None
    screen_height: int | None = None

    @property
    def has_screen_size(self) -> bool:
        return self.screen_width is not None and self.screen_height is not None


@dataclass(frozen=True, slots=True)
class StartSessionResult:
    session: AppiumSession
    #: True when this call had to launch the Appium server first.
    server_started: bool
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class EndSessionResult:
    session_id: str
    device_id: str
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class ListSessionsResult:
    sessions: tuple[AppiumSession, ...] = field(default=())

    @property
    def session_ids(self) -> tuple[str, ...]:
        return tuple(session.session_id for session in self.sessions)

    def by_session_id(self, session_id: str) -> AppiumSession | None:
        for session in self.sessions:
            if session.session_id == session_id:
                return session
        return None

    def by_device_id(self, device_id: str) -> tuple[AppiumSession, ...]:
        return tuple(session for session in self.sessions if session.device_id == device_id)


@dataclass(frozen=True, slots=True)
class ScreenshotResult:
    session_id: str
    #: Where the PNG was written, when ``save_path`` asked for a file.
    path: Path | None
    #: The PNG as base64, when no ``save_path`` was given. Exactly one of this
    #: and :attr:`path` is set.
    image_base64: str | None
    width: int | None
    height: int | None
    size_bytes: int


@dataclass(frozen=True, slots=True)
class PageSourceResult:
    session_id: str
    #: The UI hierarchy as XML.
    source: str
    #: True when ``max_characters`` cut it short, so a caller knows the tail is
    #: missing rather than the screen being empty.
    truncated: bool
    #: Length before any truncation.
    total_characters: int


@dataclass(frozen=True, slots=True)
class InteractionResult:
    """The outcome of a gesture that addressed the screen rather than an element."""

    session_id: str
    #: The gesture that was performed: ``tap``, ``swipe``, ``scroll``, ...
    action: str
    #: Human-readable statement of what was actually sent, for the caller's log.
    detail: str
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class ElementInteractionResult:
    """The outcome of a gesture that had to find an element first."""

    session_id: str
    action: str
    strategy: str
    selector: str
    #: The element's visible text at the moment it was found, when it had any.
    element_text: str | None
    duration_seconds: float
