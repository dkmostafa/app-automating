"""Integration tests for ``filesystem.py``: a real AVD home on disk.

``tmp_path`` is a real directory, not a fake filesystem -- these write actual
``.ini`` files and read them back, which is the only way to prove the store
agrees with what avdmanager writes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from modules.android_module.infrastructure.android_emulator_manager import (
    AvdNotFoundError,
    AvdStore,
)

pytestmark = pytest.mark.integration


def _write_avd(home: Path, name: str, path_line: str | None = None) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    payload = home / f"{name}.avd"
    payload.mkdir(exist_ok=True)
    body = f"avd.ini.encoding=UTF-8\npath={path_line or payload}\ntarget=android-34\n"
    (home / f"{name}.ini").write_text(body, encoding="utf-8")
    return payload


def test_the_ini_file_is_the_avds_identity(tmp_path: Path) -> None:
    store = AvdStore(tmp_path)
    assert store.ini_path("pixel") == tmp_path / "pixel.ini"
    assert not store.exists("pixel")
    _write_avd(tmp_path, "pixel")
    assert store.exists("pixel")


def test_require_raises_with_the_avd_home_attached(tmp_path: Path) -> None:
    with pytest.raises(AvdNotFoundError) as excinfo:
        AvdStore(tmp_path).require("absent")
    assert excinfo.value.name == "absent"
    assert excinfo.value.avd_home == tmp_path


def test_require_is_silent_when_the_avd_is_there(tmp_path: Path) -> None:
    _write_avd(tmp_path, "pixel")
    AvdStore(tmp_path).require("pixel")


def test_names_reads_the_ini_stems_without_a_subprocess(tmp_path: Path) -> None:
    _write_avd(tmp_path, "b_avd")
    _write_avd(tmp_path, "a_avd")
    (tmp_path / "not_an_avd.txt").write_text("ignore me", encoding="utf-8")
    assert AvdStore(tmp_path).names() == ("a_avd", "b_avd")


def test_names_of_an_empty_home_is_empty(tmp_path: Path) -> None:
    assert AvdStore(tmp_path).names() == ()


def test_directory_trusts_the_path_recorded_in_the_ini(tmp_path: Path) -> None:
    """A renamed AVD keeps its original directory, so ``<name>.avd`` is a guess
    and ``path=`` is the truth."""
    elsewhere = tmp_path / "moved" / "original_name.avd"
    elsewhere.mkdir(parents=True)
    _write_avd(tmp_path, "renamed", path_line=str(elsewhere))
    assert AvdStore(tmp_path).directory("renamed") == elsewhere


def test_directory_falls_back_to_the_conventional_name(tmp_path: Path) -> None:
    home = tmp_path
    home.mkdir(parents=True, exist_ok=True)
    (home / "no_path.ini").write_text("target=android-34\n", encoding="utf-8")
    assert AvdStore(home).directory("no_path") == home / "no_path.avd"


def test_directory_of_an_unreadable_ini_falls_back_rather_than_raising(tmp_path: Path) -> None:
    assert AvdStore(tmp_path).directory("never_written") == tmp_path / "never_written.avd"


def test_the_store_reads_the_real_avd_home(avd_home: Path) -> None:
    """Against the developer's actual AVDs: every name has a real .ini."""
    store = AvdStore(avd_home)
    for name in store.names():
        assert store.exists(name)
        assert store.ini_path(name).is_file()
