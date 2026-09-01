"""The payloads: frozen, closed, and JSON-shaped.

These carry no logic, so what is worth asserting is the contract they impose --
that a typo in a field name fails loudly rather than silently shipping an
undeclared key, and that nothing in them is a type JSON cannot express.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from modules.appium_module.presentation import schemas as schemas_module
from modules.appium_module.presentation.schemas import (
    ElementInteractionPayload,
    EnvironmentPayload,
    InteractionPayload,
    ScreenshotPayload,
    SessionListPayload,
    SessionPayload,
    StartedSessionPayload,
    ToolStatusPayload,
)

pytestmark = pytest.mark.unit

ALL_PAYLOADS = [getattr(schemas_module, name) for name in schemas_module.__all__]


def _session(**kwargs) -> SessionPayload:
    return SessionPayload(
        session_id="s1",
        device_id="emulator-5554",
        platform_name="Android",
        automation_name="UiAutomator2",
        **kwargs,
    )


@pytest.mark.parametrize("payload_type", ALL_PAYLOADS, ids=lambda t: t.__name__)
def test_every_payload_is_frozen_and_forbids_undeclared_fields(payload_type: type) -> None:
    """`extra="forbid"` is what turns a renamed field in rendering.py into a
    failing test instead of a silently missing key on the wire."""
    assert payload_type.model_config["frozen"] is True
    assert payload_type.model_config["extra"] == "forbid"


@pytest.mark.parametrize("payload_type", ALL_PAYLOADS, ids=lambda t: t.__name__)
def test_every_field_is_documented(payload_type: type) -> None:
    """The schema is shipped to the client alongside the docstring; an
    undescribed field is a field the caller has to guess at."""
    undocumented = [
        name for name, field in payload_type.model_fields.items() if not field.description
    ]
    assert not undocumented, f"{payload_type.__name__} has undescribed fields: {undocumented}"


def test_a_payload_cannot_be_mutated_after_construction() -> None:
    payload = _session()

    with pytest.raises(ValidationError):
        payload.session_id = "other"


def test_an_unknown_field_is_rejected_rather_than_ignored() -> None:
    with pytest.raises(ValidationError):
        _session(unexpected="value")


def test_a_missing_required_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SessionPayload(session_id="s1")


def test_payloads_serialise_to_json_because_that_is_the_whole_point() -> None:
    payload = StartedSessionPayload(
        session=_session(screen_width=1080, screen_height=2400),
        session_id="s1",
        server_started=True,
        duration_seconds=12.4,
    )

    decoded = json.loads(payload.model_dump_json())

    assert decoded["session_id"] == "s1"
    assert decoded["session"]["screen_width"] == 1080


def test_optional_fields_default_to_null_rather_than_being_absent() -> None:
    """A caller reading `avd_name` should find null, not a KeyError."""
    decoded = json.loads(_session().model_dump_json())

    assert decoded["app_package"] is None
    assert decoded["screen_width"] is None


def test_the_session_id_is_lifted_to_the_top_level_of_a_start_result() -> None:
    """It is the argument of the next twelve calls; a client that has to reach
    into a nested object for it will eventually reach into the wrong one."""
    assert "session_id" in StartedSessionPayload.model_fields
    assert "session" in StartedSessionPayload.model_fields


def test_a_screenshot_can_carry_either_a_path_or_an_image_but_the_schema_allows_both_null() -> None:
    """The exclusivity is rendering's invariant, not the schema's -- the schema
    stays permissive so a future third form does not need a new payload type."""
    to_file = ScreenshotPayload(session_id="s1", path="/tmp/x.png", size_bytes=10)
    inline = ScreenshotPayload(session_id="s1", image_base64="AAA=", size_bytes=10)

    assert to_file.image_base64 is None
    assert inline.path is None


def test_an_empty_session_list_is_representable() -> None:
    """Zero open sessions is a normal answer and must not need a None check."""
    payload = SessionListPayload(sessions=(), session_ids=(), count=0)

    assert payload.count == 0


def test_the_environment_payload_summarises_readiness_in_one_boolean() -> None:
    payload = EnvironmentPayload(
        ready=False,
        tools=(ToolStatusPayload(name="appium", present=False, detail="not found"),),
        missing=("appium",),
        drivers=(),
        server_url="http://127.0.0.1:4723",
        server_running=False,
    )

    assert payload.ready is False
    assert payload.missing == ("appium",)


def test_the_two_interaction_payloads_are_distinct_shapes() -> None:
    """A coordinate gesture has no locator to report, and an element gesture has
    no derived detail string. Merging them would leave half the fields null."""
    assert "strategy" in ElementInteractionPayload.model_fields
    assert "strategy" not in InteractionPayload.model_fields
    assert "detail" in InteractionPayload.model_fields
    assert "detail" not in ElementInteractionPayload.model_fields
