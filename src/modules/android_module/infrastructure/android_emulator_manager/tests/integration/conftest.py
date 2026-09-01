"""Shared fixtures for the component's integration tests.

These drive the real host toolchain, so they carry the obligations Rule 1 §6
attaches to that licence:

* Every AVD created here is named ``at_it_<random>``. :func:`destroy_avd`
  refuses to touch anything else, and :func:`guard_pre_existing_avds` fails the
  run if a pre-existing AVD disappeared or a test AVD was left behind.
* Nothing is ever downloaded. A system image already unpacked under the SDK root
  is discovered, and the suite skips if there is none.
* A host without an Android SDK skips rather than fails.

They live in ``integration/`` rather than one level up on purpose: the session
guard is autouse, and hanging it off ``sdk_config`` at ``tests/`` level would
skip the pure unit tests too on a machine with no SDK.
"""

from __future__ import annotations

import shutil
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

from modules.android_module.application import build_android_emulator_manager
from modules.android_module.domain import CreateEmulatorRequest, DeleteEmulatorRequest
from modules.android_module.infrastructure.android_emulator_manager import (
    AndroidEmulatorManager,
    AndroidSdkConfig,
    AndroidToolNotFoundError,
    AvdNotFoundError,
)

#: Every AVD these tests create carries this prefix. Deletion is gated on it.
TEST_AVD_PREFIX = "at_it_"

#: Shipped with every SDK and not tied to a Play Store image.
DEVICE_PROFILE = "pixel_6"

#: A real headless boot on a KVM host lands around 40s; leave room for a cold
#: page cache and a busy machine.
BOOT_TIMEOUT_SECONDS = 420.0

REQUIRED_TOOLS = ("adb", "emulator", "avdmanager", "sdkmanager")


@pytest.fixture(scope="session")
def sdk_config() -> AndroidSdkConfig:
    """The real host SDK, or a skip if this machine has no Android toolchain."""
    config = AndroidSdkConfig.from_environment()
    if config.sdk_root is None or not config.sdk_root.is_dir():
        pytest.skip("no Android SDK root (set ANDROID_SDK_ROOT or ANDROID_HOME)")
    probe = build_android_emulator_manager(config)
    for tool in REQUIRED_TOOLS:
        try:
            probe.tool_path(tool)
        except AndroidToolNotFoundError as exc:
            pytest.skip(f"host is missing an Android tool: {exc}")
    return config


@pytest.fixture(scope="session")
def avd_home(sdk_config: AndroidSdkConfig) -> Path:
    home = build_android_emulator_manager(sdk_config).avd_home
    home.mkdir(parents=True, exist_ok=True)
    return home


@pytest.fixture(scope="session", autouse=True)
def guard_pre_existing_avds(avd_home: Path) -> Iterator[None]:
    """Fail the run if the suite damaged the developer's own AVDs.

    The ``.ini`` filenames in the AVD home are the AVD names, so this needs no
    subprocess and works even when the run dies partway through.
    """
    before = {path.stem for path in avd_home.glob("*.ini")}
    yield
    after = {path.stem for path in avd_home.glob("*.ini")}
    assert not (before - after), f"integration tests destroyed existing AVDs: {before - after}"
    leftovers = {name for name in after - before if name.startswith(TEST_AVD_PREFIX)}
    assert not leftovers, f"integration tests leaked AVDs: {leftovers}"


@pytest.fixture(scope="session")
def installed_system_image(sdk_config: AndroidSdkConfig) -> str:
    """An sdkmanager package id already unpacked under the SDK root.

    Chosen off the filesystem so no test ever needs the network. Play Store
    images are deprioritised because they only pair with certain device
    profiles.
    """
    assert sdk_config.sdk_root is not None
    root = sdk_config.sdk_root / "system-images"
    candidates: list[tuple[int, int, str]] = []
    for abi_dir in sorted(root.glob("*/*/*")):
        if not abi_dir.is_dir() or not any(abi_dir.iterdir()):
            continue
        api, tag, abi = abi_dir.parts[-3:]
        api_level = int(api.rsplit("-", 1)[-1]) if api.rsplit("-", 1)[-1].isdigit() else 0
        package_id = f"system-images;{api};{tag};{abi}"
        candidates.append((0 if "playstore" in tag else 1, api_level, package_id))
    if not candidates:
        pytest.skip(f"no system image unpacked under {root}; these tests never download one")
    return max(candidates)[2]


@pytest.fixture
async def manager(sdk_config: AndroidSdkConfig) -> AsyncIterator[AndroidEmulatorManager]:
    instance = build_android_emulator_manager(sdk_config)
    try:
        yield instance
    finally:
        # Kills anything the test launched, even if the test blew up mid-boot.
        await instance.aclose()


@pytest.fixture
async def avd_name(manager: AndroidEmulatorManager) -> AsyncIterator[str]:
    """A unique AVD name, removed from disk afterwards whether or not it exists."""
    name = f"{TEST_AVD_PREFIX}{uuid.uuid4().hex[:8]}"
    try:
        yield name
    finally:
        await destroy_avd(manager, name)


@pytest.fixture
async def created_avd(
    manager: AndroidEmulatorManager, avd_name: str, installed_system_image: str
) -> str:
    await manager.create_emulator(
        CreateEmulatorRequest(
            name=avd_name, system_image=installed_system_image, device=DEVICE_PROFILE
        )
    )
    return avd_name


async def destroy_avd(manager: AndroidEmulatorManager, name: str) -> None:
    """Remove a test AVD, including the debris a failed create leaves behind."""
    assert name.startswith(TEST_AVD_PREFIX), f"refusing to delete non-test AVD {name!r}"
    try:
        await manager.delete_emulator(DeleteEmulatorRequest(name=name, stop_if_running=True))
    except AvdNotFoundError:
        pass
    stray = manager.avd_home / f"{name}.avd"
    if stray.is_dir():
        shutil.rmtree(stray, ignore_errors=True)
    stray_ini = manager.avd_home / f"{name}.ini"
    if stray_ini.exists():
        stray_ini.unlink(missing_ok=True)
