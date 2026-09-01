"""Unit tests for ``domain/errors.py`` -- the vocabulary use cases catch."""

from __future__ import annotations

import copy
import pickle
from pathlib import Path

import pytest

from modules.android_module.domain import errors as domain_errors
from modules.android_module.domain.errors import (
    AndroidModuleError,
    BackendFailure,
    BackendUnavailable,
    DeviceNotFound,
    EmulatorAlreadyRunning,
    EmulatorBootTimeout,
    EmulatorInUse,
    EmulatorNotFound,
    SystemImageNotInstalled,
)

pytestmark = pytest.mark.unit

SAMPLES = [
    EmulatorNotFound("pixel"),
    EmulatorInUse("pixel", "emulator-5554"),
    EmulatorAlreadyRunning("pixel", "emulator-5554"),
    EmulatorBootTimeout("pixel", "emulator-5554", 60.0),
    DeviceNotFound("emulator-5554"),
    SystemImageNotInstalled("system-images;android-34;google_apis;x86_64", Path("/sdk/x")),
    BackendUnavailable("adb missing", "looked in PATH"),
    BackendFailure("adb devices", "boom"),
]


@pytest.mark.parametrize("error", SAMPLES, ids=lambda e: type(e).__name__)
def test_every_domain_error_shares_one_base(error: Exception) -> None:
    """A use case can catch everything the module reports with one except."""
    assert isinstance(error, AndroidModuleError)


@pytest.mark.parametrize("error", SAMPLES, ids=lambda e: type(e).__name__)
def test_every_domain_error_round_trips(error: Exception) -> None:
    assert str(type(error)(*error.args)) == str(error)
    assert str(copy.copy(error)) == str(error)
    assert str(pickle.loads(pickle.dumps(error))) == str(error)


@pytest.mark.parametrize("error", SAMPLES, ids=lambda e: type(e).__name__)
def test_no_domain_error_formats_into_its_own_arguments(error: Exception) -> None:
    """The message belongs in __str__, not in what was handed to Exception."""
    assert str(error) not in [a for a in error.args if isinstance(a, str)]


def test_errors_carry_the_fact_as_attributes() -> None:
    assert EmulatorNotFound("pixel").name == "pixel"
    assert EmulatorInUse("pixel", "emulator-5554").device_id == "emulator-5554"
    assert EmulatorBootTimeout("pixel", None, 60.0).timeout_seconds == 60.0
    assert DeviceNotFound("emulator-5554").device_id == "emulator-5554"


def test_optional_evidence_is_optional() -> None:
    assert EmulatorInUse("pixel").device_id is None
    assert SystemImageNotInstalled("img").expected_path is None
    assert "never attached" in str(EmulatorBootTimeout("pixel", None, 5.0))


def test_the_domain_names_no_android_sdk_concept() -> None:
    """These names are the product's, not the SDK's -- no AVD, no adb, no avdmanager."""
    for name in domain_errors.__all__:
        lowered = name.lower()
        for sdk_word in ("avd", "adb", "sdkmanager", "avdmanager"):
            assert sdk_word not in lowered, f"{name} leaks an SDK concept into the domain"
