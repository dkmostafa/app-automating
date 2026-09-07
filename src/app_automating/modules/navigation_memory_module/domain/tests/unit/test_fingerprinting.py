"""The identity rule: what makes two observations the same place.

Unit tests because the code is pure -- hashing a string, walking an XML tree.
Nothing is replaced or patched; there is nothing here to replace.
"""

from __future__ import annotations

import pytest

from app_automating.modules.navigation_memory_module.domain.fingerprinting import (
    FINGERPRINT_LENGTH,
    extract_title,
    fingerprint,
    fingerprint_source,
    resource_ids,
    structure_signature,
)

pytestmark = pytest.mark.unit

PKG = "com.example.shop"


def native(*ids: str, text: str = "Home", extra: str = "") -> str:
    """A page source shaped like real uiautomator output."""
    elements = "".join(
        f'<node class="android.widget.Button" resource-id="{PKG}:id/{name}"/>' for name in ids
    )
    return (
        f'<hierarchy rotation="0">'
        f'<node class="android.widget.TextView" text="{text}"/>{elements}{extra}</hierarchy>'
    )


def flutter(depth: int = 3) -> str:
    """Flutter and some React Native apps expose no resource-ids at all."""
    inner = '<node class="android.view.View"/>' * depth
    return (
        f'<hierarchy rotation="0"><node class="android.widget.FrameLayout">'
        f"{inner}</node></hierarchy>"
    )


# -- the rule ---------------------------------------------------------------


def test_the_same_screen_fingerprints_the_same_twice() -> None:
    assert fingerprint(native("cart", "menu")) == fingerprint(native("cart", "menu"))


def test_two_different_screens_fingerprint_differently() -> None:
    assert fingerprint(native("cart", "menu")) != fingerprint(native("checkout", "pay"))


def test_element_order_does_not_change_identity() -> None:
    """A list that reordered is the same screen; ids are sorted for that reason."""
    assert fingerprint(native("cart", "menu")) == fingerprint(native("menu", "cart"))


def test_changing_text_does_not_change_identity() -> None:
    """The reason text is excluded: "Welcome, Sam" and "Welcome, Alex" are one screen."""
    assert fingerprint(native("cart", text="Welcome, Sam")) == fingerprint(
        native("cart", text="Welcome, Alex")
    )


def test_repeating_a_row_does_not_change_identity() -> None:
    """A cart holding two items and the same cart holding three are one screen.

    This is the property that stops the map growing a node per visit.
    """
    one_row = native("cart", extra='<node class="android.widget.TextView" resource-id="x:id/row"/>')
    three_rows = native(
        "cart",
        extra='<node class="android.widget.TextView" resource-id="x:id/row"/>' * 3,
    )
    assert fingerprint(one_row) == fingerprint(three_rows)


def test_a_new_control_does_change_identity() -> None:
    assert fingerprint(native("cart")) != fingerprint(native("cart", "checkout"))


# -- the fallback, which matters as much as the rule ------------------------


def test_an_app_with_ids_is_fingerprinted_from_them() -> None:
    computed = fingerprint_source(native("cart", "menu"))

    assert computed.basis == "resource-ids"
    assert computed.element_count == 2
    assert computed.usable


def test_an_app_with_no_ids_falls_back_to_structure() -> None:
    """Without this, every Flutter screen would hash identically and the whole
    map would collapse to a single node -- silently."""
    computed = fingerprint_source(flutter())

    assert computed.basis == "structure"
    assert computed.usable


def test_two_flutter_screens_of_different_shape_are_told_apart() -> None:
    assert fingerprint(flutter(2)) != fingerprint(flutter(5))


def test_the_two_rules_live_in_separate_hash_spaces() -> None:
    """An id-based and a structure-based fingerprint must never collide, or a
    native screen could be mistaken for a Flutter one."""
    assert fingerprint(native("cart")) != fingerprint(flutter())


@pytest.mark.parametrize("source", ["", "   ", "not xml at all", "<hierarchy><unclosed>"])
def test_an_unusable_source_says_so_rather_than_pretending(source: str) -> None:
    """A screen captured mid-transition truncates. That is normal, not an error --
    but a caller must not treat the resulting match as recognition."""
    computed = fingerprint_source(source)

    assert computed.basis == "empty"
    assert not computed.usable
    assert computed.value


def test_unstable_platform_ids_are_ignored() -> None:
    """The status-bar background is present on every screen and authored by
    nobody; including it would add noise to every fingerprint."""
    bar = '<node class="android.view.View" resource-id="android:id/statusBarBackground"/>'
    with_bars = native("cart", extra=bar)
    assert fingerprint(with_bars) == fingerprint(native("cart"))


# -- the pieces --------------------------------------------------------------


def test_resource_ids_are_sorted_and_deduplicated() -> None:
    found = resource_ids(native("menu", "cart", "menu"))

    assert found == tuple(sorted(found))
    assert len(found) == len(set(found))


def test_structure_signature_records_depth() -> None:
    """Two screens built from the same widgets in a different arrangement must
    not collide, and depth is the only signal left once ids are gone."""
    signature = structure_signature(flutter(2))

    assert signature
    assert any(entry.startswith("0:") for entry in signature)
    assert any(entry.startswith("2:") for entry in signature)


def test_a_fingerprint_is_short_enough_to_read_and_long_enough_to_be_unique() -> None:
    assert len(fingerprint(native("cart"))) == FINGERPRINT_LENGTH


# -- titles, which are labels and never identity -----------------------------


def test_a_title_is_read_from_the_first_title_bearing_element() -> None:
    assert extract_title(native("cart", text="Checkout")) == "Checkout"


def test_a_paragraph_is_not_a_title() -> None:
    body = native("cart", text="x" * 200)

    assert extract_title(body, fallback="fallback") == "fallback"


@pytest.mark.parametrize("source", ["", "not xml"])
def test_an_unreadable_source_falls_back(source: str) -> None:
    assert extract_title(source, fallback="Unknown") == "Unknown"


def test_the_title_never_takes_part_in_identity() -> None:
    """A title that changed would otherwise silently split one screen in two."""
    assert fingerprint(native("cart", text="Cart")) == fingerprint(native("cart", text="Basket"))
