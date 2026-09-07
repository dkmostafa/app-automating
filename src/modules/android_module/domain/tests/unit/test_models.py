"""Unit tests for ``domain/models.py``: the entities and boundary DTOs."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from modules.android_module.domain import models as domain_models
from modules.android_module.domain.models import (
    AndroidDevice,
    AvdInfo,
    ListDevicesRequest,
    ListDevicesResult,
    ListEmulatorsResult,
    StartEmulatorRequest,
)

pytestmark = pytest.mark.unit

EMULATOR = AndroidDevice(device_id="emulator-5554", state="device", is_emulator=True, avd_name="a")
OFFLINE = AndroidDevice(device_id="emulator-5556", state="offline", is_emulator=True, avd_name="b")
PHONE = AndroidDevice(device_id="1234abcd", state="device", is_emulator=False, model="Pixel 6")


def test_is_available_is_true_only_for_the_device_state() -> None:
    assert EMULATOR.is_available
    assert not OFFLINE.is_available


def test_list_devices_result_finds_by_avd_name_and_by_serial() -> None:
    result = ListDevicesResult(devices=(EMULATOR, OFFLINE, PHONE))
    assert result.by_avd_name("a") is EMULATOR
    assert result.by_device_id("1234abcd") is PHONE
    assert result.by_avd_name("absent") is None
    assert result.by_device_id("absent") is None


def test_list_devices_result_filters_emulators() -> None:
    result = ListDevicesResult(devices=(EMULATOR, OFFLINE, PHONE))
    assert result.emulators == (EMULATOR, OFFLINE)


def test_list_emulators_result_exposes_names_and_the_loadable_subset() -> None:
    good = AvdInfo(name="good", path=Path("/avd/good.avd"))
    bad = AvdInfo(name="bad", loadable=False, error="missing system image")
    result = ListEmulatorsResult(emulators=(good, bad))
    assert result.names == ("good", "bad")
    assert result.loadable == (good,)
    assert result.by_name("bad") is bad
    assert result.by_name("absent") is None


def test_the_derived_reads_exist_so_callers_do_not_reimplement_them() -> None:
    """Rule 1 §2: the layer above should not re-parse what this one already knows."""
    empty = ListDevicesResult(devices=())
    assert empty.emulators == ()
    assert empty.by_avd_name("anything") is None


@pytest.mark.parametrize(
    "dataclass_type",
    [
        getattr(domain_models, name)
        for name in domain_models.__all__
        if dataclasses.is_dataclass(getattr(domain_models, name))
    ],
    ids=lambda t: t.__name__,
)
def test_every_boundary_dataclass_is_frozen(dataclass_type: type) -> None:
    """Rule 1 §2: frozen and slotted, every one of them."""
    params = dataclass_type.__dataclass_params__  # type: ignore[attr-defined]
    assert params.frozen, f"{dataclass_type.__name__} must be frozen"
    assert "__slots__" in vars(dataclass_type), f"{dataclass_type.__name__} must use slots"


def test_requests_have_defaults_so_adding_a_field_never_breaks_a_call_site() -> None:
    assert ListDevicesRequest().resolve_avd_names is True
    assert ListDevicesRequest().include_unavailable is True
    assert StartEmulatorRequest(name="x").headless is False


def test_rename_refuses_to_run_a_device_out_from_under_itself_by_default() -> None:
    """A rename moves the payload directory, so the running case must be opt-in."""
    request = domain_models.RenameEmulatorRequest(name="old", new_name="new")

    assert request.stop_if_running is False


def test_a_rename_result_reports_both_names() -> None:
    """The caller has to update what it thinks the device is called."""
    result = domain_models.RenameEmulatorResult(
        name="new",
        previous_name="old",
        path=Path("/avd/new.avd"),
        previous_path=Path("/avd/old.avd"),
        stopped_first=False,
        duration_seconds=0.3,
    )

    assert (result.previous_name, result.name) == ("old", "new")
    assert result.path != result.previous_path


def test_replace_is_how_a_request_is_varied() -> None:
    original = StartEmulatorRequest(name="x")
    headless = dataclasses.replace(original, headless=True)
    assert headless.headless is True
    assert original.headless is False, "the original must not be mutated"
