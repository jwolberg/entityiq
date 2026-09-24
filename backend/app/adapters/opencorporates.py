"""Tier-1 OpenCorporates adapter (P1-T4).

Queries the public OpenCorporates companies/search API to retrieve:
  - business existence
  - registration status
  - jurisdiction
  - registration number (company_number)
  - legal address

HTTP client is injectable so tests use a deterministic fake with no network
calls.

IMPORTANT — Open Decision #5 (data-source licensing) is UNRESOLVED:
  Production use of OpenCorporates requires a license agreement.
  The public API has very strict rate limits and no SLA.
  Do NOT ship this adapter to production without resolving the licensing
  and access tier with OpenCorporates.  See docs/ARCHITECTURE.md § Open
  decisions #5 and docs/implementation-notes.md.

API reference: https://api.opencorporates.com/documentation/API-Reference
Default base URL: https://api.opencorporates.com/v0.4
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Protocol

from app.adapters.base import (
    AdapterContext,
    AdapterFailure,
    AdapterResult,
    AdapterSuccess,
)
from app.models.evidence import Evidence

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.opencorporates.com/v0.4"


# ---------------------------------------------------------------------------
# Minimal HTTP client protocol (injectable)
# ---------------------------------------------------------------------------


class HttpClient(Protocol):
    """Minimal interface the adapter needs from an HTTP client."""

    def get(
        self,
        url: str,
        *,
        params: dict | None = None,
        timeout: float = 10.0,
    ) -> "HttpResponse":
        """Perform a GET request and return a response-like object."""
        ...


class HttpResponse(Protocol):
    """Minimal response interface."""

    @property
    def status_code(self) -> int: ...

    def json(self) -> Any: ...


# ---------------------------------------------------------------------------
# Default HTTP client (httpx — already a dep via fastapi extras)
# ---------------------------------------------------------------------------


def _default_http_client() -> HttpClient:
    """Return an httpx.Client configured for OpenCorporates."""
    try:
        import httpx  # noqa: PLC0415

        return httpx.Client()
    except ImportError as exc:
        raise RuntimeError("httpx is required for the OpenCorporates adapter") from exc


# ---------------------------------------------------------------------------
# OpenCorporates adapter
# ---------------------------------------------------------------------------


class OpenCorporatesAdapter:
    """Tier-1 adapter: query the OpenCorporates companies/search endpoint.

    Args:
        http_client: Injectable HTTP client.  Defaults to httpx.Client().
        base_url:    Override the API base URL (useful in tests).
        timeout:     Per-request timeout in seconds.
        api_token:   Optional API token for higher rate limits.
                     PRODUCTION LICENSING UNRESOLVED — see module docstring.
    """

    name = "opencorporates"
    tier = 1

    def __init__(
        self,
        http_client: HttpClient | None = None,
        base_url: str = _BASE_URL,
        timeout: float = 10.0,
        api_token: str | None = None,
    ) -> None:
        self._http = http_client if http_client is not None else _default_http_client()
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._api_token = (
            api_token
            if api_token is not None
            else os.environ.get("OPENCORPORATES_API_TOKEN")
        )

    # ------------------------------------------------------------------
    # Public: SourceAdapter.fetch
    # ------------------------------------------------------------------

    def fetch(self, context: AdapterContext) -> AdapterResult:
        """Search OpenCorporates for a company and return registry evidence.

        Returns:
            AdapterSuccess with evidence rows on a match.
            AdapterFailure(kind="not_found") when no results are returned.
            AdapterFailure(kind="timeout") on a network timeout.
            AdapterFailure(kind="rate_limited") on HTTP 429.
            AdapterFailure(kind="unavailable") on any other error.
        """
        if not context.company_name:
            return AdapterFailure(
                kind="not_found",
                message="No company_name in context — cannot query OpenCorporates",
            )

        try:
            return self._search(context)
        except Exception as exc:
            # Catch-all: never let an unhandled exception escape the adapter.
            logger.warning("OpenCorporates adapter unhandled error: %s", exc)
            return AdapterFailure(kind="unavailable", message=str(exc))

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _search(self, context: AdapterContext) -> AdapterResult:
        """Build and execute the search query; parse results."""
        params: dict[str, Any] = {
            "q": context.company_name,
            "per_page": 5,
            "sparse": True,
        }
        if context.country_iso:
            params["jurisdiction_code"] = context.country_iso.lower()
        if self._api_token:
            params["api_token"] = self._api_token

        url = f"{self._base_url}/companies/search"

        try:
            response = self._http.get(url, params=params, timeout=self._timeout)
        except TimeoutError as exc:
            return AdapterFailure(kind="timeout", message=str(exc))
        except Exception as exc:
            # Covers connection errors, DNS failures, etc.
            _exc_name = type(exc).__name__
            if "timeout" in _exc_name.lower() or "timeout" in str(exc).lower():
                return AdapterFailure(kind="timeout", message=str(exc))
            return AdapterFailure(kind="unavailable", message=str(exc))

        if response.status_code == 429:
            return AdapterFailure(kind="rate_limited", message="HTTP 429")
        if response.status_code >= 500:
            return AdapterFailure(
                kind="unavailable",
                message=f"HTTP {response.status_code}",
            )
        if response.status_code >= 400:
            return AdapterFailure(
                kind="unavailable",
                message=f"HTTP {response.status_code}",
            )

        try:
            data = response.json()
        except Exception as exc:
            return AdapterFailure(
                kind="unavailable", message=f"JSON parse error: {exc}"
            )

        return self._parse_results(context, data)

    def _parse_results(self, context: AdapterContext, data: dict) -> AdapterResult:
        """Extract companies from the OpenCorporates search response."""
        try:
            results = data.get("results", {}).get("companies", [])
        except Exception:
            return AdapterFailure(
                kind="unavailable", message="Unexpected response shape"
            )

        if not results:
            return AdapterFailure(
                kind="not_found",
                message=f"No OpenCorporates results for {context.company_name!r}",
            )

        evidence_rows = []
        fetched_at = datetime.now(tz=timezone.utc)

        for item in results:
            # API sometimes wraps the record in {"company": {...}}
            company = item.get("company", item)
            evidence_rows.extend(
                self._company_to_evidence(context, company, fetched_at)
            )

        return AdapterSuccess(evidence=evidence_rows)

    def _company_to_evidence(
        self,
        context: AdapterContext,
        company: dict,
        fetched_at: datetime,
    ) -> list[Evidence]:
        """Convert a single company record to Evidence rows."""
        rows: list[Evidence] = []
        attribution = {
            "source_url": f"{self._base_url}/companies/search",
            "provider": "opencorporates",
        }
        if company.get("opencorporates_url"):
            attribution["record_url"] = company["opencorporates_url"]

        def _ev(
            field: str,
            raw: str | None,
            normalized: str | None = None,
            confidence: float = 0.85,
        ) -> Evidence:
            return Evidence(
                verification_run_id=context.run_id,
                source=self.name,
                tier=self.tier,
                field=field,
                raw_value=raw,
                normalized_value=normalized if normalized is not None else raw,
                confidence=confidence,
                raw_payload=company,
                attribution=attribution,
                fetched_at=fetched_at,
            )

        # Existence / registration status
        company_number = company.get("company_number")
        current_status = company.get("current_status") or company.get("registry_url")
        company_name = company.get("name")
        jurisdiction = company.get("jurisdiction_code")

        if company_name:
            rows.append(_ev("company_name", company_name))
        if company_number:
            rows.append(_ev("registration_number", company_number))
        if current_status:
            rows.append(_ev("registration_status", current_status))
        if jurisdiction:
            rows.append(_ev("jurisdiction", jurisdiction, jurisdiction.upper()))

        # Legal address
        registered_address = company.get("registered_address") or company.get(
            "registered_address_in_full"
        )
        if isinstance(registered_address, dict):
            addr_str = registered_address.get(
                "street_address"
            ) or registered_address.get("in_full")
        elif isinstance(registered_address, str):
            addr_str = registered_address
        else:
            addr_str = None

        if addr_str:
            rows.append(_ev("legal_address", addr_str))

        return rows


# ---------------------------------------------------------------------------
# Pipeline stage wrapper
# ---------------------------------------------------------------------------


class QueryRegistriesStage:
    """Pipeline stage: query authoritative registries (stage 3).

    Wraps the OpenCorporates adapter as a pipeline stage.  Persists returned
    Evidence rows to the database and stores a summary in context.

    The adapter is injectable so tests can pass a fake.
    """

    name = "query_registries"

    def __init__(self, adapter: OpenCorporatesAdapter | None = None) -> None:
        self._adapter = adapter if adapter is not None else OpenCorporatesAdapter()

    def run(self, run_id: str, db: "Any", context: dict) -> dict:
        from app.adapters.base import AdapterSuccess  # noqa: PLC0415

        normalized = context.get("normalized", {})
        adapter_context = AdapterContext(
            run_id=run_id,
            company_name=normalized.get("company_name"),
            domain=normalized.get("domain"),
            country_iso=normalized.get("country_iso"),
            tax_id=normalized.get("tax_id"),
            email=normalized.get("email"),
            billing_address=normalized.get("billing_address"),
        )

        result = self._adapter.fetch(adapter_context)

        registries_context: dict = {}
        if isinstance(result, AdapterSuccess):
            for ev in result.evidence:
                db.add(ev)
            db.commit()
            registries_context = {
                "status": "complete",
                "evidence_count": len(result.evidence),
            }
        else:
            registries_context = {
                "status": result.kind,
                "message": result.message,
            }

        return {**context, "registries": registries_context}
