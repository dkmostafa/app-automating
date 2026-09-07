"""The navigation-memory module's delivery surface: its MCP tools.

Rule 3 §2: ``__init__.py`` re-exports the register function and nothing else.
``src/app_automating/server.py`` imports this one name and calls it.
"""

from .navigation_tools import register_navigation_tools

__all__ = ["register_navigation_tools"]
