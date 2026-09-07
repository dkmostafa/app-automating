# Rule 1 — The infrastructure layer

Status: active. Reference implementation:
`src/app_automating/modules/android_module/infrastructure/android_emulator_manager/`
and its tests at
`src/app_automating/modules/android_module/infrastructure/tests/integration/test_android_emulator_manager.py`.

The infrastructure layer is where the process touches things it does not
control: host binaries, devices, sockets, the filesystem, the clock. Everything
above it should be able to reason in plain Python objects. That is the whole
point of the rules below.

---

## 1. One component, one package — split by kind, not by feature

A component is a **package** under `<module>/infrastructure/`, named after the
class it exposes. Inside it, code is grouped by *what kind of thing it is*.
Never one 1400-line module, and never a split along feature lines that puts a
request dataclass in one file and its result in another.

```
infrastructure/android_emulator_manager/
├── __init__.py    re-exports the whole public surface; callers import from here
├── config.py      the injected config dataclass + environment resolution
├── models.py      every *Request, *Result and value object
├── errors.py      the exception base class and every subclass
├── parsing.py     pure functions: tool output and caller input -> models or errors
├── process.py     tool resolution and child-process lifecycle
└── manager.py     the class, and nothing else
```

**The dependency order is fixed and must stay acyclic:**

```
models  ->  errors  ->  parsing  \
config  ->  process              ->  manager
```

* `models.py` imports nothing from the package. It is the bottom.
* `errors.py` imports only `models` (an error carries a `CommandResult`).
* `parsing.py` imports `models` and `errors`. It must stay pure: no subprocess,
  no filesystem, no clock. That purity is what lets validation run *before* a
  process is spawned, and it is why a formatting helper like `display_command`
  lives in `models.py` rather than `parsing.py` — putting it in `parsing.py`
  would make `errors -> parsing -> errors`.
* `process.py` imports `config`, `models` and `errors`, and knows nothing about
  the component's domain. That is deliberate: the next infrastructure component
  reuses it unchanged.
* `manager.py` imports all of them and is imported by none of them.

If a new file is needed, it slots into that order or the split is wrong.

**Rules for the package boundary:**

* Every module carries its own `__all__`, and `__init__.py` re-exports the
  complete public surface. Callers import
  `from ...infrastructure.android_emulator_manager import AndroidEmulatorManager,
  AndroidSdkConfig, ListDevicesRequest` — never from `.manager` or `.models`
  directly.
* Names shared across the package's own files are public within it
  (`parse_avd_list`, `run_command`, `ToolResolver`), not `_`-prefixed. A leading
  underscore inside the package means "private to this one file".
* Add a new component as a sibling package with its own `errors.py` and
  `models.py`. Do not grow a shared layer-wide `errors.py`: two components
  failing for unrelated reasons should not share an exception hierarchy.
* Tests do not mirror the split. See §6.

## 2. Dataclasses in, dataclasses out — always

Every public method on the manager is `async` and has exactly this shape:

```python
async def create_emulator(self, request: CreateEmulatorRequest) -> CreateEmulatorResult:
```

* Exactly one parameter, named `request`, typed as a frozen dataclass.
* Exactly one return, a frozen dataclass. Never a `dict`, `tuple`, `str`, `bool`
  or `None`.
* This holds even for operations that "have no arguments". `list_devices` takes
  a `ListDevicesRequest` because today's zero-field request is tomorrow's
  `resolve_avd_names` flag, and adding a field must never change a call site's
  shape.
* `@dataclass(frozen=True, slots=True)` for every one of them. Optional inputs
  get defaults; use `dataclasses.replace` rather than mutating.
* Put the derived reads callers keep writing by hand onto the result as
  properties or small finders — `ListDevicesResult.by_avd_name(...)`,
  `AvdInfo.loadable`. The layer above should not be re-parsing what this layer
  already knows.

This is enforced, not just documented: see
`test_public_method_takes_one_request_dataclass_and_returns_a_result_dataclass`.
A new public method must be added to `PUBLIC_METHODS` in the test file, and
`test_public_methods_are_the_whole_public_surface` fails until it is.

## 3. Every failure is a typed exception from this module

One base class per component (`AndroidEmulatorError`), and a specific subclass
for every distinguishable failure. Callers must be able to branch on *what went
wrong* without reading strings.

* **Never let a foreign exception escape.** `FileNotFoundError`,
  `PermissionError`, `OSError`, `asyncio.TimeoutError`, a non-zero exit code —
  all of it gets translated at the boundary. Catch narrowly and re-raise with
  `from exc`.
* **Never signal failure with a return value.** No `None`, no `ok=False`, no
  empty result standing in for an error.
* **Carry the evidence on the exception**, as attributes and not only in the
  message: the serial, the AVD name, the argv, the exit code, the tail of the
  child's output. `AndroidToolNotFoundError.searched`,
  `EmulatorStartError.log_tail` and `CommandFailedError.result` exist so a
  caller can act, and so a failing test says why on the first read.
* **Validate before you spawn.** An invalid AVD name, a malformed package id, a
  serial that is not `emulator-<port>` — reject these in-process. Cheap checks
  that need no subprocess belong before the subprocess.
* **Classify, do not dump.** When a tool exits non-zero, map its output to a
  precise error (`UnknownDeviceProfileError`, `AvdAlreadyExistsError`,
  `SystemImageNotInstalledError`). `CommandFailedError` is the honest fallback
  for genuinely unclassified failures, not the default.
* **Cover the whole surface:** not found, already exists, in use, busy/running,
  invalid input, missing dependency, timeout, partial success ("the tool said OK
  but the file is not there"), and cleanup after any of those.

## 4. Configuration is injected, never ambient

The component takes a config dataclass and nothing else:

```python
AndroidEmulatorManager(AndroidSdkConfig.from_environment())
```

`from_environment()` is the *only* place `os.environ` is read, and any remaining
default is resolved once in `__init__`. No operation reads the environment, and
no timeout, path or flag is hardcoded inside a method — put it on the config so a
test can pin it. This is what makes `dataclasses.replace(config,
adb_timeout_seconds=0.001)` a real timeout test instead of a mock.

Where the layer chooses between plausible sources, encode the reason in a
comment. (`AndroidSdkConfig` prefers SDK-local tools over `PATH` because a
distro `/usr/bin/adb` is routinely a different major version from the SDK's
`emulator`, and the two fight over the adb server.)

## 5. Own every process you start

Subprocesses are the layer's real state. Spawn with `start_new_session=True` so
a timeout can kill the whole tree; drain stdout and stderr into a bounded buffer
so a chatty child cannot fill its pipe and hang; kill and reap on *any* exit path
including cancellation. A component that launches long-lived processes exposes
`aclose()` and async context-manager support, and a failed startup leaves nothing
behind.

## 6. Tests live next to the code and run against the real thing

**Placement is Rule 2's job**, and Rule 2 supersedes what this section used to
say. The short version: the component package holds its own `tests/` directory,
and there is one test file per source file inside it —

```
<module>/infrastructure/<component>/
├── config.py  models.py  errors.py  parsing.py  process.py  filesystem.py  manager.py
└── tests/
    ├── unit/          test_models.py  test_errors.py  test_parsing.py  test_config.py
    └── integration/   test_config.py  test_process.py  test_filesystem.py  test_manager.py
```

What stays this rule's business is the *licence* below, and its price.

**Integration tests for this layer run on the real host. No mocking, no faking,
no patching, no stubs, no fake filesystem.** Real `adb`, real AVDs in the real
`~/.android/avd`, a real emulator boot. A test that patches a subprocess is
testing the patch. If a behaviour cannot be exercised for real, do not fake it —
leave it uncovered and say so.

That licence comes with obligations:

* **Namespace everything you create.** Test resources get a fixed prefix
  (`at_it_<random>`), and the destroy helper asserts the prefix before it deletes
  anything. Never touch a resource the test did not create.
* **Clean up in fixture teardown, including after a failure** — and clean up the
  debris of a *partial* failure too, not just the happy-path artifact.
* **Guard the developer's machine.** A session-scoped autouse fixture snapshots
  pre-existing state and fails the run if the suite destroyed something it did
  not own or leaked something it did.
* **Skip, do not fail, on a host that cannot run the test.** No SDK, no system
  image, no KVM → `pytest.skip` with a reason that says how to fix it.
* **Never download and never reach the network.** Discover what the host already
  has (an unpacked system image) and use that.
* **Test the error paths for real too.** A missing binary is a config pointing at
  a real nonexistent path; a timeout is a real 1ms timeout against a real
  process; an unloadable AVD is a real AVD whose `config.ini` was really broken.
* **Assert on typed attributes, not message text** — `excinfo.value.device_id`,
  not `"emulator-5598" in str(exc)`.
* **Budget the slow paths.** Anything that boots a device shares one boot inside
  a single test rather than multiplying boots across tests or making tests depend
  on execution order. Keep the rest of the file in the seconds range.

Mark every module with the marker matching its directory —
`pytestmark = pytest.mark.integration` under `integration/`, and
`pytest.mark.unit` under `unit/`. Unit tests are permitted here only for the
genuinely pure modules (`models`, `errors`, `parsing`, and the pure half of
`config`), and the no-mocking ban applies to them too: they are unit tests
because the code is pure, not because anything was replaced.
