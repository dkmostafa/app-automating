"""The cross-module adapter, against a real store and hand-rolled appium ports.

Integration by Rule 2 §2, and no mocking anywhere (Rule 1 §6). The recorder
here is a real :class:`NavigationStore` over a real in-memory database, and the
failure path is a real config pointing at a directory that cannot be created --
the same discipline the rest of the infrastructure layer is held to.

The three appium ports :class:`RecordingDeviceDriver` wraps are fakes written by
hand, not mocked: proving this adapter delegates and records correctly needs no
real device or Appium server, only something that answers appium_module's own
Protocols the way a real adapter would. A stubbed recorder would prove only that
the stub was called, which is why the recorder stays real throughout.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from modules.appium_module.domain.errors import ElementNotFound
from modules.appium_module.domain.models import (
    AppiumSession,
    ElementInteractionResult,
    EndSessionRequest,
    EndSessionResult,
    GetPageSourceRequest,
    ListSessionsRequest,
    ListSessionsResult,
    PageSourceResult,
    StartSessionRequest,
    StartSessionResult,
    TapElementRequest,
    TypeTextRequest,
)
from modules.appium_module.domain.ports import DeviceInteraction, ScreenInspector, SessionLifecycle
from modules.navigation_memory_module.domain.models import (
    GetRunRequest,
    SearchScreensRequest,
    WhereAmIRequest,
)
from modules.navigation_memory_module.infrastructure.appium_recorder import RecordingDeviceDriver
from modules.navigation_memory_module.infrastructure.navigation_store import (
    NavigationDatabase,
    NavigationStore,
    NavigationStoreConfig,
)

pytestmark = pytest.mark.integration

PKG = "com.example.shop"


def page(title: str, *ids: str) -> str:
    elements = "".join(f'<node class="android.widget.Button" resource-id="x:id/{i}"/>' for i in ids)
    return (
        f'<hierarchy><node class="android.widget.TextView" text="{title}"/>{elements}</hierarchy>'
    )


HOME = page("Home", "cart")
CART = page("Cart", "checkout")


class FakeSessions:
    """Answers ``SessionLifecycle`` with one fixed session, no device involved."""

    def __init__(self, *, app_package: str | None = PKG) -> None:
        self._app_package = app_package

    async def start_session(self, request: StartSessionRequest) -> StartSessionResult:
        return StartSessionResult(
            session=AppiumSession(
                session_id="s-1",
                device_id=request.device_id,
                platform_name="Android",
                automation_name="UiAutomator2",
                app_package=self._app_package,
            ),
            server_started=False,
            duration_seconds=0.01,
        )

    async def end_session(self, request: EndSessionRequest) -> EndSessionResult:
        return EndSessionResult(
            session_id=request.session_id, device_id="emulator-5554", duration_seconds=0.01
        )

    async def list_sessions(self, request: ListSessionsRequest) -> ListSessionsResult:
        return ListSessionsResult()


class FakeScreen:
    """Hands back a scripted sequence of page sources, one per call."""

    def __init__(self, *pages: str | None) -> None:
        self._pages = list(pages)

    async def get_page_source(self, request: GetPageSourceRequest) -> PageSourceResult:
        source = self._pages.pop(0) if self._pages else None
        if source is None:
            raise ElementNotFound("page_source", "unavailable")
        return PageSourceResult(
            session_id=request.session_id,
            source=source,
            truncated=False,
            total_characters=len(source),
        )

    async def take_screenshot(self, request: object) -> object:
        raise NotImplementedError


class FakeInteraction:
    """Answers ``tap_element``; every other gesture is unused by these tests."""

    def __init__(self, *, fails_with: Exception | None = None) -> None:
        self._fails_with = fails_with

    async def tap_element(self, request: TapElementRequest) -> ElementInteractionResult:
        if self._fails_with is not None:
            raise self._fails_with
        return ElementInteractionResult(
            session_id=request.session_id,
            action="tap_element",
            strategy=request.strategy,
            selector=request.selector,
            element_text=None,
            duration_seconds=0.01,
        )

    async def tap(self, request: object) -> object:
        raise NotImplementedError

    async def swipe(self, request: object) -> object:
        raise NotImplementedError

    async def scroll(self, request: object) -> object:
        raise NotImplementedError

    async def type_text(self, request: TypeTextRequest) -> ElementInteractionResult:
        return ElementInteractionResult(
            session_id=request.session_id,
            action="type_text",
            strategy=request.strategy or "",
            selector=request.selector or "",
            element_text=None,
            duration_seconds=0.01,
        )

    async def press_key(self, request: object) -> object:
        raise NotImplementedError


@pytest.fixture
async def store() -> AsyncIterator[NavigationStore]:
    config = NavigationStoreConfig()
    instance = NavigationStore(NavigationDatabase(config), config)
    try:
        yield instance
    finally:
        await instance.aclose()


def driver(
    store: NavigationStore,
    *,
    sessions: SessionLifecycle,
    screen: ScreenInspector,
    interaction: DeviceInteraction,
) -> RecordingDeviceDriver:
    return RecordingDeviceDriver(
        sessions=sessions, screen=screen, interaction=interaction, recorder=store
    )


def test_the_adapter_satisfies_the_ports_appium_module_declared(store: NavigationStore) -> None:
    """The seam, checked rather than assumed."""
    instance = driver(
        store, sessions=FakeSessions(), screen=FakeScreen(), interaction=FakeInteraction()
    )

    assert isinstance(instance, SessionLifecycle)
    assert isinstance(instance, DeviceInteraction)


async def test_a_driven_session_becomes_a_navigable_map(store: NavigationStore) -> None:
    """The whole point of the adapter, end to end: appium's ports are called
    through unchanged, and a map a planner can read comes out the other side."""
    instance = driver(
        store, sessions=FakeSessions(), screen=FakeScreen(HOME, CART), interaction=FakeInteraction()
    )

    await instance.start_session(StartSessionRequest(device_id="emulator-5554"))
    await instance.tap_element(TapElementRequest(session_id="s-1", strategy="id", selector="cart"))

    where = await store.where_am_i(WhereAmIRequest(session_id="s-1"))

    assert where.screen.label == "Cart"
    assert {s.label for s in (await store.search_screens(SearchScreensRequest(PKG))).screens} == {
        "Home",
        "Cart",
    }


async def test_the_opening_screen_seeds_the_run(store: NavigationStore) -> None:
    """Without it the first gesture has no screen to be an edge *from*, and that
    edge is lost on every session."""
    instance = driver(
        store, sessions=FakeSessions(), screen=FakeScreen(HOME, CART), interaction=FakeInteraction()
    )

    await instance.start_session(StartSessionRequest(device_id="emulator-5554"))
    await instance.tap_element(TapElementRequest(session_id="s-1", strategy="id", selector="cart"))

    where = await store.where_am_i(WhereAmIRequest(session_id="s-1"))
    home = next(
        s
        for s in (await store.search_screens(SearchScreensRequest(PKG))).screens
        if s.label == "Home"
    )
    routes = (await store.get_run(GetRunRequest(run_id=where.run.run_id))).entries

    assert routes[0].action == "observe"
    assert home.screen_id


async def test_without_an_opening_screen_the_first_edge_is_simply_missing(
    store: NavigationStore,
) -> None:
    """The documented cost of a session appium could not read at startup."""
    instance = driver(
        store, sessions=FakeSessions(), screen=FakeScreen(None, CART), interaction=FakeInteraction()
    )

    await instance.start_session(StartSessionRequest(device_id="emulator-5554"))
    await instance.tap_element(TapElementRequest(session_id="s-1", strategy="id", selector="cart"))

    where = await store.where_am_i(WhereAmIRequest(session_id="s-1"))

    assert where.screen.label == "Cart"
    assert where.routes == ()


async def test_a_session_with_no_package_is_not_recorded(store: NavigationStore) -> None:
    """Every screen is keyed by its app; filing an unknown one under the empty
    string would merge unrelated apps into a single map."""
    instance = driver(
        store,
        sessions=FakeSessions(app_package=None),
        screen=FakeScreen(HOME),
        interaction=FakeInteraction(),
    )

    await instance.start_session(StartSessionRequest(device_id="emulator-5554"))

    assert instance.tracked_sessions == ()


async def test_a_gesture_on_an_untracked_session_is_dropped(store: NavigationStore) -> None:
    instance = driver(
        store, sessions=FakeSessions(), screen=FakeScreen(CART), interaction=FakeInteraction()
    )

    await instance.tap_element(
        TapElementRequest(session_id="unknown", strategy="id", selector="cart")
    )

    assert (await store.search_screens(SearchScreensRequest(PKG))).screens == ()


async def test_a_failed_gesture_is_recorded_as_a_non_success(store: NavigationStore) -> None:
    instance = driver(
        store,
        sessions=FakeSessions(),
        screen=FakeScreen(HOME, HOME),
        interaction=FakeInteraction(fails_with=ElementNotFound("id", "ghost")),
    )

    await instance.start_session(StartSessionRequest(device_id="emulator-5554"))
    with pytest.raises(ElementNotFound):
        await instance.tap_element(
            TapElementRequest(session_id="s-1", strategy="id", selector="ghost")
        )

    where = await store.where_am_i(WhereAmIRequest(session_id="s-1"))
    journal_entries = await store.get_run(GetRunRequest(run_id=where.run.run_id))

    assert journal_entries.failures
    assert where.routes[0].success_count == 0


async def test_typed_text_is_never_recorded(store: NavigationStore) -> None:
    """It is routinely a password or a card number. The map needs the field, not
    the value."""
    instance = driver(
        store, sessions=FakeSessions(), screen=FakeScreen(HOME, HOME), interaction=FakeInteraction()
    )

    await instance.start_session(StartSessionRequest(device_id="emulator-5554"))
    await instance.type_text(
        TypeTextRequest(
            session_id="s-1", text="hunter2-my-real-password", strategy="id", selector="pin"
        )
    )

    where = await store.where_am_i(WhereAmIRequest(session_id="s-1"))
    entries = (await store.get_run(GetRunRequest(run_id=where.run.run_id))).entries
    assert all("hunter2" not in entry.target for entry in entries)


async def test_closing_a_session_closes_its_run(store: NavigationStore) -> None:
    instance = driver(
        store, sessions=FakeSessions(), screen=FakeScreen(HOME), interaction=FakeInteraction()
    )

    await instance.start_session(StartSessionRequest(device_id="emulator-5554"))
    run_id = (await store.where_am_i(WhereAmIRequest(session_id="s-1"))).run.run_id

    await instance.end_session(EndSessionRequest(session_id="s-1"))

    assert instance.tracked_sessions == ()
    assert not (await store.get_run(GetRunRequest(run_id=run_id))).run.open


async def test_closing_an_untracked_session_does_nothing(store: NavigationStore) -> None:
    instance = driver(
        store, sessions=FakeSessions(), screen=FakeScreen(), interaction=FakeInteraction()
    )

    await instance.end_session(EndSessionRequest(session_id="never-opened"))

    assert instance.tracked_sessions == ()


async def test_a_second_session_reuses_what_the_first_learned(store: NavigationStore) -> None:
    """The reason any of this exists."""
    instance = driver(
        store,
        sessions=FakeSessions(),
        screen=FakeScreen(HOME, CART, HOME),
        interaction=FakeInteraction(),
    )

    await instance.start_session(StartSessionRequest(device_id="emulator-5554"))
    await instance.tap_element(TapElementRequest(session_id="s-1", strategy="id", selector="cart"))
    await instance.end_session(EndSessionRequest(session_id="s-1"))

    await instance.start_session(StartSessionRequest(device_id="emulator-5554"))
    where = await store.where_am_i(WhereAmIRequest(session_id="s-1"))

    assert [route.target for route in where.routes] == ["id=cart"]


# -- the contract this class must hold ----------------------------------------
#
# A gesture that reached the device succeeded whether or not anyone managed to
# write it down. The broken memory below is a real one -- a real config
# pointing at a path that cannot be created -- rather than something patched to
# throw.


@pytest.fixture
async def broken_store() -> AsyncIterator[NavigationStore]:
    config = NavigationStoreConfig(
        database_path=Path("/proc/definitely-not-writable/navigation.db"),
        create_directories=False,
    )
    instance = NavigationStore(NavigationDatabase(config), config)
    try:
        yield instance
    finally:
        await instance.aclose()


async def test_a_broken_memory_never_raises_while_opening(broken_store: NavigationStore) -> None:
    instance = driver(
        broken_store,
        sessions=FakeSessions(),
        screen=FakeScreen(HOME),
        interaction=FakeInteraction(),
    )

    await instance.start_session(StartSessionRequest(device_id="emulator-5554"))

    assert instance.tracked_sessions == ()


async def test_a_broken_memory_never_raises_while_recording(broken_store: NavigationStore) -> None:
    instance = driver(
        broken_store,
        sessions=FakeSessions(),
        screen=FakeScreen(HOME, CART),
        interaction=FakeInteraction(),
    )

    await instance.start_session(StartSessionRequest(device_id="emulator-5554"))
    result = await instance.tap_element(
        TapElementRequest(session_id="s-1", strategy="id", selector="cart")
    )

    assert result.action == "tap_element"


async def test_a_broken_memory_never_raises_while_closing(broken_store: NavigationStore) -> None:
    instance = driver(
        broken_store, sessions=FakeSessions(), screen=FakeScreen(), interaction=FakeInteraction()
    )

    await instance.end_session(EndSessionRequest(session_id="s-1"))
