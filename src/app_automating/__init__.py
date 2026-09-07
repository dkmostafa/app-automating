"""app-automating: an agentic mobile-testing MCP server.

The distribution is one package so that installing it puts exactly one name into
``site-packages``. Everything the product does lives under :mod:`.modules`, one
subpackage per surface, and :mod:`.server` is the MCP server that registers
their tools.

Nothing is re-exported here on purpose. The server is the entry point
(``app-automating`` on the command line, or ``app_automating.server:run``), and
a module's public surface is its own ``presentation`` package -- importing this
one should not drag in ``fastmcp``, an Appium client or a database driver.
"""

from __future__ import annotations

__all__ = ["__version__"]

#: Kept in step with ``project.version`` in ``pyproject.toml``.
__version__ = "0.1.0"
