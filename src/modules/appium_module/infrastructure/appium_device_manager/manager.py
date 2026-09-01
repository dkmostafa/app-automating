"""The Appium adapter: one class, three injected collaborators, thirteen operations.

Structurally satisfies all four of the module's ports (Rule 0 §2) without
inheriting from any of them -- ``domain/ports.py`` declares what the application
layer needs, and this class happens to be shaped like it. That one class covers
four ports is an implementation detail the callers never see: a service is
handed the narrow port it needs and is then incapable of more.

Every public method has the shape Rule 1 §2 requires -- one ``request``
dataclass in, one result dataclass out, no exceptions -- and the work itself is
delegated:

* :class:`~.process.CommandRunner` owns the process table,
* :class:`~.server.AppiumServer` owns the server process,
* :class:`~.sessions.SessionRegistry` owns the live sessions and the thread
  boundary in front of the synchronous client.

What is left here is orchestration and translation, which is the only thing a
manager should be. Nothing below this file reads the environment, and nothing
above it sees a subprocess, a URL or a ``WebDriverException``.
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import time
from pathlib import Path
from typing import Any

from ...domain.models import (
    AppiumSession,
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
    ToolStatus,
    TypeTextRequest,
)
from .config import AppiumConfig
from .errors import (
    DriverInstallCommandError,
    DriverMissingError,
    ScreenshotCaptureError,
)
from .models import CommandResult
from .parsing import (
    parse_driver_version,
    parse_installed_drivers,
    parse_version_output,
    png_dimensions,
    truncate,
    validate_app_path,
    validate_coordinates,
    validate_device_id,
    validate_key,
    validate_locator,
    validate_scroll,
)
from .process import CommandRunner
from .server import AppiumServer
from .sessions import SessionRegistry

__all__ = ["AppiumDeviceManager"]

#: The tools reported by :meth:`AppiumDeviceManager.check_environment`, in the
#: order they matter: without node there is no appium, and without appium there
#: is no driver.
REPORTED_TOOLS = ("node", "appium")


class AppiumDeviceManager:
    """Drives Android devices -- emulated or physical -- through Appium."""

    def __init__(
        self,
        config: AppiumConfig,
        runner: CommandRunner,
        server: AppiumServer,
        sessions: SessionRegistry,
    ) -> None:
        self._config = config
        self._runner = runner
        self._server = server
        self._sessions = sessions

    # ----------------------------------------------------------------------
    # AppiumEnvironment
    # ----------------------------------------------------------------------

    async def check_environment(self, request: CheckEnvironmentRequest) -> EnvironmentStatus:
        """Report what this host can automate. Never raises for a missing tool.

        The one operation whose whole job is to describe a broken host, so a
        missing binary is data here rather than an exception -- a caller asking
        "am I set up" needs the entire picture, not the first thing that failed.
        """
        tools = tuple([await self._tool_status(name) for name in REPORTED_TOOLS])
        drivers = await self._installed_drivers()
        status = await self._server.probe() if request.probe_server else None
        return EnvironmentStatus(
            tools=tools,
            drivers=drivers,
            server_url=self._config.server_url,
            server_running=bool(status and status.running),
        )

    async def install_driver(self, request: InstallDriverRequest) -> InstallDriverResult:
        """Install an Appium driver, or report that it was already there.

        The only operation in this module that changes the host rather than a
        device, which is why it is a named call and never a side effect of
        starting a session.
        """
        started = time.monotonic()
        driver_name = request.driver_name.strip() or self._config.driver_name
        installed = await self._installed_drivers()

        if driver_name in installed and not request.reinstall:
            return InstallDriverResult(
                driver_name=driver_name,
                version=await self._driver_version(driver_name),
                already_installed=True,
                duration_seconds=time.monotonic() - started,
            )

        args = ["driver", "install", driver_name]
        if request.reinstall and driver_name in installed:
            # `install` refuses a driver that is already there; `update` is the
            # verb the CLI uses for the same intent.
            args = ["driver", "update", driver_name]
        result = await self._runner.run_tool(
            "appium",
            *args,
            operation=f"appium {' '.join(args)}",
            timeout=self._config.driver_install_timeout_seconds,
        )
        if not result.ok:
            raise DriverInstallCommandError(driver_name, result)

        return InstallDriverResult(
            driver_name=driver_name,
            version=await self._driver_version(driver_name),
            already_installed=False,
            duration_seconds=time.monotonic() - started,
        )

    # ----------------------------------------------------------------------
    # SessionLifecycle
    # ----------------------------------------------------------------------

    async def start_session(self, request: StartSessionRequest) -> StartSessionResult:
        """Open a session on a device, launching the Appium server if needed.

        Validation comes first and costs nothing: a malformed serial or an APK
        that is not on disk is rejected before a server is spawned, which is the
        difference between a one-millisecond error and a sixty-second one.
        """
        started = time.monotonic()
        device_id = validate_device_id(request.device_id)
        app_path = validate_app_path(request.app_path)
        await self._require_driver()

        _, server_started = await self._server.ensure_running()
        session = await self._sessions.create(
            request=_with_device_id(request, device_id),
            server_url=self._server.url,
            app_path=app_path,
        )
        return StartSessionResult(
            session=session,
            server_started=server_started,
            duration_seconds=time.monotonic() - started,
        )

    async def end_session(self, request: EndSessionRequest) -> EndSessionResult:
        """Close a session and release the device it was holding."""
        started = time.monotonic()
        session = await self._sessions.close(request.session_id)
        return EndSessionResult(
            session_id=session.session_id,
            device_id=session.device_id,
            duration_seconds=time.monotonic() - started,
        )

    async def list_sessions(self, request: ListSessionsRequest) -> ListSessionsResult:
        """Every session this process has open.

        Sessions started by another process are invisible here on purpose: this
        registry is what this server can drive and is responsible for closing.
        """
        if not request.verify_alive:
            return ListSessionsResult(sessions=self._sessions.all())

        alive: list[AppiumSession] = []
        for session in self._sessions.all():
            if await self._is_alive(session.session_id):
                alive.append(session)
        return ListSessionsResult(sessions=tuple(alive))

    # ----------------------------------------------------------------------
    # ScreenInspector
    # ----------------------------------------------------------------------

    async def take_screenshot(self, request: TakeScreenshotRequest) -> ScreenshotResult:
        """Capture the screen as a PNG, to a file or inline as base64."""
        png: bytes = await self._sessions.call(
            request.session_id, "take_screenshot", lambda driver: driver.get_screenshot_as_png()
        )
        if not png:
            raise ScreenshotCaptureError(request.session_id, "the device returned an empty image")

        dimensions = png_dimensions(png)
        path: Path | None = None
        encoded: str | None = None
        if request.save_path is not None:
            path = Path(request.save_path).expanduser()
            await asyncio.to_thread(_write_png, path, png)
        else:
            encoded = base64.b64encode(png).decode("ascii")

        return ScreenshotResult(
            session_id=request.session_id,
            path=path,
            image_base64=encoded,
            width=dimensions[0] if dimensions else None,
            height=dimensions[1] if dimensions else None,
            size_bytes=len(png),
        )

    async def get_page_source(self, request: GetPageSourceRequest) -> PageSourceResult:
        """The current screen's UI hierarchy as XML."""
        source: str = await self._sessions.call(
            request.session_id, "get_page_source", lambda driver: driver.page_source
        )
        text, was_truncated, total = truncate(source or "", request.max_characters)
        return PageSourceResult(
            session_id=request.session_id,
            source=text,
            truncated=was_truncated,
            total_characters=total,
        )

    # ----------------------------------------------------------------------
    # DeviceInteraction
    # ----------------------------------------------------------------------

    async def tap(self, request: TapRequest) -> InteractionResult:
        """Tap a point on the screen."""
        started = time.monotonic()
        x, y = validate_coordinates(request.x, request.y)
        duration = max(request.duration_ms, 0)

        await self._sessions.call(
            request.session_id,
            "tap",
            lambda driver: driver.tap([(x, y)], duration or None),
        )
        held = f", held {duration}ms" if duration else ""
        return InteractionResult(
            session_id=request.session_id,
            action="tap",
            detail=f"tapped ({x}, {y}){held}",
            duration_seconds=time.monotonic() - started,
        )

    async def tap_element(self, request: TapElementRequest) -> ElementInteractionResult:
        """Find an element and tap it.

        Preferred over :meth:`tap` wherever the element can be named: a
        coordinate is only correct for one screen size and one layout, and an
        element is correct for all of them.
        """
        started = time.monotonic()
        strategy, selector = validate_locator(request.strategy, request.selector)
        element = await self._sessions.find_element(
            request.session_id, strategy, selector, request.timeout_seconds
        )
        text = await self._sessions.call(
            request.session_id, "tap_element", lambda driver: _click(element)
        )
        return ElementInteractionResult(
            session_id=request.session_id,
            action="tap_element",
            strategy=strategy,
            selector=selector,
            element_text=text,
            duration_seconds=time.monotonic() - started,
        )

    async def swipe(self, request: SwipeRequest) -> InteractionResult:
        """Drag from one point to another."""
        started = time.monotonic()
        start_x, start_y = validate_coordinates(request.start_x, request.start_y)
        end_x, end_y = validate_coordinates(request.end_x, request.end_y)
        duration = max(request.duration_ms, 1)

        await self._sessions.call(
            request.session_id,
            "swipe",
            lambda driver: driver.swipe(start_x, start_y, end_x, end_y, duration),
        )
        return InteractionResult(
            session_id=request.session_id,
            action="swipe",
            detail=f"swiped ({start_x}, {start_y}) -> ({end_x}, {end_y}) over {duration}ms",
            duration_seconds=time.monotonic() - started,
        )

    async def scroll(self, request: ScrollRequest) -> InteractionResult:
        """Swipe across the middle of the screen in a named direction.

        The coordinate-free form of :meth:`swipe`. The screen size is taken from
        the session when it was recorded there and asked of the device only when
        it was not, so an ordinary scroll costs no extra round trip.
        """
        started = time.monotonic()
        direction, distance = validate_scroll(request.direction, request.distance)
        width, height = await self._screen_size(request.session_id)
        start, end = _scroll_path(direction, distance, width, height)
        duration = max(request.duration_ms, 1)

        await self._sessions.call(
            request.session_id,
            "scroll",
            lambda driver: driver.swipe(start[0], start[1], end[0], end[1], duration),
        )
        return InteractionResult(
            session_id=request.session_id,
            action="scroll",
            detail=(
                f"scrolled {direction} {distance:.0%} of a {width}x{height} screen: "
                f"{start} -> {end}"
            ),
            duration_seconds=time.monotonic() - started,
        )

    async def type_text(self, request: TypeTextRequest) -> ElementInteractionResult:
        """Type into a named element, or into whatever currently has focus."""
        started = time.monotonic()
        strategy: str | None = None
        selector: str | None = None
        text = request.text
        clear_first = request.clear_first

        if request.strategy is not None or request.selector is not None:
            strategy, selector = validate_locator(request.strategy or "", request.selector or "")
            element = await self._sessions.find_element(
                request.session_id, strategy, selector, request.timeout_seconds
            )
            found_text = await self._sessions.call(
                request.session_id,
                "type_text",
                lambda driver: _fill(element, text, clear_first),
            )
        else:
            found_text = await self._sessions.call(
                request.session_id,
                "type_text",
                lambda driver: _fill(driver.switch_to.active_element, text, clear_first),
            )

        return ElementInteractionResult(
            session_id=request.session_id,
            action="type_text",
            strategy=strategy or "focused",
            selector=selector or "",
            element_text=found_text,
            duration_seconds=time.monotonic() - started,
        )

    async def press_key(self, request: PressKeyRequest) -> InteractionResult:
        """Press a hardware or system key by name."""
        started = time.monotonic()
        keycode = validate_key(request.key)

        await self._sessions.call(
            request.session_id,
            "press_key",
            lambda driver: driver.press_keycode(keycode),
        )
        return InteractionResult(
            session_id=request.session_id,
            action="press_key",
            detail=f"pressed {request.key.strip().lower()} (keycode {keycode})",
            duration_seconds=time.monotonic() - started,
        )

    # ----------------------------------------------------------------------
    # the adapter's own lifecycle -- deliberately on no port
    # ----------------------------------------------------------------------

    async def aclose(self) -> None:
        """Quit every session, then take down a server this process started.

        In that order: a session outliving its server is a hang, and a server
        killed first would strand every session's teardown on a dead socket.
        """
        await self._sessions.aclose()
        await self._server.aclose()

    async def __aenter__(self) -> AppiumDeviceManager:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # ----------------------------------------------------------------------
    # internals
    # ----------------------------------------------------------------------

    async def _tool_status(self, tool: str) -> ToolStatus:
        path = self._runner.find_tool(tool)
        if path is None:
            return ToolStatus(
                name=tool,
                present=False,
                detail=f"not found on PATH; install it or set AT_APPIUM_{tool.upper()}_PATH",
            )
        result = await self._version(tool)
        if result is None or not result.ok:
            return ToolStatus(
                name=tool,
                present=False,
                path=Path(path),
                detail="found, but it would not report a version",
            )
        return ToolStatus(
            name=tool,
            present=True,
            version=parse_version_output(result.stdout) or parse_version_output(result.stderr),
            path=Path(path),
        )

    async def _version(self, tool: str) -> CommandResult | None:
        flag = "--version" if tool != "appium" else "-v"
        try:
            return await self._runner.run_tool(
                tool, flag, operation=f"{tool} {flag}", timeout=self._config.command_timeout_seconds
            )
        except Exception:  # noqa: BLE001 - a broken tool is a report, not a failure
            return None

    async def _installed_drivers(self) -> tuple[str, ...]:
        try:
            result = await self._runner.run_tool(
                "appium",
                "driver",
                "list",
                "--installed",
                "--json",
                operation="appium driver list",
                timeout=self._config.command_timeout_seconds,
            )
        except Exception:  # noqa: BLE001 - no appium means no drivers, not a crash
            return ()
        return parse_installed_drivers(result.stdout) if result.ok else ()

    async def _driver_version(self, driver_name: str) -> str | None:
        try:
            result = await self._runner.run_tool(
                "appium",
                "driver",
                "list",
                "--installed",
                "--json",
                operation="appium driver list",
                timeout=self._config.command_timeout_seconds,
            )
        except Exception:  # noqa: BLE001
            return None
        return parse_driver_version(result.stdout, driver_name) if result.ok else None

    async def _require_driver(self) -> None:
        """Fail before spawning a server when the driver a session needs is absent.

        Rule 1 §3: validate before you spawn. Without this the failure surfaces
        sixty seconds later as an opaque server-side capability error.
        """
        installed = await self._installed_drivers()
        if self._config.driver_name not in installed:
            raise DriverMissingError(self._config.driver_name, installed)

    async def _is_alive(self, session_id: str) -> bool:
        try:
            await self._sessions.call(
                session_id, "verify_alive", lambda driver: driver.current_package
            )
        except Exception:  # noqa: BLE001 - the question is exactly "did this fail"
            return False
        return True

    async def _screen_size(self, session_id: str) -> tuple[int, int]:
        live = self._sessions.get(session_id)
        if live.session.has_screen_size:
            return int(live.session.screen_width or 0), int(live.session.screen_height or 0)
        size = await self._sessions.call(
            session_id, "get_window_size", lambda driver: driver.get_window_size()
        )
        return int(size["width"]), int(size["height"])


def _with_device_id(request: StartSessionRequest, device_id: str) -> StartSessionRequest:
    """The request with its serial normalised, without mutating the caller's copy."""
    return dataclasses.replace(request, device_id=device_id)


def _write_png(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _click(element: Any) -> str | None:
    """Read the element's text *before* tapping it.

    After the tap the element may be gone, detached or showing something else,
    so the label that goes into the result has to be captured first.
    """
    text = _element_text(element)
    element.click()
    return text


def _fill(element: Any, text: str, clear_first: bool) -> str | None:
    if clear_first:
        element.clear()
    element.send_keys(text)
    return _element_text(element)


def _element_text(element: Any) -> str | None:
    try:
        value = element.text
    except Exception:  # noqa: BLE001 - a label is a nicety, never a failure
        return None
    return value if value else None


def _scroll_path(
    direction: str, distance: float, width: int, height: int
) -> tuple[tuple[int, int], tuple[int, int]]:
    """Start and end points for a scroll across the middle of the screen.

    The gesture is inset from the edges: a swipe that starts on the very edge of
    an Android screen is a system gesture -- back, or the notification shade --
    and not a scroll of the content at all.
    """
    mid_x, mid_y = width // 2, height // 2
    span_y = int(height * distance / 2)
    span_x = int(width * distance / 2)
    if direction == "down":
        return (mid_x, mid_y + span_y), (mid_x, mid_y - span_y)
    if direction == "up":
        return (mid_x, mid_y - span_y), (mid_x, mid_y + span_y)
    if direction == "right":
        return (mid_x + span_x, mid_y), (mid_x - span_x, mid_y)
    return (mid_x - span_x, mid_y), (mid_x + span_x, mid_y)
