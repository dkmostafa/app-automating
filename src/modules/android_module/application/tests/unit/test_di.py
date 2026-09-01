"""Unit tests for ``di.py`` -- the wiring, asserted with mocks.

Nothing real is constructed here. The point of a composition root is *which
object goes where*, so the collaborators are replaced and the assertions are
about the arguments they were called with. A real Android SDK has no business
in a test about wiring.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from modules.android_module.application import di
from modules.android_module.application.di import (
    android_emulator_ports,
    android_emulator_service,
    build_android_emulator_manager,
    build_android_emulator_service,
)
from modules.android_module.application.services import AndroidEmulatorService
from modules.android_module.domain.ports import (
    DeviceCatalog,
    EmulatorLifecycle,
    SystemImageInstaller,
)
from modules.android_module.infrastructure.android_emulator_manager import AndroidSdkConfig

pytestmark = pytest.mark.unit

PINNED = AndroidSdkConfig(sdk_root=Path("/sdk"), avd_home=Path("/avds"))


def test_the_manager_is_built_from_exactly_three_collaborators() -> None:
    with (
        patch.object(di, "AndroidEmulatorManager") as manager_cls,
        patch.object(di, "CommandRunner") as runner_cls,
        patch.object(di, "AvdStore") as store_cls,
    ):
        result = build_android_emulator_manager(PINNED)

    runner_cls.assert_called_once_with(PINNED)
    store_cls.assert_called_once_with(Path("/avds"))
    manager_cls.assert_called_once_with(
        config=PINNED,
        runner=runner_cls.return_value,
        avds=store_cls.return_value,
    )
    assert result is manager_cls.return_value


def test_the_injected_config_is_passed_through_untouched() -> None:
    """Rule 0 §4: the composition root resolves ambient state, then hands over
    explicit settings. It must not edit what the caller pinned."""
    pinned = AndroidSdkConfig(
        sdk_root=Path("/sdk"), avd_home=Path("/avds"), adb_timeout_seconds=9.5
    )
    with (
        patch.object(di, "AndroidEmulatorManager") as manager_cls,
        patch.object(di, "CommandRunner"),
        patch.object(di, "AvdStore"),
    ):
        build_android_emulator_manager(pinned)

    passed = manager_cls.call_args.kwargs["config"]
    assert passed is pinned
    assert passed.adb_timeout_seconds == 9.5


def test_no_config_means_read_the_environment_here_and_only_here() -> None:
    """``config=None`` is a decision only a composition root may make."""
    with (
        patch.object(di, "AndroidEmulatorManager"),
        patch.object(di, "CommandRunner") as runner_cls,
        patch.object(di, "AvdStore"),
        patch.object(di.AndroidSdkConfig, "from_environment") as from_env,
    ):
        from_env.return_value = PINNED
        build_android_emulator_manager()

    from_env.assert_called_once_with()
    runner_cls.assert_called_once_with(PINNED)


def test_a_config_without_an_avd_home_falls_back_here_not_in_the_manager() -> None:
    """The manager must never consult the environment, so the fallback for a
    hand-written config belongs to this layer."""
    homeless = AndroidSdkConfig(sdk_root=Path("/sdk"), avd_home=None)
    with (
        patch.object(di, "AndroidEmulatorManager"),
        patch.object(di, "CommandRunner"),
        patch.object(di, "AvdStore") as store_cls,
        patch.object(di, "avd_home_from_env") as fallback,
    ):
        fallback.return_value = Path("/resolved/avds")
        build_android_emulator_manager(homeless)

    fallback.assert_called_once_with()
    store_cls.assert_called_once_with(Path("/resolved/avds"))


def test_a_config_with_an_avd_home_does_not_touch_the_environment() -> None:
    with (
        patch.object(di, "AndroidEmulatorManager"),
        patch.object(di, "CommandRunner"),
        patch.object(di, "AvdStore") as store_cls,
        patch.object(di, "avd_home_from_env") as fallback,
    ):
        build_android_emulator_manager(PINNED)

    fallback.assert_not_called()
    store_cls.assert_called_once_with(Path("/avds"))


def test_the_ports_view_hands_out_the_same_object_three_times() -> None:
    """One adapter may satisfy several ports; the caller still receives only the
    narrow one it asked for (Rule 0 §3, ISP)."""
    with (
        patch.object(di, "AndroidEmulatorManager") as manager_cls,
        patch.object(di, "CommandRunner"),
        patch.object(di, "AvdStore"),
    ):
        manager = build_android_emulator_manager(PINNED)

    catalog, lifecycle, installer = android_emulator_ports(manager)
    assert catalog is manager_cls.return_value
    assert lifecycle is manager_cls.return_value
    assert installer is manager_cls.return_value


def test_what_the_real_composition_root_builds_satisfies_the_ports() -> None:
    """The one assertion here that uses no mock: a wiring test that never builds
    the real object would not notice the adapter drifting from its ports."""
    manager = build_android_emulator_manager(AndroidSdkConfig(sdk_root=None, avd_home=Path("/x")))
    assert isinstance(manager, DeviceCatalog)
    assert isinstance(manager, EmulatorLifecycle)
    assert isinstance(manager, SystemImageInstaller)


# --------------------------------------------------------------------------
# services
# --------------------------------------------------------------------------


def test_the_service_is_injected_with_ports_not_with_the_adapter() -> None:
    """Rule 0 §4: the service never learns which adapter it got."""
    with (
        patch.object(di, "AndroidEmulatorManager") as manager_cls,
        patch.object(di, "CommandRunner"),
        patch.object(di, "AvdStore"),
        patch.object(di, "AndroidEmulatorService") as service_cls,
    ):
        result = build_android_emulator_service(PINNED)

    built = manager_cls.return_value
    service_cls.assert_called_once_with(devices=built, emulators=built, images=built)
    assert result is service_cls.return_value


def test_building_a_service_reads_the_environment_when_given_no_config() -> None:
    with (
        patch.object(di, "AndroidEmulatorManager"),
        patch.object(di, "CommandRunner") as runner_cls,
        patch.object(di, "AvdStore"),
        patch.object(di.AndroidSdkConfig, "from_environment") as from_env,
    ):
        from_env.return_value = PINNED
        build_android_emulator_service()

    from_env.assert_called_once_with()
    runner_cls.assert_called_once_with(PINNED)


def test_the_real_service_is_wired_to_a_real_adapter() -> None:
    """The one unmocked assertion: wiring made entirely of mocks would not
    notice the service drifting from what the adapter provides."""
    service = build_android_emulator_service(AndroidSdkConfig(sdk_root=None, avd_home=Path("/x")))
    assert isinstance(service, AndroidEmulatorService)
    for operation in (
        "get_available_devices",
        "get_all_devices",
        "run_emulator",
        "run_emulator_without_window",
        "create_device",
        "download_image",
    ):
        assert callable(getattr(service, operation))


async def test_the_scoped_service_closes_the_manager_on_exit() -> None:
    """The context-manager form owns the emulators it started."""
    with (
        patch.object(di, "AndroidEmulatorManager") as manager_cls,
        patch.object(di, "CommandRunner"),
        patch.object(di, "AvdStore"),
    ):
        manager_cls.return_value.aclose = AsyncMock()
        async with android_emulator_service(PINNED) as service:
            assert isinstance(service, AndroidEmulatorService)
            manager_cls.return_value.aclose.assert_not_awaited()

    manager_cls.return_value.aclose.assert_awaited_once()


async def test_the_scoped_service_closes_the_manager_even_when_the_body_raises() -> None:
    with (
        patch.object(di, "AndroidEmulatorManager") as manager_cls,
        patch.object(di, "CommandRunner"),
        patch.object(di, "AvdStore"),
    ):
        manager_cls.return_value.aclose = AsyncMock()
        with pytest.raises(RuntimeError):
            async with android_emulator_service(PINNED):
                raise RuntimeError("boom")

    manager_cls.return_value.aclose.assert_awaited_once()
