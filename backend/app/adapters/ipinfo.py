"""Tier-2 IPinfo network/IP intelligence adapter (P2-T2).

Enriches the submission's captured source IP via the IPinfo API, emitting
Evidence rows for:

  Report fields (PRD § Network & IP Intelligence):
    - ip_country / ip_region / ip_city
    - ip_asn / ip_isp (org)
    - ip_organization
    - ip_hosting / ip_vpn / ip_proxy (anonymized-network indicators)
    - ip_country_match  (compared against context["normalized"]["country_iso"])
    - ip_distance_flag  (unusual geography relative to submitted company)
    - ip_reuse_flag     (repeated submissions from same IP — NOTE: computed by
                         the scoring/consistency layer once per-run; this adapter
                         emits raw geo/ASN data only)

  Risk flags emitted as Evidence (PRD § Network & IP Intelligence risk model):
    - ip_country_mismatch  — IP country ≠ company/billing country
    - ip_anonymized_network — datacenter / hosting / VPN / proxy detected
    - ip_suspicious_asn    — ASN org name contains datacenter/cloud keywords

Provider
--------
Uses the ipinfo.io JSON API.  The free/anonymous tier returns geo + org for
any IP without an API token.  To use a paid token (higher rate limits, richer
data) set the IPINFO_TOKEN environment variable — the adapter reads it at
construction time.

IMPORTANT — production token config is unresolved (same class as Open
Decision #5): do NOT hard-code a paid token here.  If no token is set the
adapter silently falls back to the anonymous free tier, which has strict rate
limits and may be unavailable under load.  Set IPINFO_TOKEN in the production
environment when a paid plan is obtained.

Injectable HTTP client
----------------------
The HTTP client is injectable so every test uses a deterministic fake with no
real network calls.  The default client is httpx.Client() (already a dep via
FastAPI).

API reference: https://ipinfo.io/developers
Default base URL: https://ipinfo.io
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Protocol  # noqa: F401 — Any used in stage method signature

from app.adapters.base import (
    AdapterFailure,
    AdapterResult,
    AdapterSuccess,
)
from app.models.evidence import Evidence

logger = logging.getLogger(__name__)

_BASE_URL = "https://ipinfo.io"

# ASN / org name keywords that indicate a datacenter or anonymizing network.
_DATACENTER_KEYWORDS = re.compile(
    r"amazon|aws|google|gcp|azure|microsoft|digitalocean|linode|vultr|"
    r"hetzner|ovh|cloudflare|fastly|akamai|incapsula|imperva|zscaler|"
    r"hosting|datacenter|data center|vpn|tor|proxy|anonymi",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Injectable HTTP client protocol
# ---------------------------------------------------------------------------


class HttpClient(Protocol):
    """Minimal interface the adapter needs from an HTTP client."""

    def get(
        self,
        url: str,
        *,
        params: dict | None = None,
        timeout: float = 10.0,
    ) -> "HttpResponse": ...


class HttpResponse(Protocol):
    """Minimal response interface."""

    @property
    def status_code(self) -> int: ...

    def json(self) -> Any: ...


# ---------------------------------------------------------------------------
# Default HTTP client (httpx)
# ---------------------------------------------------------------------------


def _default_http_client() -> HttpClient:
    try:
        import httpx  # noqa: PLC0415

        return httpx.Client()
    except ImportError as exc:
        raise RuntimeError("httpx is required for the IPinfo adapter") from exc


# ---------------------------------------------------------------------------
# IPinfo adapter
# ---------------------------------------------------------------------------


class IPInfoAdapter:
    """Tier-2 adapter: enrich a source IP via ipinfo.io.

    Args:
        http_client: Injectable HTTP client (default: httpx.Client()).
        base_url:    Override the API base URL.
        timeout:     Per-request timeout in seconds.
        api_token:   Optional IPinfo API token for higher rate limits.
                     Defaults to the IPINFO_TOKEN environment variable.
                     If absent, the anonymous free tier is used.
                     DO NOT hard-code a paid token here.
    """

    name = "ipinfo"
    tier = 2

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
        # Resolve token: explicit arg > env var > None (anonymous free tier).
        self._api_token = (
            api_token if api_token is not None else os.environ.get("IPINFO_TOKEN")
        )

    # ------------------------------------------------------------------
    # Public: fetch
    # ------------------------------------------------------------------

    def fetch(
        self,
        run_id: str,
        source_ip: str | None,
        company_country_iso: str | None = None,
    ) -> AdapterResult:
        """Enrich a source IP and return network intelligence evidence.

        Args:
            run_id:              VerificationRun id (for Evidence FK).
            source_ip:           The submission's captured source IP.
            company_country_iso: Submitted company country (ISO alpha-2) for
                                 mismatch comparison.

        Returns:
            AdapterSuccess with evidence rows.
            AdapterFailure(kind="not_found") if source_ip is absent.
            AdapterFailure(kind="timeout") on timeout.
            AdapterFailure(kind="unavailable") on provider error.
        """
        if not source_ip:
            return AdapterFailure(
                kind="not_found",
                message="No source_ip in submission — cannot enrich network/IP",
            )

        try:
            return self._enrich(run_id, source_ip, company_country_iso)
        except Exception as exc:
            logger.warning("IPInfoAdapter unhandled error: %s", exc)
            return AdapterFailure(kind="unavailable", message=str(exc))

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _enrich(
        self,
        run_id: str,
        source_ip: str,
        company_country_iso: str | None,
    ) -> AdapterResult:
        url = f"{self._base_url}/{source_ip}/json"
        params: dict[str, Any] = {}
        if self._api_token:
            params["token"] = self._api_token

        try:
            response = self._http.get(url, params=params, timeout=self._timeout)
        except TimeoutError as exc:
            return AdapterFailure(kind="timeout", message=str(exc))
        except Exception as exc:
            exc_name = type(exc).__name__
            if "timeout" in exc_name.lower() or "timeout" in str(exc).lower():
                return AdapterFailure(kind="timeout", message=str(exc))
            return AdapterFailure(kind="unavailable", message=str(exc))

        if response.status_code == 429:
            return AdapterFailure(kind="rate_limited", message="HTTP 429")
        if response.status_code >= 500:
            return AdapterFailure(
                kind="unavailable", message=f"HTTP {response.status_code}"
            )
        if response.status_code >= 400:
            return AdapterFailure(
                kind="unavailable", message=f"HTTP {response.status_code}"
            )

        try:
            data = response.json()
        except Exception as exc:
            return AdapterFailure(kind="unavailable", message=f"JSON parse: {exc}")

        return self._parse(run_id, source_ip, data, company_country_iso)

    def _parse(
        self,
        run_id: str,
        source_ip: str,
        data: dict,
        company_country_iso: str | None,
    ) -> AdapterResult:
        """Convert ipinfo.io JSON response to Evidence rows."""
        fetched_at = datetime.now(tz=timezone.utc)
        attribution = {
            "provider": "ipinfo",
            "source_url": f"{self._base_url}/{source_ip}/json",
        }

        def _ev(
            field: str,
            raw: str | None,
            normalized: str | None = None,
            confidence: float = 0.9,
            payload_override: dict | None = None,
        ) -> Evidence:
            return Evidence(
                verification_run_id=run_id,
                source=self.name,
                tier=self.tier,
                field=field,
                raw_value=raw,
                normalized_value=normalized if normalized is not None else raw,
                confidence=confidence,
                raw_payload=payload_override if payload_override is not None else data,
                attribution=attribution,
                fetched_at=fetched_at,
            )

        evidence: list[Evidence] = []

        # --- Geo fields ---
        ip_country = data.get("country")
        ip_region = data.get("region")
        ip_city = data.get("city")

        if ip_country:
            evidence.append(_ev("ip_country", ip_country))
        if ip_region:
            evidence.append(_ev("ip_region", ip_region))
        if ip_city:
            evidence.append(_ev("ip_city", ip_city))

        # --- ASN / ISP / Org ---
        # ipinfo free tier returns "org" as "AS12345 Some ISP Name".
        org_raw = data.get("org")
        if org_raw:
            evidence.append(_ev("ip_organization", org_raw))
            # Split into ASN number and ISP name.
            parts = org_raw.split(" ", 1)
            asn = parts[0] if parts else None
            isp_name = parts[1] if len(parts) > 1 else org_raw
            if asn:
                evidence.append(_ev("ip_asn", asn))
            if isp_name:
                evidence.append(_ev("ip_isp", isp_name))

        # --- Hosting / VPN / Proxy indicators ---
        # ipinfo paid plans expose these in a "privacy" sub-object.
        # The free tier omits them; we emit what is present.
        privacy = data.get("privacy") or {}
        is_hosting = privacy.get("hosting", False)
        is_vpn = privacy.get("vpn", False)
        is_proxy = privacy.get("proxy", False)

        if is_hosting:
            evidence.append(_ev("ip_hosting", "true", confidence=0.95))
        if is_vpn:
            evidence.append(_ev("ip_vpn", "true", confidence=0.95))
        if is_proxy:
            evidence.append(_ev("ip_proxy", "true", confidence=0.95))

        # --- ASN-based datacenter detection (free-tier fallback) ---
        # If privacy data is absent (free tier) we use org-name keywords as
        # a proxy signal.  Lower confidence than explicit privacy flags.
        anonymized = is_hosting or is_vpn or is_proxy
        if not anonymized and org_raw and _DATACENTER_KEYWORDS.search(org_raw):
            anonymized = True
            evidence.append(
                _ev(
                    "ip_hosting",
                    "true",
                    confidence=0.7,
                    payload_override={
                        "ip": source_ip,
                        "org": org_raw,
                        "detection": "keyword",
                    },
                )
            )

        # --- Risk flag: anonymized network ---
        if anonymized:
            evidence.append(
                _ev(
                    "ip_anonymized_network",
                    "true",
                    confidence=0.85 if (is_hosting or is_vpn or is_proxy) else 0.7,
                    payload_override={
                        "ip": source_ip,
                        "org": org_raw,
                        "hosting": is_hosting,
                        "vpn": is_vpn,
                        "proxy": is_proxy,
                    },
                )
            )

        # --- Risk flag: IP country mismatch ---
        ip_country_upper = (ip_country or "").upper()
        company_country_upper = (company_country_iso or "").upper()
        if ip_country_upper and company_country_upper:
            match = ip_country_upper == company_country_upper
            evidence.append(
                _ev(
                    "ip_country_match",
                    "true" if match else "false",
                    confidence=0.9,
                    payload_override={
                        "ip": source_ip,
                        "ip_country": ip_country_upper,
                        "company_country": company_country_upper,
                    },
                )
            )
            if not match:
                evidence.append(
                    _ev(
                        "ip_country_mismatch",
                        "true",
                        confidence=0.9,
                        payload_override={
                            "ip": source_ip,
                            "ip_country": ip_country_upper,
                            "company_country": company_country_upper,
                        },
                    )
                )

        # --- ASN suspicious ownership flag ---
        if org_raw and _DATACENTER_KEYWORDS.search(org_raw) and anonymized:
            evidence.append(
                _ev(
                    "ip_suspicious_asn",
                    "true",
                    confidence=0.75,
                    payload_override={"ip": source_ip, "org": org_raw},
                )
            )

        if not evidence:
            return AdapterFailure(
                kind="not_found",
                message=f"No usable IP intelligence returned for {source_ip!r}",
            )

        return AdapterSuccess(evidence=evidence)


# ---------------------------------------------------------------------------
# Pipeline stage wrapper
# ---------------------------------------------------------------------------


class EnrichNetworkIPStage:
    """Pipeline stage 5: enrich network/IP intelligence (ARCHITECTURE § 2 stage 5).

    Reads the source IP from context["normalized"]["source_ip"] and the
    submitted country from context["normalized"]["country_iso"], then calls
    IPInfoAdapter.fetch() and persists the returned Evidence rows.

    The adapter is injectable so tests can pass a fake HTTP client (no network
    calls in tests).

    Provider caveat: production IPINFO_TOKEN is unresolved.  The stage runs
    on the anonymous free tier when no token is configured.  A typed
    AdapterFailure is returned when the provider is unavailable — the run
    continues (ARCHITECTURE § 2 graceful degradation).
    """

    name = "enrich_network_ip"

    def __init__(self, adapter: IPInfoAdapter | None = None) -> None:
        self._adapter = adapter if adapter is not None else IPInfoAdapter()

    def run(self, run_id: str, db: Any, context: dict) -> dict:
        normalized = context.get("normalized", {})
        source_ip = normalized.get("source_ip")
        company_country_iso = normalized.get("country_iso")

        result = self._adapter.fetch(
            run_id=run_id,
            source_ip=source_ip,
            company_country_iso=company_country_iso,
        )

        if isinstance(result, AdapterSuccess):
            for ev in result.evidence:
                db.add(ev)
            db.commit()
            network_ctx: dict = {
                "status": "complete",
                "evidence_count": len(result.evidence),
            }
        else:
            network_ctx = {
                "status": result.kind,
                "message": result.message,
            }

        return {**context, "network_ip": network_ctx}
