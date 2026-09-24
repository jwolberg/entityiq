"""HQ geocoding stage (P2-T9) — OpenStreetMap Nominatim.

Resolves the company's headquarters address to coordinates for the operator
UI's embedded map, and rates *address confidence* by how many independent
sources agree on it:

  high    registry legal address found AND the submitted billing address
          matches it (two independent sources agree)
  medium  registry legal address found, nothing to compare it against
  low     only the self-reported address, or registry and submission disagree

Which address is geocoded: the registry's legal address when present (more
authoritative), else the submitted billing address.

Provider: Nominatim (free, no key).  Its usage policy requires an identifying
User-Agent and ≤1 request/second — fine for per-submission lookups, not for
bulk.  Open Decision #4 (map provider) resolved 2026-09-24: OpenStreetMap
embed for the map + Nominatim for geocoding; see docs/implementation-notes.md.

HTTP client is injectable; tests never touch the network.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Protocol

from app.adapters.base import AdapterFailure
from app.adapters.retry import RetryingAdapter
from app.models.evidence import Evidence

logger = logging.getLogger(__name__)

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_USER_AGENT = "EntityIQ/1.0 (+https://github.com/jwolberg/entityiq)"


class HttpClient(Protocol):
    def get(
        self,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
        timeout: float = 10.0,
    ) -> Any: ...


def _default_http_client() -> HttpClient:
    import httpx  # noqa: PLC0415

    return httpx.Client()


class GeocodeAdapter:
    name = "geocode"
    tier = 3

    def __init__(self, http_client: HttpClient | None = None, timeout: float = 10.0):
        self._http = http_client if http_client is not None else _default_http_client()
        self._timeout = timeout

    def geocode(self, address: str) -> dict | AdapterFailure:
        """Return {"lat", "lon", "display_name"} or a typed failure."""
        try:
            resp = self._http.get(
                _NOMINATIM_URL,
                params={"q": address, "format": "jsonv2", "limit": 1},
                headers={"User-Agent": _USER_AGENT},
                timeout=self._timeout,
            )
        except TimeoutError as exc:
            return AdapterFailure(kind="timeout", message=str(exc))
        except Exception as exc:  # never let a provider error escape the stage
            return AdapterFailure(kind="unavailable", message=str(exc))

        if resp.status_code == 429:
            return AdapterFailure(kind="rate_limited", message="HTTP 429")
        if resp.status_code >= 400:
            return AdapterFailure(
                kind="unavailable", message=f"HTTP {resp.status_code}"
            )
        try:
            results = resp.json()
        except Exception as exc:
            return AdapterFailure(kind="unavailable", message=f"bad JSON: {exc}")
        if not results:
            return AdapterFailure(kind="not_found", message="no geocode match")
        top = results[0]
        return {
            "lat": str(top["lat"]),
            "lon": str(top["lon"]),
            "display_name": top.get("display_name", address),
        }


def _address_confidence(has_registry: bool, billing_status: str | None) -> str:
    if has_registry and billing_status == "match":
        return "high"
    if has_registry and billing_status not in ("mismatch",):
        return "medium"
    return "low"


class GeocodeHQStage:
    """Pipeline stage: geocode HQ + rate address confidence (runs after
    consistency_checks so the billing-vs-registry comparison exists)."""

    name = "geocode_hq"

    def __init__(self, adapter: GeocodeAdapter | None = None) -> None:
        self._adapter = RetryingAdapter(
            adapter if adapter is not None else GeocodeAdapter()
        )

    def run(self, run_id: str, db: Any, context: dict) -> dict:
        from app.models.field_comparison import FieldComparison  # noqa: PLC0415

        legal = (
            db.query(Evidence)
            .filter(
                Evidence.verification_run_id == run_id,
                Evidence.field == "legal_address",
            )
            .first()
        )
        billing_cmp = (
            db.query(FieldComparison)
            .filter(
                FieldComparison.verification_run_id == run_id,
                FieldComparison.field_name == "billing_address",
            )
            .first()
        )
        billing = (context.get("normalized") or {}).get("billing_address")

        legal_value = (legal.normalized_value or legal.raw_value) if legal else None
        address = legal_value or billing
        if not address:
            return {**context, "geocode_hq": {"status": "not_found"}}

        result = self._adapter.geocode(address)
        if isinstance(result, AdapterFailure):
            return {
                **context,
                "geocode_hq": {"status": result.kind, "message": result.message},
            }

        fetched_at = datetime.now(tz=timezone.utc)
        attribution = {"provider": "openstreetmap_nominatim", "query": address}
        values = {
            "hq_latitude": result["lat"],
            "hq_longitude": result["lon"],
            "hq_display_name": result["display_name"],
            "hq_address_source": "registry" if legal_value else "submitted",
            "hq_address_confidence": _address_confidence(
                legal_value is not None,
                billing_cmp.match_status if billing_cmp else None,
            ),
        }
        evidence = [
            Evidence(
                verification_run_id=run_id,
                source=self._adapter.name,
                tier=self._adapter.tier,
                field=field,
                raw_value=value,
                normalized_value=value,
                confidence=0.7,
                raw_payload={"address": address},
                attribution=attribution,
                fetched_at=fetched_at,
            )
            for field, value in values.items()
        ]
        for ev in evidence:
            db.add(ev)
        db.commit()
        return {
            **context,
            "geocode_hq": {"status": "complete", "evidence_count": len(evidence)},
        }
