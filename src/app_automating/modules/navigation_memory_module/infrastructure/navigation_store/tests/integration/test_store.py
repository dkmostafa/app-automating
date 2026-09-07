"""The store against a real database.

Integration by Rule 2 §2: every test opens a real SQLite database and runs real
SQL. Rule 1 §6's ban applies in full -- nothing is patched, stubbed or faked. An
in-memory database is still a real database; it is used because these tests do
not care where the file is, and ``tmp_path`` covers the case where they would.

The "writing to a real file" section at the bottom exercises the same operations
against a ``tmp_path``-backed SQLite file. A test that passes against in-memory
but fails against a file points directly at the file I/O path.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from app_automating.modules.navigation_memory_module.domain.errors import (
    DestinationNotFound,
    InvalidNavigationRequest,
    NoKnownRoute,
    NoRunForSession,
    RunAlreadyEnded,
    RunNotFound,
    ScreenNotRecognised,
)
from app_automating.modules.navigation_memory_module.domain.models import (
    OBSERVE_ACTION,
    EndRunRequest,
    FindPathRequest,
    ForgetRequest,
    GetRunRequest,
    RecordInteractionRequest,
    RecordObservationRequest,
    ScreenSnapshot,
    SearchScreensRequest,
    StartRunRequest,
    WhereAmIRequest,
)
from app_automating.modules.navigation_memory_module.infrastructure.navigation_store import (
    NavigationDatabase,
    NavigationStore,
    NavigationStoreConfig,
)

pytestmark = pytest.mark.integration

PKG = "com.example.shop"
SESSION = "sess-1"


def page(title: str, *ids: str) -> ScreenSnapshot:
    elements = "".join(f'<node class="android.widget.Button" resource-id="x:id/{i}"/>' for i in ids)
    return ScreenSnapshot(
        package=PKG,
        activity=".MainActivity",
        page_source=(
            f'<hierarchy><node class="android.widget.TextView" text="{title}"/>'
            f"{elements}</hierarchy>"
        ),
    )


HOME = page("Home", "cart", "menu")
CART = page("Cart", "checkout", "back")
CHECKOUT = page("Checkout", "pay")


@pytest.fixture
async def store() -> AsyncIterator[NavigationStore]:
    config = NavigationStoreConfig()
    instance = NavigationStore(NavigationDatabase(config), config)
    try:
        yield instance
    finally:
        await instance.aclose()


@pytest.fixture
async def run(store: NavigationStore) -> int:
    result = await store.start_run(
        StartRunRequest(
            device_id="emulator-5554", package=PKG, app_version="1.2.0", session_id=SESSION
        )
    )
    await store.record_observation(
        RecordObservationRequest(run_id=result.run.run_id, snapshot=HOME)
    )
    return result.run.run_id


async def walk(store: NavigationStore, run_id: int, times: int = 1) -> None:
    """Home -> Cart -> Checkout and back again, `times` over."""
    for _ in range(times):
        await store.record_interaction(
            RecordInteractionRequest(run_id, "tap_element", "id=cart", CART)
        )
        await store.record_interaction(
            RecordInteractionRequest(run_id, "tap_element", "id=checkout", CHECKOUT)
        )
        await store.record_interaction(RecordInteractionRequest(run_id, "press_key", "back", CART))
        await store.record_interaction(RecordInteractionRequest(run_id, "press_key", "back", HOME))


# -- recording ---------------------------------------------------------------


async def test_a_run_starts_open_and_empty(store: NavigationStore) -> None:
    result = await store.start_run(StartRunRequest(device_id="emulator-5554", package=PKG))

    assert result.run.open
    assert result.run.step_count == 0
    assert result.run.package == PKG


async def test_a_run_needs_a_device_and_a_package(store: NavigationStore) -> None:
    with pytest.raises(InvalidNavigationRequest):
        await store.start_run(StartRunRequest(device_id="", package=PKG))


async def test_opening_a_second_run_for_one_session_closes_the_first(
    store: NavigationStore,
) -> None:
    """Otherwise two open runs claim the same session and `where_am_i` is
    ambiguous -- which is what a crashed process leaves behind."""
    first = await store.start_run(StartRunRequest(device_id="d", package=PKG, session_id=SESSION))
    second = await store.start_run(StartRunRequest(device_id="d", package=PKG, session_id=SESSION))

    assert (await store.get_run(GetRunRequest(run_id=first.run.run_id))).run.ended_at is not None
    assert (await store.get_run(GetRunRequest(run_id=second.run.run_id))).run.open


async def test_the_first_observation_is_a_real_journal_entry(
    store: NavigationStore, run: int
) -> None:
    """Recorded as a step so "where is this session" stays one query."""
    journal = await store.get_run(GetRunRequest(run_id=run))

    assert journal.entries[0].action == OBSERVE_ACTION
    assert journal.entries[0].from_screen is None
    assert journal.entries[0].to_screen is not None


async def test_the_same_screen_seen_twice_is_one_row_with_two_visits(
    store: NavigationStore, run: int
) -> None:
    """The deduplication the whole map depends on."""
    await store.record_interaction(RecordInteractionRequest(run, "press_key", "back", HOME))

    screens = await store.search_screens(SearchScreensRequest(package=PKG))
    home = next(s for s in screens.screens if s.label == "Home")
    assert home.visit_count == 2
    assert len(screens.screens) == 1


async def test_walking_an_edge_twice_increments_it_rather_than_duplicating_it(
    store: NavigationStore, run: int
) -> None:
    await walk(store, run, times=3)

    where = await store.where_am_i(WhereAmIRequest(session_id=SESSION))
    cart = next(r for r in where.routes if r.target == "id=cart")
    assert cart.traversal_count == 3
    assert cart.success_count == 3
    assert len([r for r in where.routes if r.target == "id=cart"]) == 1


async def test_a_failed_interaction_is_journalled_but_counts_as_a_non_success(
    store: NavigationStore, run: int
) -> None:
    """ "Tapping this here does not work" is knowledge; it is just not a success."""
    result = await store.record_interaction(
        RecordInteractionRequest(run, "tap", "id=ghost", HOME, ok=False, detail="not found")
    )

    assert result.stayed_put
    journal = await store.get_run(GetRunRequest(run_id=run))
    assert journal.failures
    where = await store.where_am_i(WhereAmIRequest(session_id=SESSION))
    ghost = next(r for r in where.routes if r.target == "id=ghost")
    assert (ghost.traversal_count, ghost.success_count) == (1, 0)


async def test_an_interaction_with_no_capture_is_journalled_but_teaches_nothing(
    store: NavigationStore, run: int
) -> None:
    result = await store.record_interaction(
        RecordInteractionRequest(run, "tap", "id=x", snapshot_after=None)
    )

    assert not result.new_route
    assert result.to_screen is None
    assert (await store.get_run(GetRunRequest(run_id=run))).run.step_count == 2


async def test_an_action_requires_a_name(store: NavigationStore, run: int) -> None:
    with pytest.raises(InvalidNavigationRequest):
        await store.record_interaction(RecordInteractionRequest(run, "  ", "x", HOME))


async def test_a_closed_run_accepts_nothing_more(store: NavigationStore, run: int) -> None:
    await store.end_run(EndRunRequest(run_id=run))

    with pytest.raises(RunAlreadyEnded):
        await store.record_interaction(RecordInteractionRequest(run, "tap", "x", HOME))


async def test_ending_a_run_twice_is_refused(store: NavigationStore, run: int) -> None:
    await store.end_run(EndRunRequest(run_id=run))

    with pytest.raises(RunAlreadyEnded):
        await store.end_run(EndRunRequest(run_id=run))


# -- reading ------------------------------------------------------------------


async def test_where_am_i_follows_the_device(store: NavigationStore, run: int) -> None:
    await store.record_interaction(RecordInteractionRequest(run, "tap_element", "id=cart", CART))

    assert (await store.where_am_i(WhereAmIRequest(session_id=SESSION))).screen.label == "Cart"


async def test_where_am_i_ranks_the_most_reliable_route_first(
    store: NavigationStore, run: int
) -> None:
    """A caller that reads only the top option must get the one most likely to
    work, not the one inserted first."""
    await walk(store, run, times=4)
    await store.record_interaction(RecordInteractionRequest(run, "tap", "id=flaky", CART, ok=False))
    await store.record_interaction(RecordInteractionRequest(run, "press_key", "back", HOME))

    routes = (await store.where_am_i(WhereAmIRequest(session_id=SESSION))).routes

    assert routes[0].target == "id=cart"
    assert routes == tuple(sorted(routes, key=lambda r: r.confidence, reverse=True))


async def test_an_unknown_session_is_reported_as_such(store: NavigationStore) -> None:
    with pytest.raises(NoRunForSession):
        await store.where_am_i(WhereAmIRequest(session_id="never-opened"))


async def test_a_run_with_no_observation_has_no_position(store: NavigationStore) -> None:
    await store.start_run(StartRunRequest(device_id="d", package=PKG, session_id="fresh"))

    with pytest.raises(ScreenNotRecognised):
        await store.where_am_i(WhereAmIRequest(session_id="fresh"))


async def test_find_path_returns_the_actions_to_perform(store: NavigationStore, run: int) -> None:
    await walk(store, run, times=2)

    path = await store.find_path(FindPathRequest(session_id=SESSION, destination="Checkout"))

    assert [step.target for step in path.steps] == ["id=cart", "id=checkout"]
    assert path.confidence > 0
    assert not path.already_there


async def test_find_path_resolves_a_destination_by_screen_id(
    store: NavigationStore, run: int
) -> None:
    """Unambiguous, and what a previous tool call handed back."""
    await walk(store, run)
    checkout = next(
        s
        for s in (await store.search_screens(SearchScreensRequest(package=PKG))).screens
        if s.label == "Checkout"
    )

    path = await store.find_path(
        FindPathRequest(session_id=SESSION, destination=str(checkout.screen_id))
    )

    assert path.destination.screen_id == checkout.screen_id


async def test_find_path_to_where_you_already_are_is_empty(
    store: NavigationStore, run: int
) -> None:
    await walk(store, run)

    path = await store.find_path(FindPathRequest(session_id=SESSION, destination="Home"))

    assert path.already_there
    assert path.steps == ()


async def test_an_unknown_destination_lists_what_is_known(store: NavigationStore, run: int) -> None:
    await walk(store, run)

    with pytest.raises(DestinationNotFound) as excinfo:
        await store.find_path(FindPathRequest(session_id=SESSION, destination="Nowhere"))

    assert excinfo.value.known


async def test_an_unreachable_destination_says_how_far_it_looked(
    store: NavigationStore, run: int
) -> None:
    await walk(store, run)

    with pytest.raises(NoKnownRoute) as excinfo:
        await store.find_path(
            FindPathRequest(session_id=SESSION, destination="Checkout", max_steps=1)
        )

    assert excinfo.value.max_steps == 1


async def test_a_path_never_routes_through_another_app(store: NavigationStore, run: int) -> None:
    """Edges are loaded per package, so one app's map can never leak into another's."""
    await walk(store, run)
    other = await store.start_run(
        StartRunRequest(device_id="d", package="com.other.app", session_id="sess-2")
    )
    await store.record_observation(
        RecordObservationRequest(
            run_id=other.run.run_id,
            snapshot=ScreenSnapshot(package="com.other.app", page_source=HOME.page_source),
        )
    )

    with pytest.raises(DestinationNotFound):
        await store.find_path(FindPathRequest(session_id="sess-2", destination="Checkout"))


async def test_search_finds_a_screen_by_part_of_its_title(store: NavigationStore, run: int) -> None:
    await walk(store, run)

    found = await store.search_screens(SearchScreensRequest(package=PKG, query="check"))

    assert [s.label for s in found.screens] == ["Checkout"]


async def test_search_of_an_unknown_app_is_empty_rather_than_an_error(
    store: NavigationStore,
) -> None:
    assert (await store.search_screens(SearchScreensRequest(package="com.nope"))).screens == ()


async def test_the_journal_keeps_every_repetition(store: NavigationStore, run: int) -> None:
    """Unlike the map: an action taken four times appears four times here."""
    await walk(store, run, times=4)

    journal = await store.get_run(GetRunRequest(run_id=run))

    taps = [e for e in journal.entries if e.target == "id=cart"]
    assert len(taps) == 4
    assert [e.seq for e in journal.entries] == list(range(1, len(journal.entries) + 1))


async def test_an_unknown_run_is_reported(store: NavigationStore) -> None:
    with pytest.raises(RunNotFound):
        await store.get_run(GetRunRequest(run_id=9999))


# -- forgetting ----------------------------------------------------------------


async def test_forgetting_nothing_is_refused(store: NavigationStore) -> None:
    """Arriving at a full wipe by omission is not a thing this makes easy."""
    with pytest.raises(InvalidNavigationRequest):
        await store.forget(ForgetRequest())


async def test_forgetting_a_package_drops_its_whole_map(store: NavigationStore, run: int) -> None:
    await walk(store, run, times=2)

    result = await store.forget(ForgetRequest(package=PKG))

    assert result.runs_deleted == 1
    assert result.screens_deleted == 3
    assert result.transitions_deleted > 0
    assert (await store.search_screens(SearchScreensRequest(package=PKG))).screens == ()


async def test_forgetting_a_run_keeps_what_it_taught(store: NavigationStore, run: int) -> None:
    """Ageing out a diary should not cost the knowledge in it."""
    await walk(store, run, times=2)

    result = await store.forget(ForgetRequest(run_id=run))

    assert result.runs_deleted == 1
    assert result.screens_deleted == 0
    assert result.transitions_deleted == 0
    assert (await store.search_screens(SearchScreensRequest(package=PKG))).screens


async def test_forgetting_by_age_keeps_the_map_too(store: NavigationStore, run: int) -> None:
    await walk(store, run)
    future = dt.datetime.now(dt.UTC) + dt.timedelta(days=1)

    result = await store.forget(ForgetRequest(before=future))

    assert result.runs_deleted == 1
    assert result.screens_deleted == 0
    assert (await store.search_screens(SearchScreensRequest(package=PKG))).screens


async def test_forgetting_one_app_leaves_another_alone(store: NavigationStore, run: int) -> None:
    await walk(store, run)
    other = await store.start_run(StartRunRequest(device_id="d", package="com.other.app"))
    await store.record_observation(
        RecordObservationRequest(
            run_id=other.run.run_id,
            snapshot=ScreenSnapshot(package="com.other.app", page_source=HOME.page_source),
        )
    )

    await store.forget(ForgetRequest(package=PKG))

    assert (await store.search_screens(SearchScreensRequest(package="com.other.app"))).screens


# -- writing to a real file ---------------------------------------------------
#
# Every test above uses an in-memory database and proves the logic is correct.
# These tests use a ``tmp_path``-backed SQLite file and prove the write path
# actually persists: a bug here that does not appear above means the problem is
# in the file I/O layer.


def _cfg(tmp_path: Path) -> NavigationStoreConfig:
    return NavigationStoreConfig(database_path=tmp_path / "nav.db")


async def test_start_run_row_survives_close_and_reopen(tmp_path: Path) -> None:
    """The Run table must hold the row after the engine is closed and a new one is opened."""
    cfg = _cfg(tmp_path)

    async with NavigationStore(NavigationDatabase(cfg), cfg) as s:
        result = await s.start_run(StartRunRequest(device_id="emulator-5554", package=PKG))
        saved_id = result.run.run_id

    async with NavigationStore(NavigationDatabase(cfg), cfg) as s:
        journal = await s.get_run(GetRunRequest(run_id=saved_id))

    assert journal.run.run_id == saved_id
    assert journal.run.package == PKG
    assert journal.run.open


async def test_screen_row_survives_close_and_reopen(tmp_path: Path) -> None:
    """The Screen table must hold the row after a close/reopen cycle."""
    cfg = _cfg(tmp_path)

    async with NavigationStore(NavigationDatabase(cfg), cfg) as s:
        result = await s.start_run(StartRunRequest(device_id="emulator-5554", package=PKG))
        obs = await s.record_observation(
            RecordObservationRequest(run_id=result.run.run_id, snapshot=HOME)
        )
        screen_id = obs.screen.screen_id

    async with NavigationStore(NavigationDatabase(cfg), cfg) as s:
        found = await s.search_screens(SearchScreensRequest(package=PKG))

    assert len(found.screens) == 1
    assert found.screens[0].screen_id == screen_id
    assert found.screens[0].label == "Home"


async def test_transition_row_survives_close_and_reopen(tmp_path: Path) -> None:
    """The Transition table must hold the edge after a close/reopen cycle."""
    cfg = _cfg(tmp_path)

    async with NavigationStore(NavigationDatabase(cfg), cfg) as s:
        result = await s.start_run(StartRunRequest(device_id="emulator-5554", package=PKG))
        run_id = result.run.run_id
        await s.record_observation(RecordObservationRequest(run_id=run_id, snapshot=HOME))
        await s.record_interaction(RecordInteractionRequest(run_id, "tap_element", "id=cart", CART))

    async with NavigationStore(NavigationDatabase(cfg), cfg) as s:
        second_run = await s.start_run(
            StartRunRequest(device_id="emulator-5554", package=PKG, session_id="sess-disk")
        )
        await s.record_observation(
            RecordObservationRequest(run_id=second_run.run.run_id, snapshot=HOME)
        )
        where = await s.where_am_i(WhereAmIRequest(session_id="sess-disk"))

    assert any(r.target == "id=cart" for r in where.routes)


async def test_step_rows_survive_close_and_reopen(tmp_path: Path) -> None:
    """The Step table must hold every journal entry after a close/reopen cycle."""
    cfg = _cfg(tmp_path)

    async with NavigationStore(NavigationDatabase(cfg), cfg) as s:
        result = await s.start_run(StartRunRequest(device_id="emulator-5554", package=PKG))
        run_id = result.run.run_id
        await s.record_observation(RecordObservationRequest(run_id=run_id, snapshot=HOME))
        await s.record_interaction(RecordInteractionRequest(run_id, "tap_element", "id=cart", CART))
        await s.record_interaction(
            RecordInteractionRequest(run_id, "tap_element", "id=checkout", CHECKOUT)
        )

    async with NavigationStore(NavigationDatabase(cfg), cfg) as s:
        journal = await s.get_run(GetRunRequest(run_id=run_id))

    assert journal.run.step_count == 3  # observe + 2 interactions
    assert [e.seq for e in journal.entries] == [1, 2, 3]


async def test_end_run_persists_to_disk(tmp_path: Path) -> None:
    """A closed run must still appear as closed after a process restart."""
    cfg = _cfg(tmp_path)

    async with NavigationStore(NavigationDatabase(cfg), cfg) as s:
        result = await s.start_run(StartRunRequest(device_id="emulator-5554", package=PKG))
        run_id = result.run.run_id
        await s.end_run(EndRunRequest(run_id=run_id))

    async with NavigationStore(NavigationDatabase(cfg), cfg) as s:
        journal = await s.get_run(GetRunRequest(run_id=run_id))

    assert journal.run.ended_at is not None
    assert not journal.run.open


async def test_all_four_tables_hold_data_after_a_full_walk(tmp_path: Path) -> None:
    """The four tables (run, screen, step, transition) must all survive a close/reopen."""
    cfg = _cfg(tmp_path)

    async with NavigationStore(NavigationDatabase(cfg), cfg) as s:
        result = await s.start_run(
            StartRunRequest(device_id="emulator-5554", package=PKG, session_id="sess-file-1")
        )
        run_id = result.run.run_id
        await s.record_observation(RecordObservationRequest(run_id=run_id, snapshot=HOME))
        await walk(s, run_id, times=2)
        await s.end_run(EndRunRequest(run_id=run_id))

    async with NavigationStore(NavigationDatabase(cfg), cfg) as s:
        journal = await s.get_run(GetRunRequest(run_id=run_id))
        screens = await s.search_screens(SearchScreensRequest(package=PKG))

        second_run = await s.start_run(
            StartRunRequest(device_id="emulator-5554", package=PKG, session_id="sess-file-2")
        )
        await s.record_observation(
            RecordObservationRequest(run_id=second_run.run.run_id, snapshot=HOME)
        )
        where = await s.where_am_i(WhereAmIRequest(session_id="sess-file-2"))

    # Run table
    assert journal.run.ended_at is not None
    assert journal.run.step_count > 0

    # Screen table — 3 distinct screens from walk()
    assert len(screens.screens) == 3

    # Step table — observe + 4 steps × 2 repetitions
    assert len(journal.entries) == 9

    # Transition table — routes still readable after reopen
    assert len(where.routes) > 0
