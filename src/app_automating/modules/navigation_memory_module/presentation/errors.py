"""Domain failures, translated into something the caller can act on.

Rule 3 §6: no exception escapes a tool untranslated, and the translation is a
table rather than an ``if``-chain (Rule 0 §3, OCP) -- a new domain error adds a
row here and edits no function.

Two things are deliberate about the shape of these messages.

* Each ends in a **remedy**: what to do next. The client is a model choosing its
  next tool call, and "this screen has not been seen before" without "explore it
  and it will be remembered" reads as a dead end rather than an instruction.
* The evidence comes from ``str(exc)``, which every domain error renders from
  the attributes it carries. No stack trace, no SQL, never a bare ``repr``.

A note on the tone of two rows. :class:`ScreenNotRecognised` and
:class:`NoKnownRoute` are not faults -- they are the memory correctly reporting
that it has nothing yet, and the remedy is to go and find out. A caller that
treats them as errors and gives up is the failure mode this table exists to
prevent.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastmcp.exceptions import ToolError

from ..domain.errors import (
    DestinationNotFound,
    InvalidNavigationRequest,
    MemoryFailure,
    MemoryUnavailable,
    NavigationMemoryError,
    NoKnownRoute,
    NoRunForSession,
    RunAlreadyEnded,
    RunNotFound,
    ScreenNotFound,
    ScreenNotRecognised,
)

__all__ = ["REMEDIES", "as_tool_error", "domain_errors_as_tool_errors"]

#: What the caller should do about each failure, most specific first.
#:
#: Order matters: the first row whose type matches wins, so a subclass must
#: appear above its base. :class:`NavigationMemoryError` is the last row and
#: the honest fallback -- a new domain error is reported rather than swallowed.
REMEDIES: tuple[tuple[type[NavigationMemoryError], str], ...] = (
    # -- nothing learned yet, which is not a fault --------------------------
    (
        ScreenNotRecognised,
        "This screen is new to the memory, which is normal on a first visit and not an "
        "error. Explore it with the appium tools; every interaction is recorded, so it "
        "will be recognised next time. Do not retry this call -- nothing will change "
        "until something has been done on this screen.",
    ),
    (
        NoKnownRoute,
        "The destination is known but nothing links it to where you are. Call "
        "navigation_memory_where_am_i to see what does lead somewhere from here and explore "
        "toward it, or raise max_steps if the route is likely to be a long one. "
        "Lowering min_confidence will also consider routes that have failed before.",
    ),
    (
        DestinationNotFound,
        "No screen matches that name. Call navigation_memory_search_screens for this package "
        "and use a label or screen_id from that list -- the match is on title and "
        "activity, so a screen never visited under that name cannot be found.",
    ),
    # -- the caller asked about something that is not there -----------------
    (
        NoRunForSession,
        "Nothing is being recorded for that session. Check the session_id against "
        "appium_get_open_sessions, and note that recording only starts when a session "
        "is opened -- a session that predates the memory has no run. Retrying will not "
        "create one.",
    ),
    (
        RunNotFound,
        "No run with that id. Call navigation_memory_where_am_i for the current session's "
        "run_id rather than guessing one.",
    ),
    (
        RunAlreadyEnded,
        "That run was closed when its session ended. Its journal is still readable with "
        "navigation_memory_get_run; nothing further can be recorded against it.",
    ),
    (
        ScreenNotFound,
        "No screen with that id. Ids come from navigation_memory_where_am_i and "
        "navigation_memory_search_screens; one from an earlier conversation may have been "
        "forgotten since.",
    ),
    # -- rejected input ------------------------------------------------------
    (
        InvalidNavigationRequest,
        "The arguments cannot be carried out as written. Fix them rather than retrying "
        "unchanged -- the detail in this message says which one is at fault.",
    ),
    # -- the memory itself ---------------------------------------------------
    (
        MemoryUnavailable,
        "The navigation memory cannot be opened on this host -- an unwritable data "
        "directory, or a database written by a different build. Nothing to retry. "
        "Report it to the user; driving the device still works, it just will not be "
        "remembered.",
    ),
    (
        MemoryFailure,
        "The memory was reached and the operation failed. If the message mentions a "
        "lock, another run was writing and retrying once is reasonable; otherwise "
        "report it rather than retrying.",
    ),
    (
        NavigationMemoryError,
        "The navigation memory reported a failure it has no specific advice for. "
        "Report the message to the user rather than retrying.",
    ),
)


def as_tool_error(exc: NavigationMemoryError) -> ToolError:
    """The MCP-facing form of a domain failure: what happened, then what to do.

    The error's own class name is kept in the message on purpose -- it is the
    stable handle a caller can branch on, where the prose around it is not.
    """
    for error_type, remedy in REMEDIES:
        if isinstance(exc, error_type):
            return ToolError(f"{type(exc).__name__}: {exc}. {remedy}")
    # Unreachable while NavigationMemoryError is the last row, and kept so that
    # deleting that row is a visible failure rather than a silent None.
    raise AssertionError(f"no remedy for {type(exc).__name__}; the table lost its fallback row")


@contextmanager
def domain_errors_as_tool_errors() -> Iterator[None]:
    """Wrap the one service call in a tool body.

    Only :class:`NavigationMemoryError` is caught. Anything else is a bug in
    this server rather than a fact about the memory, and dressing it up as a
    tool result would hide it.
    """
    try:
        yield
    except NavigationMemoryError as exc:
        raise as_tool_error(exc) from exc
