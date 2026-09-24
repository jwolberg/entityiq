"""Tier-1 tax-ID / FEIN verification adapter (IC1-T1, ticket 0007).

Confirms, for a US submission, that the submitted FEIN (a) exists, (b) maps to
an active entity, and (c) is registered to the submitted company name
(feature PRD docs/PRD-identity-corroboration.md §5).

Provider selection is Open Decision #5 / IC0-T1 (ticket 0005), so the provider
sits behind a small protocol:

  - ``UnconfiguredTaxIdProvider`` is the production default. With no provider
    configured, the source reports *unavailable*: no evidence, no signal, no
    penalty. A stub must never flag real submissions.
  - ``StubTaxIdProvider`` is deterministic, backed by an in-memory table.
    Used by tests and the demo, or opt in with ENTITYIQ_TAX_ID_PROVIDER=stub.

Evidence (``source="tax_id"``, tier 1):
  - tax_id_status          verified | not_found | inactive (raw = provider status)
  - tax_id_registered_name name on file for the identifier
  - tax_id_name_match      match | mismatch vs. the submitted company name
  - tax_id_entity_type / tax_id_jurisdiction when the provider returns them

"Not found" is a finding (PRD §5: a real risk signal), so it's recorded as a
``tax_id_status=not_found`` evidence row the scoring signal can cite. It is not
an AdapterFailure.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Protocol

from app.adapters.base import (
    AdapterContext,
    AdapterFailure,
    AdapterResult,
    AdapterSuccess,
)
from app.adapters.retry import RetryingAdapter
from app.models.evidence import Evidence

logger = logging.getLogger(__name__)

_ACTIVE_STATUSES = frozenset({"active", "good standing", "in good standing"})

# Suffixes stripped (repeatedly) before comparing registered vs. submitted
# names, so "Acme Corporation Inc." matches "Acme Corporation".
_LEGAL_SUFFIX = re.compile(
    r"\s*,?\s*\b(inc|incorporated|corp|corporation|co|company|llc|l\.l\.c|ltd|"
    r"limited|lp|llp|plc|pllc|pc)\.?\s*$",
    re.IGNORECASE,
)


class ProviderRateLimited(Exception):
    """Raised by a provider when it rejects the call for quota/rate reasons."""


class ProviderNotConfigured(Exception):
    """Raised when no real provider has been selected (Open Decision #5)."""


class TaxIdProvider(Protocol):
    """Looks up a normalized 9-digit FEIN.

    Returns a record dict (``status``, ``registered_name``, optional
    ``entity_type`` / ``jurisdiction``) or None when the identifier is unknown.
    Raises TimeoutError, ProviderRateLimited, or any other exception on
    failure; the adapter maps them to typed failures.
    """

    name: str

    def lookup(self, fein: str) -> dict | None: ...


class UnconfiguredTaxIdProvider:
    """Production default until IC0-T1 selects a provider."""

    name = "unconfigured"

    def lookup(self, fein: str) -> dict | None:
        raise ProviderNotConfigured("No tax-ID provider configured")


# Deterministic demo/test records. Fictional identifiers only.
DEFAULT_STUB_RECORDS: dict[str, dict] = {
    "123456789": {
        "status": "active",
        "registered_name": "Acme Corporation",
        "entity_type": "corporation",
        "jurisdiction": "US-DE",
    },
    "987654321": {
        "status": "dissolved",
        "registered_name": "Old Shell LLC",
        "entity_type": "llc",
        "jurisdiction": "US-NV",
    },
}


class StubTaxIdProvider:
    """Deterministic provider backed by an in-memory FEIN → record table."""

    name = "stub"

    def __init__(self, records: dict[str, dict] | None = None) -> None:
        self._records = dict(records if records is not None else DEFAULT_STUB_RECORDS)

    def lookup(self, fein: str) -> dict | None:
        return self._records.get(fein)


def provider_from_env() -> TaxIdProvider:
    """Select the provider from ENTITYIQ_TAX_ID_PROVIDER (default: none)."""
    choice = (os.environ.get("ENTITYIQ_TAX_ID_PROVIDER") or "").strip().lower()
    if choice in ("", "none", "unconfigured"):
        return UnconfiguredTaxIdProvider()
    if choice == "stub":
        return StubTaxIdProvider()
    raise ValueError(f"Unknown ENTITYIQ_TAX_ID_PROVIDER: {choice!r}")


def normalize_fein(raw: str | None) -> str | None:
    """Return the 9 digits of a US FEIN, or None if it isn't one."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    return digits if len(digits) == 9 else None


def core_name(name: str | None) -> str:
    """Lowercased name without punctuation and trailing legal suffixes."""
    if not name:
        return ""
    value = name.strip()
    while True:
        stripped = _LEGAL_SUFFIX.sub("", value).strip()
        if stripped == value or not stripped:
            break
        value = stripped
    value = re.sub(r"[^\w\s]", " ", value.lower())
    return " ".join(value.split())


def names_match(submitted: str | None, registered: str | None) -> bool:
    """Exact match on core names. A subsidiary's name is not a match."""
    a, b = core_name(submitted), core_name(registered)
    return bool(a) and a == b


class TaxIdAdapter:
    """Tier-1 adapter: verify a US FEIN against the configured provider."""

    name = "tax_id"
    tier = 1

    def __init__(self, provider: TaxIdProvider | None = None) -> None:
        self._provider = provider if provider is not None else provider_from_env()

    def fetch(self, context: AdapterContext) -> AdapterResult:
        if not context.tax_id:
            return AdapterFailure(kind="unavailable", message="No tax ID submitted")
        if (context.country_iso or "").upper() != "US":
            return AdapterFailure(
                kind="unavailable",
                message="Tax-ID verification is US-only in v1",
            )

        fetched_at = datetime.now(tz=timezone.utc)
        fein = normalize_fein(context.tax_id)
        if fein is None:
            return AdapterSuccess(
                evidence=[
                    self._ev(
                        context,
                        fetched_at,
                        "tax_id_status",
                        raw="invalid_format",
                        normalized="not_found",
                        payload={"reason": "invalid_format"},
                    )
                ]
            )

        try:
            record = self._provider.lookup(fein)
        except ProviderNotConfigured as exc:
            return AdapterFailure(kind="unavailable", message=str(exc))
        except TimeoutError as exc:
            return AdapterFailure(kind="timeout", message=str(exc))
        except ProviderRateLimited as exc:
            return AdapterFailure(kind="rate_limited", message=str(exc))
        except Exception as exc:
            logger.warning("Tax-ID provider error: %s", exc)
            return AdapterFailure(kind="unavailable", message=str(exc))

        if record is None:
            return AdapterSuccess(
                evidence=[
                    self._ev(
                        context,
                        fetched_at,
                        "tax_id_status",
                        raw="not_found",
                        normalized="not_found",
                        payload={"reason": "unknown_identifier"},
                    )
                ]
            )

        provider_status = str(record.get("status") or "").strip().lower()
        status = "verified" if provider_status in _ACTIVE_STATUSES else "inactive"
        registered = record.get("registered_name")
        rows = [
            self._ev(
                context,
                fetched_at,
                "tax_id_status",
                raw=provider_status or None,
                normalized=status,
                payload=record,
            )
        ]
        if registered:
            matched = names_match(context.company_name, registered)
            rows.append(
                self._ev(
                    context,
                    fetched_at,
                    "tax_id_registered_name",
                    raw=registered,
                    payload=record,
                )
            )
            rows.append(
                self._ev(
                    context,
                    fetched_at,
                    "tax_id_name_match",
                    raw="match" if matched else "mismatch",
                    payload={
                        "submitted": context.company_name,
                        "registered": registered,
                    },
                    confidence=0.95 if matched else 0.9,
                )
            )
        for key, field in (
            ("entity_type", "tax_id_entity_type"),
            ("jurisdiction", "tax_id_jurisdiction"),
        ):
            if record.get(key):
                rows.append(
                    self._ev(
                        context, fetched_at, field, raw=record[key], payload=record
                    )
                )
        return AdapterSuccess(evidence=rows)

    def _ev(
        self,
        context: AdapterContext,
        fetched_at: datetime,
        field: str,
        *,
        raw: str | None,
        normalized: str | None = None,
        payload: dict | None = None,
        confidence: float = 0.9,
    ) -> Evidence:
        return Evidence(
            verification_run_id=context.run_id,
            source=self.name,
            tier=self.tier,
            field=field,
            raw_value=raw,
            normalized_value=normalized if normalized is not None else raw,
            confidence=confidence,
            raw_payload=payload or {},
            attribution={"provider": self._provider.name},
            fetched_at=fetched_at,
        )


class VerifyTaxIdStage:
    """Pipeline stage: tax-ID verification (Tier-1 group, after registries)."""

    name = "verify_tax_id"

    def __init__(self, adapter: TaxIdAdapter | None = None) -> None:
        self._adapter = RetryingAdapter(
            adapter if adapter is not None else TaxIdAdapter()
        )

    def run(self, run_id: str, db: Any, context: dict) -> dict:
        normalized = context.get("normalized", {})
        adapter_context = AdapterContext(
            run_id=run_id,
            company_name=normalized.get("company_name"),
            domain=normalized.get("domain"),
            country_iso=normalized.get("country_iso"),
            tax_id=normalized.get("tax_id"),
        )

        result = self._adapter.fetch(adapter_context)

        if isinstance(result, AdapterSuccess):
            for ev in result.evidence:
                db.add(ev)
            db.commit()
            status = next(
                (
                    e.normalized_value
                    for e in result.evidence
                    if e.field == "tax_id_status"
                ),
                None,
            )
            summary = {
                "status": "complete",
                "tax_id_status": status,
                "evidence_count": len(result.evidence),
            }
        else:
            summary = {"status": result.kind, "message": result.message}

        return {**context, "tax_id": summary}
