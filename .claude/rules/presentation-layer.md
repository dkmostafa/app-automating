# Rule 3 — The presentation layer and the MCP surface

Status: active. Applies to every module's `presentation/` package and to
`src/app_automating/server.py`. Enforced by `<module>/tests/unit/test_architecture.py`, not by
review.

Rule 0 says `presentation/` is the outermost ring and may import `domain/` and
`application/` but never `infrastructure/`. This rule says what goes in that
ring: **each module exposes its own set of MCP tools from its own presentation
layer, and `src/app_automating/server.py` imports and registers them.** A module that has tools
owns them end to end — their names, their signatures, their descriptions, their
error translation. `server.py` owns only the server.

---

## 1. `src/app_automating/server.py` is the server, and nothing else

```python
# src/app_automating/server.py
mcp = StdioOnlyMCP("app-automating")

# one import and one call per module, in alphabetical order
register_android_tools(mcp, android_service)
```

* `server.py` holds the single `FastMCP` instance, pins STDIO, and registers each
  module's tools. **It defines no tool of its own** — a `@mcp.tool` written in
  `server.py` is a module's presentation layer that never got written.
* It contains no business logic, no parsing, no rendering, no error handling.
* Adding a module to the product is **one import plus one `register_*` call**.
  If it takes more than that, the module's presentation layer is doing too
  little.
* `server.py` is the process-wide composition root: it calls each module's
  `application/di.py` to build the services, inside the server's lifespan, so
  shutdown can close adapters that own processes (`await manager.aclose()`).
  It never imports a module's `infrastructure/`, and never constructs an adapter
  itself — `di.py` does that, per Rule 0 §4.

## 2. One module, one `register_<module>_tools`, split by kind

`presentation/` is a package split the way Rule 1 §1 splits an infrastructure
component: by *what kind of thing a file is*, not by feature.

```
<module>/presentation/
├── __init__.py          re-exports register_<module>_tools and nothing else
├── <surface>_tools.py   the tool functions + the register function
├── schemas.py           the pydantic payloads the tools return
├── rendering.py         pure: domain result objects -> JSON-able payloads
├── errors.py            pure: the domain-error -> ToolError translation table
└── tests/unit/test_<source>.py
```

The dependency order is fixed and acyclic: `schemas -> rendering -> errors ->
<surface>_tools`. `rendering.py` and `errors.py` stay pure — no `fastmcp`
imports beyond the `ToolError` type, no I/O, no awaits.

The register function has exactly this shape:

```python
def register_android_tools(mcp: FastMCP, service: AndroidEmulatorService) -> None:
```

* It takes the server and the already-built service(s), **typed as application
  services**, and returns `None`.
* It **never builds its collaborators**. No `build_android_emulator_service()`
  inside `presentation/`, no reading the environment, no `AndroidSdkConfig`.
  Composition happens in `di.py`, called by `server.py` (Rule 0 §4).
* It registers tools and does nothing else — no side effects, no logging setup,
  no global state. Calling it twice against two servers must be safe, which is
  what lets a test register into a throwaway `FastMCP`.
* One module may have several register functions when it has several surfaces
  (`register_android_emulator_tools`, `register_android_app_tools`); each is
  exported from `__init__.py` and called separately from `server.py`.

## 3. Tool names are module-prefixed, always

Every tool name begins with its module: `android_run_emulator`,
`android_list_devices`, `android_create_device`. No exceptions and no bare
verbs — the second module that wants `list_devices` must not collide with the
first, and a client reading a flat tool list should see the modules grouped.

The rest of the name is the operation in the product's words, matching the
service method it calls. If the tool name and the service method disagree about
what the operation is called, one of them is wrong.

## 4. The tool signature: scalars in, rendered payloads out

* Tools are `async def` and take **flat, JSON-schema-friendly parameters** —
  `str`, `int`, `float`, `bool`, `Literal`, lists of those, or a `schemas.py`
  model. Never a domain `*Request` dataclass as a parameter: the client is an
  LLM filling in a JSON schema, not Python code.
* The tool body does three things and no more: build the call from its
  arguments, `await` one service method, and hand the result to `rendering.py`.
  Branching, retries, polling and multi-step orchestration belong in the
  service, not here.
* The return is a JSON-able payload built by `rendering.py` — a dict or a
  `schemas.py` model. Never a raw domain object, and never a bare string that
  the caller has to parse back.
* Presentation never touches an adapter, a subprocess, a path or a host tool.

## 5. Every tool carries an extensive description, in a fixed template

**The docstring is the tool's entire API.** It is what FastMCP ships to the
client, and it is the only thing the model calling it will ever see — it cannot
read the signature's intent, the service, or this repository. A thin docstring
is a broken tool.

Every tool docstring has these sections, in this order, each non-empty:

```python
@mcp.tool(name="android_run_emulator")
async def run_emulator(avd_name: str, cold_boot: bool = False) -> dict:
    """Boot an existing Android AVD and wait until it is ready for input.

    What it does
    ------------
    Starts the named AVD as a headed emulator on this host and blocks until the
    device reports it has finished booting, then returns its serial. The
    emulator outlives this call and keeps running until it is stopped.

    When to use it
    --------------
    Use this before any tool that drives a device: installing an app, taking a
    screenshot, running a test. Call `android_list_devices` first if you do not
    already know which AVD names exist on this host.

    When not to use it
    ------------------
    Do not call this to check whether an emulator is already running — that is
    `android_list_devices`. Do not call it twice for the same AVD; a second boot
    of a running AVD fails.

    Arguments
    ---------
    avd_name:
        The exact name of an AVD that already exists on this host, as reported
        by `android_list_devices`. Case-sensitive. Letters, digits, `.`, `_`
        and `-` only. This tool never creates an AVD -- use
        `android_create_device` for that.
    cold_boot:
        `True` discards the saved snapshot and boots from scratch: slower
        (roughly 30-60s longer) but recovers a device left in a bad state.
        Defaults to `False`, which resumes from the snapshot when there is one.

    Returns
    -------
    `{"device_id": "emulator-5554", "avd_name": "Pixel_7", "booted": true}` --
    `device_id` is the adb serial every other Android tool takes.

    Errors
    ------
    `EmulatorNotFound`: no AVD by that name. List the devices and retry with a
    name from that list; do not guess a close spelling.
    `EmulatorAlreadyRunning`: it is already booted. Use the serial from
    `android_list_devices` rather than calling this again.
    `BackendUnavailable`: the host has no usable Android SDK. Nothing the caller
    can retry -- report it to the user.

    Example
    -------
    `android_run_emulator(avd_name="Pixel_7", cold_boot=False)`
    -> `{"device_id": "emulator-5554", "avd_name": "Pixel_7", "booted": true}`
    """
```

What the sections must actually contain:

* **Summary line** — imperative, one line, says what calling it *does*, not what
  it "is". It is what shows up in a tool list.
* **What it does** — the real mechanism, including anything that outlives the
  call (started processes, written files, changed device state) and roughly how
  long it takes.
* **When to use it** / **When not to use it** — the ordering constraints and the
  neighbouring tools. Name the sibling tool the caller should have used instead.
  This is the section that stops a model from calling the wrong tool.
* **Arguments** — every parameter of the signature, by name, with its format,
  units, valid values, default, and where a legal value comes from. No parameter
  may be omitted.
* **Returns** — the exact shape, with a literal example payload, and what each
  field is for.
* **Errors** — every failure the caller can distinguish, by its domain error
  name, each with **what the caller should do about it**. An error list without
  remedies is half a section.
* **Example** — at least one concrete call with its result.

There is no upper bound on length. Prefer the version that a model with no
context could call correctly on the first attempt.

The module-level docstring of `<surface>_tools.py` carries the same weight for
the surface as a whole: what this group of tools is for, the order they are
normally called in, and what they share (a serial, a running emulator, a host
requirement).

## 6. No exception escapes untranslated

* A tool catches the module's **domain** errors (Rule 0 §2) and raises
  `ToolError` with a message the caller can act on. It never catches an
  infrastructure error — it cannot even import that vocabulary.
* Translation is a **table** in `errors.py`, not an `if`-chain (Rule 0 §3, OCP):
  a new domain error adds a row.
* The message names the failure, the evidence carried on the exception (the AVD
  name, the serial, the tail of the tool output), and the next step. Never a
  bare `str(exc)`, and never a stack trace.
* An unexpected exception is a bug, not a tool result: let it surface as an
  error rather than returning a payload that claims success.

## 7. Tests: mock the application, never the infrastructure

Rule 2 §1 places them: `presentation/tests/unit/test_<source>.py`, one file per
source file, marked `pytestmark = pytest.mark.unit`. There is no
`presentation/tests/integration/`.

The service is a mock — that is the point of this layer's tests (Rule 2 §3).
What they assert:

* `register_<module>_tools` registers exactly the expected set of tool names on
  a throwaway `FastMCP`, and every name carries the module prefix;
* each tool calls the service method it claims to, with the arguments the caller
  gave, and awaits it exactly once;
* `rendering.py` turns a domain result into the payload the docstring's
  **Returns** section shows — the example in the docstring and the assertion in
  the test are the same payload;
* every domain error in the module maps to a `ToolError` with a message that
  names the evidence.

## 8. What the enforcement actually checks

Added to `<module>/tests/unit/test_architecture.py`, which reads source and
needs no SDK:

* the module's `presentation/__init__.py` exports at least one
  `register_*_tools`, and every register function's signature is
  `(mcp, <service>, ...) -> None`;
* every tool registered by the module is prefixed with the module name;
* nothing under `presentation/` imports `infrastructure`, names an adapter, or
  reads `os.environ`;
* every tool function has a docstring containing all eight mandatory sections,
  each with a non-empty body;
* the **Arguments** section names every parameter in the tool's
  `inspect.signature`, and names no parameter that is not in it;
* `src/app_automating/server.py` calls a register function for every module that exports one —
  a module whose tools were never registered fails the suite.
