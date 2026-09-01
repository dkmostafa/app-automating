"""The MCP server: the FastMCP instance, its transport, and the modules' tools.

This server speaks STDIO and only STDIO. The transport is pinned in :func:`run`,
and the HTTP entry point is refused outright so that no caller -- including
``fastmcp run --transport http``, which imports this module and drives the
object itself -- can serve it over a socket.

Rule 3 §1: this file defines **no tool of its own**. Each module exposes its own
set of tools from its own presentation layer, and this file imports them and
registers them -- one import and one call per module. It holds no business
logic, no parsing, no rendering and no error handling; if something here starts
looking like a tool body, it belongs in a module.

It is also the process-wide composition root. Each module's ``application/di.py``
is what builds that module's object graph; this file calls it, once, inside the
server's lifespan, and hands the result to the module's register function. It
never touches a module's ``infrastructure/``.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, NoReturn

from fastmcp import FastMCP

from modules.android_module.application.di import android_emulator_service
from modules.android_module.presentation import register_android_emulator_tools


class StdioOnlyMCP(FastMCP):
    """A FastMCP server that refuses every transport except STDIO."""

    async def run_http_async(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise RuntimeError("agentic-testing is a STDIO-only MCP server")

    async def run_async(self, transport: str | None = None, **kwargs: Any) -> None:
        if transport not in (None, "stdio"):
            raise RuntimeError(f"agentic-testing is a STDIO-only MCP server; got {transport!r}")
        await super().run_async(transport="stdio", **kwargs)


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[None]:
    """Build every module's services, register their tools, close them on exit.

    The scoped form of each module's composition root is used deliberately: it
    owns what it built, so shutting the server down takes down the emulators
    this process launched rather than leaving them running with nobody holding
    the handle. A deployment that wants emulators to survive the server swaps
    ``android_emulator_service()`` for ``build_android_emulator_service()`` and
    loses only the cleanup.
    """
    async with android_emulator_service() as android:
        register_android_emulator_tools(server, android)
        yield


mcp = StdioOnlyMCP("agentic-testing", lifespan=lifespan)


def run() -> None:
    """Serve the MCP server over STDIO."""
    mcp.run(transport="stdio")
