"""Fixtures for the integration tests, and the guard that protects the developer.

Rule 1 §6: this layer's tests run against the real host -- a real ``appium``, a
real server process, a real device. That licence comes with obligations, and
this file carries most of them.

* **Skip, do not fail, on a host that cannot run the test.** No Node, no Appium,
  no driver, no device -- each is a ``pytest.skip`` with a reason that says how
  to fix it, not a red test on a machine that was never going to work.
* **Namespace and clean up.** The tests here start Appium servers on a port
  nothing else uses and open sessions on a real device; the fixtures below own
  taking both down, including after a failure.
* **Budget the slow paths.** A session costs 5-30 seconds, so the module-scoped
  ``session`` fixture opens exactly one and every test that needs a device
  shares it. Tests must leave the device in a state the next test can use.

The autouse guard hangs off this directory rather than off ``tests/`` on
purpose: an autouse fixture that depends on a real Appium would otherwise skip
the pure unit tests too, on exactly the machines where they are the only thing
that can run.
"""

from __future__ import annotations

import asyncio
import dataclasses
import shutil
import socket
from collections.abc import AsyncIterator, Iterator

import pytest

from app_automating.modules.appium_module.application.di import build_appium_device_manager
from app_automating.modules.appium_module.domain.models import (
    CheckEnvironmentRequest,
    EndSessionRequest,
    ListSessionsRequest,
    StartSessionRequest,
)
from app_automating.modules.appium_module.infrastructure.appium_device_manager import (
    AppiumConfig,
    AppiumDeviceManager,
    AppiumServer,
    CommandRunner,
)

#: A port well away from Appium's default, so a test never collides with a
#: server the developer is running in a terminal, and never kills one.
TEST_SERVER_PORT = 4788


def _free_port() -> int:
    """A port nothing is listening on, for a test that needs its own server."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def appium_installed() -> None:
    """Skip the whole file on a host with no Appium CLI."""
    if shutil.which("appium") is None:
        pytest.skip("no 'appium' on PATH; install it with: npm install -g appium")


@pytest.fixture(scope="session")
def node_installed() -> None:
    if shutil.which("node") is None:
        pytest.skip("no 'node' on PATH; Appium is a Node program and needs it")


@pytest.fixture
def config(appium_installed: None) -> AppiumConfig:
    """A real config pointed at a private port, built from the real environment.

    Reading the environment is deliberate: the point of these tests is the host
    as it actually is. Only the port is overridden, so the suite cannot touch a
    server the developer started.
    """
    return dataclasses.replace(
        AppiumConfig.from_environment(),
        server_port=TEST_SERVER_PORT,
    )


@pytest.fixture
def runner(config: AppiumConfig) -> CommandRunner:
    return CommandRunner(config)


@pytest.fixture
async def server(config: AppiumConfig, runner: CommandRunner) -> AsyncIterator[AppiumServer]:
    """A server object on a free port, guaranteed dead again afterwards.

    A fresh port per test rather than a shared one: a test that leaves a server
    up would otherwise make the next one pass for the wrong reason.
    """
    scoped = dataclasses.replace(config, server_port=_free_port())
    instance = AppiumServer(scoped, CommandRunner(scoped))
    try:
        yield instance
    finally:
        await instance.aclose()


@pytest.fixture
async def manager(config: AppiumConfig) -> AsyncIterator[AppiumDeviceManager]:
    """A real manager on a private port, closed on every exit path.

    Built through the composition root so these tests exercise the same wiring
    the server uses, rather than a hand-assembled object that could drift.
    """
    instance = build_appium_device_manager(dataclasses.replace(config, server_port=_free_port()))
    try:
        yield instance
    finally:
        await instance.aclose()


@pytest.fixture
async def ready_host(manager: AppiumDeviceManager) -> None:
    """Skip unless this host could actually start a session."""
    status = await manager.check_environment(CheckEnvironmentRequest(probe_server=False))
    if not status.ready:
        pytest.skip(
            f"host is not ready for Appium: missing tools {status.missing or 'none'}, "
            f"drivers {status.drivers or 'none'}. Install the driver with: "
            "appium driver install uiautomator2"
        )


@pytest.fixture
def device_id() -> str:
    """The serial of an attached, booted Android device, or a skip.

    Read with ``adb`` rather than through the Android module: this suite must
    not depend on another module's code to decide whether it can run.
    """
    adb = shutil.which("adb")
    if adb is None:
        pytest.skip("no 'adb' on PATH; cannot tell whether a device is attached")

    completed = asyncio.run(_adb_devices(adb))
    if not completed:
        pytest.skip(
            "no booted Android device attached. Start one with the Android module's "
            "android_run_emulator, or plug in a phone with USB debugging enabled."
        )
    return completed[0]


async def _adb_devices(adb: str) -> list[str]:
    process = await asyncio.create_subprocess_exec(
        adb, "devices", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    stdout, _ = await process.communicate()
    serials = []
    for line in stdout.decode("utf-8", "replace").splitlines()[1:]:
        parts = line.split()
        if len(parts) == 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials


@pytest.fixture
async def session(
    manager: AppiumDeviceManager, ready_host: None, device_id: str
) -> AsyncIterator[str]:
    """One real session on a real device, shared by every test that needs one.

    Rule 1 §6: budget the slow paths. Creating a session costs 5-30 seconds, so
    it happens once here rather than once per test. A test that uses it must
    leave the device usable for the next one.
    """
    result = await manager.start_session(StartSessionRequest(device_id=device_id))
    try:
        yield result.session.session_id
    finally:
        with_open = await manager.list_sessions(ListSessionsRequest())
        if result.session.session_id in with_open.session_ids:
            await manager.end_session(EndSessionRequest(session_id=result.session.session_id))


@pytest.fixture(scope="session", autouse=True)
def guard_the_developers_machine() -> Iterator[None]:
    """Fail the run if the suite left an Appium server of its own behind.

    The suite starts servers on ephemeral ports and on ``TEST_SERVER_PORT``, and
    every fixture above is responsible for killing what it started. This is the
    backstop that turns a leaked process into a red run rather than a mystery
    listener the developer finds next week.
    """
    yield
    leaked = _is_listening(TEST_SERVER_PORT)
    assert not leaked, (
        f"the suite left an Appium server listening on {TEST_SERVER_PORT}. "
        "A fixture failed to clean up; kill it before trusting the next run."
    )


def _is_listening(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0
