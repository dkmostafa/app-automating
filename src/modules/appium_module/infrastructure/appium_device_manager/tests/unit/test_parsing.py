"""The pure layer: validation, tool-output parsing, and failure classification.

Everything here runs before a subprocess is started or a round trip is made,
which is exactly why it can be unit-tested on a host with no Appium (Rule 1 §3,
Rule 2 §2). No mocks -- there is nothing to mock, and Rule 1 §6 forbids it
regardless.
"""

from __future__ import annotations

import json
import struct
import zlib

import pytest

from modules.appium_module.domain.errors import (
    AppNotFound,
    ElementNotFound,
    InvalidCoordinates,
    InvalidDeviceId,
    InvalidKeyName,
    InvalidLocator,
    InvalidScroll,
    SessionExpired,
    SessionStartFailed,
)
from modules.appium_module.infrastructure.appium_device_manager.errors import WebDriverCallError
from modules.appium_module.infrastructure.appium_device_manager.parsing import (
    ANDROID_KEYCODES,
    LOCATOR_BY,
    classify_interaction_failure,
    classify_session_failure,
    parse_driver_version,
    parse_installed_drivers,
    parse_server_status,
    parse_version_output,
    png_dimensions,
    resolve_locator,
    truncate,
    validate_app_path,
    validate_coordinates,
    validate_device_id,
    validate_key,
    validate_locator,
    validate_scroll,
)

pytestmark = pytest.mark.unit


def _png(width: int, height: int) -> bytes:
    header = struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00"
    crc = struct.pack(">I", zlib.crc32(b"IHDR" + header))
    chunk = struct.pack(">I", 13) + b"IHDR" + header + crc
    return b"\x89PNG\r\n\x1a\n" + chunk


# --------------------------------------------------------------------------
# device ids
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "device_id", ["emulator-5554", "R58M12ABCDE", "192.168.1.5:5555", "abc.def_1-2"]
)
def test_real_serial_shapes_are_accepted(device_id: str) -> None:
    """Emulator serials, hardware serials and network-attached devices all pass."""
    assert validate_device_id(device_id) == device_id


def test_surrounding_whitespace_is_stripped_rather_than_rejected() -> None:
    """A serial pasted from a table gains spaces; that is not the caller's mistake."""
    assert validate_device_id("  emulator-5554\n") == "emulator-5554"


@pytest.mark.parametrize("device_id", ["", "   ", "has space", "quote'd", "semi;colon", "a/b"])
def test_a_serial_that_could_not_be_real_is_rejected_before_anything_runs(device_id: str) -> None:
    """The alphabet is narrow because a serial goes onto a command line."""
    with pytest.raises(InvalidDeviceId):
        validate_device_id(device_id)


# --------------------------------------------------------------------------
# locators
# --------------------------------------------------------------------------


@pytest.mark.parametrize("strategy", sorted(LOCATOR_BY))
def test_every_supported_strategy_resolves(strategy: str) -> None:
    by, value = resolve_locator(strategy, "something")

    assert by
    assert value


def test_a_strategy_is_matched_case_insensitively() -> None:
    assert validate_locator("Accessibility_ID", "ok")[0] == "accessibility_id"


@pytest.mark.parametrize("strategy", ["css", "css_selector", "name", ""])
def test_an_unknown_strategy_is_rejected_and_lists_the_real_ones(strategy: str) -> None:
    with pytest.raises(InvalidLocator) as excinfo:
        validate_locator(strategy, "ok")

    assert "accessibility_id" in str(excinfo.value)


@pytest.mark.parametrize("selector", ["", "   "])
def test_an_empty_selector_is_rejected_even_with_a_valid_strategy(selector: str) -> None:
    """An empty selector matches everything or nothing; both are a caller bug."""
    with pytest.raises(InvalidLocator):
        validate_locator("xpath", selector)


def test_text_becomes_a_uiselector_because_android_has_no_find_by_text() -> None:
    """The whole reason `text` is a first-class strategy for the caller."""
    by, value = resolve_locator("text", "Sign in")

    assert "uiautomator" in by
    assert value == 'new UiSelector().text("Sign in")'


def test_a_quote_in_the_text_is_escaped_rather_than_breaking_the_selector() -> None:
    """A UiSelector is Java source compiled on the device; an app label with an
    apostrophe or a quote is entirely ordinary and must not break it."""
    _, value = resolve_locator("text", 'say "hi"')

    assert value == 'new UiSelector().text("say \\"hi\\"")'


def test_a_backslash_in_the_text_is_escaped_too() -> None:
    _, value = resolve_locator("text", "a\\b")

    assert value == 'new UiSelector().text("a\\\\b")'


def test_a_raw_uiautomator_expression_is_passed_through_untouched() -> None:
    """`uiautomator` is the escape hatch; rewriting it would defeat it."""
    _, value = resolve_locator("uiautomator", 'new UiSelector().textContains("x")')

    assert value == 'new UiSelector().textContains("x")'


# --------------------------------------------------------------------------
# coordinates, keys, scrolling
# --------------------------------------------------------------------------


def test_coordinates_on_the_screen_are_accepted_including_the_origin() -> None:
    assert validate_coordinates(0, 0) == (0, 0)
    assert validate_coordinates(540, 1200) == (540, 1200)


@pytest.mark.parametrize(("x", "y"), [(-1, 0), (0, -1), (-5, -5)])
def test_a_negative_coordinate_is_rejected(x: int, y: int) -> None:
    """The mistake that actually happens: an offset subtracted twice."""
    with pytest.raises(InvalidCoordinates):
        validate_coordinates(x, y)


def test_a_large_coordinate_is_allowed_because_purity_forbids_asking_the_device() -> None:
    """The far edge cannot be checked without a round trip, and this layer must
    not make one. Documented here so the gap is deliberate rather than missed."""
    assert validate_coordinates(99999, 99999) == (99999, 99999)


@pytest.mark.parametrize(("key", "code"), [("back", 4), ("home", 3), ("enter", 66), ("delete", 67)])
def test_the_common_keys_map_to_their_android_keycodes(key: str, code: int) -> None:
    assert validate_key(key) == code


def test_backspace_and_delete_are_the_same_key() -> None:
    """Callers write both; refusing one would be a vocabulary trap."""
    assert validate_key("backspace") == validate_key("delete")


@pytest.mark.parametrize("spelling", ["BACK", " Back ", "app-switch", "app switch"])
def test_key_names_are_normalised_for_case_spaces_and_hyphens(spelling: str) -> None:
    assert validate_key(spelling) in (ANDROID_KEYCODES["back"], ANDROID_KEYCODES["app_switch"])


@pytest.mark.parametrize("key", ["goback", "", "4", "return"])
def test_an_unknown_key_is_rejected_and_the_error_lists_the_real_ones(key: str) -> None:
    with pytest.raises(InvalidKeyName) as excinfo:
        validate_key(key)

    assert "back" in str(excinfo.value)


def test_a_raw_keycode_number_is_not_accepted_as_a_key_name() -> None:
    """The tool documents names, not numbers; accepting "4" would make the
    documented contract a lie."""
    with pytest.raises(InvalidKeyName):
        validate_key("4")


@pytest.mark.parametrize("direction", ["up", "down", "left", "right"])
def test_every_scroll_direction_is_accepted(direction: str) -> None:
    assert validate_scroll(direction, 0.5) == (direction, 0.5)


def test_a_direction_is_normalised_for_case_and_whitespace() -> None:
    assert validate_scroll(" Down ", 1.0) == ("down", 1.0)


def test_an_unknown_direction_is_rejected() -> None:
    with pytest.raises(InvalidScroll):
        validate_scroll("sideways", 0.5)


@pytest.mark.parametrize("distance", [0.0, -0.5, 1.5, 2.0])
def test_a_distance_outside_zero_to_one_is_rejected(distance: float) -> None:
    """0 is a gesture that goes nowhere; above 1 asks the finger off the screen."""
    with pytest.raises(InvalidScroll) as excinfo:
        validate_scroll("down", distance)

    assert "distance" in str(excinfo.value)


def test_a_full_screen_scroll_is_the_largest_allowed() -> None:
    assert validate_scroll("down", 1.0) == ("down", 1.0)


# --------------------------------------------------------------------------
# app paths -- the one validator that looks at a disk
# --------------------------------------------------------------------------


def test_no_app_path_is_not_an_error_because_the_field_is_optional() -> None:
    assert validate_app_path(None) is None


def test_an_apk_that_exists_is_returned_resolved(tmp_path) -> None:
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"not really an apk")

    assert validate_app_path(apk) == apk


def test_an_apk_that_is_not_there_is_a_caller_error_not_a_session_failure(tmp_path) -> None:
    """Caught before a server is spawned, so the caller waits milliseconds
    rather than a minute for the same answer."""
    with pytest.raises(AppNotFound):
        validate_app_path(tmp_path / "missing.apk")


def test_a_directory_is_not_an_app(tmp_path) -> None:
    with pytest.raises(AppNotFound):
        validate_app_path(tmp_path)


# --------------------------------------------------------------------------
# reading tool output
# --------------------------------------------------------------------------


def test_a_bare_version_is_parsed() -> None:
    assert parse_version_output("3.1.1\n") == "3.1.1"


def test_a_version_is_found_even_behind_a_wrapper_scripts_chatter() -> None:
    """An nvm shim or a wrapper prepends a line; the version is matched, not assumed."""
    assert parse_version_output("Now using node v25.2.1\nv3.1.1\n") == "25.2.1"


def test_a_prerelease_version_keeps_its_suffix() -> None:
    assert parse_version_output("3.0.0-rc.2") == "3.0.0-rc.2"


def test_output_with_no_version_is_none_rather_than_a_guess() -> None:
    assert parse_version_output("command not found") is None


def test_driver_names_come_from_the_json_form() -> None:
    """The human form is ANSI-coloured progress output; parsing that is a parser
    waiting to break on the next release."""
    payload = json.dumps({"uiautomator2": {"version": "6.3.0"}, "espresso": {"version": "4.2.0"}})

    assert parse_installed_drivers(payload) == ("espresso", "uiautomator2")


def test_no_drivers_is_an_empty_tuple_not_a_failure() -> None:
    assert parse_installed_drivers("{}") == ()


@pytest.mark.parametrize("payload", ["", "not json", "[]", "null", "3"])
def test_unparseable_driver_output_reads_as_no_drivers(payload: str) -> None:
    """A broken Appium install must report "no drivers", which check_environment
    then renders as a not-ready host -- not an exception from a pure function."""
    assert parse_installed_drivers(payload) == ()


def test_a_drivers_version_is_read_from_the_same_payload() -> None:
    payload = json.dumps({"uiautomator2": {"version": "6.3.0"}})

    assert parse_driver_version(payload, "uiautomator2") == "6.3.0"
    assert parse_driver_version(payload, "espresso") is None


def test_a_server_status_body_yields_its_version() -> None:
    body = json.dumps({"value": {"build": {"version": "3.1.1"}}})

    assert parse_server_status(body) == "3.1.1"


@pytest.mark.parametrize("body", ["", "not json", "{}", '{"value": {}}'])
def test_a_status_body_without_a_version_is_none(body: str) -> None:
    """None here means "did not say", and the caller must not read it as "down" --
    the server answering at all is the liveness signal."""
    assert parse_server_status(body) is None


# --------------------------------------------------------------------------
# images and truncation
# --------------------------------------------------------------------------


def test_a_pngs_real_size_is_read_from_its_header() -> None:
    assert png_dimensions(_png(1080, 2400)) == (1080, 2400)


@pytest.mark.parametrize(
    "data", [b"", b"too short", b"\x89PNG\r\n\x1a\n" + b"\x00" * 20, b"GIF89a" + b"\x00" * 40]
)
def test_anything_that_is_not_a_png_yields_no_dimensions(data: bytes) -> None:
    assert png_dimensions(data) is None


def test_short_text_is_returned_whole_and_unmarked() -> None:
    assert truncate("hello", 100) == ("hello", False, 5)


def test_no_limit_returns_everything() -> None:
    assert truncate("hello", None) == ("hello", False, 5)


def test_a_long_string_is_cut_and_reports_its_original_length() -> None:
    """The original length travels with the text so a caller can tell an empty
    screen from the first 3 characters of a large one."""
    assert truncate("abcdefghij", 3) == ("abc", True, 10)


@pytest.mark.parametrize("limit", [0, -1])
def test_a_nonpositive_limit_means_no_limit_rather_than_an_empty_result(limit: int) -> None:
    assert truncate("abc", limit) == ("abc", False, 3)


# --------------------------------------------------------------------------
# classification -- Rule 0 §3, a table rather than an if-chain
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "detail",
    [
        "Could not find a connected Android device",
        "Unable to find an active device or emulator with OS 13",
    ],
)
def test_a_missing_device_is_classified_as_a_session_start_failure(detail: str) -> None:
    exc = classify_session_failure("emulator-5554", detail)

    assert isinstance(exc, SessionStartFailed)
    assert exc.device_id == "emulator-5554"


def test_an_unreadable_app_is_classified_from_the_servers_own_words() -> None:
    exc = classify_session_failure(
        "emulator-5554", "The app at /x.apk does not exist or is not accessible"
    )

    assert isinstance(exc, SessionStartFailed)
    assert "app file" in str(exc)


def test_an_unrecognised_session_failure_still_becomes_a_typed_error() -> None:
    """The honest fallback: unclassified is reported, never swallowed."""
    exc = classify_session_failure("emulator-5554", "An unknown server-side error occurred")

    assert isinstance(exc, SessionStartFailed)
    assert "unknown server-side error" in str(exc)


@pytest.mark.parametrize(
    "detail",
    [
        "invalid session id",
        "A session is either terminated or not started",
        "session is either terminated or not started",
    ],
)
def test_a_dropped_session_is_classified_as_expired_not_as_a_failed_gesture(detail: str) -> None:
    """The one interaction failure worth naming: the remedy is "start a new
    session", where every other one is "look at the screen again"."""
    exc = classify_interaction_failure("tap", "s1", detail)

    assert isinstance(exc, SessionExpired)
    assert exc.session_id == "s1"


def test_any_other_interaction_failure_keeps_the_action_and_the_reason() -> None:
    exc = classify_interaction_failure("tap", "s1", "Element is not clickable")

    assert isinstance(exc, WebDriverCallError)
    assert exc.action == "tap"
    assert "not clickable" in str(exc)


def test_classification_is_case_insensitive_because_servers_disagree_about_case() -> None:
    exc = classify_interaction_failure("tap", "s1", "INVALID SESSION ID")

    assert isinstance(exc, SessionExpired)


def test_an_element_lookup_failure_is_not_produced_by_the_interaction_table() -> None:
    """`find_element` raises the located form itself; the table must not shadow it
    with a generic interaction error."""
    exc = classify_interaction_failure("find_element", "s1", "no such element")

    assert not isinstance(exc, ElementNotFound)
