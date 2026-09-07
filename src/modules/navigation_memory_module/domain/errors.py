"""The failure vocabulary of the navigation-memory module.

A use case may catch only these. An adapter raises its own richer subclass
(Rule 1 §3) which *is-a* one of these, so the application layer never names a
SQLAlchemy type in an ``except`` and no translation step sits in the middle
waiting to be forgotten.

Two conventions hold for every error here, matching the rest of the product:

* **The constructor arguments are the state.** Each ``__init__`` passes its real
  arguments to ``Exception.__init__`` and stores them, so
  ``type(exc)(*exc.args)`` reconstructs the exception (Rule 0 §3, L).
* **``Exception.__init__`` is called explicitly**, not through ``super()``.
  Adapter errors inherit from both their component's base and one of these, and
  a co-operative ``super().__init__`` would dispatch along the MRO into the
  sibling's constructor with the wrong arity.

The failures divide by what the caller should do:

* the memory itself is unusable -- :class:`MemoryUnavailable`. A host problem;
  no retry helps and the device work should carry on unrecorded.
* the caller asked about something that is not there --
  :class:`RunNotFound`, :class:`ScreenNotFound`, :class:`NoRunForSession`.
  Recoverable by asking a different question.
* nothing has been learned yet -- :class:`ScreenNotRecognised`,
  :class:`NoKnownRoute`. Not errors in the device sense: they mean *explore*.
* the request was malformed -- :class:`InvalidNavigationRequest`.
"""

from __future__ import annotations

__all__ = [
    "NavigationMemoryError",
    "MemoryUnavailable",
    "MemoryFailure",
    "InvalidNavigationRequest",
    "RunNotFound",
    "RunAlreadyEnded",
    "NoRunForSession",
    "ScreenNotFound",
    "ScreenNotRecognised",
    "NoKnownRoute",
    "DestinationNotFound",
]


class NavigationMemoryError(Exception):
    """Base class for every failure this module reports."""


# -- the memory itself -----------------------------------------------------


class MemoryUnavailable(NavigationMemoryError):
    """The navigation memory cannot be reached or is the wrong shape.

    A provisioning problem -- an unwritable data directory, a database written
    by a different build. No retry fixes it, and the right response is to carry
    on driving the device without recording rather than to fail the device work.
    """

    def __init__(self, what: str, detail: str = "") -> None:
        Exception.__init__(self, what, detail)
        self.what = what
        self.detail = detail

    def __str__(self) -> str:
        suffix = f": {self.detail}" if self.detail else ""
        return f"navigation memory unavailable -- {self.what}{suffix}"


class MemoryFailure(NavigationMemoryError):
    """The memory was reached and the operation failed, in a way the domain
    cannot name."""

    def __init__(self, operation: str, detail: str = "") -> None:
        Exception.__init__(self, operation, detail)
        self.operation = operation
        self.detail = detail

    def __str__(self) -> str:
        suffix = f": {self.detail}" if self.detail else ""
        return f"{self.operation} failed{suffix}"


# -- rejected input --------------------------------------------------------


class InvalidNavigationRequest(NavigationMemoryError):
    """A request that cannot be carried out as written."""

    def __init__(self, what: str, reason: str = "") -> None:
        Exception.__init__(self, what, reason)
        self.what = what
        self.reason = reason

    def __str__(self) -> str:
        suffix = f": {self.reason}" if self.reason else ""
        return f"invalid navigation request -- {self.what}{suffix}"


# -- things that are not there ---------------------------------------------


class RunNotFound(NavigationMemoryError):
    def __init__(self, run_id: int) -> None:
        Exception.__init__(self, run_id)
        self.run_id = run_id

    def __str__(self) -> str:
        return f"no navigation run with id {self.run_id}"


class RunAlreadyEnded(NavigationMemoryError):
    """The run was closed, so nothing more can be recorded against it."""

    def __init__(self, run_id: int) -> None:
        Exception.__init__(self, run_id)
        self.run_id = run_id

    def __str__(self) -> str:
        return f"navigation run {self.run_id} has already ended"


class NoRunForSession(NavigationMemoryError):
    """Nothing is being recorded for that session.

    Either the session never had a run opened for it, or the run was closed.
    The remedy is not to retry: it is to check that recording is switched on.
    """

    def __init__(self, session_id: str) -> None:
        Exception.__init__(self, session_id)
        self.session_id = session_id

    def __str__(self) -> str:
        return f"no open navigation run for session {self.session_id!r}"


class ScreenNotFound(NavigationMemoryError):
    def __init__(self, screen_id: int) -> None:
        Exception.__init__(self, screen_id)
        self.screen_id = screen_id

    def __str__(self) -> str:
        return f"no screen with id {self.screen_id}"


# -- nothing learned yet ---------------------------------------------------


class ScreenNotRecognised(NavigationMemoryError):
    """This screen has never been seen before.

    Not a fault. It is the memory saying "explore here", and the only way a map
    ever gets built.
    """

    def __init__(self, package: str, fingerprint: str = "") -> None:
        Exception.__init__(self, package, fingerprint)
        self.package = package
        self.fingerprint = fingerprint

    def __str__(self) -> str:
        where = f" (fingerprint {self.fingerprint[:12]})" if self.fingerprint else ""
        return f"this screen of {self.package!r} has not been seen before{where}"


class NoKnownRoute(NavigationMemoryError):
    """No sequence of learned edges connects here to there.

    Distinct from :class:`DestinationNotFound`: the destination is a screen the
    memory knows, there is simply no path to it from where the session is.
    """

    def __init__(self, origin: str, destination: str, max_steps: int = 0) -> None:
        Exception.__init__(self, origin, destination, max_steps)
        self.origin = origin
        self.destination = destination
        self.max_steps = max_steps

    def __str__(self) -> str:
        within = f" within {self.max_steps} steps" if self.max_steps else ""
        return f"no known route from {self.origin!r} to {self.destination!r}{within}"


class DestinationNotFound(NavigationMemoryError):
    """Nothing in the memory matches what the caller asked to navigate to."""

    def __init__(self, destination: str, package: str = "", known: tuple[str, ...] = ()) -> None:
        Exception.__init__(self, destination, package, known)
        self.destination = destination
        self.package = package
        self.known = known

    def __str__(self) -> str:
        where = f" of {self.package!r}" if self.package else ""
        have = f"; known screens include: {', '.join(self.known)}" if self.known else ""
        return f"no known screen{where} matches {self.destination!r}{have}"
