"""The Appium server process, launched and killed for real.

Rule 1 §6: a real ``appium`` binary, a real listening socket, a real kill. Each
test that needs a server gets its own ephemeral port from the ``server`` fixture,
so the suite can never adopt -- or kill -- a server the developer is running.

These are the slowest tests in the module: a real server takes a few seconds to
come up. They are kept few and deliberate for that reason.
"""

from __future__ import annotations

import asyncio
import dataclasses
import socket

import pytest

from modules.appium_module.infrastructure.appium_device_manager import (
    AppiumServer,
    CommandRunner,
    ServerNotAnsweringError,
    ServerNotReadyError,
)

pytestmark = pytest.mark.integration


def _listening(url: str) -> bool:
    host, _, port = url.removeprefix("http://").partition(":")
    with socket.socket() as sock:
        sock.settimeout(1.0)
        return sock.connect_ex((host, int(port.split("/")[0]))) == 0


# -- probing ---------------------------------------------------------------


async def test_probing_a_port_with_nothing_on_it_is_an_answer_not_an_error(
    server: AppiumServer,
) -> None:
    """ "Nothing is listening" is the normal case before the first session, and
    must never raise -- `check_environment` reports it as data."""
    status = await server.probe()

    assert status.running is False
    assert status.managed is False
    assert status.url == server.url


async def test_probing_is_repeatable_and_holds_no_state(server: AppiumServer) -> None:
    first = await server.probe()
    second = await server.probe()

    assert (first.running, second.running) == (False, False)


# -- launching -------------------------------------------------------------


async def test_a_launched_server_actually_serves_before_the_call_returns(
    server: AppiumServer,
) -> None:
    """The contract of `ensure_running`: not "the process exists" but "it answers"."""
    status, started_here = await server.ensure_running()

    assert started_here is True
    assert status.running is True
    assert _listening(server.url)
    assert server.managed is True


async def test_a_second_call_reuses_the_running_server_rather_than_starting_another(
    server: AppiumServer,
) -> None:
    """Idempotent: two sessions starting at once must not race into two servers
    on one port."""
    await server.ensure_running()

    _, started_here = await server.ensure_running()

    assert started_here is False


async def test_concurrent_callers_get_one_server_between_them(server: AppiumServer) -> None:
    """The lock, exercised for real: five callers, one launch."""
    results = await asyncio.gather(*(server.ensure_running() for _ in range(5)))

    launches = [started for _, started in results if started]
    assert len(launches) == 1
    assert all(status.running for status, _ in results)


async def test_a_server_we_started_is_ours_to_kill(server: AppiumServer) -> None:
    await server.ensure_running()
    assert _listening(server.url)

    await server.aclose()

    await asyncio.sleep(0.5)
    assert not _listening(server.url)
    assert server.managed is False


async def test_closing_a_server_that_was_never_started_is_a_no_op(
    server: AppiumServer,
) -> None:
    """Teardown runs on every path, including the one where nothing was launched."""
    await server.aclose()
    await server.aclose()

    assert server.managed is False


async def test_a_server_we_merely_connected_to_is_not_ours_to_kill(
    server: AppiumServer,
) -> None:
    """The rule that protects a developer's own terminal: `managed` is False for
    a server this process did not start, and `aclose` then leaves it alone."""
    await server.ensure_running()
    borrowed = AppiumServer(server.config, CommandRunner(server.config))

    status, started_here = await borrowed.ensure_running()
    await borrowed.aclose()

    assert started_here is False
    assert status.managed is False
    assert _listening(server.url), "aclose killed a server it did not start"


# -- the failure paths, for real ------------------------------------------


async def test_a_timeout_that_expires_before_the_server_can_answer_raises(
    server: AppiumServer,
) -> None:
    """A real 1ms deadline against a real server launch (Rule 1 §6), and the
    error carries the log tail because the process is gone by then."""
    impatient = AppiumServer(
        dataclasses.replace(server.config, server_start_timeout_seconds=0.001),
        CommandRunner(server.config),
    )

    try:
        with pytest.raises(ServerNotReadyError) as excinfo:
            await impatient.ensure_running()
        assert excinfo.value.timeout_seconds == 0.001
        assert excinfo.value.url == impatient.url
    finally:
        await impatient.aclose()


async def test_manage_server_off_refuses_rather_than_launching(server: AppiumServer) -> None:
    """The whole difference between the two modes, and the error says which one
    is in force so the remedy is obvious."""
    unmanaged = AppiumServer(
        dataclasses.replace(server.config, manage_server=False),
        CommandRunner(server.config),
    )

    with pytest.raises(ServerNotAnsweringError) as excinfo:
        await unmanaged.ensure_running()

    assert excinfo.value.url == unmanaged.url
    assert "manage_server" in str(excinfo.value)
    assert not _listening(unmanaged.url), "manage_server=False started a server anyway"


async def test_a_binary_that_is_not_appium_fails_instead_of_hanging(
    server: AppiumServer,
) -> None:
    """A process that exits instantly must be detected as dead rather than
    costing the full start timeout."""
    stub = dataclasses.replace(server.config, appium_path="/bin/true")
    broken = AppiumServer(stub, CommandRunner(stub))

    try:
        with pytest.raises(ServerNotReadyError):
            await asyncio.wait_for(broken.ensure_running(), timeout=30)
    finally:
        await broken.aclose()


async def test_a_failed_launch_leaves_no_process_behind(server: AppiumServer) -> None:
    """Rule 1 §5: a failed startup leaves nothing behind."""
    stub = dataclasses.replace(server.config, appium_path="/bin/true")
    broken = AppiumServer(stub, CommandRunner(stub))

    with pytest.raises(ServerNotReadyError):
        await asyncio.wait_for(broken.ensure_running(), timeout=30)

    assert broken.managed is False
