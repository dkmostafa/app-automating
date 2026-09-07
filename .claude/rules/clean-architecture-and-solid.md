# Rule 0 — Clean Architecture and SOLID

Status: active. Applies to every line of `src/`, and outranks the layer rules
below it: where Rule 1 tells you *how to build an adapter*, this rule tells you
*whether that adapter is allowed to know what it is about to import*.

`script.py` at the repo root is a scratch file and is exempt.

---

## 1. The four layers, and the direction dependencies point

Every module under `src/app_automating/modules/` is a vertical slice of the product, and every
module has the same four layers. Not three, not five, and not a different set
per module:

```
src/app_automating/modules/<name>_module/
├── domain/           the business logic: entities, value objects, PORTS, and
│                     the use cases that orchestrate them. Zero I/O.
├── infrastructure/   the adapters: host tools, devices, filesystem, clock, DB
├── application/      the services, and the DI that wires them
└── presentation/     the delivery surface: MCP tools, CLI, HTTP
```

**The Dependency Rule:**

```
presentation  ->  application  ->  infrastructure  ->  domain
       \____________________________________________^
```

* **`domain/` is the centre and depends on nobody.** It imports nothing from
  the other three layers and nothing from a third-party I/O library — no
  `fastmcp`, no `appium`, no `sqlalchemy`, no `subprocess`, no `asyncio`.
  Standard-library data types and `dataclasses` only. If a domain file cannot be
  imported on a machine with no Android SDK, it is not a domain file.

  What lives here: entities and value objects, the failure vocabulary, and the
  ports. The *rules* of the business belong here too -- an invariant that holds
  regardless of who is asking. What does not is orchestration; that is a
  service's job, one layer out.

* **`infrastructure/` imports `domain/` in order to implement it.** This is the
  arrow that makes the whole scheme work: at runtime a use case calls an
  adapter, but at compile time the adapter depends on the domain's abstraction
  and never the reverse. It imports nothing from `application/` or
  `presentation/`.

* **`application/` holds the services and the DI that wires them.** It is the
  one layer allowed to import both `domain/` and `infrastructure/`, and the one
  for which naming a concrete adapter is not a violation. Two things live here:

  ```
  application/
  ├── services/            one class per product surface: the orchestration
  │   └── <name>_service.py
  └── di.py                the composition root (§4)
  ```

  **A service is the product's operations in the product's words.** One method
  per thing a user wants to do, named the way they would say it, taking the
  arguments they actually have — an AVD name, a package id — and building the
  domain's `*Request` objects itself. `get_available_devices()` and
  `get_all_devices()` are the same port call with one flag flipped; hiding that
  flag is what makes them two honest operations rather than one leaky one.

  **A service receives its collaborators through its constructor, typed as
  ports.** It never imports an adapter, never names a host tool, never touches a
  subprocess. `di.py` decides what satisfies those ports. A service that reaches
  for `..infrastructure` has stopped being substitutable and the ports have
  become decoration.

  Everything else in this layer is `di.py`. No parsing, no I/O, no business
  rules — those are `domain/` and `infrastructure/` respectively.

* **`presentation/` is the outermost ring.** It translates between the outside
  world and the application: MCP tool definitions, request schemas, response
  rendering. It may import `domain/` (a tool signature naturally speaks
  `StartEmulatorRequest` and catches `EmulatorNotFound`) and `application/` (to
  get its object graph). It must **never** import `infrastructure/` — the moment
  a tool reaches for a concrete adapter, the wiring layer has been bypassed and
  the abstraction is decorative.

Frameworks stay at the edges. `fastmcp` belongs in `presentation/`, `appium` and
`sqlalchemy` in `infrastructure/`, and none of them ever appears in an import
line under `domain/`.

## 2. A port is owned by the layer that calls it, not the one that implements it

This is the rule people get backwards, so it gets its own section.

The abstraction an adapter satisfies is declared **in `domain/`**, named for the
capability the caller needs, and the adapter in `infrastructure/` imports it:

```python
# domain/ports.py                    <- the abstraction lives here
class EmulatorLifecycle(Protocol):
    async def start(self, request: StartEmulator) -> RunningEmulator: ...
    async def stop(self, request: StopEmulator) -> StoppedEmulator: ...

# infrastructure/android_emulator_manager/manager.py
class AndroidEmulatorManager:      # structurally satisfies EmulatorLifecycle
    ...
```

Use `typing.Protocol`, not `ABC`: an adapter should not have to inherit from the
inner layer to satisfy it, and a Protocol keeps the arrow pointing one way even
in the class statement.

Everything that crosses a boundary is owned by the **inner** side of it:

* **DTOs.** The `*Request` and `*Result` dataclasses in a port's signature belong
  to `domain/`. A use case that imports `CreateEmulatorRequest` from
  `infrastructure/` has just inverted the dependency rule, however tidy the
  import looks.
* **Errors.** A use case may only catch domain-level exceptions. An adapter
  translates its own typed failures (Rule 1 §3) into the domain's vocabulary at
  the boundary. `AvdNotFoundError` is an infrastructure detail;
  `DeviceUnavailable` is a domain fact.

This does **not** repeal Rule 1 §1, which gives every component its own
`models.py` and `errors.py`. The two rules divide by *audience*, and the line is
the port signature:

* A type that appears in a port belongs to `domain/`. The adapter imports it.
* A type that never crosses the boundary stays in the component, and stays out
  of `domain/`. `CommandResult`, `AndroidToolNotFoundError` and
  `CommandFailedError` describe a child process's exit status — the domain has
  no business knowing that AVDs are created by a subprocess at all.

So a component keeps its own `models.py`/`errors.py` for its internal
vocabulary, and implements a port written in the domain's. When in doubt, ask
whether a second adapter for a different platform would need the same type: if
yes it is domain, if no it is the component's.

## 3. SOLID, as it applies here

Not a lecture — the five specific failures this codebase is prone to.

**S — one reason to change.** A "reason to change" is a *source of change* (a
tool's output format, a transport, a schema version), not a noun. Rule 1 §1
splits a component by kind for exactly this reason. The test: name what would
have to happen in the world for this file to need editing. Two unrelated answers
means two files. A manager that has grown a private helper doing filesystem
inspection, a second doing polling, and a third doing string-to-error
classification is three collaborators wearing one class's name.

**O — classify with data, not with an `if`-chain.** Mapping a tool's stderr onto
a typed error is a table:

```python
CREATE_FAILURES = ((("already exists",), AvdAlreadyExistsError), ...)
```

A new failure mode should add a row, not edit a method. This is the form OCP
actually takes in this codebase; do not invent inheritance hierarchies to get it.

**L — a subtype is usable wherever its base is.** For the exception hierarchies
Rule 1 §3 mandates, that has one hard, testable consequence:
`type(exc)(*exc.args)` must reconstruct the exception. An error that stores its
evidence on `self` but passes only a formatted string to `super().__init__` is
not copyable, not picklable, and will not survive crossing a process boundary.
Pass the real arguments to `super().__init__` and let `__str__` do the
formatting.

**I — ports are narrow and named after a need.** Prefer `DeviceCatalog`,
`EmulatorLifecycle` and `SystemImageInstaller` over one `AndroidPort` with
twelve methods. A use case that only lists devices must not be able to delete
one. A component may still *implement* several ports with one class — that is a
fine implementation detail, and the opposite of forcing every caller to depend
on all of it.

**D — depend on abstractions, inject collaborators.** Rule 1 §4 already requires
this for configuration; it applies to behaviour too. A class that reaches for a
module-level function from another layer has hardcoded that layer. Take the
collaborator in `__init__`, typed as a port, defaulted to nothing.

Note what DIP is *not* for here: Rule 1 §6 forbids mocks, and that stands. These
seams exist so the next adapter can reuse the last one and so the layers stay
separable — never so a test can patch them.

## 4. Composition happens in exactly one place

Wiring — "this service gets that adapter, built from this config" — lives in
`application/di.py`, and in exactly one file. Nowhere else may a class construct
its own collaborator: not a service, not an adapter in `infrastructure/`, not a
tool in `presentation/`.

`di.py` is where an adapter is bound to the ports it satisfies and the ports are
injected into a service:

```python
def build_android_emulator_service(config=None) -> AndroidEmulatorService:
    catalog, lifecycle, installer = android_emulator_ports(build_android_emulator_manager(config))
    return AndroidEmulatorService(devices=catalog, emulators=lifecycle, images=installer)
```

Where a built object owns processes or connections, `di.py` also offers a scoped
form that closes it — the composition root is the only place that knows enough
to decide a lifetime.

The composition root is also the only place in a module allowed to resolve
ambient state — to read the environment, look at the clock, or pick a default
path. Everything it builds is handed its settings explicitly.

In particular, a constructor argument that defaults to a live object is not
injection, it is a hidden dependency with a polite signature:

```python
def __init__(self, config: AndroidSdkConfig | None = None) -> None:
    self._config = config or AndroidSdkConfig.from_environment()   # NO
```

Take it required, and let the composition root call `from_environment()` once.

## 5. Enforce it, do not just document it

Rule 1 §2 is enforced by a test rather than a paragraph, and so is this rule. A
module's test package carries an architecture test that walks the AST of every
file under the module and asserts the import direction:

* every module has all four layers, and no module invents a fifth;
* nothing under `application/services/` imports `infrastructure`;
* nothing under `domain/` imports `infrastructure`, `application`,
  `presentation`, a known framework package, or an I/O module from the standard
  library;
* nothing under `infrastructure/` imports `application` or `presentation`;
* nothing under `presentation/` imports `infrastructure`;
* only the composition root resolves ambient state;
* every adapter named in the composition root satisfies the port it is injected
  as (`isinstance` against a `runtime_checkable` Protocol, or a typed
  assignment `_: PortName = AdapterClass(...)` the type checker will reject).

Mark it `pytest.mark.unit`: it reads source, touches no device, and must pass on
a host with no Android SDK.
