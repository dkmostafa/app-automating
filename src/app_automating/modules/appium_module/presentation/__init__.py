"""The Appium module's presentation layer: the delivery surface.

The module's MCP tools live here and are registered from ``src/app_automating/server.py`` -- one
import and one call, per Rule 3 §1. This package owns everything about how the
module looks from the outside: the tool names, their arguments, their
descriptions, the JSON shape of their results, and the translation of a domain
failure into something a caller can act on.

The package is split by kind, in a fixed acyclic order:

    schemas -> rendering -> errors -> device_tools

* ``schemas.py``     -- the pydantic payloads the tools return, so every tool
  publishes an output schema and not just prose.
* ``rendering.py``   -- pure: a domain result becomes a payload here and nowhere
  else.
* ``errors.py``      -- pure: the domain-error to ``ToolError`` table, remedies
  included.
* ``device_tools.py`` -- the thirteen tools and
  :func:`register_appium_device_tools`.

What this layer may import (Rule 0 §1):

* ``..domain`` -- a tool naturally speaks the domain's results and catches its
  errors.
* ``..application`` -- the service it is handed, typed as what it is.

What it may never import is ``..infrastructure``. A tool that reaches for a
concrete adapter has bypassed the wiring layer, and the ports stop meaning
anything. It also builds nothing: the service arrives through
:func:`register_appium_device_tools`, because composition happens in
``application/di.py`` and in one other place, ``src/app_automating/server.py``, which calls it.
``fastmcp`` may be imported here and in no other layer.
"""

from .device_tools import register_appium_device_tools

__all__ = ["register_appium_device_tools"]
