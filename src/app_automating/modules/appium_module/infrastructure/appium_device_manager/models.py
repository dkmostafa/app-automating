"""The component's internal value objects: things that never cross a port.

Rule 0 §2 draws the line by audience. A type that appears in a port signature
belongs to ``domain/`` and the adapter imports it; a type that only ever
describes *how this adapter does its job* stays here and stays out of the
domain. A child process's exit status is the clearest example -- the domain has
no business knowing that Appium drivers are installed by a subprocess at all.

This file is the bottom of the package's dependency order and imports nothing
from it. :func:`display_command` lives here rather than in :mod:`.parsing` on
purpose: :mod:`.errors` needs it to format a message, and putting it in parsing
would make the graph ``errors -> parsing -> errors``.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

__all__ = ["CommandResult", "ServerStatus", "display_command", "MAX_OUTPUT_TAIL"]

#: How much of a child's output an error carries. Enough to read the actual
#: complaint, bounded so a chatty tool cannot put a megabyte of npm progress
#: into an exception message.
MAX_OUTPUT_TAIL = 2000


def display_command(argv: tuple[str, ...] | list[str]) -> str:
    """An argv as a copy-pasteable shell command, for messages and logs."""
    return shlex.join(argv)


@dataclass(frozen=True, slots=True)
class CommandResult:
    """What a finished child process left behind."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def command(self) -> str:
        return display_command(self.argv)

    @property
    def output(self) -> str:
        """stdout and stderr together: tools disagree about which one to use."""
        return "\n".join(part for part in (self.stdout, self.stderr) if part.strip())

    @property
    def tail(self) -> str:
        """The last :data:`MAX_OUTPUT_TAIL` characters of the combined output."""
        combined = self.output
        if len(combined) <= MAX_OUTPUT_TAIL:
            return combined
        return "..." + combined[-MAX_OUTPUT_TAIL:]


@dataclass(frozen=True, slots=True)
class ServerStatus:
    """The Appium server this adapter would talk to, as it stands right now."""

    url: str
    #: True when something answered ``/status`` at :attr:`url`.
    running: bool
    #: True when *this process* launched it, and is therefore responsible for
    #: killing it. False for a server that was already up.
    managed: bool = False
    #: Host pid of a managed server. None for one we did not start.
    pid: int | None = None
    #: Version string the server reported, when it answered.
    version: str | None = None
