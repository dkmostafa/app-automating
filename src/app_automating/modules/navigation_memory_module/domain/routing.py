"""Choosing a route through the learned map. Pure, and the second business rule.

Like :mod:`.fingerprinting`, this is here rather than beside the database
because it is a rule about navigation rather than about storage: which of two
known routes is the better one to attempt is the same question whatever the
edges were loaded from. The store's job is to hand this function the edges; the
policy below is not its business.

The policy, and why it is not shortest-path
-------------------------------------------

A route is scored by its **weakest hop**, and the best route is the one whose
weakest hop is strongest -- a widest-path search rather than a shortest-path
one. Two hops that work nine times in ten are worth more than one hop that
worked once, because a plan is only useful if it survives being executed: the
short route that fails at step one costs a recovery, and the long reliable one
costs a few extra taps.

Ties on that score are broken by fewer hops, so among equally reliable routes
the direct one wins.

Self-edges -- an action that left the device where it was -- are skipped. They
are worth *recording*, because "this button does nothing here" is real
knowledge, but they can never make progress and would otherwise pad a plan.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass

__all__ = ["RouteEdge", "plan_route", "adjacency_of"]


@dataclass(frozen=True, slots=True)
class RouteEdge:
    """One learned edge, reduced to what the search needs.

    Deliberately not :class:`~.models.RouteOption`: the search has no use for
    titles, timestamps or visit counts, and taking the smaller type keeps this
    function callable from a test with four integers.
    """

    from_screen_id: int
    to_screen_id: int
    action: str
    target: str
    confidence: float

    @property
    def is_self_loop(self) -> bool:
        return self.from_screen_id == self.to_screen_id


def adjacency_of(edges: tuple[RouteEdge, ...]) -> dict[int, list[RouteEdge]]:
    """Group edges by where they start, dropping the ones that go nowhere."""
    graph: dict[int, list[RouteEdge]] = {}
    for edge in edges:
        if edge.is_self_loop:
            continue
        graph.setdefault(edge.from_screen_id, []).append(edge)
    return graph


def plan_route(
    edges: tuple[RouteEdge, ...],
    origin_id: int,
    destination_id: int,
    *,
    max_steps: int = 12,
    min_confidence: float = 0.0,
) -> tuple[RouteEdge, ...]:
    """The most reliable known route from ``origin_id`` to ``destination_id``.

    Returns the hops in order, or an empty tuple when no route exists within
    the limits -- "already there" and "no way there" are both empty, and the
    caller distinguishes them by comparing the two ids, which it already has.

    A widest-path Dijkstra: the queue is ordered by the bottleneck confidence of
    the best route found to each screen so far, so the first time the
    destination is popped, the route that reached it is optimal under the policy
    above. ``max_steps`` bounds the search rather than filtering afterwards,
    which is what keeps a densely-connected app from being explored in full.
    """
    if origin_id == destination_id:
        return ()

    graph = adjacency_of(edges)

    #: screen -> (bottleneck confidence, hop count) of the best route so far.
    best: dict[int, tuple[float, int]] = {origin_id: (1.0, 0)}
    came_from: dict[int, tuple[int, RouteEdge]] = {}

    # heapq is a min-heap and this is a maximisation, so confidence is negated;
    # hops stay positive so that fewer hops wins a tie.
    queue: list[tuple[float, int, int]] = [(-1.0, 0, origin_id)]
    settled: set[int] = set()

    while queue:
        negated, hops, screen_id = heapq.heappop(queue)
        if screen_id in settled:
            continue
        settled.add(screen_id)
        bottleneck = -negated

        if screen_id == destination_id:
            return _rebuild(came_from, origin_id, destination_id)

        if hops >= max_steps:
            continue

        for edge in graph.get(screen_id, ()):
            if edge.confidence < min_confidence:
                continue
            reachable = min(bottleneck, edge.confidence)
            candidate = (reachable, hops + 1)
            known = best.get(edge.to_screen_id)
            # Strictly better bottleneck, or the same bottleneck in fewer hops.
            if known is None or (candidate[0], -candidate[1]) > (known[0], -known[1]):
                best[edge.to_screen_id] = candidate
                came_from[edge.to_screen_id] = (screen_id, edge)
                heapq.heappush(queue, (-reachable, hops + 1, edge.to_screen_id))

    return ()


def _rebuild(
    came_from: dict[int, tuple[int, RouteEdge]], origin_id: int, destination_id: int
) -> tuple[RouteEdge, ...]:
    """Walk the predecessor chain back to the origin and reverse it."""
    hops: list[RouteEdge] = []
    cursor = destination_id
    # Bounded by the number of screens that were reached, so a corrupt chain
    # cannot spin here.
    while cursor != origin_id and cursor in came_from and len(hops) <= len(came_from):
        previous, edge = came_from[cursor]
        hops.append(edge)
        cursor = previous
    hops.reverse()
    return tuple(hops)
