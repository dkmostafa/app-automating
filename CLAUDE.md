# app-automating

Agentic mobile-testing MCP server. Capabilities live under
`src/app_automating/modules/`, one package per surface (`android_module`, ...),
each split into layers. `src/app_automating/` is the distribution package: it is
the single name the wheel puts into `site-packages`, and `server.py` sits beside
`modules/` inside it.

## Layout

Every module has the same four layers -- no module has three, and none invents
a fifth:

```
src/app_automating/modules/<name>_module/
├── domain/                         # BUSINESS LOGIC. no I/O, no frameworks.
│   ├── models.py                   # entities and the *Request / *Result DTOs
│   ├── errors.py                   # the failure vocabulary use cases catch
│   └── ports.py                    # the Protocols adapters are measured against
├── infrastructure/                 # ADAPTERS: host tools, devices, files, clock
│   └── <component>/                # one package per component, split by kind
│       ├── __init__.py             # re-exports the adapter's public surface
│       ├── config.py               # injected config dataclass
│       ├── models.py               # value objects that never cross a port
│       ├── errors.py               # typed failures, each one a domain error too
│       ├── parsing.py              # pure text -> models or errors
│       ├── process.py              # tool resolution, child-process lifecycle
│       ├── filesystem.py           # the directories the tools read and write
│       ├── manager.py              # the class
│       └── tests/{unit,integration}/test_<source>.py
├── application/                    # SERVICES + WIRING
│   ├── services/<name>_service.py  # the product's operations, port-injected
│   ├── di.py                       # the composition root
│   └── tests/unit/                 # mocked ports, always
├── presentation/                   # DELIVERY: the module's own MCP tools
│   ├── <surface>_tools.py          # tools + register_<module>_tools(mcp, service)
│   ├── schemas.py                  # the pydantic payloads the tools return
│   ├── rendering.py                # pure: domain results -> JSON-able payloads
│   ├── errors.py                   # pure: domain errors -> ToolError, table-driven
│   └── tests/unit/                 # mocked application, always
└── tests/unit/test_architecture.py # Rules 0 and 2, enforced
```

Every layer carries its own tests the same way: `domain/tests/unit/`,
`application/tests/unit/`, and so on.

**Tests live in a `tests/` directory beside the code they cover, one test file
per source file**: `x/service.py` is covered by `x/tests/unit/test_service.py`
or `x/tests/integration/test_service.py`. Never a top-level `tests/` tree. The
module-wide architecture test is the one exception — it covers no single source
file, so it sits at the module root and runs without an SDK.

Mocking is layer-specific: **forbidden** in `infrastructure` (it tests the real
host) and **required** in `application` (it tests wiring). See Rule 2.

## Principles

The project follows **Clean Architecture** and the **SOLID** principles, and
that is not decoration. Each module is a vertical slice split into four layers
with a fixed direction of dependency:

| Layer | Holds | May import |
|---|---|---|
| `domain` | entities, value objects, errors, ports | nothing else in the module |
| `infrastructure` | adapters for host tools, devices, the filesystem | `domain` |
| `application` | services (orchestration) + `di.py` (wiring) | `domain`, `infrastructure` |
| `presentation` | the delivery surface: MCP tools | `domain`, `application` |

`application` is the only layer allowed to name a concrete adapter, and only in
`di.py` -- a **service** is written against the domain's ports and never imports
`infrastructure`. `presentation` must never reach past the application layer
into `infrastructure` either. Each module exposes its own MCP tools from its
presentation layer; `src/app_automating/server.py` is the server -- it owns the single STDIO
`FastMCP` instance, builds each module's services through that module's `di.py`,
registers the module's tools, and defines no tool of its own (Rule 3). Every port is declared by the layer that *calls* it rather than
the one that implements it, and collaborators are injected at a single
composition root. Rule 0 is binding on every line under `src/`, outranks the
layer rules, and is enforced by an architecture test rather than by review. Read
it before adding a layer, a component, or a dependency between two of them.

## Rules

Read Rule 0 before writing any code, then the rule for the layer you are in.

- [Rule 0 — Clean Architecture and SOLID](.claude/rules/clean-architecture-and-solid.md)
- [Rule 1 — The infrastructure layer](.claude/rules/infrastructure-layer.md)
- [Rule 2 — Where tests live, and what they may fake](.claude/rules/test-placement.md)
- [Rule 3 — The presentation layer and the MCP surface](.claude/rules/presentation-layer.md)

## Commands

**Never run the tests unless you were explicitly told to.** Write them, change
them, reason about them -- but leave running them to the developer. `uv run
pytest` boots a real emulator on this host, takes over the display, and costs a
minute of someone's machine; that is the developer's call to make, not the
agent's. "The change looks done" is not permission. Neither is a failure you are
curious about. Ask, or say which command you would run and stop there.

This covers the whole suite and any subset of it -- a single file, a single
`-k` selection, `-m unit`. `ruff check` and `ruff format` are not tests and may
be run freely.

```bash
uv run pytest                       # everything, including a real emulator boot (~1 min)
uv run pytest -m unit               # the architecture tests: no SDK, no device, <1s
uv run pytest -m integration        # the real toolchain: adb, avdmanager, a real boot
uv run ruff check src && uv run ruff format src
```

Integration tests drive the real Android SDK on this host. They create AVDs
prefixed `at_it_`, clean up after themselves, and fail the run if they touch an
AVD they did not create. They never download anything and skip on a host with no
SDK.
