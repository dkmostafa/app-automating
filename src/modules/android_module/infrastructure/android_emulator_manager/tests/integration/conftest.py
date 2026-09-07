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
from modules.android_module.domain import (
    CreateEmulatorRequest,
    DeleteEmulatorRequest,
    ListDevicesRequest,
)
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

#: What one emulator actually costs the host, in MiB. Measured, not guessed: a
#: ``pixel_6`` AVD asks for 1536 MiB of guest RAM and the ``qemu-system-x86_64``
#: process behind it holds around 3.6 GiB resident, the difference being qemu
#: itself, the GPU translation layer and the guest's page cache. A machine that
#: cannot spare this will not boot a device inside any timeout worth writing.
EMULATOR_MEMORY_COST_MIB = 3600

#: Below this share of free swap the kernel is already reclaiming to keep what
#: is running alive, and everything that touches the disk slows by an order of
#: magnitude -- including a boot. A host with no swap configured is exempt
#: rather than skipped: no swap is a choice, an exhausted swap is a symptom.
MIN_FREE_SWAP_FRACTION = 0.1


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
async def second_avd_name(manager: AndroidEmulatorManager) -> AsyncIterator[str]:
    """A second unique name, for the tests that need a target as well as a source.

    Cleaned up the same way and for the same reason: a rename leaves the AVD
    under a name the ``avd_name`` fixture has never heard of, and the session
    guard would report that as a leak.
    """
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


@pytest.fixture
async def host_can_boot_an_emulator(manager: AndroidEmulatorManager) -> None:
    """Skip unless this machine has room to boot a device right now.

    Rule 1 §6 says skip rather than fail on a host that cannot run the test, and
    a boot is the one thing in this suite whose *host* can be unable rather than
    unequipped. Two states make it so, and both are ordinary on a workstation:

    * **An emulator is already running.** Almost always the developer's own, or
      the one the ``appium_module`` suite needs attached. A second concurrent
      boot on a desktop competes with the first for exactly the resources a boot
      is bound by, and outlives any timeout. Run the two suites separately.
    * **The machine is already out of memory headroom.** Either less free than
      an emulator costs, or a swap that is effectively spent -- at which point
      the kernel is reclaiming to keep what is running alive and a 40s boot
      becomes an unbounded one.

    Neither is a defect in the code under test, which is why this is a skip and
    not a longer timeout: waiting longer for a machine that is thrashing only
    finds out more slowly.
    """
    attached = await manager.list_devices(ListDevicesRequest(resolve_avd_names=False))
    running = [device.device_id for device in attached.emulators]
    if running:
        pytest.skip(
            f"an emulator is already running ({', '.join(running)}); this test boots its own "
            "and two at once will not fit. Stop it, or run this file on its own."
        )

    available, free_swap_fraction = _memory_headroom()
    if available is not None and available < EMULATOR_MEMORY_COST_MIB:
        pytest.skip(
            f"only {available} MiB available and an emulator costs about "
            f"{EMULATOR_MEMORY_COST_MIB} MiB; close something and rerun"
        )
    if free_swap_fraction is not None and free_swap_fraction < MIN_FREE_SWAP_FRACTION:
        pytest.skip(
            f"swap is {(1 - free_swap_fraction):.0%} consumed, so this host is already "
            "reclaiming memory; a boot here would outlive any timeout"
        )


def _memory_headroom() -> tuple[int | None, float | None]:
    """``(MemAvailable in MiB, free share of swap)``, or ``None`` where unknown.

    Read straight from ``/proc/meminfo`` rather than through a dependency: this
    is a guard on the developer's machine, and it must not be the reason the
    suite needs a package installed. A host without ``/proc`` yields ``None``
    twice and is allowed to try -- unable to tell is not the same as unable.
    """
    try:
        text = Path("/proc/meminfo").read_text()
    except OSError:
        return None, None

    values: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            values[key] = int(parts[0])  # kB

    available = values["MemAvailable"] // 1024 if "MemAvailable" in values else None
    swap_total = values.get("SwapTotal", 0)
    swap_free = values.get("SwapFree", 0)
    return available, (swap_free / swap_total if swap_total else None)


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
