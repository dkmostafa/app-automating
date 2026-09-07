"""The boundary DTOs: their invariants and the derived reads they carry."""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest

from app_automating.modules.navigation_memory_module.domain import models as domain_models
from app_automating.modules.navigation_memory_module.domain.models import (
    ForgetRequest,
    GetRunResult,
    JournalEntry,
    KnownScreen,
    RouteOption,
    RunSummary,
    ScreenIdentity,
    ScreenSnapshot,
    WhereAmIResult,
)

pytestmark = pytest.mark.unit

NOW = dt.datetime(2026, 9, 4, 10, 0, tzinfo=dt.UTC)


def screen(screen_id: int = 1, title: str | None = "Home", activity: str = ".Main") -> KnownScreen:
    return KnownScreen(
        screen_id=screen_id,
        identity=ScreenIdentity(
            package="com.example.shop", activity=activity, fingerprint="ab" * 16
        ),
        title=title,
        visit_count=3,
        first_seen_at=NOW,
        last_seen_at=NOW,
    )


@pytest.mark.parametrize(
    "dataclass_type",
    [
        getattr(domain_models, name)
        for name in domain_models.__all__
        if dataclasses.is_dataclass(getattr(domain_models, name))
    ],
    ids=lambda t: t.__name__,
)
def test_every_boundary_dataclass_is_frozen_and_slotted(dataclass_type: type) -> None:
    params = dataclass_type.__dataclass_params__  # type: ignore[attr-defined]
    assert params.frozen, f"{dataclass_type.__name__} must be frozen"
    assert "__slots__" in vars(dataclass_type), f"{dataclass_type.__name__} must use slots"


# -- a screen's label, which is what a caller reads --------------------------


def test_a_screen_prefers_its_title() -> None:
    assert screen(title="Checkout").label == "Checkout"


def test_a_screen_without_a_title_falls_back_to_its_activity() -> None:
    assert screen(title=None).label == ".Main"


def test_a_screen_with_neither_still_has_a_label() -> None:
    """Never empty: a label is what a listing prints, and a blank row is useless."""
    assert screen(screen_id=7, title=None, activity="").label == "screen 7"


# -- confidence, the number a planner sorts on -------------------------------


def route(traversals: int, successes: int) -> RouteOption:
    return RouteOption(
        action="tap_element",
        target="id=cart",
        to_screen=screen(),
        traversal_count=traversals,
        success_count=successes,
    )


def test_one_lucky_traversal_is_not_certainty() -> None:
    """The reason the ratio is smoothed: 1/1 and 40/40 are not the same evidence."""
    assert route(1, 1).confidence < 0.7
    assert route(40, 40).confidence > 0.95


def test_more_evidence_of_the_same_rate_scores_higher() -> None:
    assert route(20, 20).confidence > route(2, 2).confidence


def test_a_route_that_always_fails_scores_low_but_not_zero() -> None:
    """Not zero, because "it failed twice" is weaker evidence than "it failed
    forty times" and the ordering should say so."""
    assert 0.0 < route(2, 0).confidence
    assert route(40, 0).confidence < route(2, 0).confidence


def test_an_unwalked_route_has_no_confidence() -> None:
    assert route(0, 0).confidence == 0.0


def test_reliable_needs_more_than_one_walk() -> None:
    assert not route(1, 1).reliable
    assert route(5, 5).reliable
    assert not route(5, 4).reliable


# -- the derived reads on results --------------------------------------------


def test_an_unexplored_screen_says_so() -> None:
    run = RunSummary(run_id=1, device_id="emulator-5554", package="p", started_at=NOW)

    assert WhereAmIResult(run=run, screen=screen(), routes=()).unexplored
    assert not WhereAmIResult(run=run, screen=screen(), routes=(route(2, 2),)).unexplored


def test_a_run_is_open_until_it_has_an_end_time() -> None:
    run = RunSummary(run_id=1, device_id="d", package="p", started_at=NOW)

    assert run.open
    assert not dataclasses.replace(run, ended_at=NOW).open


def test_a_journal_entry_distinguishes_moving_from_succeeding() -> None:
    """An action can succeed and leave the device exactly where it was; that is
    the most useful negative result the memory holds."""
    stayed = JournalEntry(
        seq=1,
        action="tap",
        target="x",
        at=NOW,
        ok=True,
        from_screen=screen(1),
        to_screen=screen(1),
    )
    moved = JournalEntry(
        seq=2,
        action="tap",
        target="y",
        at=NOW,
        ok=True,
        from_screen=screen(1),
        to_screen=screen(2),
    )

    assert not stayed.moved and stayed.ok
    assert moved.moved


def test_an_entry_with_an_unknown_endpoint_did_not_move() -> None:
    entry = JournalEntry(seq=1, action="observe", target="", at=NOW, to_screen=screen())

    assert not entry.moved


def test_a_journal_surfaces_its_failures() -> None:
    run = RunSummary(run_id=1, device_id="d", package="p", started_at=NOW)
    entries = (
        JournalEntry(seq=1, action="tap", target="a", at=NOW, ok=True),
        JournalEntry(seq=2, action="tap", target="b", at=NOW, ok=False, detail="gone"),
    )

    assert len(GetRunResult(run=run, entries=entries).failures) == 1


# -- forget, where an omission must not mean "everything" ---------------------


def test_a_forget_request_that_names_nothing_says_so() -> None:
    assert ForgetRequest().selects_nothing


@pytest.mark.parametrize(
    "request_",
    [ForgetRequest(package="p"), ForgetRequest(run_id=1), ForgetRequest(before=NOW)],
)
def test_any_selector_makes_a_forget_request_actionable(request_: ForgetRequest) -> None:
    assert not request_.selects_nothing


# -- snapshots ----------------------------------------------------------------


def test_a_snapshot_pairs_itself_with_a_computed_fingerprint() -> None:
    snapshot = ScreenSnapshot(package="com.example.shop", activity=".Main")

    identity = snapshot.identify("beef" * 8)

    assert identity.package == "com.example.shop"
    assert identity.fingerprint == "beef" * 8


def test_an_identity_reads_as_something_a_human_can_scan() -> None:
    assert "com.example.shop" in str(ScreenIdentity("com.example.shop", ".Main", "ab" * 16))
