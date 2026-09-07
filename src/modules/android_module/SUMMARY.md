# `android_module`

Owns the **Android SDK on the host running this server**: the AVDs defined on
disk, the emulators booted from them, and the system images they are built on.
It is the module you call *first* — nothing else in the product can drive a
device until this one has produced a serial.

It does **not** drive a device. Tapping, typing and reading a screen belong to
`appium_module`, which takes the `device_id` this module hands back.

---

## What it does

Ten operations, one MCP tool each, in the order a caller normally meets them:

| Tool | Service method | What it does |
|---|---|---|
| `android_get_installed_emulators` | `get_installed_emulators` | What AVDs exist on disk, running or not |
| `android_get_available_devices` | `get_available_devices` | What is attached **and** will accept commands |
| `android_get_all_devices` | `get_all_devices` | Same, including offline/unauthorized |
| `android_download_image` | `download_image` | Unpack a system image under the SDK root |
| `android_create_device` | `create_device` | Define a new AVD from an image + hardware profile |
| `android_run_emulator` | `run_emulator` | Boot with a window, wait for boot, return the serial |
| `android_run_emulator_without_window` | `run_emulator_without_window` | The same, headless, for CI |
| `android_stop_emulator` | `stop_emulator` | `adb emu kill`, then wait for the serial to go |
| `android_rename_emulator` | `rename_emulator` | Rename an AVD, moving its payload with it. Keeps everything |
| `android_delete_emulator` | `delete_emulator` | Remove the AVD and its data. Irreversible |

Two handles run through all of it, and they are not interchangeable:

* an **AVD name** (`Pixel_7`) names a device *definition* on disk. Create,
  start and delete take it.
* a **device_id** (`emulator-5554`) names a *running* device. Stop takes it, and
  so does every tool in `appium_module`.

Only a booted emulator has both, and `run_emulator` is what turns the first into
the second.

## How it is built

```
android_module/
├── domain/                                  # zero I/O, importable with no SDK
│   ├── models.py      7 *Request + 9 result/value dataclasses, all frozen+slots
│   ├── errors.py      16 error classes: the vocabulary a use case may catch
│   └── ports.py       DeviceCatalog · EmulatorLifecycle · SystemImageInstaller
├── infrastructure/
│   └── android_emulator_manager/            # one component, split by kind
│       ├── config.py      AndroidSdkConfig + the only os.environ reader
│       ├── models.py      CommandResult — never crosses a port
│       ├── errors.py      19 typed failures, each also a domain error
│       ├── parsing.py     pure: adb/avdmanager text -> models, + failure tables
│       ├── process.py     ToolResolver, CommandRunner, spawn/run/drain/terminate
│       ├── filesystem.py  AvdStore: reads ~/.android/avd, never writes it
│       └── manager.py     AndroidEmulatorManager — orchestration only
├── application/
│   ├── services/android_emulator_service.py AndroidEmulatorService, 9 operations
│   └── di.py                                the composition root
├── presentation/
│   ├── schemas.py     9 pydantic payloads, so every tool publishes a schema
│   ├── rendering.py   pure: domain result -> payload. The only converter
│   ├── errors.py      the REMEDIES table: domain error -> ToolError + next step
│   └── emulator_tools.py  the 10 tools + register_android_emulator_tools
└── tests/unit/test_architecture.py          Rules 0, 2 and 3, enforced
```

18 test files: 14 unit, 4 integration. Integration tests boot a real emulator,
namespace everything they create as `at_it_*`, and skip rather than fail on a
host with no SDK.

## The architecture

Dependencies point one way — `presentation → application → infrastructure →
domain` — and the domain is the centre that depends on nobody.

```
                        ┌─────────────────────────────────────────┐
  src/index.py ────────▶│  presentation/                          │
  register_android_     │    emulator_tools.py  (9 @mcp.tool)     │
  emulator_tools(...)   │    rendering.py · errors.py · schemas.py│
                        └────────────────┬────────────────────────┘
                                         │ calls one method, renders one result
                        ┌────────────────▼────────────────────────┐
                        │  application/                           │
                        │    AndroidEmulatorService  ◀── ports    │
                        │    di.py  ── the ONLY place an adapter  │
                        └────────────────┬───────────  is named ──┘
                        builds & injects │
                        ┌────────────────▼────────────────────────┐
                        │  infrastructure/                        │
                        │    AndroidEmulatorManager               │
                        │    CommandRunner · AvdStore             │
                        └────────────────┬────────────────────────┘
                          implements     │ imports
                        ┌────────────────▼────────────────────────┐
                        │  domain/  models · errors · PORTS       │
                        └─────────────────────────────────────────┘
                                    adb · emulator · avdmanager · sdkmanager
```

**The port inversion.** `domain/ports.py` declares what the application layer
needs; `AndroidEmulatorManager` in `infrastructure/` imports those Protocols and
happens to be shaped like all three. At runtime a service calls the adapter; at
import time the adapter depends on the domain and never the reverse. The ports
are `typing.Protocol`, so the adapter inherits nothing from the inner layer.

**Segregated ports.** `DeviceCatalog` (2 methods), `EmulatorLifecycle` (5) and
`SystemImageInstaller` (1) are separate because a caller that only lists devices
must be structurally incapable of deleting one. One class satisfying all three
is the adapter's business, not the caller's.

**Errors invert too.** `AvdNotFoundError` in the adapter *is-a*
`EmulatorNotFound` in the domain, by inheritance. So the service never names an
SDK concept in an `except`, and no translation shim sits in the middle waiting
to be forgotten. The domain error carries the fact; the adapter subclass adds
the evidence (argv, exit code, log tail). Every one of them reconstructs from
`type(exc)(*exc.args)`, which is what makes them picklable.

**One composition root.** `config.py` holds the environment-reading code;
`di.py` is the only file that *calls* it, names an adapter, or builds a
collaborator. Everything below is handed its settings explicitly and reaches for
no ambient default. `build_android_emulator_service()`
hands the caller the lifetime; `android_emulator_service()` is the scoped form
that kills every emulator it launched on exit — which is what `src/index.py`
uses, so shutting the server down takes its emulators with it.

**Process ownership.** Every child is spawned with `start_new_session=True` so a
timeout kills the whole tree; stdout and stderr are drained into a bounded
`deque` so a chatty emulator cannot fill its pipe and hang; a failed start
leaves nothing behind, and `aclose()` reaps whatever is left.

**Classification is a table, not an `if`-chain.** `CREATE_FAILURES` and
`INSTALL_FAILURES` in `parsing.py` map a tool's stderr onto a typed error. A
newly discovered failure mode adds a row and edits no function. `CommandFailedError`
is the honest fallback, not the default.

**Validation happens before a spawn.** An AVD name, a package id and a serial
are all checked in-process by pure functions in `parsing.py`. That is why a
malformed name costs microseconds instead of a subprocess.
