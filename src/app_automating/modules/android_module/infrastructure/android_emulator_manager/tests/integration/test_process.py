"""Integration tests for ``process.py``.

Real binaries, real child processes, real timeouts. The runner is exercised
directly rather than through the manager, so a failure here says "the process
layer is broken" and not "something in the emulator flow is broken".
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from app_automating.modules.android_module.infrastructure.android_emulator_manager import (
    AndroidSdkConfig,
    AndroidToolNotFoundError,
    CommandRunner,
    CommandTimeoutError,
)
from app_automating.modules.android_module.infrastructure.android_emulator_manager.process import (
    terminate,
)

from .conftest import REQUIRED_TOOLS

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("tool", REQUIRED_TOOLS)
def test_tools_resolve_to_real_executables(sdk_config: AndroidSdkConfig, tool: str) -> None:
    resolved = Path(CommandRunner(sdk_config).tool_path(tool))
    assert resolved.is_file()
    assert resolved.stat().st_mode & 0o111, f"{resolved} is not executable"


def test_sdk_local_tools_win_over_path(sdk_config: AndroidSdkConfig) -> None:
    """A distro ``/usr/bin/adb`` is routinely a different major version from the
    SDK's ``emulator``; the two then fight over the adb server."""
    assert sdk_config.sdk_root is not None
    sdk_adb = sdk_config.sdk_root / "platform-tools" / "adb"
    if not sdk_adb.is_file():
        pytest.skip("this SDK has no platform-tools/adb to prefer")
    assert Path(CommandRunner(sdk_config).tool_path("adb")) == sdk_adb


def test_resolution_is_cached_so_the_filesystem_is_walked_once(
    sdk_config: AndroidSdkConfig,
) -> None:
    runner = CommandRunner(sdk_config)
    assert runner.tool_path("adb") == runner.tool_path("adb")


def test_an_absolute_tool_path_that_does_not_exist_is_reported(tmp_path: Path) -> None:
    missing = tmp_path / "nowhere" / "adb"
    runner = CommandRunner(AndroidSdkConfig(adb_path=str(missing), sdk_root=None))
    with pytest.raises(AndroidToolNotFoundError) as excinfo:
        runner.tool_path("adb")
    assert excinfo.value.tool == "adb"
    assert excinfo.value.searched == (str(missing),)


def test_a_bare_tool_name_missing_from_path_is_reported() -> None:
    runner = CommandRunner(
        AndroidSdkConfig(adb_path="definitely-not-an-android-tool-xyz", sdk_root=None)
    )
    with pytest.raises(AndroidToolNotFoundError) as excinfo:
        runner.tool_path("adb")
    assert excinfo.value.configured == "definitely-not-an-android-tool-xyz"
    assert "PATH" in excinfo.value.searched


async def test_a_command_that_succeeds_reports_its_output_and_duration(
    sdk_config: AndroidSdkConfig,
) -> None:
    result = await CommandRunner(sdk_config).run(["/bin/echo", "hello"], timeout=10.0)
    assert result.returncode == 0
    assert result.stdout.strip() == "hello"
    assert result.duration_seconds >= 0.0
    assert result.argv == ("/bin/echo", "hello")


async def test_a_nonzero_exit_is_returned_not_raised(sdk_config: AndroidSdkConfig) -> None:
    """Classification is the caller's job; the runner only reports."""
    result = await CommandRunner(sdk_config).run(["/bin/sh", "-c", "exit 3"], timeout=10.0)
    assert result.returncode == 3


async def test_stdin_is_delivered_to_the_child(sdk_config: AndroidSdkConfig) -> None:
    """avdmanager and sdkmanager both block forever without this."""
    result = await CommandRunner(sdk_config).run(["/bin/cat"], timeout=10.0, stdin_data=b"fed\n")
    assert result.stdout.strip() == "fed"


async def test_a_real_timeout_kills_the_child_and_raises(sdk_config: AndroidSdkConfig) -> None:
    runner = CommandRunner(sdk_config)
    with pytest.raises(CommandTimeoutError) as excinfo:
        await runner.run(["/bin/sleep", "30"], timeout=0.05)
    assert excinfo.value.timeout_seconds == 0.05
    assert "sleep" in " ".join(excinfo.value.argv)


async def test_a_timeout_takes_down_the_whole_process_tree(
    sdk_config: AndroidSdkConfig, tmp_path: Path
) -> None:
    """The child is spawned in its own session so a grandchild cannot outlive it."""
    marker = tmp_path / "grandchild_still_alive"
    script = f"( sleep 30; touch {marker} ) & wait"
    with pytest.raises(CommandTimeoutError):
        await CommandRunner(sdk_config).run(["/bin/sh", "-c", script], timeout=0.2)
    await asyncio.sleep(1.0)
    assert not marker.exists(), "a grandchild survived the timeout"


async def test_a_missing_binary_raises_the_typed_error_not_filenotfound(
    sdk_config: AndroidSdkConfig,
) -> None:
    with pytest.raises(AndroidToolNotFoundError):
        await CommandRunner(sdk_config).run(["/nonexistent/binary/xyz"], timeout=5.0)


async def test_spawn_and_terminate_reap_a_long_lived_child(
    sdk_config: AndroidSdkConfig,
) -> None:
    proc = await CommandRunner(sdk_config).spawn_tool(["/bin/sleep", "30"])
    assert proc.returncode is None
    await terminate(proc)
    assert proc.returncode is not None


async def test_drains_copy_a_chatty_child_into_a_bounded_buffer(
    sdk_config: AndroidSdkConfig,
) -> None:
    """Without draining, the emulator fills its pipe and blocks forever."""
    from collections import deque

    runner = CommandRunner(sdk_config)
    proc = await runner.spawn_tool(["/bin/sh", "-c", "for i in $(seq 1 500); do echo $i; done"])
    log: deque[str] = deque(maxlen=100)
    drains = runner.drain_into(proc, log)
    await proc.wait()
    await asyncio.gather(*drains)
    assert len(log) == 100, "the buffer should be bounded, and full"
    assert log[-1] == "500"


# --------------------------------------------------------------------------
# the child environment
#
# Real children, real environments. The scrubbed-environment bug lived here:
# every tool the runner starts inherits whatever the server was given, and a
# server started without a display hands that on to the emulator.
# --------------------------------------------------------------------------


async def test_a_child_receives_the_configured_environment() -> None:
    config = AndroidSdkConfig(child_env=(("AT_MARKER", "handed-down"), ("PATH", "/usr/bin:/bin")))
    result = await CommandRunner(config).run(
        ["/bin/sh", "-c", 'printf %s "$AT_MARKER"'], timeout=10.0
    )
    assert result.stdout == "handed-down"


async def test_the_configured_environment_replaces_rather_than_extends() -> None:
    """``child_env`` is the whole environment, so a variable the server happens
    to carry does not leak into a child that was not given it."""
    os.environ["AT_MUST_NOT_LEAK"] = "from-the-parent"
    try:
        config = AndroidSdkConfig(child_env=(("PATH", "/usr/bin:/bin"),))
        result = await CommandRunner(config).run(
            ["/bin/sh", "-c", 'printf %s "$AT_MUST_NOT_LEAK"'], timeout=10.0
        )
        assert result.stdout == ""
    finally:
        del os.environ["AT_MUST_NOT_LEAK"]


async def test_a_config_without_a_child_environment_lets_the_child_inherit() -> None:
    """The default every hand-written config has relied on."""
    os.environ["AT_INHERITED"] = "yes"
    try:
        result = await CommandRunner(AndroidSdkConfig()).run(
            ["/bin/sh", "-c", 'printf %s "$AT_INHERITED"'], timeout=10.0
        )
        assert result.stdout == "yes"
    finally:
        del os.environ["AT_INHERITED"]


async def test_a_spawned_child_receives_it_too() -> None:
    """``spawn`` is the path a windowed emulator actually takes."""
    config = AndroidSdkConfig(child_env=(("AT_MARKER", "spawned"), ("PATH", "/usr/bin:/bin")))
    proc = await CommandRunner(config).spawn_tool(["/bin/sh", "-c", 'printf %s "$AT_MARKER"'])
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), 10.0)
        assert stdout.decode() == "spawned"
    finally:
        await terminate(proc)
