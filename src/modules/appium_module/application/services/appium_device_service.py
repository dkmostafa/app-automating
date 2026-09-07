"""The Appium device service: the product's operations, in the product's words.

Every method here is one thing a user of this module wants to do, named the way
they would say it, and its arguments are the ones they actually have -- a
serial, a session id, a piece of text. The domain's ``*Request`` dataclasses are
built *inside* the service, which is the point: a caller should not have to know
that "scroll down" and "swipe from here to there" are the same gesture with the
arithmetic done for them.

Dependency injection, per Rule 0 §4: the four collaborators arrive through the
constructor, typed as the domain's ports. This class never names an adapter, the
Appium CLI, a subprocess or a URL. Swap the ports for a device cloud and the
service is unchanged -- and because the ports are segregated (Rule 0 §3), what
it holds is exactly the authority it needs and no more.

This service keeps no record of what it did. It drives the device and returns
what happened; nothing about persistence, journaling or a navigation map is
visible from here. A caller that wants a record of every gesture gets one by
decorating the ports this class is built from, at the composition root -- this
file, and everything below it, stays ignorant that such a thing exists.

The operations fall into the order a caller meets them: check the host, open a
session, read the screen, drive it, close the session.
"""

from __future__ import annotations

from pathlib import Path

from ...domain.models import (
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
from ...domain.ports import (
    AppiumEnvironment,
    DeviceInteraction,
    ScreenInspector,
    SessionLifecycle,
)

__all__ = ["AppiumDeviceService"]


class AppiumDeviceService:
    """Driving a device through Appium, as the rest of the product sees it."""

    def __init__(
        self,
        environment: AppiumEnvironment,
        sessions: SessionLifecycle,
        screen: ScreenInspector,
        interaction: DeviceInteraction,
    ) -> None:
        self._environment = environment
        self._sessions = sessions
        self._screen = screen
        self._interaction = interaction

    # -- the host ----------------------------------------------------------

    async def check_environment(self, *, probe_server: bool = True) -> EnvironmentStatus:
        """What this host can automate: which tools, which drivers, which server.

        The first call to make on an unfamiliar machine, and the one to make
        when anything else reports the backend is unavailable. It never raises
        for a missing tool -- an absent binary is the answer, not a failure.
        """
        return await self._environment.check_environment(
            CheckEnvironmentRequest(probe_server=probe_server)
        )

    async def install_driver(
        self, driver_name: str, *, reinstall: bool = False
    ) -> InstallDriverResult:
        """Install an Appium driver on this host.

        The only operation in the module that changes the machine rather than a
        device, which is why it is spelled out as its own call: starting a
        session will never quietly install anything.
        """
        return await self._environment.install_driver(
            InstallDriverRequest(driver_name=driver_name, reinstall=reinstall)
        )

    # -- sessions ----------------------------------------------------------

    async def start_session(
        self,
        device_id: str,
        *,
        app_path: Path | None = None,
        app_package: str | None = None,
        app_activity: str | None = None,
        no_reset: bool = True,
        startup_timeout_seconds: float | None = None,
    ) -> StartSessionResult:
        """Open an automation session on a device and return its id.

        Works the same for an emulator and for a phone on the end of a USB
        cable: both are a serial, and the difference stops mattering here.

        The three app arguments are three ways to say what to drive -- an APK to
        install, an installed package to launch, or nothing at all, which
        attaches to whatever is already on screen.
        """
        result = await self._sessions.start_session(
            StartSessionRequest(
                device_id=device_id,
                app_path=app_path,
                app_activity=app_activity,
                app_package=app_package,
                no_reset=no_reset,
                startup_timeout_seconds=startup_timeout_seconds,
            )
        )
        return result

    async def end_session(self, session_id: str) -> EndSessionResult:
        """Close a session and release the device it was holding.

        Worth calling rather than leaving to the server's idle timeout: until it
        runs, the device stays claimed and a second session on it will fail.
        """
        return await self._sessions.end_session(EndSessionRequest(session_id=session_id))

    async def get_open_sessions(self, *, verify_alive: bool = False) -> ListSessionsResult:
        """Every session this server currently holds.

        ``verify_alive`` asks each one whether it is still there, at one round
        trip apiece -- the way to tell a session that has silently timed out
        from one that is merely idle.
        """
        return await self._sessions.list_sessions(ListSessionsRequest(verify_alive=verify_alive))

    # -- reading the screen ------------------------------------------------

    async def take_screenshot(
        self, session_id: str, *, save_path: Path | None = None
    ) -> ScreenshotResult:
        """Capture what is on screen right now.

        With ``save_path`` the PNG goes to a file; without it the image comes
        back inline as base64. A file is the better default for anything a
        person will look at, and inline is for a caller that will decode it.
        """
        return await self._screen.take_screenshot(
            TakeScreenshotRequest(session_id=session_id, save_path=save_path)
        )

    async def get_page_source(
        self, session_id: str, *, max_characters: int | None = None
    ) -> PageSourceResult:
        """The screen's UI hierarchy as XML: every element, with its attributes.

        The counterpart to a screenshot -- a screenshot shows what a person
        would see, this shows what is addressable. It is where the
        ``resource-id``, ``content-desc`` and ``text`` values that
        :meth:`tap_element` needs come from.
        """
        return await self._screen.get_page_source(
            GetPageSourceRequest(session_id=session_id, max_characters=max_characters)
        )

    # -- driving the screen ------------------------------------------------

    async def tap(
        self, session_id: str, x: int, y: int, *, duration_ms: int = 0
    ) -> InteractionResult:
        """Tap a point, in pixels from the top-left of the screen.

        The fallback for something that cannot be named. Prefer
        :meth:`tap_element` where the element has an id or a label: a coordinate
        is right for one screen size and wrong for every other.
        """
        return await self._interaction.tap(
            TapRequest(session_id=session_id, x=x, y=y, duration_ms=duration_ms)
        )

    async def tap_element(
        self,
        session_id: str,
        strategy: str,
        selector: str,
        *,
        timeout_seconds: float = 10.0,
    ) -> ElementInteractionResult:
        """Find an element and tap it, waiting for it to appear.

        The wait is what makes this reliable on a screen that is still
        settling: an animation, a list still loading, a dialog on its way in.
        """
        return await self._interaction.tap_element(
            TapElementRequest(
                session_id=session_id,
                strategy=strategy,
                selector=selector,
                timeout_seconds=timeout_seconds,
            )
        )

    async def swipe(
        self,
        session_id: str,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        *,
        duration_ms: int = 300,
    ) -> InteractionResult:
        """Drag from one point to another.

        The precise form of :meth:`scroll`, for a gesture whose exact path
        matters: a slider, a drawer pulled a specific distance, a swipe on one
        row of a list rather than the whole screen.
        """
        return await self._interaction.swipe(
            SwipeRequest(
                session_id=session_id,
                start_x=start_x,
                start_y=start_y,
                end_x=end_x,
                end_y=end_y,
                duration_ms=duration_ms,
            )
        )

    async def scroll(
        self,
        session_id: str,
        direction: str,
        *,
        distance: float = 0.5,
        duration_ms: int = 300,
    ) -> InteractionResult:
        """Scroll the screen in a direction, without knowing its resolution.

        The same gesture as :meth:`swipe` with the arithmetic done from the
        device's own screen size, which is why a caller can say "down" instead
        of measuring a phone.
        """
        return await self._interaction.scroll(
            ScrollRequest(
                session_id=session_id,
                direction=direction,
                distance=distance,
                duration_ms=duration_ms,
            )
        )

    async def type_text(
        self,
        session_id: str,
        text: str,
        *,
        strategy: str | None = None,
        selector: str | None = None,
        clear_first: bool = False,
    ) -> ElementInteractionResult:
        """Type text into a named field, or into whatever has focus.

        Naming the field is the reliable form: without it the text goes wherever
        the cursor happens to be, which is only predictable right after
        something was tapped.
        """
        return await self._interaction.type_text(
            TypeTextRequest(
                session_id=session_id,
                text=text,
                strategy=strategy,
                selector=selector,
                clear_first=clear_first,
            )
        )

    async def press_key(self, session_id: str, key: str) -> InteractionResult:
        """Press a hardware or system key by name -- ``back``, ``home``, ``enter``.

        The way to leave a screen, dismiss a keyboard or submit a field, none of
        which have anything on screen to tap.
        """
        return await self._interaction.press_key(PressKeyRequest(session_id=session_id, key=key))
