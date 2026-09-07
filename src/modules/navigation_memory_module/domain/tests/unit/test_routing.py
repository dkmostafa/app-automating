"""The routing policy: which of two known routes is the better one to attempt.

Pure, so these are unit tests over plain integers and floats -- no store, no
database, and no screen anywhere in the file.
"""

from __future__ import annotations

import pytest

from modules.navigation_memory_module.domain.routing import (
    RouteEdge,
    adjacency_of,
    plan_route,
)

pytestmark = pytest.mark.unit


def edge(source: int, target: int, name: str, confidence: float = 0.9) -> RouteEdge:
    return RouteEdge(
        from_screen_id=source,
        to_screen_id=target,
        action="tap_element",
        target=name,
        confidence=confidence,
    )


def test_the_direct_route_wins_when_it_is_as_reliable() -> None:
    edges = (edge(1, 3, "direct"), edge(1, 2, "via"), edge(2, 3, "second"))

    assert [hop.target for hop in plan_route(edges, 1, 3)] == ["direct"]


def test_a_longer_reliable_route_beats_a_short_flaky_one() -> None:
    """The whole policy in one assertion: a plan is only useful if it survives
    being executed, so the weakest hop is what a route is scored by."""
    edges = (
        edge(1, 3, "lucky", 0.34),
        edge(1, 2, "proven", 0.95),
        edge(2, 3, "proven2", 0.92),
    )

    assert [hop.target for hop in plan_route(edges, 1, 3)] == ["proven", "proven2"]


def test_the_route_returned_is_the_one_with_the_strongest_weakest_hop() -> None:
    edges = (
        edge(1, 2, "a", 0.9),
        edge(2, 4, "b", 0.5),
        edge(1, 3, "c", 0.8),
        edge(3, 4, "d", 0.8),
    )

    hops = plan_route(edges, 1, 4)

    assert min(hop.confidence for hop in hops) == 0.8
    assert [hop.target for hop in hops] == ["c", "d"]


def test_being_already_there_is_an_empty_route() -> None:
    assert plan_route((edge(1, 2, "a"),), 1, 1) == ()


def test_an_unreachable_destination_is_an_empty_route() -> None:
    """Empty and "already there" are both empty; the caller tells them apart by
    comparing the two ids, which it already holds."""
    assert plan_route((edge(1, 2, "a"),), 2, 1) == ()


def test_max_steps_bounds_the_search_rather_than_filtering_afterwards() -> None:
    chain = tuple(edge(n, n + 1, f"hop{n}") for n in range(1, 8))

    assert plan_route(chain, 1, 8, max_steps=3) == ()
    assert len(plan_route(chain, 1, 8, max_steps=10)) == 7


def test_min_confidence_excludes_routes_that_have_failed_before() -> None:
    edges = (edge(1, 2, "shaky", 0.2), edge(2, 3, "fine", 0.9))

    assert plan_route(edges, 1, 3, min_confidence=0.5) == ()
    assert plan_route(edges, 1, 3, min_confidence=0.1)


def test_a_self_loop_never_appears_in_a_plan() -> None:
    """Worth recording -- "this button does nothing here" is knowledge -- but it
    can never make progress, so it must not pad a route."""
    edges = (edge(1, 1, "noop"), edge(1, 2, "real"))

    hops = plan_route(edges, 1, 2)

    assert [hop.target for hop in hops] == ["real"]
    assert all(not hop.is_self_loop for hop in hops)


def test_a_cycle_does_not_trap_the_search() -> None:
    edges = (edge(1, 2, "a"), edge(2, 1, "back"), edge(2, 3, "on"))

    assert [hop.target for hop in plan_route(edges, 1, 3)] == ["a", "on"]


def test_the_hops_come_back_in_execution_order() -> None:
    edges = (edge(1, 2, "first"), edge(2, 3, "second"), edge(3, 4, "third"))

    hops = plan_route(edges, 1, 4)

    assert [hop.target for hop in hops] == ["first", "second", "third"]
    assert hops[0].from_screen_id == 1
    assert hops[-1].to_screen_id == 4


def test_adjacency_drops_self_loops_and_groups_by_origin() -> None:
    graph = adjacency_of((edge(1, 2, "a"), edge(1, 3, "b"), edge(2, 2, "noop")))

    assert set(graph) == {1}
    assert len(graph[1]) == 2


def test_an_empty_map_plans_nothing() -> None:
    assert plan_route((), 1, 2) == ()
