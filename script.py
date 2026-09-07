"""Ad-hoc script: drive the Android emulator infrastructure component by hand.

Each function is callable on its own, and every argument has a default::

    uv run python script.py                  # same as: available
    uv run python script.py available
    uv run python script.py all
    uv run python script.py start            # boots DEFAULT_AVD
    uv run python script.py start myPixel
"""

import asyncio
import sys
from pathlib import Path

# Before any `app_automating` import: `uv run` installs the package, but a bare
# `python script.py` does not, and this script should work either way.
sys.path.insert(0, str(Path(__file__).parent / "src"))

from app_automating.modules.android_module.application import (  # noqa: E402
    build_android_emulator_manager,
)
from app_automating.modules.android_module.domain import (  # noqa: E402
    AndroidDevice,
    ListDevicesRequest,
    StartEmulatorRequest,
    StartEmulatorResult,
)
from app_automating.modules.android_module.infrastructure.android_emulator_manager import (  # noqa: E402
    AndroidEmulatorManager,
)

#: The AVD :func:`start_device` boots when the caller names none.
DEFAULT_AVD = "myPixel"


def build_manager() -> AndroidEmulatorManager:
    """One manager, assembled by the composition root.

    The manager takes its collaborators as required arguments (Rule 0 §4), so
    building one by hand is not this script's job -- `build_android_emulator_manager`
    is the single place that resolves the environment and wires them together.
    """
    return build_android_emulator_manager()


async def list_available_devices(*, resolve_avd_names: bool = True) -> tuple[AndroidDevice, ...]:
    """Return only the devices adb will actually accept commands for."""
    async with build_manager() as manager:
        result = await manager.list_devices(
            ListDevicesRequest(resolve_avd_names=resolve_avd_names, include_unavailable=False)
        )
    return result.devices


async def list_all_devices(*, resolve_avd_names: bool = True) -> tuple[AndroidDevice, ...]:
    """Return every device adb knows about, offline and unauthorized ones included."""
    async with build_manager() as manager:
        result = await manager.list_devices(
            ListDevicesRequest(resolve_avd_names=resolve_avd_names, include_unavailable=True)
        )
    return result.devices


async def start_device(
    name: str = DEFAULT_AVD,
    *,
    headless: bool = False,
    wait_for_boot: bool = True,
) -> StartEmulatorResult:
    """Boot the AVD called ``name`` and wait until it reports boot_completed.

    No ``async with`` here on purpose: ``aclose()`` kills every emulator the
    manager launched, so the context manager would shut the device down again
    the moment this function returned.
    """
    manager = build_manager()
    return await manager.start_emulator(
        StartEmulatorRequest(name=name, headless=headless, wait_for_boot=wait_for_boot)
    )


# -- printing, so each function can be run on its own from the command line ---


def print_devices(title: str, devices: tuple[AndroidDevice, ...]) -> None:
    print(f"== {title} ==")
    if not devices:
        print("  (none)")
        return
    for device in devices:
        print(f"  {device.device_id}\t{device.state}\t{device.avd_name or device.model or '-'}")


async def show_available_devices() -> None:
    print_devices("available", await list_available_devices())


async def show_all_devices() -> None:
    print_devices("all (offline included)", await list_all_devices())


async def run_new_device(name: str = DEFAULT_AVD) -> None:
    result = await start_device(name)
    print(
        f"started {result.name} as {result.device_id} "
        f"(pid {result.pid}, booted={result.booted}, "
        f"{result.startup_duration_seconds:.1f}s)"
    )


async def main(argv: list[str] | None = None) -> None:

    await run_new_device(DEFAULT_AVD)

    # args = list(sys.argv[1:] if argv is None else argv)
    # command = args[0] if args else "available"
    #
    # if command == "available":
    #     await show_available_devices()
    # elif command == "all":
    #     await show_all_devices()
    # elif command == "start":
    #     await run_new_device(args[1] if len(args) > 1 else DEFAULT_AVD)
    # else:
    #     print(f"unknown command: {command}\nuse one of: available, all, start [avd-name]")
    #     raise SystemExit(2)


if __name__ == "__main__":
    asyncio.run(main())
