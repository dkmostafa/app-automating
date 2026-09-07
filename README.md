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

## Prerequisites

The server itself is pure Python, but it is a remote control for other people's
tools. What you need depends on which tools you intend to call — you can install
just the Android half, or just the Appium half, and the rest still works.

| For | You need | Checked by |
|---|---|---|
| Anything | Python 3.12+ | — |
| `android_*` | The Android SDK, with `adb`, `emulator`, `avdmanager` and `sdkmanager`. Set `ANDROID_SDK_ROOT` | `android_get_all_devices` |
| Booting an emulator | Hardware acceleration (KVM on Linux, Hypervisor.Framework on macOS) and a downloaded system image | `android_get_installed_emulators` |
| `appium_*` | Node.js 18+, the `appium` CLI, and the `uiautomator2` driver | `appium_check_environment` |
| `navigation_memory_*` | Nothing. It is a local SQLite file | — |

Call `appium_check_environment` once everything is in place — it reports exactly
what is still missing and how to install it, rather than failing on your first
gesture.

### Supported platforms

| Platform | Status |
|---|---|
| **Linux** | Supported and tested |
| **Windows** | **Not supported natively** — use WSL2, below |
| **macOS** | Should work; POSIX throughout, but not tested by the maintainers |

Native Windows is not a packaging gap, it is a real one: the process handling in
`infrastructure/*/process.py` uses `os.killpg`, `os.getpgid` and
`signal.SIGKILL`, none of which exist on Windows, so every timeout and shutdown
path would fail there. WSL2 is the supported route, and it is a good one — see
[Windows (WSL2)](#windows-wsl2).

---

## Installing the prerequisites

### Ubuntu / Debian

Tested on Ubuntu 24.04 LTS. Every block below can be pasted as-is.

#### 1. Python and uv

```bash
sudo apt update
sudo apt install -y python3 python3-venv curl unzip
curl -LsSf https://astral.sh/uv/install.sh | sh
```

`uv` manages its own Python, so a distro Python older than 3.12 is not a problem
— `uv` will fetch 3.12 when it needs one.

#### 2. A JDK

`sdkmanager` and `avdmanager` are Java programs and will not start without one.
JDK 17 or 21; 21 is what this project is developed against.

```bash
sudo apt install -y openjdk-21-jdk-headless
java -version
```

#### 3. The Android SDK command-line tools

You do **not** need Android Studio. Note the `latest/` directory in the last
step — `sdkmanager` refuses to run unless it sits at
`cmdline-tools/latest/bin/sdkmanager`, and unzipping the archive gives you
`cmdline-tools/bin/` instead. Getting this wrong is the single most common
setup failure.

```bash
export ANDROID_SDK_ROOT="$HOME/Android/Sdk"
mkdir -p "$ANDROID_SDK_ROOT/cmdline-tools"

cd /tmp
curl -O https://dl.google.com/android/repository/commandlinetools-linux-13114758_latest.zip
unzip -q commandlinetools-linux-13114758_latest.zip

# The archive unpacks to ./cmdline-tools/ — it has to end up under latest/
mv cmdline-tools "$ANDROID_SDK_ROOT/cmdline-tools/latest"
```

Check the newest build number at
[developer.android.com/studio](https://developer.android.com/studio) under
"Command line tools only" if that URL has moved on.

#### 4. Set the environment variables

```bash
cat >> ~/.bashrc <<'EOF'

# Android SDK
export ANDROID_SDK_ROOT="$HOME/Android/Sdk"
export ANDROID_HOME="$ANDROID_SDK_ROOT"
export PATH="$ANDROID_SDK_ROOT/cmdline-tools/latest/bin:$ANDROID_SDK_ROOT/platform-tools:$ANDROID_SDK_ROOT/emulator:$PATH"
EOF

source ~/.bashrc
```

#### 5. SDK packages, and the licences

```bash
sdkmanager --licenses          # answer y to each; nothing installs until you do
sdkmanager "platform-tools" "emulator" "platforms;android-34"
```

#### 6. A system image

An AVD cannot be created without one, and this project **never downloads one for
you** — `android_download_image` exists precisely so that the download is an
explicit choice.

```bash
# google_apis (not playstore) pairs with any device profile
sdkmanager "system-images;android-34;google_apis;x86_64"
```

Use `arm64-v8a` instead of `x86_64` on ARM hardware.

#### 7. Hardware acceleration (KVM)

Without this an emulator boots by software emulation and is unusably slow —
slow enough that it will exceed the boot timeout rather than merely annoy you.

```bash
# Does the CPU support it at all? A number greater than 0 means yes.
egrep -c '(vmx|svm)' /proc/cpuinfo

sudo apt install -y qemu-kvm
sudo usermod -aG kvm "$USER"
# Log out and back in, then confirm — this must print your username in the group:
ls -l /dev/kvm && groups | tr ' ' '\n' | grep -x kvm
```

If you are inside a VM, enable nested virtualisation on the host first.

#### 8. Node.js, Appium and the driver

The distro's `nodejs` package is usually too old; nvm avoids `sudo npm` entirely.

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
source ~/.bashrc
nvm install --lts

npm install -g appium
appium driver install uiautomator2
```

#### 9. Verify

```bash
adb --version                    # SDK platform-tools
emulator -version                # SDK emulator
avdmanager list device | head    # the JDK works
appium --version
appium driver list --installed   # uiautomator2 should be listed
```

> [!WARNING]
> **Do not `apt install adb`.** The distro package puts an `adb` on your `PATH`
> that is routinely a different major version from the SDK's `emulator`, and the
> two fight over the adb server — the symptom is devices appearing and vanishing
> at random. On the machine this project is developed on, `/usr/bin/adb` is
> `34.0.4-debian` while the SDK's is `37.0.1`. This server resolves SDK-local
> tools ahead of `PATH` specifically to survive that, but your own shell will
> not, so prefer the SDK's `platform-tools` on your `PATH` as step 4 does. If
> you already installed it: `sudo apt remove adb`.

---

### Windows (WSL2)

Native Windows does not work — see [Supported platforms](#supported-platforms)
for the specific reason. Run the whole stack inside WSL2 instead.

> [!NOTE]
> These steps are the standard WSL2 route, but the maintainers develop on Linux
> and have **not** tested them end to end. The nested-virtualisation step in
> particular is the one that varies between machines. Corrections via issue or
> PR are very welcome.

#### 1. Install WSL2 with Ubuntu

In PowerShell **as Administrator**:

```powershell
wsl --install -d Ubuntu-24.04
wsl --update
```

Reboot when prompted, then set your Linux username and password.

#### 2. Enable nested virtualisation, so the emulator can use KVM

This is the step that decides whether you get a usable emulator or a slideshow.
It needs **Windows 11** (or Windows 10 build 19041+ with a recent WSL2). In
PowerShell, create or edit `%UserProfile%\.wslconfig`:

```ini
[wsl2]
nestedVirtualization=true
memory=8GB
processors=4
```

Then, back in PowerShell:

```powershell
wsl --shutdown
```

Reopen Ubuntu and confirm KVM is really there:

```bash
egrep -c '(vmx|svm)' /proc/cpuinfo   # must be > 0
ls -l /dev/kvm                       # must exist
```

If `/dev/kvm` is missing, nested virtualisation did not take effect and the
emulator will not be usable — fix that before going further.

#### 3. Everything else, inside WSL2

Follow the [Ubuntu / Debian](#ubuntu--debian) steps above **from inside the WSL2
shell**. The Android SDK, Node, Appium and this server all live in WSL2, not on
Windows.

Do not install the Android SDK on the Windows side and try to reach it from
WSL2 through `/mnt/c`. Windows `.exe` tools cannot be driven the way this server
drives them, and the path translation will bite you.

#### 4. A physical Android phone over USB

WSL2 has no direct USB access, so a cable alone will not do. Either use an
emulator inside WSL2, or share the device with
[usbipd-win](https://github.com/dorssel/usbipd-win):

```powershell
winget install usbipd
usbipd list                      # find your phone's BUSID
usbipd bind --busid <BUSID>
usbipd attach --wsl --busid <BUSID>
```

Then `adb devices` inside WSL2 should list it.

#### 5. Point your MCP client at WSL2

A client running on Windows must launch the server *through* WSL:

```json
{
  "mcpServers": {
    "app-automating": {
      "command": "wsl",
      "args": ["-d", "Ubuntu-24.04", "--", "bash", "-lc", "uvx app-automating"]
    }
  }
}
```

`bash -lc` matters: it loads your login profile, which is where step 4 of the
Ubuntu instructions put `ANDROID_SDK_ROOT`. Without it the server starts and
reports no Android SDK on a machine that plainly has one.

---

### macOS

Untested by the maintainers, but the code is POSIX throughout and nothing in it
is Linux-specific.

```bash
brew install --cask temurin          # a JDK
brew install --cask android-commandlinetools
brew install node
npm install -g appium
appium driver install uiautomator2

export ANDROID_SDK_ROOT="$(brew --prefix)/share/android-commandlinetools"
sdkmanager --licenses
sdkmanager "platform-tools" "emulator" "system-images;android-34;google_apis;arm64-v8a"
```

Use `arm64-v8a` on Apple Silicon and `x86_64` on Intel. Acceleration is
Hypervisor.Framework and needs no setup.

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
