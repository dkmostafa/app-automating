"""The Android module's presentation layer: the delivery surface.

The module's MCP tools live here and are registered from ``src/index.py`` -- one
import and one call, per Rule 3 §1. This package owns everything about how the
module looks from the outside: the tool names, their arguments, their
descriptions, the JSON shape of their results, and the translation of a domain
failure into something a caller can act on.

The package is split by kind, in a fixed acyclic order:

    schemas -> rendering -> errors -> emulator_tools

* ``schemas.py`` -- the pydantic payloads the tools return, so every tool
  publishes an output schema and not just prose.
* ``rendering.py`` -- pure: a domain result becomes a payload here and nowhere
  else.
* ``errors.py`` -- pure: the domain-error to ``ToolError`` table, remedies
  included.
* ``emulator_tools.py`` -- the nine tools and
  :func:`register_android_emulator_tools`.

What this layer may import (Rule 0 §1):

* ``..domain`` -- a tool naturally speaks the domain's results and catches its
  errors.
* ``..application`` -- the service it is handed, typed as what it is.

What it may never import is ``..infrastructure``. A tool that reaches for a
concrete adapter has bypassed the wiring layer, and the ports stop meaning
anything. It also builds nothing: the service arrives through
:func:`register_android_emulator_tools`, because composition happens in
``application/di.py`` and in one other place, ``src/index.py``, which calls it.
``fastmcp`` may be imported here and in no other layer.
"""

from .emulator_tools import register_android_emulator_tools

__all__ = ["register_android_emulator_tools"]
