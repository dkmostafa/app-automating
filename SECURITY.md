# Security Policy

## Supported versions

The project is pre-1.0. Only the latest release on `main` receives fixes.

| Version | Supported |
|---|---|
| 0.1.x | ✅ |
| < 0.1 | ❌ |

## Reporting a vulnerability

**Please do not open a public issue.**

Report it privately through GitHub's own channel:
[**Report a vulnerability**](https://github.com/dkmostafa/app-automating/security/advisories/new).
That opens a draft advisory only you and the maintainers can see.

Please include what it affects, how to reproduce it, and what an attacker gets
out of it. You will get an initial response within 7 days. If a report is
accepted, the fix and the advisory are published together, and you are credited
unless you ask not to be.

## What this software actually does

Worth stating plainly, because it changes what counts as a vulnerability here.

This is an MCP server that **executes host binaries and drives attached
devices**. By design it can start and kill processes (`emulator`, `adb`,
`appium`), create and delete AVDs, install drivers over `npm`, download system
images over `sdkmanager`, and tap, type into and read the screen of any device
attached to the machine. Those are its features, not flaws. Run it only against
devices and hosts you control.

Three deliberate choices are load-bearing, and breaking one **is** a
vulnerability:

- **STDIO only.** The server pins the STDIO transport and refuses every other
  one at the source, including when a caller imports the module and drives the
  object directly. It is not reachable over a socket, so it inherits the trust
  boundary of the process that launched it.
- **Appium binds to loopback.** An Appium server drives every attached device
  with **no authentication of any kind**. `AT_APPIUM_SERVER_HOST` defaults to
  `127.0.0.1`; setting it to `0.0.0.0` hands full control of your devices to
  anyone on the network. Do not.
- **PRAGMA values are validated against an allow-list.** A SQLite `PRAGMA` value
  cannot be a bound parameter, so configuration that reaches one is checked
  against a fixed set rather than interpolated.

## What is stored, and where

The navigation memory is a local SQLite file under
`$XDG_DATA_HOME/app-automating/` (or `~/.local/share/app-automating/`), with
screenshots beside it on the filesystem. It records the view hierarchy and
screenshots of every screen visited — **which may include whatever was on screen
at the time, such as text you typed into a field.** Nothing is transmitted
anywhere: no telemetry, no network calls, no sharing between machines.

If you drive an app through a login screen, the page source captured for that
screen can contain what was in the fields. Use
`AT_NAVIGATION_MEMORY_STORE_PAGE_SOURCE=false` to keep the map and drop the
labels, `AT_NAVIGATION_MEMORY_RECORD_INTERACTIONS=false` to record nothing at
all, or `navigation_memory_forget` to remove a run after the fact.

## Scope

In scope: anything that breaks one of the three choices above, an injection
through configuration or tool arguments, a path traversal, a leak of the
navigation memory off the machine, or a dependency vulnerability that is
reachable from this code.

Out of scope: the fact that the server runs host commands at all, vulnerabilities
in Appium / the Android SDK / Node (report those upstream), and anything that
requires an attacker who already has code execution as your user.
