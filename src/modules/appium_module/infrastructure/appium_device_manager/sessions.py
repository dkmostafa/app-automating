"""Live automation sessions: creating them, holding them, and driving them.

This is the component's second piece of real state, and the only place the
Appium client library is touched. Two things about it shape everything else.

**The client is synchronous.** ``appium-python-client`` is built on Selenium and
every call blocks. The ports are ``async``, so every one of those calls goes
through :meth:`SessionRegistry.call`, which hands the blocking work to a worker
thread. Nothing in this file may be awaited from inside a driver call, and
nothing outside it may touch a driver object directly -- that is what keeps the
event loop free while a 30-second gesture runs.

**A session is expensive and stateful.** It costs seconds to create, holds a
device, and dies on its own if left idle past the server's timeout. So it gets
an id, a registry entry, and a teardown path that runs on every exit including
:meth:`aclose` (Rule 1 §5).

Exception translation lives here too, at the exact boundary where a foreign
``WebDriverException`` would otherwise escape into the layers above (Rule 1 §3).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from appium import webdriver
from appium.options.android import UiAutomator2Options
from selenium.common.exceptions import (
    InvalidSessionIdException,
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.support import expected_conditions as expected
from selenium.webdriver.support.ui import WebDriverWait

from ...domain.models import AppiumSession, StartSessionRequest
from .config import AppiumConfig
from .errors import (
    AppiumComponentError,
    ElementLookupError,
    SessionCreateError,
    UnknownSessionError,
)
from .parsing import classify_interaction_failure, classify_session_failure, resolve_locator

__all__ = ["SessionRegistry", "LiveSession"]

T = TypeVar("T")


@dataclass(slots=True)
class LiveSession:
    """One open session: the domain's view of it, plus the driver behind it.

    Not frozen and not a domain type: it holds a live network object, which is
    exactly the kind of thing Rule 0 §2 keeps out of ``domain/``. The
    :attr:`session` field is what crosses the port; :attr:`driver` never does.
    """

    session: AppiumSession
    driver: Any


class SessionRegistry:
    """Every session this process has open, keyed by the id callers hold."""

    def __init__(self, config: AppiumConfig) -> None:
        self._config = config
        self._sessions: dict[str, LiveSession] = {}

    # -- reading -----------------------------------------------------------

    def __len__(self) -> int:
        return len(self._sessions)

    @property
    def session_ids(self) -> tuple[str, ...]:
        return tuple(self._sessions)

    def all(self) -> tuple[AppiumSession, ...]:
        return tuple(live.session for live in self._sessions.values())

    def get(self, session_id: str) -> LiveSession:
        """The live session, or :class:`UnknownSessionError` naming what is open."""
        live = self._sessions.get(session_id)
        if live is None:
            raise UnknownSessionError(session_id, self.session_ids)
        return live

    # -- the thread boundary ----------------------------------------------

    async def call(
        self,
        session_id: str,
        action: str,
        work: Callable[[Any], T],
        *,
        timeout_seconds: float | None = None,
    ) -> T:
        """Run one blocking driver call on a worker thread, translated.

        ``work`` receives the driver and must not await. Every foreign exception
        the client can raise is caught here and re-raised as a typed error, so
        no ``WebDriverException`` ever reaches the application layer.

        A component error raised *by* ``work`` passes through untouched. That is
        what lets :meth:`find_element` classify its own failure inside the
        thread: an element that never appeared is an ``ElementLookupError``, and
        re-classifying it here as a generic interaction failure would lose both
        the locator and the remedy that goes with it.

        ``timeout_seconds`` raises the budget for a call that carries its own
        deadline. Without it a lookup willing to wait longer than
        ``interaction_timeout_seconds`` would be cut off by the wrong timer and
        report the wrong reason for the wrong duration.
        """
        live = self.get(session_id)
        budget = (
            self._config.interaction_timeout_seconds
            if timeout_seconds is None
            else max(timeout_seconds, self._config.interaction_timeout_seconds)
        )
        try:
            return await asyncio.wait_for(asyncio.to_thread(work, live.driver), budget)
        except AppiumComponentError:
            # Already this module's vocabulary: `work` classified it better than
            # this handler could.
            raise
        except (InvalidSessionIdException, WebDriverException) as exc:
            raise classify_interaction_failure(action, session_id, _reason(exc)) from exc
        except TimeoutError as exc:
            raise classify_interaction_failure(
                action, session_id, f"the device did not answer within {budget}s"
            ) from exc

    async def find_element(
        self, session_id: str, strategy: str, selector: str, timeout_seconds: float
    ) -> Any:
        """Locate one element, waiting up to ``timeout_seconds`` for it.

        The whole wait happens inside a single worker thread rather than as a
        poll loop across the event loop: one hand-off instead of one per
        attempt, and the client's own waiter decides the polling interval.
        """
        by, value = resolve_locator(strategy, selector)

        def locate(driver: Any) -> Any:
            """Find the element, and name its absence precisely.

            The translation happens here, inside the worker thread, rather than
            around the call. Both failures mean the same thing to a caller --
            "matched nothing" and "the wait ran out" are equally "it is not
            there" -- and only this closure knows which locator was being looked
            for, which is the evidence the error has to carry.
            """
            try:
                if timeout_seconds <= 0:
                    return driver.find_element(by=by, value=value)
                return WebDriverWait(driver, timeout_seconds).until(
                    expected.presence_of_element_located((by, value))
                )
            except (NoSuchElementException, TimeoutException) as exc:
                raise ElementLookupError(strategy, selector, timeout_seconds) from exc

        return await self.call(session_id, "find_element", locate, timeout_seconds=timeout_seconds)

    # -- lifecycle ---------------------------------------------------------

    async def create(
        self, request: StartSessionRequest, server_url: str, app_path: Path | None
    ) -> AppiumSession:
        """Open a session against ``server_url`` and register it.

        The capabilities are assembled here and nowhere else. ``extra_capabilities``
        is merged last on purpose: it is the caller's escape hatch, and an escape
        hatch that loses to a default is not one.
        """
        capabilities = self._capabilities(request, app_path)
        options = UiAutomator2Options()
        options.load_capabilities(capabilities)
        timeout = request.startup_timeout_seconds or self._config.session_startup_timeout_seconds

        def connect() -> Any:
            return webdriver.Remote(command_executor=server_url, options=options)

        try:
            driver = await asyncio.wait_for(asyncio.to_thread(connect), timeout)
        except TimeoutError as exc:
            raise SessionCreateError(
                request.device_id, f"the server did not create a session within {timeout}s"
            ) from exc
        except WebDriverException as exc:
            raise classify_session_failure(request.device_id, _reason(exc)) from exc
        except OSError as exc:
            raise classify_session_failure(request.device_id, str(exc)) from exc

        session = await self._describe(driver, request, app_path)
        self._sessions[session.session_id] = LiveSession(session=session, driver=driver)
        return session

    async def close(self, session_id: str) -> AppiumSession:
        """Quit a session and forget it, even if quitting failed.

        The registry entry is dropped in a ``finally``: a session whose teardown
        raised is still gone as far as the server is concerned, and keeping the
        id alive would strand it forever.
        """
        live = self.get(session_id)
        try:
            await asyncio.to_thread(live.driver.quit)
        except WebDriverException:
            # The session was already gone. That is the state we wanted.
            pass
        finally:
            self._sessions.pop(session_id, None)
        return live.session

    async def aclose(self) -> None:
        """Quit every open session. Runs on server shutdown."""
        for session_id in tuple(self._sessions):
            try:
                await self.close(session_id)
            except Exception:  # noqa: BLE001 - shutdown must reach every session
                self._sessions.pop(session_id, None)

    # -- internals ---------------------------------------------------------

    def _capabilities(self, request: StartSessionRequest, app_path: Path | None) -> dict[str, Any]:
        capabilities: dict[str, Any] = {
            "platformName": self._config.platform_name,
            "appium:automationName": self._config.automation_name,
            "appium:udid": request.device_id,
            # Both are sent because the driver keys device selection off udid
            # while some tooling still reads deviceName; disagreeing is worse
            # than repeating.
            "appium:deviceName": request.device_id,
            "appium:noReset": request.no_reset,
            "appium:newCommandTimeout": int(
                request.new_command_timeout_seconds
                if request.new_command_timeout_seconds is not None
                else self._config.new_command_timeout_seconds
            ),
        }
        if app_path is not None:
            capabilities["appium:app"] = str(app_path)
        if request.app_package:
            capabilities["appium:appPackage"] = request.app_package
        if request.app_activity:
            capabilities["appium:appActivity"] = request.app_activity
        for key, value in request.extra_capabilities:
            capabilities[key] = value
        return capabilities

    async def _describe(
        self, driver: Any, request: StartSessionRequest, app_path: Path | None
    ) -> AppiumSession:
        """Build the domain view of a freshly created session.

        The screen size is read once, here, because every coordinate-free
        gesture needs it and asking the device on each scroll would be a round
        trip per swipe. A device that will not report it yields ``None`` rather
        than failing the session -- a session that works for taps is still
        worth having.
        """
        size = await asyncio.to_thread(_screen_size, driver)
        return AppiumSession(
            session_id=str(driver.session_id),
            device_id=request.device_id,
            platform_name=self._config.platform_name,
            automation_name=self._config.automation_name,
            app_package=request.app_package or _capability(driver, "appPackage"),
            app_activity=request.app_activity or _capability(driver, "appActivity"),
            screen_width=size[0] if size else None,
            screen_height=size[1] if size else None,
        )


def _screen_size(driver: Any) -> tuple[int, int] | None:
    try:
        size = driver.get_window_size()
        return int(size["width"]), int(size["height"])
    except (WebDriverException, KeyError, TypeError, ValueError):
        return None


def _capability(driver: Any, name: str) -> str | None:
    try:
        value = driver.capabilities.get(name) or driver.capabilities.get(f"appium:{name}")
    except (AttributeError, TypeError):
        return None
    return str(value) if value else None


def _reason(exc: BaseException) -> str:
    """The useful half of a WebDriver exception.

    ``str(exc)`` on these carries a stacktrace and a session dump; the ``msg``
    attribute is the sentence the server actually sent, which is the part a
    caller can act on.
    """
    message = getattr(exc, "msg", None)
    text = message if isinstance(message, str) and message.strip() else str(exc)
    return text.strip().splitlines()[0] if text.strip() else type(exc).__name__
