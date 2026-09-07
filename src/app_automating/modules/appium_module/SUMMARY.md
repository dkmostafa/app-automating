# `appium_module`

Owns **driving an Android device** — emulated or physical — through Appium:
opening a session on a serial, reading what is on screen, and tapping, swiping,
scrolling and typing on it.

It does **not** list or boot devices. A `device_id` comes from `android_module`
(or from a physical device already attached), and this module takes it as given.
The two modules meet at that one string and share nothing else.

---

## What it does

Thirteen operations, one MCP tool each, in the order a caller normally meets
them:

| Tool | Service method | What it does |
|---|---|---|
| `appium_check_environment` | `check_environment` | What this host can automate. Never raises for a missing tool |
| `appium_install_driver` | `install_driver` | Install an Appium driver. The one call that changes the host |
| `appium_start_session` | `start_session` | Open a session on a serial, launching the server if needed |
| `appium_get_open_sessions` | `get_open_sessions` | What this process has open, optionally verified alive |
| `appium_take_screenshot` | `take_screenshot` | PNG to a file, or inline as base64 |
| `appium_get_page_source` | `get_page_source` | The UI hierarchy as XML, truncatable |
| `appium_tap_element` | `tap_element` | Find an element by locator and tap it |
| `appium_tap` | `tap` | Tap raw coordinates |
| `appium_swipe` | `swipe` | Drag point to point |
| `appium_scroll` | `scroll` | Swipe a named direction across the middle of the screen |
| `appium_type_text` | `type_text` | Type into an element, or into whatever has focus |
| `appium_press_key` | `press_key` | `back`, `home`, `enter` … by name, not keycode |
| `appium_end_session` | `end_session` | Quit the session and release the device |

Two handles run through it:

* a **device_id** — an adb serial (`emulator-5554`, or hardware like
  `R58M12ABCDE`). Only `start_session` takes one.
* a **session_id** — a live session bound to one device and one app. Every other
  tool takes one, and it exists only between `start_session` and `end_session`.

Prefer `appium_tap_element` over `appium_tap`: a coordinate is correct for one
screen size and one layout, an element is correct for all of them.

## How it is built

```
appium_module/
├── domain/                                  # zero I/O; no appium, no node needed
│   ├── models.py      13 *Request + 11 result/value dataclasses, all frozen+slots
│   │                  plus LOCATOR_STRATEGIES and SCROLL_DIRECTIONS
│   ├── errors.py      20 error classes, in four groups (see below)
│   └── ports.py       AppiumEnvironment · SessionLifecycle
│                      ScreenInspector · DeviceInteraction
├── infrastructure/
│   └── appium_device_manager/               # one component, split by kind
│       ├── config.py     AppiumConfig + the only os.environ reader (AT_APPIUM_*)
│       ├── models.py     CommandResult, ServerStatus — never cross a port
│       ├── errors.py     21 typed failures, each also a domain error
│       ├── parsing.py    pure: validation, locator resolution, keycodes, tables
│       ├── process.py    ToolResolver, CommandRunner, spawn/run/drain/terminate
│       ├── server.py     AppiumServer: probe /status, launch, own, kill
│       ├── sessions.py   SessionRegistry: live drivers + the thread boundary
│       └── manager.py    AppiumDeviceManager — orchestration only
├── application/
│   ├── services/appium_device_service.py    AppiumDeviceService, 13 operations
│   └── di.py                                the composition root
├── presentation/
│   ├── schemas.py     the pydantic payloads, so every tool publishes a schema
│   ├── rendering.py   pure: domain result -> payload. The only converter
│   ├── errors.py      the REMEDIES table: domain error -> ToolError + next step
│   └── device_tools.py    the 13 tools + register_appium_device_tools
└── tests/unit/test_architecture.py          Rules 0, 2 and 3, enforced
```

19 test files: 14 unit, 5 integration. Integration tests drive a real Appium
server against a real device and skip rather than fail when the host has none.

## The architecture

Same four layers and the same one-way dependency rule as every module:

```
                        ┌─────────────────────────────────────────┐
  server.py ───────────▶│  presentation/                          │
  register_appium_      │    device_tools.py  (13 @mcp.tool)      │
  device_tools(...)     │    rendering.py · errors.py · schemas.py│
                        └────────────────┬────────────────────────┘
                                         │ calls one method, renders one result
                        ┌────────────────▼────────────────────────┐
                        │  application/                           │
                        │    AppiumDeviceService  ◀── 4 ports     │
                        │    di.py  ── the ONLY place an adapter  │
                        └────────────────┬───────────  is named ──┘
                        builds & injects │
                        ┌────────────────▼────────────────────────┐
                        │  infrastructure/                        │
                        │    AppiumDeviceManager                  │
                        │    CommandRunner · AppiumServer         │
                        │                  · SessionRegistry      │
                        └────────────────┬────────────────────────┘
                          implements     │ imports
                        ┌────────────────▼────────────────────────┐
                        │  domain/  models · errors · PORTS       │
                        └─────────────────────────────────────────┘
                             appium CLI · the server · UiAutomator2 · the device
```

**Four ports, and the split earns its keep.** `AppiumEnvironment` (is this host
usable), `SessionLifecycle` (the expensive stateful thing), `ScreenInspector`
(reads, changes nothing) and `DeviceInteraction` (changes everything). A use case
that only wants to read a screen takes `ScreenInspector` and is then
structurally incapable of tapping. `AppiumDeviceManager` satisfies all four —
that is the adapter's business, and no caller sees it.

**Three collaborators, three kinds of state.** The manager owns no I/O itself:

* `CommandRunner` owns the process table — resolving `appium`/`node`/`npm` on
  `PATH`, spawning in a new session, draining pipes, killing the tree.
* `AppiumServer` owns the server process. It **probes before it launches**, so a
  developer with `appium` already running in a terminal does not get a second
  one fighting for the port, and it kills only a server *this* process started.
  The `/status` probe is stdlib `urllib` on a worker thread rather than a new
  HTTP dependency.
* `SessionRegistry` owns the live sessions **and the thread boundary**. This is
  the load-bearing one: `appium-python-client` is synchronous and every call
  blocks, so every driver call goes through `SessionRegistry.call`, which hands
  the work to `asyncio.to_thread` and translates every `WebDriverException` on
  the way out. Nothing outside that file touches a driver object.

**Errors invert, and group by what the caller should do.** Each adapter error
inherits both `AppiumComponentError` and a domain error, so
`SessionCreateError` *is-a* `SessionStartFailed` and the service never names
Appium in an `except`. The four domain groups are exactly the four remedies:
the host is not provisioned (install something), the server is not answering
(fix the config), the session is gone (start a new one), or the input was wrong
(retry with different arguments).

**Validate before you spawn.** `start_session` checks the serial and the APK
path, and confirms the driver is installed, *before* the server is launched —
the difference between a one-millisecond error and a sixty-second one. All of it
is pure code in `parsing.py`, which is why it is unit-tested on a host with no
Appium at all.

**Teardown order is deliberate.** `aclose()` quits every session, *then* takes
down a managed server: a session outliving its server is a hang, and killing the
server first would strand every teardown on a dead socket. `src/app_automating/server.py` nests
`appium` inside `android` for the same reason — sessions are torn down while the
emulators they were driving are still up.

**One composition root.** `config.py` holds the environment-reading code
(`AT_APPIUM_*`); `di.py` is the only file that *calls* it, names the adapter, or
builds a collaborator. The runner is built
once and shared with the server, so a version probe and a launch cannot disagree
about which `appium` this host has.
