"""The navigation-memory surface, as MCP tools.

Five tools, all of them **reads**. Nothing here records anything: routes are
written by ``appium_module`` as it drives the device, so the map fills in
whether or not a caller thinks to help. That is deliberate -- a memory that
depended on being told would have holes exactly where a model was busy.

The order they are normally called in:

1. ``navigation_memory_where_am_i`` -- the first call on any screen. Says which screen
   the session is on and what has previously worked from it.
2. ``navigation_memory_search_screens`` -- what this app's map contains at all. How a
   destination gets a name.
3. ``navigation_memory_find_path`` -- the route from here to a named destination, as a
   list of actions to perform with the appium tools.
4. ``navigation_memory_get_run`` -- after the fact: what a run actually did, failures
   included.
5. ``navigation_memory_forget`` -- drop learned memory. The only destructive tool.

What they share: a **session_id** from ``appium_start_session``, which is how
the memory knows which device and which app is being asked about, and a
**package** (``com.example.shop``) for the tools that are not tied to a live
session. A ``screen_id`` is stable and can be passed back as a destination.

Two failures are not faults. ``ScreenNotRecognised`` means this screen is new --
explore it and it will be remembered. ``NoKnownRoute`` means nothing links here
to there yet. Both are the memory reporting honestly that it has nothing, and
the answer to both is to go and find out rather than to retry.

Layering (Rule 0 §1, Rule 3): every tool below is three statements -- build the
call from its arguments, await one service method, render the result. There is
no branching and no second call.
"""

from __future__ import annotations

import datetime as dt

from fastmcp import FastMCP

from ..application.services import NavigationMemoryService
from ..domain.errors import InvalidNavigationRequest
from .errors import domain_errors_as_tool_errors
from .rendering import (
    render_forget,
    render_path,
    render_run_journal,
    render_screen_list,
    render_where_am_i,
)
from .schemas import (
    ForgetPayload,
    PathPayload,
    RunJournalPayload,
    ScreenListPayload,
    WhereAmIPayload,
)

__all__ = ["register_navigation_tools"]


def _parse_moment(value: str | None) -> dt.datetime | None:
    """An ISO-8601 tool argument as a domain datetime, or a rejection.

    Tool arguments arrive as JSON, so a timestamp is a string here and a
    :class:`datetime` everywhere inward of this file. A naive value is read as
    UTC, matching what the memory stores; a malformed one is rejected as a
    domain error so it reaches the caller through the same remedy table as
    every other bad argument rather than as a raw ``ValueError``.
    """
    if value is None or not value.strip():
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise InvalidNavigationRequest("forget", f"{value!r} is not an ISO-8601 timestamp") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def register_navigation_tools(mcp: FastMCP, service: NavigationMemoryService) -> None:
    """Add the navigation-memory tools to ``mcp``, bound to ``service``.

    Rule 3 §2: this builds nothing. The service arrives already wired by the
    module's composition root, which is why registering the same surface on a
    second server -- a throwaway one in a test -- is free of side effects.
    """

    @mcp.tool(name="navigation_memory_where_am_i")
    async def where_am_i(session_id: str, limit: int = 20) -> WhereAmIPayload:
        """Identify the screen a session is on and list what has worked from it.

        What it does
        ------------
        Looks up the screen the recorder last saw this session reach, and
        returns it together with every action previously observed to lead
        somewhere from it, best first. Reads only what was already written while
        the device was being driven -- it does not touch the device, take a
        screenshot or fetch a page source, so it costs a database query and
        nothing else. Nothing is recorded by this call.

        When to use it
        --------------
        Call it first on any screen, before deciding what to tap. It is what
        turns "I am looking at some app" into "I am on the cart screen, and
        tapping `id=checkout` has reached checkout 15 times out of 16". Also
        call it to get the ``run_id`` that ``navigation_memory_get_run`` takes.

        When not to use it
        ------------------
        Do not call it to read the screen's contents -- it returns what the
        memory knows, not what is on the display right now; that is
        ``appium_get_page_source`` or ``appium_take_screenshot``. Do not call it
        repeatedly after each action hoping the answer changes: it only moves
        when the device does. To plan more than one hop ahead, use
        ``navigation_memory_find_path`` rather than chaining this call.

        Arguments
        ---------
        session_id:
            An open Appium session, as returned by ``appium_start_session`` and
            listed by ``appium_get_open_sessions``. The memory finds the app and
            device from it, so no package is needed here.
        limit:
            How many outgoing routes to return, most reliable first. Defaults to
            20, which is more than any real screen offers; lower it when only
            the best few matter.

        Returns
        -------
        ``{"session_id": "abc-123", "run_id": 7, "package": "com.example.shop",
        "screen": {"screen_id": 4, "label": "Cart", "visit_count": 12, ...},
        "routes": [{"action": "tap_element", "target": "id=checkout",
        "leads_to": {"screen_id": 9, "label": "Checkout", ...},
        "confidence": 0.89, "traversals": 16, "successes": 15}],
        "unexplored": false}``

        ``confidence`` is a smoothed success rate in 0-1, so a single lucky
        traversal never reads as certainty; ``traversals`` and ``successes`` are
        the raw counts behind it. ``unexplored: true`` means nothing is known to
        lead anywhere from here -- pick something and try it.

        Errors
        ------
        ``ScreenNotRecognised``: this screen is new. Not a fault -- explore it
        with the appium tools and it will be recognised next time. Retrying
        changes nothing.
        ``NoRunForSession``: nothing is being recorded for that session. Check
        the id against ``appium_get_open_sessions``.
        ``MemoryUnavailable``: the memory cannot be opened on this host. Report
        it; the device still works, it just will not be remembered.

        Example
        -------
        ``navigation_memory_where_am_i(session_id="abc-123")``
        -> ``{"run_id": 7, "package": "com.example.shop", "screen": {"screen_id": 4,
        "label": "Cart"}, "routes": [{"action": "tap_element", "target":
        "id=checkout", "confidence": 0.89}], "unexplored": false}``
        """
        with domain_errors_as_tool_errors():
            result = await service.where_am_i(session_id, limit=limit)
        return render_where_am_i(result)

    @mcp.tool(name="navigation_memory_find_path")
    async def find_path(
        session_id: str,
        destination: str,
        max_steps: int = 12,
        min_confidence: float = 0.0,
    ) -> PathPayload:
        """Plan the most reliable known route from here to a named screen.

        What it does
        ------------
        Searches the learned map for a sequence of actions leading from wherever
        the session currently is to the destination, and returns those actions
        in order. The search maximises the **weakest** hop rather than
        minimising the number of hops: two steps that work nine times in ten
        beat one step that worked once, because a plan that fails halfway costs
        a recovery. Performs nothing on the device -- it returns a plan, and the
        caller executes it with the appium tools.

        When to use it
        --------------
        Use it when you know where you want to end up and the app has been
        explored before. Call ``navigation_memory_search_screens`` first if you are not
        sure what the destination is called. Execute the returned steps one at a
        time with ``appium_tap_element``, ``appium_press_key`` and friends,
        checking ``navigation_memory_where_am_i`` if a step does not land where the
        plan expected.

        When not to use it
        ------------------
        Do not use it on an app that has never been driven -- there is no map
        yet, and the answer will be ``NoKnownRoute``. Explore with
        ``navigation_memory_where_am_i`` one hop at a time instead. Do not treat the
        result as a guarantee: it is what worked before, and an app that has
        changed will not match. Do not call it to find out where you are; that
        is ``navigation_memory_where_am_i``.

        Arguments
        ---------
        session_id:
            An open Appium session, as returned by ``appium_start_session``. The
            route starts from wherever the memory last saw this session.
        destination:
            Where to go. Matched against a screen's title, then its activity,
            then as a substring of either -- so ``"Checkout"`` works. A numeric
            string is taken as a ``screen_id`` from
            ``navigation_memory_search_screens`` or ``navigation_memory_where_am_i``, which is
            unambiguous and the safer form when a name might match twice.
        max_steps:
            Refuse to plan a route longer than this. Defaults to 12. A plan of
            twenty blind taps is a guess with extra steps; raise it only when
            the app really is that deep.
        min_confidence:
            Ignore routes below this 0-1 score. Defaults to 0.0, which considers
            everything ever seen to work. Raise it to about 0.7 to plan only
            with routes that have been walked repeatedly and rarely failed.

        Returns
        -------
        ``{"origin": {"screen_id": 4, "label": "Cart"}, "destination":
        {"screen_id": 9, "label": "Checkout"}, "steps": [{"action":
        "tap_element", "target": "id=checkout", "leads_to": {"screen_id": 9,
        "label": "Checkout"}, "confidence": 0.89}], "confidence": 0.89,
        "already_there": false}``

        ``steps`` are in execution order. The top-level ``confidence`` is the
        weakest hop, not the average -- that is the number to judge the plan by.
        ``already_there: true`` with empty ``steps`` means no movement is
        needed.

        Errors
        ------
        ``DestinationNotFound``: nothing in the map matches that name. Call
        ``navigation_memory_search_screens`` and use a label or id from the list.
        ``NoKnownRoute``: the destination is known but nothing links it to here
        yet. Not a fault -- explore toward it, raise ``max_steps``, or lower
        ``min_confidence``.
        ``ScreenNotRecognised``: the current screen is new, so there is nowhere
        to plan from. Explore it first.
        ``NoRunForSession``: nothing is being recorded for that session.
        ``MemoryUnavailable``: the memory cannot be opened. Report it.

        Example
        -------
        ``navigation_memory_find_path(session_id="abc-123", destination="Checkout",
        min_confidence=0.7)``
        -> ``{"origin": {"label": "Home"}, "destination": {"label": "Checkout"},
        "steps": [{"action": "tap_element", "target": "id=cart_button",
        "confidence": 0.94}, {"action": "tap_element", "target": "id=checkout",
        "confidence": 0.89}], "confidence": 0.89, "already_there": false}``
        """
        with domain_errors_as_tool_errors():
            result = await service.find_path(
                session_id,
                destination,
                max_steps=max_steps,
                min_confidence=min_confidence,
            )
        return render_path(result)

    @mcp.tool(name="navigation_memory_search_screens")
    async def search_screens(package: str, query: str = "", limit: int = 50) -> ScreenListPayload:
        """List the screens this memory knows for an app, newest first.

        What it does
        ------------
        Returns the screens recorded for one app package, optionally filtered by
        a substring of their title or activity, ordered by how recently each was
        seen. Reads the map only: no device, no session, no recording. This is
        the map's table of contents.

        When to use it
        --------------
        Use it to find out what an app's map contains before planning anything,
        and to turn a vague goal into something ``navigation_memory_find_path`` can
        resolve -- search for ``"check"``, see that the screen is called
        ``"Checkout"``, then pass that name or its ``screen_id`` as the
        destination. Also useful for judging whether an app has been explored
        enough to be worth planning against at all.

        When not to use it
        ------------------
        Do not use it to find out where the session is now -- that is
        ``navigation_memory_where_am_i``, and this tool does not take a session at all.
        Do not use it to check what is on screen right now; it reports what was
        remembered, which may be from a previous run or a previous app version.

        Arguments
        ---------
        package:
            The Android application id, e.g. ``com.example.shop``. Exact match,
            case-sensitive. ``navigation_memory_where_am_i`` reports it for a live
            session if you do not know it.
        query:
            Substring matched case-insensitively against each screen's title and
            activity. Defaults to empty, which lists everything known for the
            package.
        limit:
            Cap on screens returned, most recently seen first. Defaults to 50.

        Returns
        -------
        ``{"package": "com.example.shop", "screens": [{"screen_id": 9, "label":
        "Checkout", "activity": ".CheckoutActivity", "visit_count": 8,
        "last_seen_at": "2026-09-04T10:12:00+00:00"}], "count": 1}``

        ``screen_id`` is stable and is the unambiguous thing to pass to
        ``navigation_memory_find_path`` as a destination. An empty ``screens`` with
        ``count: 0`` means this app has never been driven.

        Errors
        ------
        ``MemoryUnavailable``: the memory cannot be opened on this host. Nothing
        to retry -- report it to the user.
        ``MemoryFailure``: the query failed. If the message mentions a lock,
        another run was writing and one retry is reasonable.

        Example
        -------
        ``navigation_memory_search_screens(package="com.example.shop", query="check")``
        -> ``{"package": "com.example.shop", "screens": [{"screen_id": 9,
        "label": "Checkout", "visit_count": 8}], "count": 1}``
        """
        with domain_errors_as_tool_errors():
            result = await service.search_screens(package, query=query, limit=limit)
        return render_screen_list(result)

    @mcp.tool(name="navigation_memory_get_run")
    async def get_run(run_id: int, limit: int = 200) -> RunJournalPayload:
        """Replay what one recorded run actually did, failures included.

        What it does
        ------------
        Returns a run's journal: every action in the order it happened, which
        screen it went from and to, whether it succeeded, and the failure
        message when it did not. This is the append-only record of a real drive,
        not the deduplicated map -- so an action repeated forty times appears
        forty times. Reads only.

        When to use it
        --------------
        Use it to work out why something went wrong: which step failed, what the
        device did instead, where a run got stuck in a loop. The first entry of
        every run has ``action: "observe"`` and records where the run started.
        Get a ``run_id`` from ``navigation_memory_where_am_i``.

        When not to use it
        ------------------
        Do not use it to decide what to do next -- a journal is history, and
        ``navigation_memory_where_am_i`` is the tool that says what works. Do not use
        it to list an app's screens; that is ``navigation_memory_search_screens``. On a
        long run, prefer raising ``limit`` deliberately over reading thousands
        of entries you will not use.

        Arguments
        ---------
        run_id:
            The run to replay, as reported by ``navigation_memory_where_am_i``. Runs
            are per session, so a new session means a new run.
        limit:
            Cap on entries returned, counting from the start of the run.
            Defaults to 200.

        Returns
        -------
        ``{"run": {"run_id": 7, "device_id": "emulator-5554", "package":
        "com.example.shop", "started_at": "2026-09-04T10:02:00+00:00",
        "ended_at": null, "step_count": 12, "open": true}, "entries":
        [{"seq": 1, "action": "observe", "target": "", "ok": true, "moved":
        false, "from_label": null, "to_label": "Home", "at": "..."},
        {"seq": 2, "action": "tap_element", "target": "id=cart_button",
        "ok": true, "moved": true, "from_label": "Home", "to_label": "Cart",
        "at": "..."}], "failure_count": 0}``

        ``moved`` distinguishes an action that changed screen from one that did
        not, which is not the same as ``ok`` -- an action can succeed and leave
        the device exactly where it was.

        Errors
        ------
        ``RunNotFound``: no run with that id. Call ``navigation_memory_where_am_i`` for
        the current session's run rather than guessing an id.
        ``MemoryUnavailable``: the memory cannot be opened. Report it.

        Example
        -------
        ``navigation_memory_get_run(run_id=7, limit=50)``
        -> ``{"run": {"run_id": 7, "package": "com.example.shop", "step_count":
        12, "open": true}, "entries": [{"seq": 1, "action": "observe",
        "to_label": "Home", "ok": true}], "failure_count": 0}``
        """
        with domain_errors_as_tool_errors():
            result = await service.get_run(run_id, limit=limit)
        return render_run_journal(result)

    @mcp.tool(name="navigation_memory_forget")
    async def forget(
        package: str | None = None,
        run_id: int | None = None,
        before: str | None = None,
    ) -> ForgetPayload:
        """Delete learned navigation memory. Irreversible.

        What it does
        ------------
        Removes recorded history, and how much it removes depends on which
        selector is given. ``run_id`` or ``before`` retires **journals only** --
        the runs and their steps go, and the map of screens and routes those
        runs taught is deliberately kept, because ageing out a diary should not
        cost the knowledge in it. ``package`` on its own is the full wipe: runs,
        journals, screens and every learned route for that app. **Nothing here
        can be undone**, and the map is only rebuilt by driving the app again.

        When to use it
        --------------
        Use ``package`` when an app has changed enough that its remembered
        routes are wrong -- a redesign, a major version -- and stale plans are
        worse than no plans. Use ``before`` to keep the database small while
        keeping the map. Use ``run_id`` to drop a single bad run, such as one
        recorded against a misconfigured device.

        When not to use it
        ------------------
        Do not use it to fix a single wrong route: the counts already handle
        that, because a route that stops working accumulates failures and sinks
        below the reliable ones on its own. Do not call it speculatively --
        there is no undo, and re-learning an app's map costs a full exploration.
        Do not use it to end a run; runs close by themselves when the session
        does.

        Arguments
        ---------
        package:
            An Android application id, e.g. ``com.example.shop``. On its own
            this deletes that app's entire map as well as its journals. Combined
            with ``before``, it restricts the age-out to that app and keeps the
            map.
        run_id:
            A single run to delete, with its journal. The map it taught is kept.
        before:
            An ISO-8601 timestamp, e.g. ``"2026-01-01T00:00:00+00:00"``. Deletes
            runs that started before it. The map is kept.

        At least one of the three must be given. A call with none is refused
        rather than treated as "delete everything".

        Returns
        -------
        ``{"runs_deleted": 3, "steps_deleted": 412, "screens_deleted": 0,
        "transitions_deleted": 0, "total": 415}``

        ``screens_deleted`` and ``transitions_deleted`` are non-zero only for a
        whole-package wipe; seeing zeroes there confirms the map survived.

        Errors
        ------
        ``InvalidNavigationRequest``: no selector was given, or ``before`` is
        not a valid timestamp. Name what to delete and call again.
        ``MemoryUnavailable``: the memory cannot be opened. Report it.
        ``MemoryFailure``: the delete failed; the memory is unchanged.

        Example
        -------
        ``navigation_memory_forget(package="com.example.shop")``
        -> ``{"runs_deleted": 3, "steps_deleted": 412, "screens_deleted": 27,
        "transitions_deleted": 64, "total": 506}``
        """
        with domain_errors_as_tool_errors():
            result = await service.forget(
                package=package, run_id=run_id, before=_parse_moment(before)
            )
        return render_forget(result)
