"""Where the configuration's defaults actually land on this host.

Integration, not unit, because these read the real environment and the real home
directory (Rule 2 §2: the split is by what the code *touches*, not by how long it
takes). The resolution rules themselves are pure and live in
``tests/unit/test_config.py``; what is asserted here is "does this work on this
machine", which no amount of passing a mapping in can answer.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app_automating.modules.navigation_memory_module.infrastructure.navigation_store.config import (
    DEFAULT_DATA_DIRNAME,
    DEFAULT_DATABASE_FILENAME,
    NavigationStoreConfig,
    default_data_directory,
)

pytestmark = pytest.mark.integration


def test_the_real_environment_resolves_to_a_usable_config() -> None:
    """The default argument is the only impure part of `from_environment`."""
    config = NavigationStoreConfig.from_environment()

    assert config.database_path is not None
    assert config.database_path.is_absolute()
    assert config.screenshot_directory is not None
    assert config.screenshot_directory.is_absolute()


def test_the_default_lands_under_this_users_data_directory() -> None:
    directory = default_data_directory(os.environ)

    assert directory.is_absolute()
    assert directory.name == DEFAULT_DATA_DIRNAME
    assert Path.home() in directory.parents or not str(directory).startswith(str(Path.home()))


def test_the_default_database_is_a_file_under_that_directory() -> None:
    config = NavigationStoreConfig.from_environment()

    assert config.database_path is not None
    assert config.database_path.parent == default_data_directory(os.environ)
    assert config.database_path.name == DEFAULT_DATABASE_FILENAME


def test_resolving_the_config_creates_nothing() -> None:
    """Reading the environment is not the same as claiming a directory.

    The engine creates the directory when it connects, so a process that only
    imports the module leaves no trace on the developer's filesystem.
    """
    before = default_data_directory(os.environ).exists()

    NavigationStoreConfig.from_environment()

    assert default_data_directory(os.environ).exists() is before


def test_a_real_temporary_path_produces_a_url_sqlite_accepts(tmp_path: Path) -> None:
    """A real directory, because a URL that only looks right is not worth much."""
    location = NavigationStoreConfig(database_path=tmp_path / "nav.db").location

    assert location.url.startswith("sqlite+aiosqlite:////")
    assert location.path == tmp_path / "nav.db"
    assert location.directory == tmp_path
    assert location.directory.is_dir()
