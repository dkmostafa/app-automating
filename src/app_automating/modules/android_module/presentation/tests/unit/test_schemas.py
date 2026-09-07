"""Unit tests for the tool payloads.

Pure pydantic models, so these are unit tests because the code is pure and not
because anything was replaced. What is worth asserting is the promises the
payloads make to a client: they are frozen, they reject fields the schema never
declared, and every field carries a description -- an undocumented field in an
output schema is exactly the guessing that Rule 3 §5 exists to prevent.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from app_automating.modules.android_module.presentation import schemas
from app_automating.modules.android_module.presentation.schemas import (
    AvdListPayload,
    AvdPayload,
    DeviceListPayload,
    DevicePayload,
    StartedEmulatorPayload,
)

pytestmark = pytest.mark.unit


def _payload_types() -> list[type[BaseModel]]:
    return [getattr(schemas, name) for name in schemas.__all__]


def test_every_exported_name_is_a_payload_model() -> None:
    """Guard the guard: a typo in __all__ would otherwise empty every check below."""
    types = _payload_types()
    assert len(types) == 10
    assert all(issubclass(t, BaseModel) for t in types)


@pytest.mark.parametrize("payload_type", _payload_types(), ids=lambda t: t.__name__)
def test_every_field_documents_itself(payload_type: type[BaseModel]) -> None:
    """The output schema is read by a model with no other context."""
    undocumented = [
        name for name, field in payload_type.model_fields.items() if not field.description
    ]
    assert not undocumented, f"{payload_type.__name__} has undescribed fields: {undocumented}"


@pytest.mark.parametrize("payload_type", _payload_types(), ids=lambda t: t.__name__)
def test_payloads_are_frozen(payload_type: type[BaseModel]) -> None:
    assert payload_type.model_config["frozen"] is True


def test_a_payload_rejects_a_field_it_never_declared() -> None:
    """extra='forbid' is what stops rendering.py from inventing a field silently."""
    with pytest.raises(ValidationError):
        DevicePayload(
            device_id="emulator-5554",
            state="device",
            available=True,
            is_emulator=True,
            serial="emulator-5554",
        )


def test_optional_device_details_default_to_null() -> None:
    """A physical device has no AVD name, and adb does not always report a model."""
    device = DevicePayload(
        device_id="emulator-5554", state="device", available=True, is_emulator=True
    )
    assert device.avd_name is None
    assert device.model is None
    assert device.transport_id is None


def test_a_payload_serialises_to_the_json_the_docstring_advertises() -> None:
    payload = StartedEmulatorPayload(
        name="Pixel_7",
        device_id="emulator-5554",
        pid=48213,
        headless=False,
        booted=True,
        startup_duration_seconds=42.7,
    )
    assert payload.model_dump() == {
        "name": "Pixel_7",
        "device_id": "emulator-5554",
        "pid": 48213,
        "headless": False,
        "booted": True,
        "startup_duration_seconds": 42.7,
    }


def test_list_payloads_hold_their_entries_as_models_not_dicts() -> None:
    devices = DeviceListPayload(
        devices=(
            DevicePayload(
                device_id="emulator-5554", state="device", available=True, is_emulator=True
            ),
        ),
        count=1,
        emulator_count=1,
    )
    avds = AvdListPayload(
        emulators=(AvdPayload(name="Pixel_7", loadable=True),),
        names=("Pixel_7",),
        count=1,
        unloadable_count=0,
    )
    assert isinstance(devices.devices[0], DevicePayload)
    assert isinstance(avds.emulators[0], AvdPayload)
