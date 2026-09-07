"""Unit tests for ``errors.py``.

Two properties matter here and neither needs a device: every error carries its
evidence as typed attributes (Rule 1 §3), and every error is *also* the domain
error a use case would catch (Rule 0 §2).
"""

from __future__ import annotations

import copy
import pickle
from pathlib import Path

import pytest

from app_automating.modules.android_module.domain import (
    AndroidModuleError,
    BackendFailure,
    BackendUnavailable,
    DeviceNotFound,
    EmulatorAlreadyExists,
    EmulatorNotFound,
    InvalidDeviceId,
    SystemImageNotInstalled,
)
from app_automating.modules.android_module.infrastructure.android_emulator_manager import (
    AndroidToolNotFoundError,
    AvdAlreadyExistsError,
    AvdNotFoundError,
    CommandFailedError,
    CommandResult,
    CommandTimeoutError,
    DeviceNotFoundError,
    NotAnEmulatorError,
    SystemImageNotInstalledError,
)

pytestmark = pytest.mark.unit

FAILED = CommandResult(
    argv=("avdmanager", "list", "avd"),
    returncode=1,
    stdout="",
    stderr="Error: boom",
    duration_seconds=0.1,
)


@pytest.mark.parametrize(
    ("error", "domain_type"),
    [
        (AvdNotFoundError("pixel", Path("/avd")), EmulatorNotFound),
        (AvdAlreadyExistsError("pixel"), EmulatorAlreadyExists),
        (DeviceNotFoundError("emulator-5554"), DeviceNotFound),
        (NotAnEmulatorError("abc123"), InvalidDeviceId),
        (
            SystemImageNotInstalledError("system-images;android-34;google_apis;x86_64"),
            SystemImageNotInstalled,
        ),
        (AndroidToolNotFoundError("adb", "adb"), BackendUnavailable),
        (CommandFailedError(FAILED), BackendFailure),
        (CommandTimeoutError(("adb", "devices"), 0.5), BackendFailure),
    ],
)
def test_every_adapter_error_is_also_the_domain_error(
    error: Exception, domain_type: type[Exception]
) -> None:
    """A use case catches the domain type and gets the adapter's error for free."""
    assert isinstance(error, domain_type)
    assert isinstance(error, AndroidModuleError)


def test_errors_carry_their_evidence_as_attributes_not_message_text() -> None:
    not_found = AvdNotFoundError("pixel", Path("/home/x/.android/avd"))
    assert not_found.name == "pixel"
    assert not_found.avd_home == Path("/home/x/.android/avd")

    failed = CommandFailedError(FAILED)
    assert failed.result is FAILED
    assert failed.argv == ("avdmanager", "list", "avd")
    assert failed.detail == "Error: boom"

    timed_out = CommandTimeoutError(("adb", "devices"), 0.5)
    assert timed_out.timeout_seconds == 0.5

    missing_tool = AndroidToolNotFoundError("adb", "adb", ("/a/adb", "PATH"))
    assert missing_tool.tool == "adb"
    assert missing_tool.searched == ("/a/adb", "PATH")


@pytest.mark.parametrize(
    "error",
    [
        AvdNotFoundError("pixel", Path("/avd")),
        CommandFailedError(FAILED),
        CommandTimeoutError(("adb", "devices"), 0.5),
        AndroidToolNotFoundError("adb", "adb", ("/a/adb",)),
        DeviceNotFoundError("emulator-5554", ("emulator-5556",)),
    ],
    ids=lambda e: type(e).__name__,
)
def test_errors_survive_copy_and_pickle(error: Exception) -> None:
    """Rule 0 §3 (LSP): ``type(exc)(*exc.args)`` must reconstruct the exception."""
    assert str(type(error)(*error.args)) == str(error)
    assert str(copy.copy(error)) == str(error)
    assert str(pickle.loads(pickle.dumps(error))) == str(error)


def test_messages_name_the_thing_that_went_wrong() -> None:
    assert "pixel" in str(AvdNotFoundError("pixel", Path("/avd")))
    assert "emulator-<port>" in str(NotAnEmulatorError("abc123"))
    assert "force=True" in str(AvdAlreadyExistsError("pixel"))
