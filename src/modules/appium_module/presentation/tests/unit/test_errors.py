"""The remedy table: every domain error becomes a ToolError that says what to do.

Rule 3 §6 asks for three things, and each is asserted below: the translation is
a *table* rather than an if-chain so a new error adds a row; the message carries
the evidence the exception was holding; and it ends in a remedy, because the
client is a model choosing its next tool call and an error without a next step
is an invitation to guess.

Pure, so no mocks -- and none are permitted here anyway (Rule 2 §3): this layer
may fake the application, never the infrastructure, and this file needs neither.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

from modules.appium_module.domain import errors as domain_errors
from modules.appium_module.domain.errors import (
    AppiumModuleError,
    BackendUnavailable,
    DriverNotInstalled,
    ElementNotFound,
    InvalidKeyName,
    InvalidLocator,
    SessionExpired,
    SessionNotFound,
)
from modules.appium_module.presentation.errors import (
    REMEDIES,
    as_tool_error,
    domain_errors_as_tool_errors,
)

pytestmark = pytest.mark.unit

DOMAIN_ERROR_TYPES = [
    value
    for value in vars(domain_errors).values()
    if inspect.isclass(value) and issubclass(value, AppiumModuleError)
]


# -- the table itself ------------------------------------------------------


def test_every_domain_error_has_a_row_or_inherits_one() -> None:
    """A new domain error must not fall off the end of the table unremarked."""
    for error_type in DOMAIN_ERROR_TYPES:
        assert any(issubclass(error_type, row_type) for row_type, _ in REMEDIES), (
            f"{error_type.__name__} has no remedy"
        )


def test_the_fallback_row_is_last_so_it_never_shadows_a_specific_one() -> None:
    """Order is the table's only subtlety: the first match wins, so the base
    class must be at the bottom or every error would get generic advice."""
    assert REMEDIES[-1][0] is AppiumModuleError
    assert AppiumModuleError not in [row_type for row_type, _ in REMEDIES[:-1]]


def test_no_row_is_shadowed_by_a_more_general_one_above_it() -> None:
    """The mechanical form of the same rule, so a row added in the wrong place
    fails here rather than silently producing the wrong advice.

    The violation is a *base class* appearing above one of its subclasses: the
    first match wins, so the general row would answer for the specific one and
    the specific row would be dead code. Specific-above-general is the correct
    order and must not trip this.
    """
    seen: list[type] = []
    for row_type, _ in REMEDIES:
        for earlier in seen:
            assert not issubclass(row_type, earlier), (
                f"{row_type.__name__} is shadowed by {earlier.__name__}, which is listed above it"
            )
        seen.append(row_type)


def test_every_remedy_tells_the_caller_what_to_do_next() -> None:
    """An error list without remedies is half a section (Rule 3 §5)."""
    for error_type, remedy in REMEDIES:
        assert len(remedy) > 40, f"{error_type.__name__}'s remedy is too thin to act on"
        assert remedy.strip().endswith("."), f"{error_type.__name__}'s remedy is unterminated"


def test_remedies_point_at_real_tools_by_name() -> None:
    """The remedy that says "call X" is only useful if X exists. Every tool named
    across the table is one this server actually registers."""
    named = {
        word.strip(".,;")
        for _, remedy in REMEDIES
        for word in remedy.split()
        if word.strip(".,;").startswith(("appium_", "android_"))
    }

    assert named, "no remedy names a follow-up tool"
    assert "appium_get_page_source" in named
    assert "appium_check_environment" in named


# -- what a translated error looks like ------------------------------------


def test_a_translated_error_names_the_class_the_evidence_and_the_remedy() -> None:
    """The three parts, in order. The class name is the stable handle a caller
    can branch on; the prose around it is not."""
    error = as_tool_error(ElementNotFound("accessibility_id", "Wi-Fi", 10.0))

    message = str(error)

    assert message.startswith("ElementNotFound:")
    assert "Wi-Fi" in message
    assert "appium_get_page_source" in message


def test_the_evidence_carried_on_the_exception_reaches_the_message() -> None:
    """Rule 1 §3's payoff at the far end: the attributes the adapter attached are
    what the caller finally reads."""
    error = as_tool_error(SessionNotFound("gone", ("live-1", "live-2")))

    assert "live-1" in str(error)


def test_a_missing_driver_is_told_exactly_which_tool_installs_it() -> None:
    error = as_tool_error(DriverNotInstalled("uiautomator2", ()))

    assert "appium_install_driver" in str(error)


def test_an_expired_session_is_told_to_start_a_new_one_not_to_check_the_id() -> None:
    """The distinction the two error types exist for, visible in the advice."""
    expired = str(as_tool_error(SessionExpired("s1", "dropped")))
    missing = str(as_tool_error(SessionNotFound("s1", ())))

    assert "start a new one" in expired.lower()
    assert "appium_get_open_sessions" in missing


def test_an_unavailable_backend_is_told_that_no_retry_will_help() -> None:
    """A model that retries a provisioning failure burns turns for nothing."""
    assert "No retry" in str(as_tool_error(BackendUnavailable("appium", "not on PATH")))


def test_an_invalid_locator_is_pointed_at_the_page_source_rather_than_a_second_guess() -> None:
    error = as_tool_error(InvalidLocator("css", "div", ("xpath",)))

    assert "appium_get_page_source" in str(error)


def test_an_unknown_key_is_told_the_error_already_lists_the_real_ones() -> None:
    error = as_tool_error(InvalidKeyName("goback", ("back", "home")))

    assert "back" in str(error)


@pytest.mark.parametrize(
    "exc",
    [
        ElementNotFound("text", "Submit", 10.0),
        SessionNotFound("s1", ("s2",)),
        SessionExpired("s1", "dropped"),
        DriverNotInstalled("uiautomator2", ()),
        InvalidLocator("css", "div", ("xpath",)),
        InvalidKeyName("nope", ("back",)),
        BackendUnavailable("appium", "not on PATH"),
        domain_errors.InteractionFailed("tap", "s1", "not clickable"),
        domain_errors.ServerStartFailed("http://x:4723", "died", "log tail"),
        domain_errors.AppNotFound(Path("/tmp/missing.apk")),
    ],
    ids=lambda e: type(e).__name__,
)
def test_no_translation_leaks_a_stack_trace_or_a_repr(exc: AppiumModuleError) -> None:
    """Never a bare `str(exc)` of a foreign object, and never a traceback."""
    message = str(as_tool_error(exc))

    assert "Traceback" not in message
    assert "object at 0x" not in message
    assert message.startswith(f"{type(exc).__name__}:")


def test_an_unlisted_error_still_gets_the_fallback_advice() -> None:
    """The base class is the honest fallback: reported, never swallowed."""

    class BrandNew(AppiumModuleError):
        pass

    message = str(as_tool_error(BrandNew("something went sideways")))

    assert message.startswith("BrandNew:")
    assert "Report the message to the user" in message


# -- the context manager ---------------------------------------------------


def test_a_domain_error_inside_the_scope_becomes_a_tool_error() -> None:
    with pytest.raises(ToolError) as excinfo:
        with domain_errors_as_tool_errors():
            raise domain_errors.AppNotFound(Path("/tmp/missing.apk"))

    assert "missing.apk" in str(excinfo.value)


def test_the_original_exception_is_kept_as_the_cause() -> None:
    """So a server log still shows what really happened, even though the client
    only sees the translated message."""
    original = SessionNotFound("s1", ())

    with pytest.raises(ToolError) as excinfo:
        with domain_errors_as_tool_errors():
            raise original

    assert excinfo.value.__cause__ is original


def test_anything_that_is_not_a_domain_error_passes_straight_through() -> None:
    """Rule 3 §6: an unexpected exception is a bug in this server, not a tool
    result. Dressing it up as one would hide it."""
    with pytest.raises(ZeroDivisionError):
        with domain_errors_as_tool_errors():
            raise ZeroDivisionError("a real bug")


def test_a_scope_that_raises_nothing_is_transparent() -> None:
    with domain_errors_as_tool_errors():
        value = 1 + 1

    assert value == 2
