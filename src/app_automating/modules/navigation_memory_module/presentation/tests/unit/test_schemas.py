"""The tool payloads.

Pure pydantic models, so these are unit tests because the code is pure and not
because anything was replaced. What is asserted is the promises the payloads
make to a client: frozen, no undeclared fields, and every field described -- an
undocumented field in an output schema is exactly the guessing Rule 3 §5 exists
to prevent.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from app_automating.modules.navigation_memory_module.presentation import schemas
from app_automating.modules.navigation_memory_module.presentation.schemas import ScreenPayload

pytestmark = pytest.mark.unit


def payload_types() -> list[type[BaseModel]]:
    return [getattr(schemas, name) for name in schemas.__all__]


def a_screen(**overrides: object) -> ScreenPayload:
    fields: dict[str, object] = {
        "screen_id": 1,
        "label": "Home",
        "package": "com.example.shop",
        "visit_count": 3,
    }
    return ScreenPayload(**{**fields, **overrides})  # type: ignore[arg-type]


def test_every_payload_is_exported() -> None:
    assert payload_types()


@pytest.mark.parametrize("payload_type", payload_types(), ids=lambda t: t.__name__)
def test_every_payload_is_frozen(payload_type: type[BaseModel]) -> None:
    assert payload_type.model_config.get("frozen") is True


@pytest.mark.parametrize("payload_type", payload_types(), ids=lambda t: t.__name__)
def test_every_payload_refuses_a_field_it_never_declared(payload_type: type[BaseModel]) -> None:
    assert payload_type.model_config.get("extra") == "forbid"


@pytest.mark.parametrize("payload_type", payload_types(), ids=lambda t: t.__name__)
def test_every_field_carries_a_description(payload_type: type[BaseModel]) -> None:
    """The output schema is the only thing a client sees about the shape."""
    undocumented = [
        name for name, field in payload_type.model_fields.items() if not field.description
    ]
    assert not undocumented, f"{payload_type.__name__} leaves {undocumented} undescribed"


def test_a_payload_rejects_an_unknown_field() -> None:
    with pytest.raises(ValidationError):
        ScreenPayload(screen_id=1, label="Home", package="p", visit_count=0, colour="blue")


def test_a_payload_cannot_be_mutated_after_construction() -> None:
    with pytest.raises(ValidationError):
        a_screen().label = "elsewhere"  # type: ignore[misc]


def test_a_screen_serialises_to_plain_json_types() -> None:
    """No Path, no datetime, no tuple-of-dataclass -- everything JSON can carry."""
    dumped = a_screen(last_seen_at="2026-09-04T10:00:00+00:00").model_dump()

    plain = str | int | float | bool | type(None)
    assert all(isinstance(value, plain) for value in dumped.values())
