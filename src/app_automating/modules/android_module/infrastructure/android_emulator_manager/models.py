"""The dataclasses this adapter speaks internally.

The ``*Request`` and ``*Result`` types the manager takes and returns are *not*
here: they cross the port boundary, so Rule 0 §2 puts them in
``android_module.domain.models`` and this package imports them.

What is left is the vocabulary of a child process. :class:`CommandResult`
describes an exit status and two pipes -- the domain has no business knowing
that AVDs are created by running a subprocess at all.

This module imports nothing from the rest of the package; it is the bottom of
its dependency graph.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

__all__ = ["CommandResult", "display_command"]


def display_command(argv: Sequence[str]) -> str:
    """Render an argv for a human reading an error message."""
    return " ".join(argv)


@dataclass(frozen=True, slots=True)
class CommandResult:
    """One completed host-tool invocation."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float

    @property
    def display_command(self) -> str:
        return display_command(self.argv)

    @property
    def output(self) -> str:
        """stdout and stderr together -- Android tools are inconsistent about which."""
        return f"{self.stdout}\n{self.stderr}"

    def first_error_line(self) -> str:
        for line in self.output.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
        return "(no output)"
