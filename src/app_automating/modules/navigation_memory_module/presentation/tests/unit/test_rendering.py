"""Domain results to JSON payloads. Pure, so nothing here is replaced.

The assertions and the docstring examples in ``navigation_tools.py`` are the
same payloads on purpose: if one changes without the other, a caller is being
told a shape the tool no longer returns.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app_automating.modules.navigation_memory_module.domain.models import (
    FindPathResult,
    ForgetResult,
    GetRunResult,
    JournalEntry,
    KnownScreen,
    PathStep,
    RouteOption,
    RunSummary,
    ScreenIdentity,
    SearchScreensResult,
    WhereAmIResult,
)
from app_automating.modules.navigation_memory_module.presentation.rendering import (
    render_forget,
    render_path,
    render_run_journal,
    render_screen,
    render_screen_list,
    render_where_am_i,
)

pytestmark = pytest.mark.unit

NOW = dt.datetime(2026, 9, 4, 10, 0, tzinfo=dt.UTC)
PKG = "com.example.shop"


def screen(screen_id: int = 4, title: str | None = "Cart") -> KnownScreen:
    return KnownScreen(
        screen_id=screen_id,
        identity=ScreenIdentity(package=PKG, activity=".MainActivity", fingerprint="ab" * 16),
        title=title,
        visit_count=12,
        first_seen_at=NOW,
        last_seen_at=NOW,
    )


def run() -> RunSummary:
    return RunSummary(
        run_id=7,
        device_id="emulator-5554",
        package=PKG,
        started_at=NOW,
        session_id="abc-123",
        step_count=12,
    )


def test_a_screen_renders_its_label_rather_than_making_the_caller_derive_one() -> None:
    payload = render_screen(screen())

    assert payload.label == "Cart"
    assert payload.screen_id == 4
    assert payload.last_seen_at == "2026-09-04T10:00:00+00:00"


def test_a_datetime_becomes_a_string() -> None:
    """A datetime is not JSON. That is the one reason the renderer exists."""
    assert isinstance(render_screen(screen()).last_seen_at, str)


def test_a_screen_with_no_activity_renders_null_not_empty_string() -> None:
    bare = KnownScreen(screen_id=1, identity=ScreenIdentity(package=PKG))

    assert render_screen(bare).activity is None


def test_where_am_i_matches_the_docstrings_returns_example() -> None:
    result = WhereAmIResult(
        run=run(),
        screen=screen(),
        routes=(
            RouteOption(
                action="tap_element",
                target="id=checkout",
                to_screen=screen(9, "Checkout"),
                traversal_count=16,
                success_count=15,
                last_seen_at=NOW,
            ),
        ),
    )

    payload = render_where_am_i(result)

    assert payload.session_id == "abc-123"
    assert payload.run_id == 7
    assert payload.screen.label == "Cart"
    assert payload.routes[0].confidence == 0.89
    assert (payload.routes[0].traversals, payload.routes[0].successes) == (16, 15)
    assert payload.routes[0].leads_to.label == "Checkout"
    assert payload.unexplored is False


def test_an_unexplored_screen_says_so_in_the_payload() -> None:
    payload = render_where_am_i(WhereAmIResult(run=run(), screen=screen(), routes=()))

    assert payload.unexplored is True
    assert payload.routes == ()


def test_confidence_is_rounded_so_a_caller_does_not_read_noise_as_signal() -> None:
    option = RouteOption(
        action="tap", target="x", to_screen=screen(), traversal_count=7, success_count=5
    )

    rendered = render_where_am_i(WhereAmIResult(run=run(), screen=screen(), routes=(option,)))

    assert rendered.routes[0].confidence == round(option.confidence, 2)
    assert len(str(rendered.routes[0].confidence).split(".")[-1]) <= 2


def test_a_path_reports_its_weakest_hop_not_its_average() -> None:
    """A plan is only as good as the step most likely to fail."""
    result = FindPathResult(
        origin=screen(1, "Home"),
        destination=screen(9, "Checkout"),
        steps=(
            PathStep("tap_element", "id=cart", screen(1, "Home"), screen(4), 0.94),
            PathStep("tap_element", "id=checkout", screen(4), screen(9, "Checkout"), 0.81),
        ),
    )

    payload = render_path(result)

    assert payload.confidence == 0.81
    assert [step.target for step in payload.steps] == ["id=cart", "id=checkout"]
    assert payload.already_there is False


def test_already_being_there_renders_as_an_empty_plan() -> None:
    payload = render_path(FindPathResult(origin=screen(), destination=screen(), steps=()))

    assert payload.already_there is True
    assert payload.steps == ()
    assert payload.confidence == 0.0


def test_a_screen_list_carries_the_count_a_caller_would_else_compute() -> None:
    payload = render_screen_list(SearchScreensResult(package=PKG, screens=(screen(1), screen(2))))

    assert payload.count == 2
    assert payload.package == PKG


def test_a_journal_surfaces_its_failure_count_before_any_line_is_read() -> None:
    """A forty-step run with nine failures is a different thing from a clean one,
    and that should be visible without reading every entry."""
    result = GetRunResult(
        run=run(),
        entries=(
            JournalEntry(seq=1, action="observe", target="", at=NOW, to_screen=screen(1, "Home")),
            JournalEntry(
                seq=2,
                action="tap_element",
                target="id=cart",
                at=NOW,
                ok=False,
                from_screen=screen(1, "Home"),
                to_screen=screen(1, "Home"),
                detail="gone",
            ),
        ),
    )

    payload = render_run_journal(result)

    assert payload.failure_count == 1
    assert payload.run.step_count == 12
    assert payload.entries[0].action == "observe"
    assert payload.entries[0].from_label is None
    assert payload.entries[1].moved is False
    assert payload.entries[1].detail == "gone"


def test_a_journal_entry_renders_screens_as_labels_not_nested_objects() -> None:
    """A journal is read top to bottom; full screen records at every line would
    bury the sequence that is the point of reading it."""
    entry = JournalEntry(
        seq=1,
        action="tap",
        target="x",
        at=NOW,
        from_screen=screen(1, "Home"),
        to_screen=screen(4, "Cart"),
    )

    payload = render_run_journal(GetRunResult(run=run(), entries=(entry,)))

    assert payload.entries[0].from_label == "Home"
    assert payload.entries[0].to_label == "Cart"


def test_an_open_run_renders_a_null_end_time() -> None:
    payload = render_run_journal(GetRunResult(run=run(), entries=()))

    assert payload.run.ended_at is None
    assert payload.run.open is True


def test_forget_totals_what_it_removed() -> None:
    payload = render_forget(
        ForgetResult(runs_deleted=3, steps_deleted=412, screens_deleted=27, transitions_deleted=64)
    )

    assert payload.total == 506
    assert payload.runs_deleted == 3


def test_an_age_out_shows_zero_map_damage() -> None:
    """Seeing zeroes there is how a caller confirms the map survived."""
    payload = render_forget(ForgetResult(runs_deleted=3, steps_deleted=412))

    assert payload.screens_deleted == 0
    assert payload.transitions_deleted == 0
