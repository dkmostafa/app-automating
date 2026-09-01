"""The Android emulator surface, as MCP tools.

Nine tools covering one workflow, in the order they are normally called:

1. ``android_get_installed_emulators`` -- what AVDs exist on this host at all.
2. ``android_get_available_devices`` / ``android_get_all_devices`` -- what is
   attached and running right now.
3. ``android_download_image`` then ``android_create_device`` -- provision an AVD
   that does not exist yet.
4. ``android_run_emulator`` or ``android_run_emulator_without_window`` -- boot
   one, and get back the ``device_id`` every later device operation needs.
5. ``android_stop_emulator`` -- shut it down, leaving the AVD on disk.
6. ``android_delete_emulator`` -- remove the AVD itself, irreversibly.

What they share: an **AVD name** identifies a virtual device *definition* on
disk and is what the create/start/delete tools take; a **device_id** (an adb
serial such as ``emulator-5554``) identifies a *running* device and is what the
stop tool takes. The two are not interchangeable, and only a running emulator
has both. Everything here needs a working Android SDK on the host running this
server: if that is missing, every tool fails with ``BackendUnavailable`` and no
retry will help.

Layering (Rule 0 §1, Rule 3): every tool below is three statements -- build the
call from its arguments, await one service method, render the result. There is
no branching, no retry loop, and no second call. Orchestration belongs to the
service; the SDK belongs to the infrastructure this file cannot import.
"""

from __future__ import annotations

from fastmcp import FastMCP

from ..application.services import AndroidEmulatorService
from .errors import domain_errors_as_tool_errors
from .rendering import (
    render_avd_list,
    render_created_emulator,
    render_deleted_emulator,
    render_device_list,
    render_installed_image,
    render_started_emulator,
    render_stopped_emulator,
)
from .schemas import (
    AvdListPayload,
    CreatedEmulatorPayload,
    DeletedEmulatorPayload,
    DeviceListPayload,
    InstalledImagePayload,
    StartedEmulatorPayload,
    StoppedEmulatorPayload,
)

__all__ = ["register_android_emulator_tools"]


def register_android_emulator_tools(mcp: FastMCP, service: AndroidEmulatorService) -> None:
    """Add the Android emulator tools to ``mcp``, bound to ``service``.

    Rule 3 §2: this builds nothing. The service arrives already wired by the
    module's composition root, which is why registering the same surface on a
    second server -- a throwaway one in a test, for instance -- is free of side
    effects.
    """

    # -- reading -----------------------------------------------------------

    @mcp.tool(name="android_get_available_devices")
    async def get_available_devices(resolve_avd_names: bool = True) -> DeviceListPayload:
        """List the Android devices that will accept commands right now.

        What it does
        ------------
        Asks the host which devices are attached and keeps only the ones in a
        usable state, dropping anything offline, unauthorized or still booting.
        Returns immediately -- it starts nothing and changes nothing. Emulators
        and physical devices both appear here.

        When to use it
        --------------
        Use this to find the ``device_id`` to drive: it is the list of devices
        that will actually respond. Call it after ``android_run_emulator`` to
        confirm a device came up, and before any device-scoped operation when
        you do not already hold a serial.

        When not to use it
        ------------------
        Do not use it to discover what AVDs exist -- an AVD that is not running
        never appears here; that is ``android_get_installed_emulators``. Do not
        use it to diagnose a device that has gone quiet: an offline or
        unauthorized device is filtered out of this list, and
        ``android_get_all_devices`` is the one that will still show it.

        Arguments
        ---------
        resolve_avd_names:
            True (the default) asks each emulator which AVD it is running and
            fills in ``avd_name``, at the cost of one extra call per emulator.
            Set it to False when you only need serials and want the fastest
            possible answer; ``avd_name`` then comes back null for every entry.

        Returns
        -------
        ``{"devices": [{"device_id": "emulator-5554", "state": "device",
        "available": true, "is_emulator": true, "avd_name": "Pixel_7",
        "model": null, "product": null, "transport_id": "3"}],
        "count": 1, "emulator_count": 1}``

        ``device_id`` is the serial every other device tool takes. ``count`` is
        zero when nothing is attached, which is a normal answer and not an error.

        Errors
        ------
        ``BackendUnavailable``: this host has no usable Android SDK. Nothing to
        retry -- report it to the user.
        ``BackendFailure``: the SDK was reached and the query failed; the tool's
        own output is in the message.

        Example
        -------
        ``android_get_available_devices(resolve_avd_names=True)``
        -> ``{"devices": [...], "count": 1, "emulator_count": 1}``
        """
        with domain_errors_as_tool_errors():
            result = await service.get_available_devices(resolve_avd_names=resolve_avd_names)
        return render_device_list(result)

    @mcp.tool(name="android_get_all_devices")
    async def get_all_devices(resolve_avd_names: bool = True) -> DeviceListPayload:
        """List every Android device attached to the host, working or not.

        What it does
        ------------
        The same query as ``android_get_available_devices`` with no filtering:
        devices that are offline, unauthorized or still booting are included,
        each carrying its raw ``state`` and ``available: false``. Returns
        immediately, starts nothing, changes nothing.

        When to use it
        --------------
        Use it to answer "what is attached", and to diagnose a device that is
        connected but not responding -- comparing this list against
        ``android_get_available_devices`` is what tells you a device exists but
        is unusable, rather than being absent.

        When not to use it
        ------------------
        Do not pick a ``device_id`` out of this list to drive without checking
        ``available``: a device with ``state: "offline"`` will fail every
        subsequent call. Use ``android_get_available_devices`` when the question
        is "what can I use". Neither tool shows AVDs that are not running --
        that is ``android_get_installed_emulators``.

        Arguments
        ---------
        resolve_avd_names:
            True (the default) resolves each emulator's AVD name into
            ``avd_name``, one extra call per emulator. False skips it and leaves
            ``avd_name`` null. Note that an offline emulator cannot answer, so
            its ``avd_name`` may be null even when this is True.

        Returns
        -------
        ``{"devices": [{"device_id": "emulator-5554", "state": "offline",
        "available": false, "is_emulator": true, "avd_name": null,
        "model": null, "product": null, "transport_id": null}],
        "count": 1, "emulator_count": 1}``

        The superset of ``android_get_available_devices``; ``available``
        distinguishes the two.

        Errors
        ------
        ``BackendUnavailable``: no usable Android SDK on this host; report it
        rather than retrying.
        ``BackendFailure``: the query itself failed; the message carries the
        tool's output.

        Example
        -------
        ``android_get_all_devices(resolve_avd_names=False)``
        -> ``{"devices": [...], "count": 2, "emulator_count": 1}``
        """
        with domain_errors_as_tool_errors():
            result = await service.get_all_devices(resolve_avd_names=resolve_avd_names)
        return render_device_list(result)

    @mcp.tool(name="android_get_installed_emulators")
    async def get_installed_emulators(include_unloadable: bool = True) -> AvdListPayload:
        """List the AVDs defined on this host, running or not.

        What it does
        ------------
        Reads the host's AVD catalogue -- the virtual device definitions on
        disk -- and returns each one's name, hardware profile, system image and
        whether it can be loaded at all. Reads only; nothing is started and
        nothing is changed.

        When to use it
        --------------
        This is the answer to "what could I start", and the source of every AVD
        name the other tools take. Call it before ``android_run_emulator`` when
        you do not already know a name, and before ``android_create_device`` to
        see whether the AVD you were about to create already exists.

        When not to use it
        ------------------
        Do not use it to find out what is *running*: an entry here may be
        stopped, and it carries no ``device_id``. Use
        ``android_get_available_devices`` for that. A name whose ``loadable`` is
        false must not be passed to ``android_run_emulator`` -- it will fail
        until whatever ``error`` describes is fixed.

        Arguments
        ---------
        include_unloadable:
            True (the default) keeps AVDs the backend could not read -- a
            deleted system image, a retired hardware profile. They appear with
            ``loadable: false`` and an ``error``. Set it to False to see only
            AVDs that can actually boot; a broken AVD then vanishes from the
            list entirely, which reads as "no such AVD" rather than "that one is
            broken", so prefer the default when reporting to a user.

        Returns
        -------
        ``{"emulators": [{"name": "Pixel_7", "path": "/home/u/.android/avd/Pixel_7.avd",
        "device": "pixel_7", "target": "android-34", "based_on": "Android 14",
        "tag_abi": "google_apis/x86_64", "sdcard": "512M", "loadable": true,
        "error": null}], "names": ["Pixel_7"], "count": 1, "unloadable_count": 0}``

        ``names`` is the same list flattened, in the same order.
        ``unloadable_count`` above zero means some of those names cannot boot.

        Errors
        ------
        ``BackendUnavailable``: no usable Android SDK on this host; report it.
        ``BackendFailure``: the catalogue could not be read; the message carries
        the tool's output.

        Example
        -------
        ``android_get_installed_emulators(include_unloadable=True)``
        -> ``{"emulators": [...], "names": ["Pixel_7"], "count": 1, "unloadable_count": 0}``
        """
        with domain_errors_as_tool_errors():
            result = await service.get_installed_emulators(include_unloadable=include_unloadable)
        return render_avd_list(result)

    # -- running -----------------------------------------------------------

    @mcp.tool(name="android_run_emulator")
    async def run_emulator(
        avd_name: str,
        wait_for_boot: bool = True,
        cold_boot: bool = False,
        wipe_data: bool = False,
        boot_timeout_seconds: float | None = None,
    ) -> StartedEmulatorPayload:
        """Boot an existing AVD with a visible window and return its device_id.

        What it does
        ------------
        Launches the named AVD as a headed emulator on the host running this
        server and, by default, blocks until the device reports it has finished
        booting. A first boot commonly takes 30-90 seconds. **The emulator
        outlives this call**: it keeps running until ``android_stop_emulator``
        stops it or the host is rebooted.

        When to use it
        --------------
        Use it to get a usable device before anything that drives one --
        installing an app, taking a screenshot, running a test. The
        ``device_id`` it returns is what those operations take. Prefer this over
        the headless variant when a human is watching, or when the thing being
        tested involves what is on screen.

        When not to use it
        ------------------
        Do not use it on a machine with no display, or in CI -- use
        ``android_run_emulator_without_window``. Do not use it to check whether
        an emulator is already up: that is
        ``android_get_available_devices``, and starting an AVD twice fails.
        Do not use it to create an AVD; the name must already exist
        (``android_create_device``).

        Arguments
        ---------
        avd_name:
            The exact name of an AVD that already exists on this host, as listed
            by ``android_get_installed_emulators``. Case-sensitive; letters,
            digits, ``.``, ``_`` and ``-`` only. Not a serial, not a hardware
            profile.
        wait_for_boot:
            True (the default) blocks until the device is ready for input and
            comes back with ``booted: true``. False returns as soon as the
            emulator attaches to adb, with ``booted: false`` -- the device is
            then still coming up and will reject most commands for a while.
        cold_boot:
            True discards the saved snapshot and boots from scratch: roughly
            30-60 seconds slower, and the way to recover a device that a bad
            snapshot has left wedged. Defaults to False, which resumes from the
            snapshot when there is one.
        wipe_data:
            True resets userdata before booting, so the device comes up as if
            freshly created: installed apps and their data are gone. Defaults to
            False. Destructive to the AVD's contents, though not to the AVD.
        boot_timeout_seconds:
            How long to wait for the boot to complete, in seconds. Null (the
            default) uses the server's configured timeout. Raise it on a slow
            host rather than retrying a call that timed out; it only has an
            effect when ``wait_for_boot`` is True.

        Returns
        -------
        ``{"name": "Pixel_7", "device_id": "emulator-5554", "pid": 48213,
        "headless": false, "booted": true, "startup_duration_seconds": 42.7}``

        ``device_id`` is the handle every other device tool takes. ``booted``
        is false only when ``wait_for_boot`` was turned off.

        Errors
        ------
        ``EmulatorNotFound``: no AVD by that name. List them with
        ``android_get_installed_emulators`` and retry with a name from that
        list; do not guess a near spelling.
        ``EmulatorAlreadyRunning``: it is already up, and the serial is in the
        message -- use that instead of starting it again.
        ``EmulatorBootTimeout``: it launched but did not finish booting in time.
        Retry with a larger ``boot_timeout_seconds``, or ``cold_boot=True``.
        ``EmulatorStartFailed``: the emulator process refused to start; the
        message carries the tool's output. An unchanged retry fails the same way.
        ``BackendUnavailable``: no usable Android SDK on this host; report it.

        Example
        -------
        ``android_run_emulator(avd_name="Pixel_7", cold_boot=False)``
        -> ``{"name": "Pixel_7", "device_id": "emulator-5554", "pid": 48213,
        "headless": false, "booted": true, "startup_duration_seconds": 42.7}``
        """
        with domain_errors_as_tool_errors():
            result = await service.run_emulator(
                avd_name,
                wait_for_boot=wait_for_boot,
                cold_boot=cold_boot,
                wipe_data=wipe_data,
                boot_timeout_seconds=boot_timeout_seconds,
            )
        return render_started_emulator(result)

    @mcp.tool(name="android_run_emulator_without_window")
    async def run_emulator_without_window(
        avd_name: str,
        wait_for_boot: bool = True,
        cold_boot: bool = False,
        wipe_data: bool = False,
        boot_timeout_seconds: float | None = None,
    ) -> StartedEmulatorPayload:
        """Boot an existing AVD headless -- no window, no audio -- for automation.

        What it does
        ------------
        The same operation as ``android_run_emulator`` with the display, audio
        and boot animation switched off, which is what a CI machine or a
        headless host wants. Blocks until the device has booted by default; a
        first boot commonly takes 30-90 seconds. **The emulator outlives this
        call** and keeps running until ``android_stop_emulator`` stops it.

        When to use it
        --------------
        Use it as the default for automated work: running a test suite,
        installing an app, anything where nobody is looking at the screen. It is
        the only variant that works on a host with no display.

        When not to use it
        ------------------
        Do not use it when a human needs to watch the device, or when you are
        about to ask the user to look at something on screen -- that is
        ``android_run_emulator``. Screenshots still work headless; a visible
        window does not come back without restarting the emulator. As with the
        headed variant, do not start an AVD that is already running.

        Arguments
        ---------
        avd_name:
            The exact name of an existing AVD, as listed by
            ``android_get_installed_emulators``. Case-sensitive; letters,
            digits, ``.``, ``_`` and ``-`` only.
        wait_for_boot:
            True (the default) blocks until the device is ready and reports
            ``booted: true``. False returns as soon as it attaches to adb, with
            ``booted: false`` and a device that is not yet usable.
        cold_boot:
            True discards the saved snapshot and boots from scratch -- slower,
            and the way out of a wedged snapshot. Defaults to False.
        wipe_data:
            True resets userdata first: installed apps and their data are gone
            and the device comes up factory-fresh. Defaults to False.
        boot_timeout_seconds:
            Seconds to wait for the boot. Null (the default) uses the server's
            configured timeout. Only meaningful when ``wait_for_boot`` is True.

        Returns
        -------
        ``{"name": "Pixel_7", "device_id": "emulator-5554", "pid": 48219,
        "headless": true, "booted": true, "startup_duration_seconds": 38.1}``

        Identical in shape to ``android_run_emulator``, with ``headless: true``.

        Errors
        ------
        ``EmulatorNotFound``: no AVD by that name -- list them and retry with an
        exact name.
        ``EmulatorAlreadyRunning``: already up; use the serial in the message.
        ``EmulatorBootTimeout``: launched but never became ready. Retry with a
        larger ``boot_timeout_seconds`` or ``cold_boot=True``.
        ``EmulatorStartFailed``: the process refused to start; the message
        carries the tool's own output.
        ``BackendUnavailable``: no usable Android SDK on this host; report it.

        Example
        -------
        ``android_run_emulator_without_window(avd_name="Pixel_7", wait_for_boot=True)``
        -> ``{"name": "Pixel_7", "device_id": "emulator-5554", "pid": 48219,
        "headless": true, "booted": true, "startup_duration_seconds": 38.1}``
        """
        with domain_errors_as_tool_errors():
            result = await service.run_emulator_without_window(
                avd_name,
                wait_for_boot=wait_for_boot,
                cold_boot=cold_boot,
                wipe_data=wipe_data,
                boot_timeout_seconds=boot_timeout_seconds,
            )
        return render_started_emulator(result)

    @mcp.tool(name="android_stop_emulator")
    async def stop_emulator(
        device_id: str,
        wait_for_exit: bool = True,
        timeout_seconds: float | None = None,
    ) -> StoppedEmulatorPayload:
        """Shut down the running emulator attached as device_id.

        What it does
        ------------
        Asks the emulator on that serial to shut down and, by default, waits
        until the serial has left the device list. The AVD and everything on it
        survive: this stops a running device, it does not delete anything. Takes
        a few seconds.

        When to use it
        --------------
        Use it when you are finished with a device you started -- an emulator
        left running holds RAM and CPU on the host indefinitely. Also use it
        before ``android_delete_emulator`` on a running AVD, and to clear an
        ``EmulatorAlreadyRunning`` failure when you want a fresh boot.

        When not to use it
        ------------------
        Do not use it to reset a device's contents: a stop-and-start keeps the
        data, and ``wipe_data=True`` on a start tool is what resets it. Do not
        pass an AVD name here -- this tool takes a serial, and the AVD name is
        what ``android_delete_emulator`` takes.

        Arguments
        ---------
        device_id:
            The adb serial of a running emulator, such as ``emulator-5554``, as
            returned by a start tool or listed by
            ``android_get_available_devices``. Emulator serials only -- a
            physical device cannot be stopped this way.
        wait_for_exit:
            True (the default) polls until the serial is gone and reports
            ``stopped: true``. False returns immediately with ``stopped:
            false``; the device is then still going down, and starting the same
            AVD again before it has gone will fail.
        timeout_seconds:
            Seconds to wait for the serial to disappear. Null (the default) uses
            the server's configured timeout. Only meaningful when
            ``wait_for_exit`` is True.

        Returns
        -------
        ``{"device_id": "emulator-5554", "avd_name": "Pixel_7",
        "stopped": true, "duration_seconds": 3.4}``

        ``avd_name`` is null when the device could no longer be asked which AVD
        it was running.

        Errors
        ------
        ``InvalidDeviceId``: that is not an emulator serial. Serials look like
        ``emulator-5554``; take one from ``android_get_available_devices``.
        ``DeviceNotFound``: nothing is attached on that serial -- it may already
        have stopped. Check ``android_get_all_devices`` before treating it as a
        failure.
        ``EmulatorStopFailed``: the shutdown was requested but the device was
        still attached when time ran out; check the device list to see whether
        it has gone since.
        ``BackendUnavailable``: no usable Android SDK on this host; report it.

        Example
        -------
        ``android_stop_emulator(device_id="emulator-5554")``
        -> ``{"device_id": "emulator-5554", "avd_name": "Pixel_7", "stopped": true,
        "duration_seconds": 3.4}``
        """
        with domain_errors_as_tool_errors():
            result = await service.stop_emulator(
                device_id,
                wait_for_exit=wait_for_exit,
                timeout_seconds=timeout_seconds,
            )
        return render_stopped_emulator(result)

    # -- provisioning ------------------------------------------------------

    @mcp.tool(name="android_download_image")
    async def download_image(
        system_image: str,
        accept_licenses: bool = True,
        reinstall: bool = False,
    ) -> InstalledImagePayload:
        """Download and unpack an Android system image onto this host.

        What it does
        ------------
        Installs the named sdkmanager package into the host's SDK, accepting the
        licence prompts on the way. **This downloads over a gigabyte and can take
        many minutes** on a first install. When the image is already unpacked it
        returns almost immediately with ``already_installed: true`` and
        downloads nothing.

        When to use it
        --------------
        Use it only when ``android_create_device`` has failed with
        ``SystemImageNotInstalled``, or when you already know the image you need
        is absent. It is the prerequisite step for creating an AVD on an image
        the host has never had.

        When not to use it
        ------------------
        Do not call it speculatively before every ``android_create_device`` --
        the create call tells you when an image is missing, and this one is
        expensive. Do not use it to install anything but a system image; other
        SDK packages are not reachable from here. Avoid it entirely on a metered
        or offline host.

        Arguments
        ---------
        system_image:
            The full sdkmanager package id, e.g.
            ``system-images;android-34;google_apis;x86_64``. Semicolon-separated
            and exact -- the API level, the tag (``google_apis``,
            ``google_apis_playstore``, ``default``) and the ABI (``x86_64``,
            ``arm64-v8a``) all matter. The ABI must match the host's CPU or the
            emulator will be unusably slow.
        accept_licenses:
            True (the default) answers the SDK licence prompts automatically.
            Set it to False only if you intend the install to stop at an
            unaccepted licence -- the installer otherwise blocks forever waiting
            on a prompt nobody can answer.
        reinstall:
            True runs the download even when the image is already unpacked,
            which is how a corrupted image gets repaired. Defaults to False,
            which makes an already-installed image a fast no-op.

        Returns
        -------
        ``{"system_image": "system-images;android-34;google_apis;x86_64",
        "path": "/opt/android-sdk/system-images/android-34/google_apis/x86_64",
        "already_installed": false, "duration_seconds": 184.6}``

        ``already_installed: true`` means nothing was downloaded and
        ``duration_seconds`` is near zero.

        Errors
        ------
        ``InvalidSystemImage``: the package id is malformed. Fix the id rather
        than retrying -- it must have the ``system-images;...`` shape.
        ``BackendFailure``: sdkmanager was reached and refused; its output is in
        the message. A blocked licence or no disk space looks like this.
        ``BackendUnavailable``: this host has no usable Android SDK at all;
        report it rather than retrying.

        Example
        -------
        ``android_download_image(system_image="system-images;android-34;google_apis;x86_64")``
        -> ``{"system_image": "system-images;android-34;google_apis;x86_64",
        "path": "/opt/android-sdk/system-images/android-34/google_apis/x86_64",
        "already_installed": true, "duration_seconds": 0.1}``
        """
        with domain_errors_as_tool_errors():
            result = await service.download_image(
                system_image,
                accept_licenses=accept_licenses,
                reinstall=reinstall,
            )
        return render_installed_image(result)

    @mcp.tool(name="android_create_device")
    async def create_device(
        avd_name: str,
        system_image: str,
        device: str,
        sdcard_size: str | None = None,
        abi: str | None = None,
        replace_existing: bool = False,
    ) -> CreatedEmulatorPayload:
        """Define a new AVD on this host from an installed system image.

        What it does
        ------------
        Creates a virtual device definition on disk -- a name, a hardware
        profile, and a system image to boot. Takes a second or two. **It does not
        start anything**: the AVD exists and is stopped, and
        ``android_run_emulator`` is what boots it. The system image must already
        be installed; this call never downloads one.

        When to use it
        --------------
        Use it when ``android_get_installed_emulators`` has no AVD suited to
        what you need -- a different API level, a different form factor. Check
        that list first: reusing an existing AVD is faster and destroys nothing.

        When not to use it
        ------------------
        Do not use it to boot a device, and do not use it to fix a broken AVD --
        an AVD listed as unloadable is repaired by deleting and recreating it,
        deliberately, not by creating over it by accident. Be careful with
        ``replace_existing``: it destroys the existing AVD's data irreversibly.

        Arguments
        ---------
        avd_name:
            The name for the new AVD. Letters, digits, ``.``, ``_`` and ``-``
            only -- no spaces. This is the name every later call uses, so make
            it descriptive (``Pixel_7_API_34``).
        system_image:
            Full sdkmanager package id of an **already installed** image, e.g.
            ``system-images;android-34;google_apis;x86_64``. If it is not
            installed the call fails with ``SystemImageNotInstalled``; install
            it with ``android_download_image`` and retry.
        device:
            The hardware profile to base it on, e.g. ``pixel_6`` or ``pixel_7``
            -- screen size, density and RAM. Use a real profile id; an invented
            one fails with ``UnknownDeviceProfile``.
        sdcard_size:
            Size of a virtual SD card, as a string with a unit: ``"512M"``,
            ``"2G"``. Null (the default) leaves the backend's default, which is
            usually no SD card. Only needed when the app under test writes to
            external storage.
        abi:
            The ABI to use when the image ships more than one, e.g. ``x86_64``
            or ``arm64-v8a``. Null (the default) lets the backend choose, which
            is right almost always. An ABI that does not match the host's CPU
            produces an emulator too slow to use.
        replace_existing:
            True overwrites an AVD that already has this name, **destroying its
            data irreversibly**. Defaults to False, which fails with
            ``EmulatorAlreadyExists`` instead -- prefer a different name unless
            the user has asked for the old one to be replaced.

        Returns
        -------
        ``{"name": "Pixel_7_API_34", "path": "/home/u/.android/avd/Pixel_7_API_34.avd",
        "config_path": "/home/u/.android/avd/Pixel_7_API_34.avd/config.ini",
        "system_image": "system-images;android-34;google_apis;x86_64",
        "device": "pixel_7", "replaced_existing": false, "duration_seconds": 2.1}``

        The AVD is defined and stopped; pass ``name`` to a start tool to boot it.

        Errors
        ------
        ``EmulatorAlreadyExists``: that name is taken. Pick another, or pass
        ``replace_existing=True`` to overwrite and lose the old one's data.
        ``SystemImageNotInstalled``: install it with ``android_download_image``,
        then retry this exact call.
        ``UnknownDeviceProfile``: no such hardware profile on this host. Use a
        common one such as ``pixel_6``; do not invent profile names.
        ``InvalidEmulatorName``: the name uses characters AVDs cannot hold.
        Rename it to letters, digits, dots, underscores and hyphens.
        ``InvalidSystemImage``: the package id is malformed; fix it rather than
        retrying.
        ``BackendUnavailable``: no usable Android SDK on this host; report it.

        Example
        -------
        ``android_create_device(avd_name="Pixel_7_API_34",
        system_image="system-images;android-34;google_apis;x86_64", device="pixel_7")``
        -> ``{"name": "Pixel_7_API_34", "path": "...", "config_path": "...",
        "system_image": "system-images;android-34;google_apis;x86_64",
        "device": "pixel_7", "replaced_existing": false, "duration_seconds": 2.1}``
        """
        with domain_errors_as_tool_errors():
            result = await service.create_device(
                avd_name,
                system_image,
                device,
                sdcard_size=sdcard_size,
                abi=abi,
                replace_existing=replace_existing,
            )
        return render_created_emulator(result)

    @mcp.tool(name="android_delete_emulator")
    async def delete_emulator(
        avd_name: str, stop_if_running: bool = False
    ) -> DeletedEmulatorPayload:
        """Delete an AVD and everything on it. Irreversible.

        What it does
        ------------
        Removes the AVD's definition and its whole payload from disk: snapshots,
        installed apps, userdata, SD card. **Nothing here can be undone**, and
        there is no recycle bin -- recreating the AVD afterwards gives you a
        factory-fresh device, not the old one.

        When to use it
        --------------
        Use it when the user has asked for an AVD to be removed, or to clear an
        AVD that ``android_get_installed_emulators`` reports as unloadable and
        that you are about to recreate. Confirm the exact name against that list
        first -- names are case-sensitive and a near-miss deletes the wrong
        device.

        When not to use it
        ------------------
        Do not use it to free up a device you are finished with: that is
        ``android_stop_emulator``, which leaves the AVD intact. Do not use it to
        reset a device's contents -- ``wipe_data=True`` on a start tool does
        that without destroying the AVD. Do not call it on an AVD you did not
        create unless the user asked for that specific name by name.

        Arguments
        ---------
        avd_name:
            The exact name of the AVD to destroy, as listed by
            ``android_get_installed_emulators``. Case-sensitive. There is no
            partial matching and no undo, so pass a name you have just seen in
            that list rather than one you remember.
        stop_if_running:
            True shuts the AVD down first if it is running, then deletes it.
            Defaults to False, which fails with ``EmulatorInUse`` instead -- the
            safe default, because it makes you say that you know something is
            using the device.

        Returns
        -------
        ``{"name": "Pixel_7_API_34",
        "deleted_path": "/home/u/.android/avd/Pixel_7_API_34.avd",
        "stopped_first": false, "duration_seconds": 0.9}``

        ``stopped_first: true`` means the AVD was running and was shut down as
        part of this call.

        Errors
        ------
        ``EmulatorNotFound``: no AVD by that name -- it may already be gone.
        List them before retrying; do not guess a near spelling.
        ``EmulatorInUse``: it is running. Stop it with
        ``android_stop_emulator``, or pass ``stop_if_running=True``.
        ``InvalidEmulatorName``: the name is not one an AVD could have; check it
        against the list rather than retrying.
        ``BackendUnavailable``: no usable Android SDK on this host; report it.

        Example
        -------
        ``android_delete_emulator(avd_name="Pixel_7_API_34", stop_if_running=True)``
        -> ``{"name": "Pixel_7_API_34", "deleted_path": "/home/u/.android/avd/Pixel_7_API_34.avd",
        "stopped_first": true, "duration_seconds": 4.2}``
        """
        with domain_errors_as_tool_errors():
            result = await service.delete_emulator(avd_name, stop_if_running=stop_if_running)
        return render_deleted_emulator(result)
