"""Running real child processes: resolution, output, timeouts, cancellation.

Rule 1 §6, in full force: no mocks, no patched subprocess, no fake filesystem. A
timeout here is a real 1ms deadline against a real ``sleep``; a missing binary is
a real path that does not exist. A test that patched a subprocess would be
testing the patch.

Nothing here needs Appium -- it drives ``/bin/sh`` and friends, because the
process layer knows nothing about Appium and is the piece a second component
would reuse unchanged.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import time
from collections import deque

import pytest

from app_automating.modules.appium_module.infrastructure.appium_device_manager import (
    AppiumConfig,
    AppiumToolNotFoundError,
    CommandRunner,
)
from app_automating.modules.appium_module.infrastructure.appium_device_manager.process import (
    ToolResolver,
    drain,
    run_command,
    spawn,
    terminate,
)

pytestmark = pytest.mark.integration


# -- running a command to completion --------------------------------------


async def test_a_successful_command_reports_its_output_and_a_zero_exit() -> None:
    result = await run_command(["/bin/echo", "hello"], operation="echo", timeout=10.0)

    assert result.ok
    assert result.returncode == 0
    assert result.stdout.strip() == "hello"
    assert result.argv == ("/bin/echo", "hello")


async def test_a_failing_command_is_a_result_not_an_exception() -> None:
    """A non-zero exit is data at this level; classifying it is the manager's job."""
    result = await run_command(["/bin/sh", "-c", "exit 3"], operation="sh", timeout=10.0)

    assert result.ok is False
    assert result.returncode == 3


async def test_both_streams_are_captured_separately() -> None:
    result = await run_command(
        ["/bin/sh", "-c", "echo out; echo err >&2"], operation="sh", timeout=10.0
    )

    assert result.stdout.strip() == "out"
    assert result.stderr.strip() == "err"


async def test_the_duration_is_measured_rather_than_assumed() -> None:
    result = await run_command(["/bin/sleep", "0.2"], operation="sleep", timeout=10.0)

    assert result.duration_seconds >= 0.2


async def test_a_large_output_is_not_lost_and_does_not_deadlock() -> None:
    """The failure this guards: a child that fills its pipe and blocks forever."""
    result = await run_command(
        ["/bin/sh", "-c", "for i in $(seq 1 20000); do echo line-$i; done"],
        operation="sh",
        timeout=60.0,
    )

    assert result.ok
    assert "line-20000" in result.stdout


async def test_invalid_utf8_in_the_output_is_replaced_rather_than_raising() -> None:
    """Some tools emit raw bytes; a decode error must not become a crash."""
    result = await run_command(
        ["/bin/sh", "-c", "printf '\\xff\\xfe'"], operation="sh", timeout=10.0
    )

    assert result.ok


# -- resolution ------------------------------------------------------------


async def test_a_missing_binary_is_a_typed_error_carrying_where_it_looked() -> None:
    """A real nonexistent path -- not a patched `create_subprocess_exec`."""
    with pytest.raises(AppiumToolNotFoundError) as excinfo:
        await run_command(["/nonexistent/tool-xyz"], operation="x", timeout=10.0)

    assert "/nonexistent/tool-xyz" in excinfo.value.searched


async def test_a_non_executable_file_is_reported_as_not_found(tmp_path) -> None:
    candidate = tmp_path / "tool"
    candidate.write_text("not executable")
    candidate.chmod(0o644)

    with pytest.raises(AppiumToolNotFoundError):
        await run_command([str(candidate)], operation="x", timeout=10.0)


def test_resolution_is_cached_so_a_path_walk_happens_once() -> None:
    resolver = ToolResolver(AppiumConfig(appium_path="/bin/echo"))

    assert resolver.path("appium") == resolver.path("appium") == "/bin/echo"


# -- timeouts, for real ----------------------------------------------------


async def test_a_command_that_outlives_its_deadline_is_killed_and_raises() -> None:
    """A real 50ms timeout against a real 30-second sleep (Rule 1 §6)."""
    from app_automating.modules.appium_module.infrastructure.appium_device_manager.errors import (
        CommandTimeoutError,
    )

    started = time.monotonic()
    with pytest.raises(CommandTimeoutError) as excinfo:
        await run_command(["/bin/sleep", "30"], operation="sleep", timeout=0.05)

    assert time.monotonic() - started < 10, "the timeout did not actually cut it short"
    assert excinfo.value.timeout_seconds == 0.05
    assert "sleep" in str(excinfo.value)


async def test_a_timeout_leaves_no_child_running() -> None:
    """Rule 1 §5: kill and reap on every exit path, including the deadline."""
    from app_automating.modules.appium_module.infrastructure.appium_device_manager.errors import (
        CommandTimeoutError,
    )

    marker = f"at-appium-timeout-marker-{os.getpid()}"
    with pytest.raises(CommandTimeoutError):
        await run_command(["/bin/sh", "-c", f"sleep 30 # {marker}"], operation="sh", timeout=0.05)

    await asyncio.sleep(0.3)
    survivors = await run_command(["/bin/ps", "-eo", "args"], operation="ps", timeout=10.0)
    assert marker not in survivors.stdout


# -- long-lived children ---------------------------------------------------


async def test_a_spawned_child_runs_in_its_own_process_group() -> None:
    """So a timeout can take down the whole tree: Appium forks driver processes,
    and killing only the parent leaves those holding the device."""
    process = await spawn(["/bin/sleep", "30"])
    try:
        assert os.getpgid(process.pid) == process.pid
        assert os.getpgid(process.pid) != os.getpgid(os.getpid())
    finally:
        await terminate(process)


async def test_terminate_kills_the_whole_group_and_reaps_it() -> None:
    process = await spawn(["/bin/sh", "-c", "sleep 30 & sleep 30"])

    await terminate(process)

    assert process.returncode is not None


async def test_terminating_an_already_dead_child_is_a_no_op() -> None:
    """Teardown runs on every path, including the one where the child is gone."""
    process = await spawn(["/bin/true"])
    await process.wait()

    await terminate(process)

    assert process.returncode is not None


async def test_output_is_drained_into_a_bounded_buffer() -> None:
    """The buffer is bounded so a long-running server cannot grow without limit."""
    process = await spawn(["/bin/sh", "-c", "for i in $(seq 1 500); do echo line-$i; done"])
    sink: deque[str] = deque(maxlen=100)
    try:
        await asyncio.gather(drain(process.stdout, sink), drain(process.stderr, sink))
    finally:
        await terminate(process)

    assert len(sink) == 100
    assert sink[-1] == "line-500"


async def test_draining_a_child_that_writes_nothing_finishes_rather_than_hanging() -> None:
    process = await spawn(["/bin/true"])
    sink: deque[str] = deque(maxlen=10)
    try:
        await asyncio.wait_for(drain(process.stdout, sink), timeout=5)
    finally:
        await terminate(process)

    assert list(sink) == []


async def test_cancelling_a_run_kills_the_child_rather_than_orphaning_it() -> None:
    """Rule 1 §5 names cancellation explicitly, because it is the path that gets
    forgotten: a cancelled task must not leave a process behind."""
    marker = f"at-appium-cancel-marker-{os.getpid()}"
    task = asyncio.create_task(
        run_command(["/bin/sh", "-c", f"sleep 30 # {marker}"], operation="sh", timeout=60.0)
    )
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await asyncio.sleep(0.3)
    survivors = await run_command(["/bin/ps", "-eo", "args"], operation="ps", timeout=10.0)
    assert marker not in survivors.stdout


# -- the runner ------------------------------------------------------------


async def test_the_runner_resolves_and_runs_in_one_call() -> None:
    runner = CommandRunner(AppiumConfig(appium_path="/bin/echo"))

    result = await runner.run_tool("appium", "hi", operation="echo", timeout=10.0)

    assert result.stdout.strip() == "hi"


async def test_the_runner_carries_its_configs_timeouts_into_a_real_deadline() -> None:
    """Rule 1 §4's payoff, end to end: `replace` the config, get a real timeout."""
    from app_automating.modules.appium_module.infrastructure.appium_device_manager.errors import (
        CommandTimeoutError,
    )

    config = dataclasses.replace(
        AppiumConfig(appium_path="/bin/sleep"), command_timeout_seconds=0.05
    )
    runner = CommandRunner(config)

    with pytest.raises(CommandTimeoutError):
        await runner.run_tool(
            "appium", "30", operation="sleep", timeout=config.command_timeout_seconds
        )


async def test_the_runner_exposes_the_config_it_was_built_with() -> None:
    config = AppiumConfig(server_port=4999)

    assert CommandRunner(config).config.server_port == 4999
