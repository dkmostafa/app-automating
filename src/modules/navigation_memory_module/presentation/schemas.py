"""The typed payloads the navigation tools return.

Every tool returns one of these rather than a bare ``dict``, so FastMCP
publishes a real output schema next to each description and the client sees
field names and types instead of inferring them from prose.

This file is the bottom of the presentation package's dependency order
(``schemas -> rendering -> errors -> navigation_tools``). It imports nothing
from the module: a payload knows nothing about the domain result it was built
from, which is what keeps the conversion in one place (:mod:`.rendering`).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ScreenPayload",
    "RoutePayload",
    "WhereAmIPayload",
    "PathStepPayload",
    "PathPayload",
    "ScreenListPayload",
    "JournalEntryPayload",
    "RunPayload",
    "RunJournalPayload",
    "ForgetPayload",
]


class _Payload(BaseModel):
    """Shared configuration: frozen, and no field the schema did not declare."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ScreenPayload(_Payload):
    """One screen the memory knows."""

    screen_id: int = Field(description="Stable id. Pass it to a tool that takes a destination.")
    label: str = Field(description="Best human name: title, else activity, else the id.")
    package: str = Field(description="The app this screen belongs to.")
    activity: str | None = Field(default=None, description="The Android activity, when known.")
    title: str | None = Field(default=None, description="Best-effort label read from the UI.")
    visit_count: int = Field(description="How many times this screen has been observed.")
    last_seen_at: str | None = Field(
        default=None, description="ISO-8601 UTC timestamp of the most recent visit."
    )


class RoutePayload(_Payload):
    """One learned way out of a screen."""

    action: str = Field(description="What to do: tap_element, swipe, press_key, and so on.")
    target: str = Field(description="What to do it to: a locator, key name or direction.")
    leads_to: ScreenPayload = Field(description="Where this action has previously arrived.")
    confidence: float = Field(
        description="0-1. Smoothed success rate, so one lucky try never reads as certainty."
    )
    traversals: int = Field(description="How many times this route has been walked.")
    successes: int = Field(description="How many of those reported success.")
    last_app_version: str | None = Field(
        default=None, description="App version this was last confirmed on. Staleness signal."
    )


class WhereAmIPayload(_Payload):
    """Where a session is, and what is known from there."""

    session_id: str = Field(description="The session this describes.")
    run_id: int = Field(description="The recording run. Pass it to navigation_get_run.")
    package: str = Field(description="The app being driven.")
    screen: ScreenPayload = Field(description="The screen the session is currently on.")
    routes: tuple[RoutePayload, ...] = Field(
        default=(), description="Known ways out, most reliable first. Empty means unexplored."
    )
    unexplored: bool = Field(
        description="True when nothing is known to lead anywhere from this screen."
    )


class PathStepPayload(_Payload):
    """One hop of a planned route."""

    action: str = Field(description="The action to perform, via the matching appium tool.")
    target: str = Field(description="What to perform it on.")
    leads_to: ScreenPayload = Field(description="Where this hop should arrive.")
    confidence: float = Field(description="0-1 for this hop alone.")


class PathPayload(_Payload):
    """A planned route between two known screens."""

    origin: ScreenPayload = Field(description="Where the session is now.")
    destination: ScreenPayload = Field(description="Where the route ends.")
    steps: tuple[PathStepPayload, ...] = Field(
        default=(), description="The hops in order. Empty means already there."
    )
    confidence: float = Field(
        description="The weakest hop's confidence -- a plan is only as good as that."
    )
    already_there: bool = Field(description="True when the session is already on the destination.")


class ScreenListPayload(_Payload):
    """The screens known for one app."""

    package: str = Field(description="The app these belong to.")
    screens: tuple[ScreenPayload, ...] = Field(default=(), description="Most recently seen first.")
    count: int = Field(description="How many screens were returned.")


class JournalEntryPayload(_Payload):
    """One recorded action inside a run."""

    seq: int = Field(description="Position in the run, 1-based.")
    action: str = Field(description="What was done. 'observe' is the run's starting position.")
    target: str = Field(description="What it was done to.")
    ok: bool = Field(description="Whether the action itself reported success.")
    moved: bool = Field(description="True when the action actually changed screen.")
    at: str = Field(description="ISO-8601 UTC timestamp.")
    from_label: str | None = Field(default=None, description="Screen before the action.")
    to_label: str | None = Field(default=None, description="Screen after the action.")
    detail: str | None = Field(default=None, description="Failure message when ok is false.")


class RunPayload(_Payload):
    """One recorded drive of one app."""

    run_id: int = Field(description="Stable id of this run.")
    device_id: str = Field(description="The adb serial it was driven on.")
    package: str = Field(description="The app that was driven.")
    started_at: str = Field(description="ISO-8601 UTC timestamp.")
    ended_at: str | None = Field(default=None, description="Null while the run is still open.")
    app_version: str | None = Field(default=None, description="App version, when it was known.")
    session_id: str | None = Field(
        default=None, description="The Appium session this run was recorded through."
    )
    step_count: int = Field(description="How many actions were recorded.")
    open: bool = Field(description="True while the run is still in flight.")


class RunJournalPayload(_Payload):
    """A run and everything that happened in it."""

    run: RunPayload = Field(description="The run itself.")
    entries: tuple[JournalEntryPayload, ...] = Field(
        default=(), description="Actions in order, failures included."
    )
    failure_count: int = Field(description="How many entries reported failure.")


class ForgetPayload(_Payload):
    """What was deleted. Nothing here can be undone."""

    runs_deleted: int = Field(description="Runs removed.")
    steps_deleted: int = Field(description="Journal entries removed with them.")
    screens_deleted: int = Field(description="Screens removed from the map.")
    transitions_deleted: int = Field(description="Learned routes removed with them.")
    total: int = Field(description="Everything above, summed.")
