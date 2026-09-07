"""The configuration's resolution rules, which are pure.

``from_environment`` takes an explicit mapping precisely so these rules can be
asserted without touching the real environment (Rule 1 §4). Nothing here is
patched -- the seam is a parameter, which is the point of it being one. The
half of this file that *is* host-dependent -- where the XDG default actually
lands on this machine -- lives in ``tests/integration/test_config.py``.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from app_automating.modules.navigation_memory_module.infrastructure.navigation_store.config import (
    DEFAULT_DATA_DIRNAME,
    DEFAULT_DATABASE_FILENAME,
    DEFAULT_SCREENSHOT_DIRNAME,
    ENV_PREFIX,
    NavigationStoreConfig,
    default_data_directory,
)
from app_automating.modules.navigation_memory_module.infrastructure.navigation_store.models import (
    MEMORY_URL,
)

pytestmark = pytest.mark.unit


def _from(**environ: str) -> NavigationStoreConfig:
    return NavigationStoreConfig.from_environment(
        {f"{ENV_PREFIX}{key}": value for key, value in environ.items()}
    )


def test_the_prefix_matches_the_products_namespace() -> None:
    assert ENV_PREFIX == "AT_NAVIGATION_MEMORY_"


# ---------------------------------------------------------------------------
# defaults
# ---------------------------------------------------------------------------


def test_an_unconfigured_host_still_gets_a_working_database_path() -> None:
    """The product must work on a fresh machine with no .env file at all."""
    config = NavigationStoreConfig.from_environment({})

    assert config.database_path is not None
    assert config.database_path.name == DEFAULT_DATABASE_FILENAME
    assert DEFAULT_DATA_DIRNAME in config.database_path.parts


def test_the_default_is_a_file_and_never_memory() -> None:
    """A memory that silently evaporates would look like the module not working."""
    assert NavigationStoreConfig.from_environment({}).location.in_memory is False


def test_the_defaults_are_the_documented_ones() -> None:
    config = NavigationStoreConfig()

    assert config.enforce_foreign_keys is True
    assert config.journal_mode == "WAL"
    assert config.synchronous == "NORMAL"
    assert config.busy_timeout_seconds == 10.0
    assert config.store_page_source is True
    assert config.echo_sql is False
    assert config.max_page_source_bytes == 256 * 1024


def test_a_bare_config_is_in_memory() -> None:
    """`NavigationStoreConfig()` builds nothing on disk, which is what a test wants."""
    assert NavigationStoreConfig().location.in_memory is True


# ---------------------------------------------------------------------------
# the XDG default
# ---------------------------------------------------------------------------


def test_an_absolute_xdg_data_home_is_honoured() -> None:
    assert (
        default_data_directory({"XDG_DATA_HOME": "/srv/data"})
        == Path("/srv/data") / DEFAULT_DATA_DIRNAME
    )


@pytest.mark.parametrize("raw", ["relative/path", "", "   "])
def test_a_relative_or_empty_xdg_data_home_is_ignored_as_the_spec_requires(raw: str) -> None:
    """The XDG spec says a relative value must be treated as unset, not resolved."""
    resolved = default_data_directory({"XDG_DATA_HOME": raw})

    assert resolved.is_absolute()
    assert resolved.parts[-3:] == (".local", "share", DEFAULT_DATA_DIRNAME)


def test_screenshots_default_beside_the_database_not_inside_it() -> None:
    config = NavigationStoreConfig.from_environment({"XDG_DATA_HOME": "/srv/data"})

    assert config.screenshot_directory == Path("/srv/data") / DEFAULT_DATA_DIRNAME / (
        DEFAULT_SCREENSHOT_DIRNAME
    )
    assert config.database_path is not None
    assert config.screenshot_directory.parent == config.database_path.parent


# ---------------------------------------------------------------------------
# reading the environment
# ---------------------------------------------------------------------------


def test_an_explicit_path_wins_over_the_default() -> None:
    assert _from(DATABASE_PATH="/srv/nav.db").database_path == Path("/srv/nav.db")


def test_the_memory_sentinel_selects_an_in_memory_database() -> None:
    """The documented way to ask for a database that dies with the process."""
    config = _from(DATABASE_PATH=":memory:")

    assert config.database_path is None
    assert config.location.url == MEMORY_URL


def test_a_tilde_in_a_path_is_expanded() -> None:
    resolved = _from(DATABASE_PATH="~/nav.db").database_path

    assert resolved is not None
    assert "~" not in str(resolved)
    assert resolved.is_absolute()


@pytest.mark.parametrize("raw", ["true", "TRUE", "1", "yes", "on"])
def test_the_truthy_spellings_a_human_writes_all_work(raw: str) -> None:
    assert _from(ECHO_SQL=raw).echo_sql is True


@pytest.mark.parametrize("raw", ["false", "0", "no", "off", "nonsense"])
def test_anything_else_is_false(raw: str) -> None:
    assert _from(ECHO_SQL=raw).echo_sql is False


def test_numbers_are_read_from_the_environment() -> None:
    config = _from(BUSY_TIMEOUT_SECONDS="2.5", MAX_PAGE_SOURCE_BYTES="4096")

    assert config.busy_timeout_seconds == 2.5
    assert config.max_page_source_bytes == 4096


@pytest.mark.parametrize("raw", ["", "   ", "abc", "10s", "1,5"])
def test_a_malformed_number_falls_back_rather_than_refusing_to_start(raw: str) -> None:
    """A typo in a .env file is not a reason to take the whole server down."""
    assert _from(BUSY_TIMEOUT_SECONDS=raw).busy_timeout_seconds == 10.0


def test_whitespace_around_a_value_is_stripped() -> None:
    assert _from(JOURNAL_MODE="  DELETE  ").journal_mode == "DELETE"


def test_an_unset_variable_leaves_the_default_alone() -> None:
    assert _from().enforce_foreign_keys is True


def test_foreign_keys_can_be_disabled_from_the_environment() -> None:
    assert _from(ENFORCE_FOREIGN_KEYS="false").enforce_foreign_keys is False


# ---------------------------------------------------------------------------
# derived values
# ---------------------------------------------------------------------------


def test_a_file_path_becomes_an_aiosqlite_url() -> None:
    location = NavigationStoreConfig(database_path=Path("/srv/nav.db")).location

    assert location.url == "sqlite+aiosqlite:////srv/nav.db"
    assert location.path == Path("/srv/nav.db")


def test_the_busy_timeout_is_offered_in_the_units_sqlite_wants() -> None:
    """Seconds in the config a human edits; milliseconds in the PRAGMA."""
    assert NavigationStoreConfig(busy_timeout_seconds=2.5).busy_timeout_milliseconds == 2500
    assert NavigationStoreConfig(busy_timeout_seconds=-1).busy_timeout_milliseconds == 0


def test_the_config_is_frozen_so_a_replace_is_the_only_way_to_change_it() -> None:
    config = NavigationStoreConfig()

    with pytest.raises(dataclasses.FrozenInstanceError):
        config.echo_sql = True  # type: ignore[misc]

    assert dataclasses.replace(config, echo_sql=True).echo_sql is True


def test_every_setting_can_be_pinned_for_a_test_without_the_environment() -> None:
    """Rule 1 §4: what makes a 1ms lock test real instead of a fake."""
    config = dataclasses.replace(NavigationStoreConfig(), busy_timeout_seconds=0.001)

    assert config.busy_timeout_milliseconds == 1
