"""What makes two observations of a device the same place.

This is the module's central business rule, which is why it is in the domain
rather than beside the database. "Is this the screen I was on yesterday" is a
question about navigation, not about SQLite, and a second storage backend must
not have to reimplement the answer to keep the memory consistent.

It stays pure: ``xml`` and ``hashlib`` are standard-library computation over a
string, with no I/O of any kind, so this file imports on a host with no
database, no Android SDK and no Appium.

The rule, and why it is this one
--------------------------------

A screen is fingerprinted from the **structure** of its UI, never its content.
The sorted ``resource-id`` values of the elements present are the signal: they
are assigned by the app's developer, they are stable across runs, and they do
not change when the data does. A cart holding two items and the same cart
holding three produce the same fingerprint, which is what stops the map growing
a new node on every visit.

Text is deliberately excluded. Including it would make ``Welcome, Sam`` and
``Welcome, Alex`` different places, and a badge counting up from 2 to 3 would
mint a screen that is never seen again.

The fallback matters as much as the rule
----------------------------------------

Flutter, and some React Native apps, expose **no resource-ids at all**. Hashing
ids alone would give every screen in such an app an identical fingerprint and
collapse its entire map to a single node -- the module would appear to work and
would in fact be recording nothing. So when an observation carries no usable
ids, the fingerprint falls back to the *shape* of the hierarchy: the element
class names in document order, depth-tagged. Coarser, and much better than one
node.

:func:`fingerprint_source` reports which of the two it used, because "this app
exposes no ids" is something a caller may want to know and is invisible in the
hash itself.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from typing import Literal

__all__ = [
    "FINGERPRINT_LENGTH",
    "FingerprintBasis",
    "Fingerprint",
    "fingerprint_source",
    "fingerprint",
    "resource_ids",
    "structure_signature",
    "extract_title",
]

#: Hex characters kept from the digest. 32 is 128 bits -- far past collision
#: risk for the thousands of screens one app has, and half the width of a full
#: sha256 in every row and every log line.
FINGERPRINT_LENGTH = 32

#: Which rule produced a fingerprint. ``"empty"`` means there was nothing to
#: hash at all -- an unparseable or absent page source.
FingerprintBasis = Literal["resource-ids", "structure", "empty"]

#: Attributes that carry an element's developer-assigned id, in preference
#: order. Android's uiautomator emits ``resource-id``; some drivers and some
#: cross-platform toolkits emit one of the others instead.
ID_ATTRIBUTES = ("resource-id", "resourceId", "name", "id")

#: Attributes a human-readable screen title might be hiding in.
TITLE_ATTRIBUTES = ("text", "content-desc", "contentDescription", "label")

#: Element classes whose text is a plausible screen title rather than content.
TITLE_BEARING = ("TextView", "Toolbar", "ActionBar", "AppBarLayout", "StaticText")

#: Ids matching these are generated per-inflation rather than authored, so they
#: are noise: including them would make the fingerprint unstable across visits.
UNSTABLE_ID_MARKERS = ("android:id/statusBarBackground", "android:id/navigationBarBackground")


@dataclass(frozen=True, slots=True)
class Fingerprint:
    """A computed fingerprint, and which rule produced it."""

    value: str
    basis: FingerprintBasis
    #: How many distinct ids or elements went into it. Zero means the page
    #: source held nothing usable, and the fingerprint is the empty-source one.
    element_count: int = 0

    @property
    def usable(self) -> bool:
        """False when there was nothing to hash; every empty source collides."""
        return self.basis != "empty"

    def __str__(self) -> str:
        return self.value


def _digest(parts: list[str], salt: str) -> str:
    """A stable short hash. The salt keeps the two rules in separate spaces, so
    an id-based and a structure-based fingerprint can never collide."""
    joined = "\n".join(parts)
    return hashlib.sha256(f"{salt}\x00{joined}".encode()).hexdigest()[:FINGERPRINT_LENGTH]


def _parse(page_source: str) -> ElementTree.Element | None:
    """The hierarchy, or ``None`` when it cannot be read.

    A malformed page source is a normal event -- a screen captured mid-
    transition truncates -- so it is not an error here. The caller gets an
    unusable fingerprint and decides.
    """
    if not page_source or not page_source.strip():
        return None
    try:
        return ElementTree.fromstring(page_source)
    except ElementTree.ParseError:
        return None


def _element_id(element: ElementTree.Element) -> str:
    for attribute in ID_ATTRIBUTES:
        value = element.get(attribute, "").strip()
        if value:
            return value
    return ""


def resource_ids(page_source: str) -> tuple[str, ...]:
    """Every developer-assigned id in the hierarchy, sorted and deduplicated.

    Sorted because document order changes when a list reorders, and the screen
    is still the same screen. Deduplicated because a list of ten rows sharing
    one row id says the same thing about identity as one row does.
    """
    root = _parse(page_source)
    if root is None:
        return ()
    found = {
        identifier
        for element in root.iter()
        if (identifier := _element_id(element))
        and not any(marker in identifier for marker in UNSTABLE_ID_MARKERS)
    }
    return tuple(sorted(found))


def structure_signature(page_source: str) -> tuple[str, ...]:
    """The shape of the hierarchy: each element's class, tagged with its depth.

    The fallback for apps that expose no ids. Depth is included so that two
    screens built from the same widget vocabulary in different arrangements do
    not collide, and document order is kept for the same reason -- unlike ids,
    the arrangement *is* the only signal left.
    """
    root = _parse(page_source)
    if root is None:
        return ()

    signature: list[str] = []

    def walk(element: ElementTree.Element, depth: int) -> None:
        name = element.get("class") or element.tag
        signature.append(f"{depth}:{name}")
        for child in element:
            walk(child, depth + 1)

    walk(root, 0)
    return tuple(signature)


def fingerprint_source(page_source: str) -> Fingerprint:
    """Fingerprint an observation, reporting which rule was used.

    Prefers ids; falls back to structure when the app exposes none; reports
    ``"empty"`` when the source held nothing to hash, in which case every
    unparseable screen shares one fingerprint and the caller should not treat
    a match as recognition.
    """
    ids = resource_ids(page_source)
    if ids:
        return Fingerprint(_digest(list(ids), "ids"), "resource-ids", len(ids))

    shape = structure_signature(page_source)
    if shape:
        return Fingerprint(_digest(list(shape), "structure"), "structure", len(shape))

    return Fingerprint(_digest([], "empty"), "empty", 0)


def fingerprint(page_source: str) -> str:
    """Just the hash, for the callers that do not care how it was derived."""
    return fingerprint_source(page_source).value


def extract_title(page_source: str, fallback: str | None = None) -> str | None:
    """A best-effort human label for a screen.

    Never used for matching -- :func:`fingerprint_source` decides identity, and
    a title that changed would otherwise silently split a screen in two. This
    exists so a listing reads "Checkout" instead of a hex string.

    The heuristic is deliberately shallow: the first non-empty text on a
    title-bearing element near the top of the tree. A wrong guess costs a
    slightly worse label and nothing else.
    """
    root = _parse(page_source)
    if root is None:
        return fallback

    for element in root.iter():
        name = element.get("class") or element.tag
        if not any(marker in name for marker in TITLE_BEARING):
            continue
        for attribute in TITLE_ATTRIBUTES:
            value = element.get(attribute, "").strip()
            # A whole paragraph is body copy, not a title.
            if value and len(value) <= 64:
                return value
    return fallback
