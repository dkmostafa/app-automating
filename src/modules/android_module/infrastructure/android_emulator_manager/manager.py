"""The Android emulator manager: the component's public operations.

:class:`AndroidEmulatorManager` is the adapter that satisfies the Android
module's three ports -- :class:`~...domain.ports.DeviceCatalog`,
:class:`~...domain.ports.EmulatorLifecycle` and
:class:`~...domain.ports.SystemImageInstaller`. Each public method is ``async``,
takes exactly one frozen request dataclass from the domain, returns exactly one
frozen result dataclass, and raises only :class:`~.errors.AndroidEmulatorError`
subclasses -- which are themselves domain errors, so a use case never has to
name an Android SDK concept to catch one.

It owns no I/O of its own. Everything it touches arrives through the
constructor (Rule 0 §4): the SDK settings, the :class:`~.process.CommandRunner`
that reaches the host toolchain, and the :class:`~.filesystem.AvdStore` that
reads the AVD home. Assembling those three is the composition root's job, in
``android_module.application.composition``.

What is left here is exactly the thing a manager should be: the order in which
those collaborators are called, and what to do when one of them says no.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, replace

from ...domain.models import (
    AndroidDevice,
    CreateEmulatorRequest,
    CreateEmulatorResult,
    DeleteEmulatorRequest,
    DeleteEmulatorResult,
    InstallSystemImageRequest,
    InstallSystemImageResult,
    ListDevicesRequest,
    ListDevicesResult,
    ListEmulatorsRequest,
    ListEmulatorsResult,
    StartEmulatorRequest,
    StartEmulatorResult,
    StopEmulatorRequest,
    StopEmulatorResult,
)
from .config import AndroidSdkConfig
from .errors import (
    AndroidEmulatorError,
    AvdAlreadyExistsError,
    AvdInUseError,
    AvdNotFoundError,
    CommandFailedError,
    CommandTimeoutError,
    DeviceNotFoundError,
    EmulatorAlreadyRunningError,
    EmulatorBootTimeoutError,
    EmulatorStartError,
    EmulatorStopTimeoutError,
    NotAnEmulatorError,
    SdkRootNotConfiguredError,
    SystemImageNotInstalledError,
    UnknownDeviceProfileError,
)
from .filesystem import AvdStore
from .parsing import (
    classify_create_failure,
    classify_install_failure,
    is_emulator_serial,
    parse_adb_devices,
    parse_avd_list,
    system_image_path,
    validate_avd_name,
    validate_system_image_id,
)
from .process import CommandRunner

__all__ = ["AndroidEmulatorManager"]


@dataclass(slots=True)
class _LaunchedEmulator:
    """An emulator process this manager started, kept so it can be reaped."""

    name: str
    device_id: str
    process: asyncio.subprocess.Process
    log: deque[str]
    drains: tuple[asyncio.Task[None], ...]


class AndroidEmulatorManager:
    """Drives adb, emulator, avdmanager and sdkmanager on the local host.

    Satisfies ``DeviceCatalog``, ``EmulatorLifecycle`` and
    ``SystemImageInstaller``. Build it through
    ``android_module.application.composition.build_android_emulator_manager``
    rather than by hand -- the collaborators below are required precisely so
    that nothing reaches for an ambient default.
    """

    def __init__(
        self,
        config: AndroidSdkConfig,
        runner: CommandRunner,
        avds: AvdStore,
    ) -> None:
        self._config = config
        self._runner = runner
        self._avds = avds
        self._launched: dict[str, _LaunchedEmulator] = {}

    # -- introspection -----------------------------------------------------

    @property
    def config(self) -> AndroidSdkConfig:
        return self._config

    @property
    def sdk_root(self):  # type: ignore[no-untyped-def]
        return self._config.sdk_root

    @property
    def avd_home(self):  # type: ignore[no-untyped-def]
        return self._avds.home

    def tool_path(self, tool: str) -> str:
        """Absolute path of a host tool, raising if it cannot be resolved."""
        return self._runner.tool_path(tool)

    # -- DeviceCatalog -----------------------------------------------------

    async def list_devices(self, request: ListDevicesRequest) -> ListDevicesResult:
        """List currently connected Android devices and their state."""
        result = await self._runner.run_tool(
            "adb", "devices", "-l", timeout=self._config.adb_timeout_seconds
        )
        if result.returncode != 0:
            raise CommandFailedError(result)

        devices = parse_adb_devices(result.stdout)
        if not request.include_unavailable:
            devices = [device for device in devices if device.is_available]

        if request.resolve_avd_names and devices:
            names = await asyncio.gather(*(self._maybe_avd_name(device) for device in devices))
            devices = [
                replace(device, avd_name=name) if name else device
                for device, name in zip(devices, names, strict=True)
            ]
        return ListDevicesResult(devices=tuple(devices))

    async def list_emulators(self, request: ListEmulatorsRequest) -> ListEmulatorsResult:
        """List installed Android Virtual Devices (AVDs)."""
        result = await self._runner.run_tool(
            "avdmanager", "list", "avd", timeout=self._config.avdmanager_timeout_seconds
        )
        if result.returncode != 0:
            raise CommandFailedError(result)

        avds = parse_avd_list(result.output)
        if not request.include_unloadable:
            avds = [avd for avd in avds if avd.loadable]
        return ListEmulatorsResult(emulators=tuple(avds))

    # -- SystemImageInstaller ----------------------------------------------

    async def install_system_image(
        self, request: InstallSystemImageRequest
    ) -> InstallSystemImageResult:
        """Download an Android system image."""
        started = time.monotonic()
        validate_system_image_id(request.system_image)
        sdk_root = self._require_sdk_root("install_system_image")
        target = system_image_path(sdk_root, request.system_image)

        if target.is_dir() and not request.reinstall:
            return InstallSystemImageResult(
                system_image=request.system_image,
                path=target,
                already_installed=True,
                duration_seconds=time.monotonic() - started,
            )

        # sdkmanager prompts once per unaccepted licence and blocks on a closed
        # stdin, so feed it a stream of confirmations rather than one.
        stdin_data = b"y\n" * 64 if request.accept_licenses else None
        result = await self._runner.run_tool(
            "sdkmanager",
            f"--sdk_root={sdk_root}",
            "--install",
            request.system_image,
            timeout=self._config.sdkmanager_timeout_seconds,
            stdin_data=stdin_data,
        )
        if result.returncode != 0:
            raise classify_install_failure(request.system_image, result)
        if not target.is_dir():
            raise SystemImageNotInstalledError(request.system_image, target)

        return InstallSystemImageResult(
            system_image=request.system_image,
            path=target,
            already_installed=False,
            duration_seconds=time.monotonic() - started,
        )

    # -- EmulatorLifecycle -------------------------------------------------

    async def create_emulator(self, request: CreateEmulatorRequest) -> CreateEmulatorResult:
        """Create a new AVD."""
        started = time.monotonic()
        validate_avd_name(request.name)
        validate_system_image_id(request.system_image)
        if not request.device.strip():
            raise UnknownDeviceProfileError(request.device)

        existed = self._avds.exists(request.name)
        if existed and not request.force:
            raise AvdAlreadyExistsError(request.name)
        if existed:
            running = await self._running_device_for(request.name)
            if running is not None:
                raise AvdInUseError(request.name, running.device_id)

        if self._config.sdk_root is not None:
            image_path = system_image_path(self._config.sdk_root, request.system_image)
            if not image_path.is_dir():
                raise SystemImageNotInstalledError(request.system_image, image_path)

        argv = [
            "create",
            "avd",
            "-n",
            request.name,
            "-k",
            request.system_image,
            "-d",
            request.device,
        ]
        if request.abi:
            argv += ["-b", request.abi]
        if request.sdcard_size:
            argv += ["-c", request.sdcard_size]
        if request.force:
            argv.append("--force")

        # avdmanager asks whether to create a custom hardware profile and hangs
        # on an empty stdin.
        result = await self._runner.run_tool(
            "avdmanager",
            *argv,
            timeout=self._config.avdmanager_timeout_seconds,
            stdin_data=b"no\n",
        )
        if result.returncode != 0:
            raise classify_create_failure(request, result)

        ini_path = self._avds.ini_path(request.name)
        if not ini_path.exists():
            raise AndroidEmulatorError(
                f"avdmanager reported success but {ini_path} was not created: "
                f"{result.first_error_line()}"
            )
        avd_dir = self._avds.directory(request.name)
        return CreateEmulatorResult(
            name=request.name,
            path=avd_dir,
            config_path=avd_dir / "config.ini",
            system_image=request.system_image,
            device=request.device,
            replaced_existing=existed,
            duration_seconds=time.monotonic() - started,
        )

    async def start_emulator(self, request: StartEmulatorRequest) -> StartEmulatorResult:
        """Start an emulator with a visible window."""
        started = time.monotonic()
        validate_avd_name(request.name)
        self._avds.require(request.name)

        attached = await self.list_devices(ListDevicesRequest())
        already = attached.by_avd_name(request.name)
        if already is not None:
            raise EmulatorAlreadyRunningError(request.name, already.device_id)
        known_serials = {device.device_id for device in attached.devices}

        argv = self._emulator_argv(request)
        timeout = (
            request.boot_timeout_seconds
            if request.boot_timeout_seconds is not None
            else self._config.emulator_boot_timeout_seconds
        )
        poll = (
            request.poll_interval_seconds
            if request.poll_interval_seconds is not None
            else self._config.boot_poll_interval_seconds
        )
        deadline = time.monotonic() + timeout

        proc = await self._runner.spawn_tool(argv)
        log: deque[str] = deque(maxlen=400)
        drains = self._runner.drain_into(proc, log)
        try:
            device_id = await self._await_serial(
                request.name, proc, known_serials, deadline, poll, log, timeout
            )
            booted = False
            if request.wait_for_boot:
                await self._await_boot(request.name, device_id, proc, deadline, poll, log, timeout)
                booted = True
        except BaseException:
            await self._runner.terminate(proc)
            for task in drains:
                task.cancel()
            raise

        self._launched[device_id] = _LaunchedEmulator(
            name=request.name, device_id=device_id, process=proc, log=log, drains=drains
        )
        return StartEmulatorResult(
            name=request.name,
            device_id=device_id,
            pid=proc.pid,
            headless=request.headless,
            booted=booted,
            startup_duration_seconds=time.monotonic() - started,
            launch_argv=tuple(argv),
        )

    async def start_emulator_headless(self, request: StartEmulatorRequest) -> StartEmulatorResult:
        """Start an emulator in headless mode for automation."""
        return await self.start_emulator(replace(request, headless=True))

    async def stop_emulator(self, request: StopEmulatorRequest) -> StopEmulatorResult:
        """Stop a running emulator."""
        started = time.monotonic()
        if not is_emulator_serial(request.device_id):
            raise NotAnEmulatorError(request.device_id)

        attached = await self.list_devices(ListDevicesRequest())
        device = attached.by_device_id(request.device_id)
        if device is None:
            raise DeviceNotFoundError(
                request.device_id, tuple(d.device_id for d in attached.devices)
            )

        result = await self._runner.run_tool(
            "adb",
            "-s",
            request.device_id,
            "emu",
            "kill",
            timeout=self._config.adb_timeout_seconds,
        )
        if result.returncode != 0 and "device offline" not in result.output.lower():
            raise CommandFailedError(result)

        timeout = (
            request.timeout_seconds
            if request.timeout_seconds is not None
            else self._config.emulator_stop_timeout_seconds
        )
        stopped = False
        if request.wait_for_exit:
            deadline = time.monotonic() + timeout
            while True:
                current = await self.list_devices(ListDevicesRequest(resolve_avd_names=False))
                if current.by_device_id(request.device_id) is None:
                    stopped = True
                    break
                if time.monotonic() >= deadline:
                    break
                await asyncio.sleep(min(self._config.boot_poll_interval_seconds, 1.0))
            if not stopped:
                raise EmulatorStopTimeoutError(request.device_id, timeout)

        await self._reap(request.device_id)
        return StopEmulatorResult(
            device_id=request.device_id,
            avd_name=device.avd_name,
            stopped=stopped,
            duration_seconds=time.monotonic() - started,
        )

    async def delete_emulator(self, request: DeleteEmulatorRequest) -> DeleteEmulatorResult:
        """Delete an AVD."""
        started = time.monotonic()
        validate_avd_name(request.name)
        self._avds.require(request.name)

        avd_dir = self._avds.directory(request.name)
        running = await self._running_device_for(request.name)
        stopped_first = False
        if running is not None:
            if not request.stop_if_running:
                raise AvdInUseError(request.name, running.device_id)
            await self.stop_emulator(StopEmulatorRequest(device_id=running.device_id))
            stopped_first = True

        result = await self._runner.run_tool(
            "avdmanager",
            "delete",
            "avd",
            "-n",
            request.name,
            timeout=self._config.avdmanager_timeout_seconds,
        )
        if result.returncode != 0:
            if "no android virtual device" in result.output.lower():
                raise AvdNotFoundError(request.name, self._avds.home)
            raise CommandFailedError(result)
        if self._avds.exists(request.name):
            raise AndroidEmulatorError(
                f"avdmanager reported success but {self._avds.ini_path(request.name)} still exists"
            )

        return DeleteEmulatorResult(
            name=request.name,
            deleted_path=avd_dir,
            stopped_first=stopped_first,
            duration_seconds=time.monotonic() - started,
        )

    # -- shutdown ----------------------------------------------------------

    async def aclose(self) -> None:
        """Kill every emulator this manager launched and still owns."""
        for device_id in list(self._launched):
            await self._reap(device_id, force=True)

    async def __aenter__(self) -> AndroidEmulatorManager:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    # -- internals ---------------------------------------------------------

    def _require_sdk_root(self, operation: str):  # type: ignore[no-untyped-def]
        if self._config.sdk_root is None:
            raise SdkRootNotConfiguredError(operation)
        return self._config.sdk_root

    def _emulator_argv(self, request: StartEmulatorRequest) -> list[str]:
        argv = [self._runner.tool_path("emulator"), "-avd", request.name]
        argv.extend(self._config.emulator_launch_args)
        if request.headless:
            argv.extend(self._config.headless_args)
        if request.cold_boot:
            argv.append("-no-snapshot-load")
        if request.wipe_data:
            argv.append("-wipe-data")
        argv.extend(request.extra_args)
        return argv

    async def _maybe_avd_name(self, device: AndroidDevice) -> str | None:
        """Resolve the AVD name for an emulator that is ready to answer."""
        if device.is_emulator and device.is_available:
            return await self._query_avd_name(device.device_id)
        return None

    async def _query_avd_name(self, device_id: str) -> str | None:
        """``adb emu avd name`` -- the only serial-to-AVD mapping adb offers.

        Best effort: a device that is still booting answers with an error, and
        that is not a failure of the caller's operation.
        """
        try:
            result = await self._runner.run_tool(
                "adb",
                "-s",
                device_id,
                "emu",
                "avd",
                "name",
                timeout=self._config.adb_timeout_seconds,
            )
        except AndroidEmulatorError:
            return None
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            candidate = line.strip()
            if candidate and candidate != "OK" and not candidate.startswith("KO"):
                return candidate
        return None

    async def _running_device_for(self, name: str) -> AndroidDevice | None:
        devices = await self.list_devices(ListDevicesRequest())
        return devices.by_avd_name(name)

    async def _await_serial(
        self,
        name: str,
        proc: asyncio.subprocess.Process,
        known_serials: set[str],
        deadline: float,
        poll: float,
        log: deque[str],
        timeout: float,
    ) -> str:
        """Wait for the launched emulator to show up in ``adb devices``.

        Prefers a serial whose ``emu avd name`` matches; falls back to "exactly
        one emulator appeared that was not there before".
        """
        while True:
            if proc.returncode is not None:
                raise EmulatorStartError(name, proc.returncode, log)

            devices = await self.list_devices(ListDevicesRequest())
            fresh = [
                device for device in devices.emulators if device.device_id not in known_serials
            ]
            for device in fresh:
                if device.avd_name == name:
                    return device.device_id
            if len(fresh) == 1:
                return fresh[0].device_id

            if time.monotonic() >= deadline:
                raise EmulatorBootTimeoutError(name, None, timeout, log)
            await asyncio.sleep(poll)

    async def _await_boot(
        self,
        name: str,
        device_id: str,
        proc: asyncio.subprocess.Process,
        deadline: float,
        poll: float,
        log: deque[str],
        timeout: float,
    ) -> None:
        while True:
            if proc.returncode is not None:
                raise EmulatorStartError(name, proc.returncode, log)
            try:
                result = await self._runner.run_tool(
                    "adb",
                    "-s",
                    device_id,
                    "shell",
                    "getprop",
                    "sys.boot_completed",
                    timeout=self._config.adb_timeout_seconds,
                )
            except CommandTimeoutError:
                result = None
            # An unbooted device answers with "device offline" or empty output;
            # only "1" means the boot finished.
            if result is not None and result.returncode == 0 and result.stdout.strip() == "1":
                return
            if time.monotonic() >= deadline:
                raise EmulatorBootTimeoutError(name, device_id, timeout, log)
            await asyncio.sleep(poll)

    async def _reap(self, device_id: str, *, force: bool = False) -> None:
        """Drop our record of a launched emulator, killing it if it is still up."""
        launched = self._launched.pop(device_id, None)
        if launched is None:
            return
        if force or launched.process.returncode is None:
            await self._runner.terminate(launched.process)
        for task in launched.drains:
            task.cancel()
