# app-automating

An MCP server that drives Android emulators and real devices — and remembers how
it got there.

It gives a model three things: control of the Android SDK (create, boot, stop and
delete emulators), control of the screen through Appium (tap, type, swipe, scroll,
screenshot, read the view hierarchy), and a navigation memory that records every
route taken so the next run can look up how it reached a screen instead of
rediscovering it.

[![CI](https://github.com/dkmostafa/app-automating/actions/workflows/ci.yml/badge.svg)](https://github.com/dkmostafa/app-automating/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)

---

## What you get

28 tools across three modules. Every tool name is prefixed with its module, so a
flat tool list stays grouped.

| Module | Tools | What it does |
|---|---|---|
| `android_` | 10 | Lists devices, creates and renames AVDs, downloads system images, boots emulators headed or headless, stops and deletes them |
| `appium_` | 13 | Starts and ends sessions, taps by coordinate or locator, types, swipes, scrolls, presses keys, screenshots, dumps the view hierarchy, checks the host, installs drivers |
| `navigation_memory_` | 5 | Answers "where am I", "how do I get from here to there", searches remembered screens, replays a run, forgets one |

The navigation memory is not a separate step. When it is enabled — it is by
default — it wraps the Appium session and gesture ports at the composition root,
so **every** interaction records itself with no extra tool call. The cost is one
extra page-source capture per gesture; `AT_NAVIGATION_MEMORY_RECORD_INTERACTIONS=false`
turns it off and gives that latency back, leaving the read tools working against
whatever was learned before.

## Requirements

The server itself is pure Python, but it is a remote control for other people's
tools. What you need depends on which tools you intend to call.

| For | You need | Checked by |
|---|---|---|
| Anything | Python 3.12+ | — |
| `android_*` | The Android SDK, with `adb`, `emulator`, `avdmanager` and `sdkmanager`. Set `ANDROID_SDK_ROOT` or `ANDROID_HOME` | `android_get_all_devices` |
| Booting an emulator | Hardware acceleration (KVM on Linux, HAXM/Hypervisor.Framework on macOS) and a system image already downloaded | `android_get_installed_emulators` |
| `appium_*` | Node.js, the `appium` CLI, and the `uiautomator2` driver | `appium_check_environment` |
| `navigation_memory_*` | Nothing. It is a local SQLite file | — |

Call `appium_check_environment` first if you are unsure — it reports exactly what
is missing and how to install it, rather than failing on the first gesture.

Nothing here reaches the network except `android_download_image`, which shells out
to `sdkmanager`, and `appium_install_driver`, which shells out to `npm`.

## Install

> [!NOTE]
> **Not on PyPI yet.** Until the first release is tagged, install from the
> repository — the two commands below it will start working the moment `v0.1.0`
> is published.

### From the repository

```bash
uvx --from git+https://github.com/dkmostafa/app-automating app-automating
```

### As a tool, once released

```bash
uv tool install app-automating
```

Or run it without installing anything permanent:

```bash
uvx app-automating
```

### From a clone, for development

```bash
git clone https://github.com/dkmostafa/app-automating
cd app-automating
uv sync
uv run app-automating
```

## Connect it to an MCP client

The server speaks **STDIO and only STDIO**. It refuses every other transport at
the source, so it cannot be accidentally exposed on a socket — the tools it
fronts drive every attached device with no authentication of any kind.

### Claude Code

```bash
claude mcp add app-automating -- uvx app-automating
```

Before the first PyPI release, point it at the repository instead:

```bash
claude mcp add app-automating -- uvx --from git+https://github.com/dkmostafa/app-automating app-automating
```

### Claude Desktop, Cursor, and other clients that read a JSON config

Add this to the client's MCP configuration file:

```json
{
  "mcpServers": {
    "app-automating": {
      "command": "uvx",
      "args": ["app-automating"],
      "env": {
        "ANDROID_SDK_ROOT": "/home/you/Android/Sdk"
      }
    }
  }
}
```

From a clone instead of an install:

```json
{
  "mcpServers": {
    "app-automating": {
      "command": "uv",
      "args": ["--directory", "/path/to/app-automating", "run", "app-automating"],
      "env": {
        "ANDROID_SDK_ROOT": "/home/you/Android/Sdk"
      }
    }
  }
}
```

`ANDROID_SDK_ROOT` is worth setting explicitly. An MCP client launches the server
without your shell profile, so a variable that is set in `.bashrc` will not be
there — which shows up as "no Android SDK" on a machine that plainly has one.

## Configuration

Every setting is an environment variable prefixed `AT_`, and every one has a
working default. **[`.env.example`](.env.example) documents all of them**, with
the reasoning for each default; copy it to `.env` and edit if you want to pin
something.

The settings you are most likely to touch:

| Variable | Default | What it is for |
|---|---|---|
| `ANDROID_SDK_ROOT` / `ANDROID_HOME` | auto-detected | Where the Android SDK lives |
| `AT_EMULATOR_LAUNCH_ARGS` | empty | Flags added to every emulator launch, e.g. `-gpu swiftshader_indirect` on a machine with no usable GPU |
| `AT_EMULATOR_BOOT_TIMEOUT_SECONDS` | `300` | How long to wait for a booting AVD |
| `AT_APPIUM_MANAGE_SERVER` | `true` | Launch an Appium server when nothing is listening, and kill it on shutdown |
| `AT_APPIUM_SERVER_PORT` | `4723` | Where the Appium server listens, on loopback only |
| `AT_NAVIGATION_MEMORY_DATABASE_PATH` | `$XDG_DATA_HOME/app-automating/navigation_memory.db` | The navigation memory. `:memory:` for one that dies with the process |
| `AT_NAVIGATION_MEMORY_RECORD_INTERACTIONS` | `true` | Whether every gesture records itself |

Configuration is read in exactly one place per module — the composition root — and
handed down explicitly. Nothing deeper in the code reads the environment.

## How it is built

Three modules under `src/app_automating/modules/`, one per surface, each a
vertical slice split into the same four layers with a fixed direction of
dependency:

```
presentation  ->  application  ->  infrastructure  ->  domain
       \____________________________________________^
```

- **`domain/`** — entities, the failure vocabulary, and the ports. No I/O, no
  frameworks, importable on a machine with no Android SDK.
- **`infrastructure/`** — the adapters: host binaries, devices, the filesystem,
  the database.
- **`application/`** — the services, and the `di.py` that wires them. The only
  layer allowed to name a concrete adapter.
- **`presentation/`** — the module's own MCP tools, their schemas, their
  rendering and their error translation.

`src/app_automating/server.py` is the server and nothing else: it owns the single
STDIO `FastMCP` instance, builds each module's services through that module's
`di.py`, registers the module's tools, and defines no tool of its own.

This is not decoration — it is enforced. Each module carries a
`tests/unit/test_architecture.py` that walks the AST of every file in it and
fails the build on a layer violation, a missing tool docstring section, an
unregistered module, or a mock in the wrong place. The rules themselves are
written down in [`CLAUDE.md`](CLAUDE.md) and [`.claude/rules/`](.claude/rules/).

## Tests

```bash
uv run pytest -m unit          # pure code: no SDK, no device, <1s
uv run pytest -m integration   # the real toolchain: adb, avdmanager, Appium, a real boot
uv run pytest                  # everything
```

Tests live beside the code they cover, one test file per source file — there is
no top-level `tests/` tree.

> [!WARNING]
> **The integration suite drives this host for real.** It creates AVDs, boots
> emulators, starts Appium servers and writes real files. It namespaces
> everything it creates, cleans up after itself even after a failure, and fails
> the run if it touched something it did not own — but it will take over your
> display and cost a minute of your machine. It never downloads anything, and it
> skips with a reason rather than failing on a host that cannot run it.

CI runs the unit and architecture tests only. There is no Android SDK and no
device on a GitHub runner, so the integration tests skip there by design — they
are yours to run.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The short version: the four-layer rules
are enforced by tests rather than by review, so read
[`.claude/rules/`](.claude/rules/) before adding a layer, a component, or a
dependency between two of them.

## Security

An Appium server drives every attached device with no authentication of any
kind, which is why this server binds it to loopback and speaks STDIO only. To
report a vulnerability, see [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE).
