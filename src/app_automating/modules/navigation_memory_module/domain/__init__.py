"""The navigation-memory domain: entities, the failure vocabulary, and ports.

Zero I/O and zero frameworks. Every file here imports on a host with no
database, no Android SDK and no Appium -- which is the practical test of whether
this really is the centre.

Four files rather than the usual three. ``fingerprinting.py`` is the extra one,
and it belongs here rather than beside the database because "what makes two
observations the same place" is the module's central *business rule*, not a
storage detail: a second backend must not be able to answer it differently.
"""

from .errors import (
    DestinationNotFound,
    InvalidNavigationRequest,
    MemoryFailure,
    MemoryUnavailable,
    NavigationMemoryError,
    NoKnownRoute,
    NoRunForSession,
    RunAlreadyEnded,
    RunNotFound,
    ScreenNotFound,
    ScreenNotRecognised,
)
from .fingerprinting import (
    FINGERPRINT_LENGTH,
    Fingerprint,
    extract_title,
    fingerprint,
    fingerprint_source,
    resource_ids,
    structure_signature,
)
from .models import (
    OBSERVE_ACTION,
    EndRunRequest,
    EndRunResult,
    FindPathRequest,
    FindPathResult,
    ForgetRequest,
    ForgetResult,
    GetRunRequest,
    GetRunResult,
    JournalEntry,
    KnownScreen,
    PathStep,
    RecordInteractionRequest,
    RecordInteractionResult,
    RecordObservationRequest,
    RecordObservationResult,
    RouteOption,
    RunSummary,
    ScreenIdentity,
    ScreenSnapshot,
    SearchScreensRequest,
    SearchScreensResult,
    StartRunRequest,
    StartRunResult,
    WhereAmIRequest,
    WhereAmIResult,
)
from .ports import MemoryMaintenance, RouteMemory, RouteRecorder

__all__ = [
    # ports
    "RouteRecorder",
    "RouteMemory",
    "MemoryMaintenance",
    # value objects
    "ScreenIdentity",
    "ScreenSnapshot",
    "KnownScreen",
    "RouteOption",
    "PathStep",
    "RunSummary",
    "JournalEntry",
    "OBSERVE_ACTION",
    # requests
    "StartRunRequest",
    "RecordObservationRequest",
    "RecordInteractionRequest",
    "EndRunRequest",
    "WhereAmIRequest",
    "FindPathRequest",
    "SearchScreensRequest",
    "GetRunRequest",
    "ForgetRequest",
    # results
    "StartRunResult",
    "RecordObservationResult",
    "RecordInteractionResult",
    "EndRunResult",
    "WhereAmIResult",
    "FindPathResult",
    "SearchScreensResult",
    "GetRunResult",
    "ForgetResult",
    # errors
    "NavigationMemoryError",
    "MemoryUnavailable",
    "MemoryFailure",
    "InvalidNavigationRequest",
    "RunNotFound",
    "RunAlreadyEnded",
    "NoRunForSession",
    "ScreenNotFound",
    "ScreenNotRecognised",
    "NoKnownRoute",
    "DestinationNotFound",
    # the identity rule
    "fingerprint",
    "fingerprint_source",
    "Fingerprint",
    "resource_ids",
    "structure_signature",
    "extract_title",
    "FINGERPRINT_LENGTH",
]
