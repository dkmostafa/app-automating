"""The Appium device-control surface, as MCP tools.

Thirteen tools covering one workflow, in the order they are normally called:

1. ``appium_check_environment`` -- can this host automate anything at all.
2. ``appium_install_driver`` -- provision the driver, if it said no.
3. ``appium_start_session`` -- claim a device and get back a ``session_id``.
4. ``appium_get_page_source`` / ``appium_take_screenshot`` -- see what is there.
5. ``appium_tap_element``, ``appium_tap``, ``appium_swipe``, ``appium_scroll``,
   ``appium_type_text``, ``appium_press_key`` -- drive it.
6. ``appium_get_open_sessions`` -- what is still open.
7. ``appium_end_session`` -- release the device.

What they share: a **device_id** is an adb serial (``emulator-5554``, or a
hardware serial like ``R58M12ABCDE``) and identifies a device attached to the
host -- it comes from ``android_get_available_devices``, and this module never
lists devices itself. A **session_id** identifies a live automation session and
is what all eleven interaction tools take. The two are not interchangeable, and
a device has a session only while one is open.

Emulators and physical devices are driven identically: the same tools, the same
arguments, the same driver. The serial is the only thing that differs.

The Appium server is managed by this MCP server: ``appium_start_session``
launches one if nothing is listening, and shutting this server down takes down
every session and any server it started. Nothing here asks the caller to run
``appium`` by hand.

Layering (Rule 0 §1, Rule 3): every tool below is three statements -- build the
call from its arguments, await one service method, render the result. There is
no branching, no retry loop, and no second call. Orchestration belongs to the
service; the Appium client belongs to the infrastructure this file cannot import.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastmcp import FastMCP

from ..application.services import AppiumDeviceService
from .errors import domain_errors_as_tool_errors
from .rendering import (
    render_element_interaction,
    render_ended_session,
    render_environment,
    render_installed_driver,
    render_interaction,
    render_page_source,
    render_screenshot,
    render_session_list,
    render_started_session,
)
from .schemas import (
    ElementInteractionPayload,
    EndedSessionPayload,
    EnvironmentPayload,
    InstalledDriverPayload,
    InteractionPayload,
    PageSourcePayload,
    ScreenshotPayload,
    SessionListPayload,
    StartedSessionPayload,
)

__all__ = ["register_appium_device_tools"]

#: The locator strategies a caller may name, as a closed set so the client sees
#: them in the schema rather than discovering them from an error message.
Strategy = Literal["accessibility_id", "id", "text", "class_name", "xpath", "uiautomator"]

#: Scroll directions, named for the way the *content* moves.
Direction = Literal["up", "down", "left", "right"]


def register_appium_device_tools(mcp: FastMCP, service: AppiumDeviceService) -> None:
    """Add the Appium device tools to ``mcp``, bound to ``service``.

    Rule 3 §2: this builds nothing. The service arrives already wired by the
    module's composition root, which is why registering the same surface on a
    second server -- a throwaway one in a test, for instance -- is free of side
    effects.
    """

    # -- the host ----------------------------------------------------------

    @mcp.tool(name="appium_check_environment")
    async def check_environment(probe_server: bool = True) -> EnvironmentPayload:
        """Report whether this host can automate a device, and what is missing.

        What it does
        ------------
        Looks for Node and the Appium CLI, asks each for its version, lists the
        Appium drivers installed on this host, and optionally asks whether an
        Appium server is already listening. Returns in under a second, starts
        nothing, and changes nothing. A missing tool is reported as data rather
        than raised as an error -- describing a broken host is this tool's whole
        job.

        When to use it
        --------------
        Call it first on an unfamiliar machine, and call it whenever another
        Appium tool reports `BackendUnavailable` or `DriverNotInstalled`: it is
        the only tool that says *which* piece is missing. Its `ready` field
        answers "could I start a session right now" in one boolean.

        When not to use it
        ------------------
        Do not call it before every session -- the answer does not change
        between calls unless something was installed in between. Do not use it
        to find devices; it inspects the host's toolchain and never looks at
        what is attached, which is `android_get_available_devices`.

        Arguments
        ---------
        probe_server:
            `True` (the default) also sends one request to the Appium server's
            status endpoint, which is the only way to distinguish "Appium is
            installed" from "Appium is installed and answering". Set it to
            `False` to skip that request when the host is known to be offline
            or slow; `server_running` then always comes back `false`.

        Returns
        -------
        `{"ready": true, "tools": [{"name": "node", "present": true, "version":
        "25.2.1", "path": "/usr/bin/node", "detail": null}, {"name": "appium",
        "present": true, "version": "3.1.1", "path": "/usr/bin/appium",
        "detail": null}], "missing": [], "drivers": ["uiautomator2"],
        "server_url": "http://127.0.0.1:4723", "server_running": false}`

        `ready` is the summary: true means a session can be started now.
        `missing` names the unusable tools, and `drivers` must contain
        `uiautomator2` for Android. `server_running: false` is normal --
        `appium_start_session` launches one on demand.

        Errors
        ------
        This tool does not raise for a missing tool; that is what `present:
        false` and `missing` are for. An error here means the check itself could
        not run, which is a bug in this server rather than a fact about the
        host -- report it to the user.

        Example
        -------
        `appium_check_environment(probe_server=True)`
        -> `{"ready": true, "drivers": ["uiautomator2"], "missing": [],
        "server_url": "http://127.0.0.1:4723", "server_running": false, ...}`
        """
        with domain_errors_as_tool_errors():
            return render_environment(await service.check_environment(probe_server=probe_server))

    @mcp.tool(name="appium_install_driver")
    async def install_driver(
        driver_name: str = "uiautomator2", reinstall: bool = False
    ) -> InstalledDriverPayload:
        """Install an Appium driver on the host running this server.

        What it does
        ------------
        Runs the Appium CLI's driver installer, which downloads the driver from
        the npm registry and unpacks it under the host's Appium home. This
        changes the machine, not a device, and the change persists after the
        call. A driver that is already installed is reported as
        `already_installed: true` and nothing is downloaded. Expect minutes on a
        real download and milliseconds otherwise.

        When to use it
        --------------
        Use it when `appium_check_environment` shows an empty `drivers` list, or
        when a session fails with `DriverNotInstalled`. Android automation needs
        `uiautomator2`, which is the default and almost always the right answer.

        When not to use it
        ------------------
        Do not call it speculatively before every session -- starting a session
        never installs anything, so this is only ever needed once per host. Do
        not use it to install Appium itself; if `appium_check_environment`
        reports the `appium` tool absent, that is a host provisioning job for
        the user and this tool cannot do it.

        Arguments
        ---------
        driver_name:
            The Appium driver package name. `uiautomator2` (the default) drives
            Android emulators and physical Android devices alike. Other values
            are passed through to the installer untouched, so a typo becomes a
            download failure rather than a rejected argument.
        reinstall:
            `False` (the default) makes an already-installed driver a no-op.
            `True` updates a driver that is already present -- use it only to
            recover from a corrupted install or to move to a newer version, as
            it re-downloads and takes minutes.

        Returns
        -------
        `{"driver_name": "uiautomator2", "version": "6.3.0",
        "already_installed": false, "duration_seconds": 42.7}`

        `already_installed: true` means the call did nothing, which is a success
        and not a warning. `version` is what is on disk now.

        Errors
        ------
        `DriverInstallFailed`: the installer ran and failed; its own output is
        in the message. Usually no network or an npm permissions problem --
        report it to the user rather than retrying unchanged.
        `BackendUnavailable`: there is no Appium CLI on this host to install
        anything with. Nothing to retry; the user must install Appium first.

        Example
        -------
        `appium_install_driver(driver_name="uiautomator2", reinstall=False)`
        -> `{"driver_name": "uiautomator2", "version": "6.3.0",
        "already_installed": true, "duration_seconds": 0.9}`
        """
        with domain_errors_as_tool_errors():
            return render_installed_driver(
                await service.install_driver(driver_name, reinstall=reinstall)
            )

    # -- sessions ----------------------------------------------------------

    @mcp.tool(name="appium_start_session")
    async def start_session(
        device_id: str,
        app_package: str | None = None,
        app_activity: str | None = None,
        app_path: str | None = None,
        no_reset: bool = True,
        startup_timeout_seconds: float | None = None,
    ) -> StartedSessionPayload:
        """Open an automation session on a device and return its session_id.

        What it does
        ------------
        Launches an Appium server if none is listening, then creates a session
        bound to one device and one app. The session outlives this call: it
        holds the device until `appium_end_session` closes it, this server shuts
        down, or it idles past the server's timeout. Takes 5-30 seconds
        typically, longer on a cold emulator or a physical device over USB.
        Works identically for emulators and physical devices.

        When to use it
        --------------
        Call it before any other interaction tool -- every one of them takes the
        `session_id` this returns. Get the `device_id` from
        `android_get_available_devices` first; if the emulator is not booted
        yet, `android_run_emulator` is what boots it.

        When not to use it
        ------------------
        Do not call it twice for the same device: the first session holds it and
        the second will fail. Call `appium_get_open_sessions` to find a session
        you already have. Do not call it to check whether a device exists --
        that is `android_get_available_devices`, and it is far cheaper.

        Arguments
        ---------
        device_id:
            The adb serial of an attached, booted device, exactly as
            `android_get_available_devices` reports it: `emulator-5554` for an
            emulator, or a hardware serial like `R58M12ABCDE` for a phone.
            Case-sensitive. This is not an AVD name.
        app_package:
            Package id of an already-installed app to launch, e.g.
            `com.android.settings`. Optional. Leave it unset along with
            `app_path` to attach to whatever the device is currently showing,
            which is usually what you want for exploring a running device.
        app_activity:
            The activity to launch inside `app_package`, e.g.
            `.Settings`. Optional, and only meaningful together with
            `app_package`; without it the app's default launcher activity is
            used, which is nearly always correct.
        app_path:
            Absolute path to an `.apk` on the machine running this server. When
            given, the APK is installed and launched, overriding `app_package`.
            Optional. The file must exist on the *server's* host, not the
            caller's.
        no_reset:
            `True` (the default) keeps the app's existing data and does not
            clear it when the session starts -- the right choice for driving a
            device as a person left it. `False` clears app data first, for a
            test that needs a known starting state.
        startup_timeout_seconds:
            How long to wait for the session to be created. `None` (the default)
            uses the server's configured value, which is generous enough for a
            cold device. Raise it for a slow physical device; lowering it below
            about 30 does nothing but turn slow successes into failures.

        Returns
        -------
        `{"session_id": "8f2c...", "session": {"session_id": "8f2c...",
        "device_id": "emulator-5554", "platform_name": "Android",
        "automation_name": "UiAutomator2", "app_package": "com.android.settings",
        "app_activity": ".Settings", "screen_width": 1080, "screen_height":
        2400}, "server_started": true, "duration_seconds": 12.4}`

        `session_id` is repeated at the top level because it is the argument of
        every subsequent call. `screen_width` and `screen_height` are what tap
        coordinates must stay inside.

        Errors
        ------
        `InvalidDeviceId`: the serial is malformed. Use one from
        `android_get_available_devices` rather than fixing the spelling.
        `DriverNotInstalled`: install it with `appium_install_driver` and retry.
        `AppNotFound`: no APK at `app_path` on the server's host. Fix the path,
        or drop it and use `app_package`.
        `SessionStartFailed`: the server refused; the reason is in the message.
        Confirm the device is attached and booted before retrying.
        `ServerStartFailed`: an Appium server could not be launched -- usually a
        port already in use. `appium_check_environment` shows what is listening.
        `BackendUnavailable`: no usable Appium on this host. Report it to the
        user; no retry will help.

        Example
        -------
        `appium_start_session(device_id="emulator-5554",
        app_package="com.android.settings")`
        -> `{"session_id": "8f2c...", "server_started": true,
        "duration_seconds": 12.4, "session": {...}}`
        """
        with domain_errors_as_tool_errors():
            return render_started_session(
                await service.start_session(
                    device_id,
                    app_path=Path(app_path) if app_path else None,
                    app_package=app_package,
                    app_activity=app_activity,
                    no_reset=no_reset,
                    startup_timeout_seconds=startup_timeout_seconds,
                )
            )

    @mcp.tool(name="appium_end_session")
    async def end_session(session_id: str) -> EndedSessionPayload:
        """Close an automation session and release the device it was holding.

        What it does
        ------------
        Quits the session on the Appium server and forgets it here. The device
        becomes free for a new session immediately. The app itself is left
        exactly as it was -- this closes the automation connection, not the
        app, and not the device. Returns in under a second. The session_id is
        dead afterwards and can never be reused.

        When to use it
        --------------
        Call it when you are finished driving a device, and always before
        starting a second session on the same device. A session left open holds
        its device against every other caller until the server's idle timeout
        eventually reaps it.

        When not to use it
        ------------------
        Do not call it between two interactions on the same screen -- reopening
        a session costs 5-30 seconds and is never the way to recover from a
        failed tap. Do not call it to stop an emulator; the device keeps running
        and `android_stop_emulator` is what shuts it down.

        Arguments
        ---------
        session_id:
            The id returned by `appium_start_session`, or one listed by
            `appium_get_open_sessions`. Closing an already-closed session is an
            error rather than a no-op, so take the id from a live listing if you
            are unsure.

        Returns
        -------
        `{"session_id": "8f2c...", "device_id": "emulator-5554",
        "duration_seconds": 0.6}`

        `device_id` is the serial that is now free, so a new session can be
        started on it right away.

        Errors
        ------
        `SessionNotFound`: no such session is open here; the open ones are named
        in the message. Call `appium_get_open_sessions`, and treat an
        already-closed session as the outcome you wanted.

        Example
        -------
        `appium_end_session(session_id="8f2c...")`
        -> `{"session_id": "8f2c...", "device_id": "emulator-5554",
        "duration_seconds": 0.6}`
        """
        with domain_errors_as_tool_errors():
            return render_ended_session(await service.end_session(session_id))

    @mcp.tool(name="appium_get_open_sessions")
    async def get_open_sessions(verify_alive: bool = False) -> SessionListPayload:
        """List the automation sessions this server currently holds.

        What it does
        ------------
        Returns every session opened through this MCP server and not yet closed,
        with the device and app each one is driving. Returns immediately, starts
        nothing and changes nothing. Sessions opened by some other process -- a
        developer's own Appium client -- are invisible here, because this list
        is what *this* server can drive and is responsible for closing.

        When to use it
        --------------
        Use it to recover a `session_id` you no longer have, to check whether a
        device is already claimed before calling `appium_start_session`, and to
        find sessions that should be closed. With `verify_alive` it is also the
        way to confirm a quiet session has not silently timed out.

        When not to use it
        ------------------
        Do not use it to discover devices -- a device with no session never
        appears here; that is `android_get_available_devices`. Do not call it
        after every interaction: the list only changes when a session is started
        or ended.

        Arguments
        ---------
        verify_alive:
            `False` (the default) returns what this server believes is open,
            with no network calls. `True` asks each session whether it is still
            there and drops the ones that are not, at one round trip per session
            -- use it when a session has been idle long enough that the server
            may have reaped it, and accept that the call then takes a moment.

        Returns
        -------
        `{"sessions": [{"session_id": "8f2c...", "device_id": "emulator-5554",
        "platform_name": "Android", "automation_name": "UiAutomator2",
        "app_package": "com.android.settings", "app_activity": ".Settings",
        "screen_width": 1080, "screen_height": 2400}], "session_ids":
        ["8f2c..."], "count": 1}`

        `count: 0` with an empty list is a normal answer meaning nothing is
        open, not an error.

        Errors
        ------
        This tool reads local state and does not fail for an absent server or a
        dead session -- with `verify_alive=True` a dead session is simply
        omitted. An error here is a bug in this server; report it to the user.

        Example
        -------
        `appium_get_open_sessions(verify_alive=False)`
        -> `{"sessions": [{"session_id": "8f2c...", "device_id":
        "emulator-5554", ...}], "session_ids": ["8f2c..."], "count": 1}`
        """
        with domain_errors_as_tool_errors():
            return render_session_list(await service.get_open_sessions(verify_alive=verify_alive))

    # -- reading the screen ------------------------------------------------

    @mcp.tool(name="appium_take_screenshot")
    async def take_screenshot(session_id: str, save_path: str | None = None) -> ScreenshotPayload:
        """Capture what is on the device's screen right now, as a PNG.

        What it does
        ------------
        Asks the device for a screenshot of the current screen and either writes
        it to a file on the server's host or returns it inline as base64. Takes
        roughly a second. Reads the screen and changes nothing about it.

        When to use it
        --------------
        Use it to see what a person would see: to confirm a tap landed, to find
        out why an element could not be located, or to check which screen the
        app is actually on. Pair it with `appium_get_page_source`, which shows
        what is addressable rather than what is visible.

        When not to use it
        ------------------
        Do not use it to find selectors -- an image cannot give you a
        resource-id, and `appium_get_page_source` can. Avoid the inline base64
        form for anything but a small image; a full-resolution phone screenshot
        runs to megabytes of text.

        Arguments
        ---------
        session_id:
            An open session, from `appium_start_session` or
            `appium_get_open_sessions`.
        save_path:
            Absolute path to write the PNG to, on the machine running this
            server. Parent directories are created. When omitted (the default),
            the image comes back inline as base64 instead -- prefer a path for
            anything a person will look at.

        Returns
        -------
        `{"session_id": "8f2c...", "path": "/tmp/screen.png", "image_base64":
        null, "width": 1080, "height": 2400, "size_bytes": 184320}`

        Exactly one of `path` and `image_base64` is set, according to whether
        `save_path` was given. `width` and `height` are read from the image
        itself, so they are the true pixel size.

        Errors
        ------
        `SessionNotFound`: that session is not open here. Check
        `appium_get_open_sessions`.
        `SessionExpired`: the session timed out; start a new one.
        `ScreenshotFailed`: the device would not produce an image. Retry once if
        the screen is mid-transition; a repeat failure means the session is
        wedged and wants restarting.

        Example
        -------
        `appium_take_screenshot(session_id="8f2c...",
        save_path="/tmp/settings.png")`
        -> `{"session_id": "8f2c...", "path": "/tmp/settings.png",
        "image_base64": null, "width": 1080, "height": 2400, "size_bytes":
        184320}`
        """
        with domain_errors_as_tool_errors():
            return render_screenshot(
                await service.take_screenshot(
                    session_id, save_path=Path(save_path) if save_path else None
                )
            )

    @mcp.tool(name="appium_get_page_source")
    async def get_page_source(
        session_id: str, max_characters: int | None = None
    ) -> PageSourcePayload:
        """Dump the current screen's UI hierarchy as XML.

        What it does
        ------------
        Asks the device for every element on the current screen with its
        attributes -- `resource-id`, `content-desc`, `text`, `class`, `bounds`,
        `clickable` and the rest -- as an XML tree. Takes one to a few seconds
        on a busy screen. Reads the screen and changes nothing.

        When to use it
        --------------
        This is where selectors come from. Call it before `appium_tap_element`
        or `appium_type_text` on an unfamiliar screen and take a `content-desc`
        (use it with `strategy="accessibility_id"`), a `resource-id`
        (`strategy="id"`) or a `text` value (`strategy="text"`) straight out of
        it. Call it again after an `ElementNotFound` -- the screen is often not
        the one you assumed.

        When not to use it
        ------------------
        Do not use it to see what something looks like; that is
        `appium_take_screenshot`. Do not fetch it unbounded on a long list
        screen -- the hierarchy can run to hundreds of kilobytes, and
        `max_characters` exists for exactly that.

        Arguments
        ---------
        session_id:
            An open session, from `appium_start_session` or
            `appium_get_open_sessions`.
        max_characters:
            Cut the XML to this many characters. `None` (the default) returns
            all of it. A few thousand is usually enough to find the element you
            want near the top of the tree; when the value cuts the output,
            `truncated` comes back `true` so a missing element can be told from
            a missing tail.

        Returns
        -------
        `{"session_id": "8f2c...", "source": "<?xml version='1.0'?><hierarchy
        ...><android.widget.Button resource-id='com.app:id/ok' text='OK'
        .../></hierarchy>", "truncated": false, "total_characters": 14820}`

        `total_characters` is the length before any truncation, so `truncated:
        true` with a large `total_characters` means to narrow the screen rather
        than raise the limit.

        Errors
        ------
        `SessionNotFound`: that session is not open here. Check
        `appium_get_open_sessions`.
        `SessionExpired`: the session timed out; start a new one.
        `InteractionFailed`: the device would not produce a hierarchy, usually
        because the screen is mid-transition. Retry once.

        Example
        -------
        `appium_get_page_source(session_id="8f2c...", max_characters=4000)`
        -> `{"session_id": "8f2c...", "source": "<?xml ...", "truncated": true,
        "total_characters": 14820}`
        """
        with domain_errors_as_tool_errors():
            return render_page_source(
                await service.get_page_source(session_id, max_characters=max_characters)
            )

    # -- driving the screen ------------------------------------------------

    @mcp.tool(name="appium_tap_element")
    async def tap_element(
        session_id: str,
        strategy: Strategy,
        selector: str,
        timeout_seconds: float = 10.0,
    ) -> ElementInteractionPayload:
        """Find an element on screen and tap it, waiting for it to appear.

        What it does
        ------------
        Looks for one element matching the locator, retrying until it appears or
        the timeout runs out, reads its visible text, then taps it. Changes the
        state of the device. Usually returns in under a second, or after
        `timeout_seconds` when nothing matches.

        When to use it
        --------------
        This is the preferred way to tap anything. It is correct on every screen
        size, it waits out animations and loading, and it tells you what it hit.
        Get the selector from `appium_get_page_source`.

        When not to use it
        ------------------
        Do not use it for something with no stable identity -- a point on a map,
        a canvas, a specific pixel; `appium_tap` takes coordinates for those. Do
        not use it to type: tapping a text field only focuses it, and
        `appium_type_text` both finds the field and fills it in one call.

        Arguments
        ---------
        session_id:
            An open session, from `appium_start_session` or
            `appium_get_open_sessions`.
        strategy:
            How to interpret `selector`. `accessibility_id` matches
            `content-desc` and is the most stable -- prefer it. `id` matches
            `resource-id`. `text` matches visible text exactly. `class_name`
            matches the widget class. `xpath` is the slow last resort and breaks
            whenever the hierarchy moves. `uiautomator` takes a raw Android
            UiSelector expression.
        selector:
            The value to match, taken verbatim from `appium_get_page_source`.
            Case-sensitive, and never a guess -- a near-miss spelling fails the
            same way an absent element does.
        timeout_seconds:
            How long to keep retrying the lookup, in seconds. `10.0` is the
            default and suits a screen that is still settling. `0` checks once
            and fails immediately. Raising it past ~30 rarely helps: if the
            element is not there by then, it is usually the wrong screen.

        Returns
        -------
        `{"session_id": "8f2c...", "action": "tap_element", "strategy":
        "accessibility_id", "selector": "Wi-Fi", "element_text": "Wi-Fi",
        "duration_seconds": 0.7}`

        `element_text` is the element's label captured *before* the tap, and is
        the cheapest confirmation that the right thing was hit.

        Errors
        ------
        `ElementNotFound`: nothing matched before the timeout. Call
        `appium_get_page_source` and use a selector from it -- do not retry the
        same one with a longer timeout.
        `InvalidLocator`: the strategy is not supported or the selector is
        empty. Fix the arguments.
        `SessionNotFound` / `SessionExpired`: the session is gone. Check
        `appium_get_open_sessions` or start a new session.
        `InteractionFailed`: the element was found and the tap was refused --
        it may be disabled or covered. Take a screenshot before retrying.

        Example
        -------
        `appium_tap_element(session_id="8f2c...", strategy="accessibility_id",
        selector="Wi-Fi", timeout_seconds=10.0)`
        -> `{"session_id": "8f2c...", "action": "tap_element", "strategy":
        "accessibility_id", "selector": "Wi-Fi", "element_text": "Wi-Fi",
        "duration_seconds": 0.7}`
        """
        with domain_errors_as_tool_errors():
            return render_element_interaction(
                await service.tap_element(
                    session_id, strategy, selector, timeout_seconds=timeout_seconds
                )
            )

    @mcp.tool(name="appium_tap")
    async def tap(session_id: str, x: int, y: int, duration_ms: int = 0) -> InteractionPayload:
        """Tap a point on the screen, in pixels from the top-left corner.

        What it does
        ------------
        Sends a touch at the given coordinates, optionally holding before
        releasing. Changes the state of the device. Returns in well under a
        second. The tap is sent whether or not anything is there -- a tap on
        empty space succeeds and does nothing.

        When to use it
        --------------
        Use it only when the target cannot be named: a point on a map, a canvas,
        an image, a custom control that exposes no id or text. Set
        `duration_ms` to around 1000 for a long press. Coordinates come from
        `appium_take_screenshot`, and must stay inside the `screen_width` and
        `screen_height` reported by `appium_start_session`.

        When not to use it
        ------------------
        Prefer `appium_tap_element` for anything with an id, a label or visible
        text: a coordinate is correct for one screen size and silently wrong on
        every other, and this tool cannot tell you whether it hit the thing you
        meant. Do not use it to scroll -- a tap does not drag; that is
        `appium_swipe` or `appium_scroll`.

        Arguments
        ---------
        session_id:
            An open session, from `appium_start_session` or
            `appium_get_open_sessions`.
        x:
            Pixels from the left edge. Never negative. Must be less than the
            session's `screen_width`; beyond it the tap is accepted and lands
            nowhere.
        y:
            Pixels from the top edge, measured from the very top of the display
            including the status bar. Never negative, and less than the
            session's `screen_height`.
        duration_ms:
            How long to hold before releasing, in milliseconds. `0` (the
            default) is an ordinary tap. Around `1000` is a long press, which is
            what opens context menus.

        Returns
        -------
        `{"session_id": "8f2c...", "action": "tap", "detail": "tapped (540,
        1200)", "duration_seconds": 0.3}`

        `detail` restates the coordinates that were actually sent, which is
        worth checking against the screenshot they came from.

        Errors
        ------
        `InvalidCoordinates`: a coordinate was negative. Coordinates are pixels
        from the top-left; fix the arithmetic rather than retrying.
        `SessionNotFound` / `SessionExpired`: the session is gone. Check
        `appium_get_open_sessions` or start a new session.
        `InteractionFailed`: the device refused the gesture; its reason is in
        the message. Take a screenshot before retrying.

        Example
        -------
        `appium_tap(session_id="8f2c...", x=540, y=1200, duration_ms=0)`
        -> `{"session_id": "8f2c...", "action": "tap", "detail": "tapped (540,
        1200)", "duration_seconds": 0.3}`
        """
        with domain_errors_as_tool_errors():
            return render_interaction(await service.tap(session_id, x, y, duration_ms=duration_ms))

    @mcp.tool(name="appium_swipe")
    async def swipe(
        session_id: str,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration_ms: int = 300,
    ) -> InteractionPayload:
        """Drag from one point on the screen to another.

        What it does
        ------------
        Presses at the start point, moves to the end point over `duration_ms`,
        and releases. Changes the state of the device. Returns once the gesture
        completes, typically in about a second.

        When to use it
        --------------
        Use it when the exact path matters: dragging a slider to a value,
        pulling a drawer a specific distance, swiping one row of a list rather
        than the whole screen, or a diagonal gesture. Coordinates come from
        `appium_take_screenshot`.

        When not to use it
        ------------------
        For plain scrolling use `appium_scroll`, which derives the path from the
        device's own screen size and so works on any resolution without you
        measuring anything. Do not use this to tap -- a zero-length swipe is not
        a reliable tap; `appium_tap` is.

        Arguments
        ---------
        session_id:
            An open session, from `appium_start_session` or
            `appium_get_open_sessions`.
        start_x:
            Pixels from the left edge where the finger goes down. Never
            negative. Starting on the very edge of the screen triggers Android's
            own back gesture instead of reaching the app.
        start_y:
            Pixels from the top edge where the finger goes down. Never negative.
            Starting at the very top pulls the notification shade down.
        end_x:
            Pixels from the left edge where the finger lifts. Never negative.
        end_y:
            Pixels from the top edge where the finger lifts. Never negative.
        duration_ms:
            How long the finger travels, in milliseconds. `300` (the default) is
            an ordinary swipe. Much faster reads as a fling and keeps
            scrolling after release; much slower reads as a drag, which is what
            you want for a slider.

        Returns
        -------
        `{"session_id": "8f2c...", "action": "swipe", "detail": "swiped (540,
        1600) -> (540, 600) over 300ms", "duration_seconds": 0.9}`

        `detail` restates the full path that was sent.

        Errors
        ------
        `InvalidCoordinates`: a coordinate was negative. Fix the arithmetic.
        `SessionNotFound` / `SessionExpired`: the session is gone. Check
        `appium_get_open_sessions` or start a new session.
        `InteractionFailed`: the device refused the gesture; its reason is in
        the message. Take a screenshot before retrying.

        Example
        -------
        `appium_swipe(session_id="8f2c...", start_x=540, start_y=1600,
        end_x=540, end_y=600, duration_ms=300)`
        -> `{"session_id": "8f2c...", "action": "swipe", "detail": "swiped (540,
        1600) -> (540, 600) over 300ms", "duration_seconds": 0.9}`
        """
        with domain_errors_as_tool_errors():
            return render_interaction(
                await service.swipe(
                    session_id, start_x, start_y, end_x, end_y, duration_ms=duration_ms
                )
            )

    @mcp.tool(name="appium_scroll")
    async def scroll(
        session_id: str,
        direction: Direction,
        distance: float = 0.5,
        duration_ms: int = 300,
    ) -> InteractionPayload:
        """Scroll the screen in a direction, without knowing its resolution.

        What it does
        ------------
        Reads the device's screen size, derives a swipe across the middle of it,
        and sends that gesture -- inset from the edges so it scrolls the content
        rather than triggering Android's own back or notification gestures.
        Changes the state of the device. Returns in about a second.

        When to use it
        --------------
        Use it for ordinary scrolling: further down a list, back up a page,
        sideways through a carousel. It is the right choice whenever you do not
        care about the exact path, because it needs no coordinates and works on
        any screen size.

        When not to use it
        ------------------
        Use `appium_swipe` when the endpoints matter -- a slider, a drawer, a
        gesture on one row. If the thing you are scrolling toward has a name,
        note that scrolling and then calling `appium_tap_element` is often
        unnecessary: that tool waits for the element on its own.

        Arguments
        ---------
        session_id:
            An open session, from `appium_start_session` or
            `appium_get_open_sessions`.
        direction:
            Which way the *content* moves. `down` reveals what is further down a
            list, `up` goes back toward the top, and `left` and `right` move
            through horizontal content.
        distance:
            Fraction of the screen to travel, greater than 0 and at most `1.0`.
            `0.5` (the default) is about half a screen, which is a comfortable
            page. `1.0` covers the whole screen and can overshoot past what you
            were looking for.
        duration_ms:
            How long the gesture takes, in milliseconds. `300` (the default)
            scrolls and stops. Lower values fling, and the list keeps moving
            after the gesture ends, which makes the screen unpredictable.

        Returns
        -------
        `{"session_id": "8f2c...", "action": "scroll", "detail": "scrolled down
        50% of a 1080x2400 screen: (540, 1800) -> (540, 600)",
        "duration_seconds": 0.9}`

        `detail` names the coordinates that were derived, so an unexpected
        result can be checked against the screen size.

        Errors
        ------
        `InvalidScroll`: the direction is not one of the four, or `distance` is
        outside `0 < d <= 1`. Fix the argument.
        `SessionNotFound` / `SessionExpired`: the session is gone. Check
        `appium_get_open_sessions` or start a new session.
        `InteractionFailed`: the device refused the gesture; its reason is in
        the message. Take a screenshot before retrying.

        Example
        -------
        `appium_scroll(session_id="8f2c...", direction="down", distance=0.5,
        duration_ms=300)`
        -> `{"session_id": "8f2c...", "action": "scroll", "detail": "scrolled
        down 50% of a 1080x2400 screen: (540, 1800) -> (540, 600)",
        "duration_seconds": 0.9}`
        """
        with domain_errors_as_tool_errors():
            return render_interaction(
                await service.scroll(
                    session_id, direction, distance=distance, duration_ms=duration_ms
                )
            )

    @mcp.tool(name="appium_type_text")
    async def type_text(
        session_id: str,
        text: str,
        strategy: Strategy | None = None,
        selector: str | None = None,
        clear_first: bool = False,
    ) -> ElementInteractionPayload:
        """Type text into a named field, or into whatever currently has focus.

        What it does
        ------------
        With a locator, finds the field, optionally clears it, and types into
        it. Without one, types into whatever holds focus. Changes the state of
        the device. Returns in about a second. The text is entered as if typed;
        it does not submit anything -- `appium_press_key` with `enter` does
        that.

        When to use it
        --------------
        Use it to fill in any text field. Naming the field with
        `strategy`/`selector` is the reliable form and is what you should
        normally do; get the selector from `appium_get_page_source`.

        When not to use it
        ------------------
        Do not use it for a key that is not a character -- back, home, enter,
        tab are `appium_press_key`. Do not rely on the focused-field form except
        immediately after tapping a field: focus is not predictable otherwise,
        and the text will go somewhere you did not intend.

        Arguments
        ---------
        session_id:
            An open session, from `appium_start_session` or
            `appium_get_open_sessions`.
        text:
            The literal text to type. Sent as-is, including spaces and
            punctuation. A newline in it is typed as a newline and does not
            submit the field.
        strategy:
            How to interpret `selector`, with the same meanings as in
            `appium_tap_element` -- `accessibility_id` is the most stable, then
            `id`, then `text`. `None` (the default) types into the focused
            element instead; pass it together with `selector` or not at all.
        selector:
            The value to match, taken verbatim from `appium_get_page_source`.
            `None` (the default) goes with `strategy=None` and types into
            whatever has focus.
        clear_first:
            `True` empties the field before typing, which is what you want when
            replacing an existing value. `False` (the default) appends to what
            is already there.

        Returns
        -------
        `{"session_id": "8f2c...", "action": "type_text", "strategy": "id",
        "selector": "com.app:id/search", "element_text": "coffee",
        "duration_seconds": 0.8}`

        `strategy` comes back as `"focused"` with an empty `selector` when no
        locator was given. `element_text` is the field's content after typing,
        which is the cheapest confirmation the text landed.

        Errors
        ------
        `ElementNotFound`: the field was not found. Call
        `appium_get_page_source` and use a selector from it.
        `InvalidLocator`: only one of `strategy` and `selector` was given, or
        the strategy is unsupported. Pass both or neither.
        `SessionNotFound` / `SessionExpired`: the session is gone. Check
        `appium_get_open_sessions` or start a new session.
        `InteractionFailed`: the field refused the text -- it may be read-only
        or not actually focused. Tap it first, then retry.

        Example
        -------
        `appium_type_text(session_id="8f2c...", text="coffee", strategy="id",
        selector="com.app:id/search", clear_first=True)`
        -> `{"session_id": "8f2c...", "action": "type_text", "strategy": "id",
        "selector": "com.app:id/search", "element_text": "coffee",
        "duration_seconds": 0.8}`
        """
        with domain_errors_as_tool_errors():
            return render_element_interaction(
                await service.type_text(
                    session_id,
                    text,
                    strategy=strategy,
                    selector=selector,
                    clear_first=clear_first,
                )
            )

    @mcp.tool(name="appium_press_key")
    async def press_key(session_id: str, key: str) -> InteractionPayload:
        """Press a hardware or system key by name, such as back, home or enter.

        What it does
        ------------
        Sends an Android key event to the device. Changes the state of the
        device -- `back` leaves a screen, `home` leaves the app entirely.
        Returns in well under a second. The key name is checked against a known
        list before anything is sent, so a typo fails immediately rather than
        doing something unexpected.

        When to use it
        --------------
        Use it for everything that is not on screen to be tapped: leaving a
        screen (`back`), dismissing the keyboard (`back`), submitting a field
        (`enter`), moving between fields (`tab`), or going to the launcher
        (`home`).

        When not to use it
        ------------------
        Do not use it to type characters -- `appium_type_text` is for text, and
        this tool takes key names rather than characters. Do not use `home` as a
        way to reset an app; it backgrounds the app rather than restarting it,
        and a fresh `appium_start_session` with `no_reset=False` is what gives a
        clean state.

        Arguments
        ---------
        session_id:
            An open session, from `appium_start_session` or
            `appium_get_open_sessions`.
        key:
            A key name, case-insensitive. The common ones: `back`, `home`,
            `enter`, `tab`, `space`, `delete` (same as `backspace`), `escape`,
            `menu`, `search`, `app_switch`, `power`, `volume_up`,
            `volume_down`, `notification`, `page_up`, `page_down`,
            `dpad_up`, `dpad_down`, `dpad_left`, `dpad_right`, `dpad_center`.
            A name outside the list is rejected before anything is sent, and the
            error names every accepted value. Never a raw keycode number.

        Returns
        -------
        `{"session_id": "8f2c...", "action": "press_key", "detail": "pressed
        back (keycode 4)", "duration_seconds": 0.2}`

        `detail` names both the key and the Android keycode it mapped to.

        Errors
        ------
        `InvalidKeyName`: the name is not one this tool knows; every accepted
        name is listed in the message. Pick one from that list rather than
        guessing a synonym.
        `SessionNotFound` / `SessionExpired`: the session is gone. Check
        `appium_get_open_sessions` or start a new session.
        `InteractionFailed`: the device refused the key event; its reason is in
        the message.

        Example
        -------
        `appium_press_key(session_id="8f2c...", key="back")`
        -> `{"session_id": "8f2c...", "action": "press_key", "detail": "pressed
        back (keycode 4)", "duration_seconds": 0.2}`
        """
        with domain_errors_as_tool_errors():
            return render_interaction(await service.press_key(session_id, key))
