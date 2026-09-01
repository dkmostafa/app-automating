"""Unit tests for the domain-error to ToolError translation.

A pure table and two pure functions, so nothing is mocked. What matters is that
the table stays exhaustive -- a domain error added tomorrow must not fall
through to a generic message -- and that every message tells the caller what to
do next, which is the whole reason this layer rewrites errors at all.
"""

from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError

from modules.android_module.domain import errors as domain_errors
from modules.android_module.domain.errors import (
    AndroidModuleError,
    BackendUnavailable,
    EmulatorAlreadyRunning,
    EmulatorInUse,
    EmulatorNotFound,
    SystemImageNotInstalled,
)
from modules.android_module.presentation.errors import (
    REMEDIES,
    as_tool_error,
    domain_errors_as_tool_errors,
)

pytestmark = pytest.mark.unit


def _domain_error_types() -> list[type[AndroidModuleError]]:
    return [
        getattr(domain_errors, name)
        for name in domain_errors.__all__
        if name != "AndroidModuleError"
    ]


def test_every_domain_error_has_its_own_row() -> None:
    """Rule 3 §6: a new failure adds a row. Falling through to the base class's
    generic advice is exactly the silence this asserts against."""
    tabled = {error_type for error_type, _ in REMEDIES}
    missing = [t.__name__ for t in _domain_error_types() if t not in tabled]
    assert not missing, f"these domain errors have no remedy of their own: {missing}"


def test_the_base_class_is_the_last_row_and_the_only_fallback() -> None:
    """Order is the dispatch rule: a subclass above its base, the base at the end."""
    assert REMEDIES[-1][0] is AndroidModuleError
    assert [t for t, _ in REMEDIES[:-1] if t is AndroidModuleError] == []


@pytest.mark.parametrize("error_type, remedy", REMEDIES, ids=lambda v: getattr(v, "__name__", ""))
def test_every_remedy_says_what_to_do_next(error_type: type, remedy: str) -> None:
    assert len(remedy) > 40, f"{error_type.__name__}'s remedy is too thin to act on"


def test_a_translated_error_carries_the_class_name_the_evidence_and_the_remedy() -> None:
    """The class name is the stable handle; the prose around it is not."""
    error = as_tool_error(EmulatorNotFound("Pixel_7"))

    assert isinstance(error, ToolError)
    assert "EmulatorNotFound" in str(error)
    assert "Pixel_7" in str(error)
    assert "android_get_installed_emulators" in str(error)


def test_the_evidence_comes_from_the_errors_own_rendering() -> None:
    """`str(exc)` is built from the attributes the domain error carries, so the
    serial reaches the caller without presentation reaching for it."""
    error = as_tool_error(EmulatorAlreadyRunning("Pixel_7", "emulator-5554"))
    assert "emulator-5554" in str(error)


def test_the_first_matching_row_wins_so_siblings_do_not_collide() -> None:
    in_use = str(as_tool_error(EmulatorInUse("Pixel_7", "emulator-5554")))
    already_running = str(as_tool_error(EmulatorAlreadyRunning("Pixel_7", "emulator-5554")))

    assert "android_stop_emulator" in in_use
    assert in_use != already_running


def test_an_unknown_subclass_still_gets_advice_from_the_fallback_row() -> None:
    """An adapter's richer subclass is a domain error too, and may not be tabled."""

    class SomeAdapterFailure(AndroidModuleError):
        pass

    error = as_tool_error(SomeAdapterFailure("boom"))
    assert "SomeAdapterFailure" in str(error)
    assert "Report the message" in str(error)


def test_the_context_manager_translates_a_domain_failure() -> None:
    with pytest.raises(ToolError) as excinfo:
        with domain_errors_as_tool_errors():
            raise SystemImageNotInstalled("system-images;android-34;google_apis;x86_64")

    assert "android_download_image" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, SystemImageNotInstalled)


def test_the_context_manager_lets_a_bug_through_untouched() -> None:
    """A KeyError in this server is not a fact about the device, and dressing it
    up as a tool result would hide it."""
    with pytest.raises(KeyError):
        with domain_errors_as_tool_errors():
            raise KeyError("a bug")


def test_the_context_manager_is_transparent_when_nothing_fails() -> None:
    with domain_errors_as_tool_errors():
        value = 1
    assert value == 1


def test_backend_unavailable_tells_the_caller_not_to_retry() -> None:
    """The one failure where trying another tool is strictly wasted effort."""
    message = str(as_tool_error(BackendUnavailable("emulator", "not on PATH")))
    assert "No retry will fix it" in message
