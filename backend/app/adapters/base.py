"""Adapter contract and typed failure types (P1-T4).

Every external-source adapter must satisfy the SourceAdapter Protocol:

    fetch(context: AdapterContext) -> AdapterResult

AdapterResult is either:
  - AdapterSuccess(evidence=[...])  — one or more Evidence rows to persist
  - AdapterFailure(reason=..., kind=...)  — typed failure; never raises out
    of the pipeline

FailureKind values:
  - "timeout"       — external call exceeded its deadline
  - "unavailable"   — provider is down or returned a non-retryable error
  - "not_found"     — lookup succeeded but found no matching record
  - "rate_limited"  — provider returned 429 / quota exhausted

AdapterContext carries the data downstream from the normalize stage that
adapters need to form queries.  Adapters read what they need; unused fields
are silently ignored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# AdapterContext — input to every adapter fetch() call
# ---------------------------------------------------------------------------

FailureKind = Literal["timeout", "unavailable", "not_found", "rate_limited"]


@dataclass
class AdapterContext:
    """Normalised context passed to each adapter.

    All fields are optional so callers can populate only what they know.
    Adapters must handle None gracefully.
    """

    run_id: str
    company_name: str | None = None
    domain: str | None = None
    country_iso: str | None = None
    tax_id: str | None = None
    email: str | None = None
    billing_address: str | None = None


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class AdapterSuccess:
    """Successful adapter result carrying zero or more Evidence rows.

    An empty evidence list is valid (the provider was reachable but found
    nothing — use AdapterFailure with kind="not_found" for the explicit
    not-found signal instead).
    """

    evidence: list = field(default_factory=list)  # list[Evidence]


@dataclass
class AdapterFailure:
    """Typed failure — the adapter could not return usable evidence.

    The pipeline records this as an 'unavailable' signal for this source
    section; it does NOT abort the run.

    Attributes:
        kind:    Failure category (see FailureKind).
        message: Human-readable detail (logged; not shown to operators).
    """

    kind: FailureKind
    message: str = ""


AdapterResult = AdapterSuccess | AdapterFailure


# ---------------------------------------------------------------------------
# SourceAdapter Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class SourceAdapter(Protocol):
    """Protocol all source adapters must satisfy.

    name:   Unique snake_case identifier (used as the source field on
            Evidence rows and as the key in source_availability).
    tier:   1, 2, or 3 per ARCHITECTURE § 4.
    fetch:  Perform the lookup; return AdapterResult.  Must NOT raise —
            catch all exceptions internally and return AdapterFailure.
    """

    @property
    def name(self) -> str:
        """Unique source identifier, e.g. 'opencorporates'."""
        ...

    @property
    def tier(self) -> int:
        """Source tier (1 = authoritative, 2 = infrastructure, 3 = web)."""
        ...

    def fetch(self, context: AdapterContext) -> AdapterResult:
        """Perform the lookup and return a typed result.

        Must not raise.  Any uncaught exception should be caught inside the
        implementation and returned as AdapterFailure(kind="unavailable").
        """
        ...
