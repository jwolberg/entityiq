"""Tier-2 domain/infrastructure signals adapter (P1-T5).

Emits infrastructure evidence for a company domain:
  - WHOIS: domain registration date, registrar, expiry, domain age (days)
  - DNS: A/AAAA records presence
  - MX: mail exchange records (presence + first host)
  - SPF: TXT record containing "v=spf1"
  - DKIM: TXT lookup for a common selector (not exhaustive — presence probe only)
  - SSL: certificate issuer, subject, not_before / not_after via socket/ssl
  - Risk signals: recently_registered (< 180 days), no_mx

All external clients (WHOIS, DNS resolver, SSL) are injectable so every
test is fully offline.

Design notes:
  - Does NOT raise out of the stage — typed AdapterFailure on any client error.
  - DKIM probe uses the _domainkey._domainkey.<domain> convention (common
    selector probe); a real implementation would iterate known selectors.
  - SSL metadata uses the stdlib ssl module against port 443; injectable for tests.
  - Domain age is computed from WHOIS creation_date; if unavailable, the signal
    is omitted rather than estimated.
"""

from __future__ import annotations

import logging
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

# A domain registered within this many days is flagged as recently registered.
_RECENTLY_REGISTERED_DAYS = 180


# ---------------------------------------------------------------------------
# Injectable client protocols
# ---------------------------------------------------------------------------


class WhoisClient(Protocol):
    """Minimal interface for WHOIS lookups."""

    def query(self, domain: str) -> dict:
        """Return dict with optional keys: creation_date, expiration_date, registrar."""
        ...


class DnsClient(Protocol):
    """Minimal interface for DNS lookups."""

    def query_mx(self, domain: str) -> list[str]:
        """Return a list of MX host strings (may be empty)."""
        ...

    def query_txt(self, domain: str) -> list[str]:
        """Return a list of TXT record strings (may be empty)."""
        ...

    def query_a(self, domain: str) -> list[str]:
        """Return a list of A/AAAA record strings (may be empty)."""
        ...


class SslClient(Protocol):
    """Minimal interface for SSL certificate metadata lookups."""

    def get_cert(self, domain: str, port: int = 443) -> dict:
        """Return a dict with optional keys: issuer, subject, not_before, not_after."""
        ...


# ---------------------------------------------------------------------------
# Default client implementations (real network — used in production only)
# ---------------------------------------------------------------------------


def _default_whois_client() -> WhoisClient:
    """Return a WHOIS client using python-whois (optional dep)."""
    try:
        import whois  # noqa: PLC0415

        class _WhoisClientImpl:
            def query(self, domain: str) -> dict:
                try:
                    result = whois.whois(domain)
                    creation = result.get("creation_date")
                    if isinstance(creation, list):
                        creation = creation[0]
                    expiry = result.get("expiration_date")
                    if isinstance(expiry, list):
                        expiry = expiry[0]
                    return {
                        "creation_date": creation,
                        "expiration_date": expiry,
                        "registrar": result.get("registrar"),
                    }
                except Exception as exc:
                    raise RuntimeError(f"WHOIS lookup failed: {exc}") from exc

        return _WhoisClientImpl()
    except ImportError:
        # python-whois not installed — return a stub that raises.
        class _UnavailableWhois:
            def query(self, domain: str) -> dict:
                raise RuntimeError("python-whois is not installed")

        return _UnavailableWhois()


def _default_dns_client() -> DnsClient:
    """Return a DNS client using dnspython (optional dep)."""
    try:
        import dns.resolver  # noqa: PLC0415

        class _DnsClientImpl:
            def query_mx(self, domain: str) -> list[str]:
                try:
                    answers = dns.resolver.resolve(domain, "MX")
                    return [str(r.exchange) for r in answers]
                except Exception:
                    return []

            def query_txt(self, domain: str) -> list[str]:
                try:
                    answers = dns.resolver.resolve(domain, "TXT")
                    return [b.decode() for r in answers for b in r.strings]
                except Exception:
                    return []

            def query_a(self, domain: str) -> list[str]:
                try:
                    answers = dns.resolver.resolve(domain, "A")
                    return [str(r) for r in answers]
                except Exception:
                    return []

        return _DnsClientImpl()
    except ImportError:

        class _UnavailableDns:
            def query_mx(self, domain: str) -> list[str]:
                raise RuntimeError("dnspython is not installed")

            def query_txt(self, domain: str) -> list[str]:
                raise RuntimeError("dnspython is not installed")

            def query_a(self, domain: str) -> list[str]:
                raise RuntimeError("dnspython is not installed")

        return _UnavailableDns()


def _default_ssl_client() -> SslClient:
    """Return an SSL metadata client using stdlib ssl."""
    import socket  # noqa: PLC0415
    import ssl  # noqa: PLC0415

    class _SslClientImpl:
        def get_cert(self, domain: str, port: int = 443) -> dict:
            try:
                ctx = ssl.create_default_context()
                with ctx.wrap_socket(
                    socket.create_connection((domain, port), timeout=5),
                    server_hostname=domain,
                ) as sock:
                    cert = sock.getpeercert()
                issuer = dict(x[0] for x in cert.get("issuer", []))
                subject = dict(x[0] for x in cert.get("subject", []))
                return {
                    "issuer": issuer.get("organizationName"),
                    "subject": subject.get("commonName"),
                    "not_before": cert.get("notBefore"),
                    "not_after": cert.get("notAfter"),
                }
            except Exception as exc:
                raise RuntimeError(f"SSL lookup failed: {exc}") from exc

    return _SslClientImpl()


# ---------------------------------------------------------------------------
# Domain adapter
# ---------------------------------------------------------------------------


class DomainAdapter:
    """Tier-2 adapter: emit domain/infrastructure signals as Evidence.

    Args:
        whois_client: Injectable WHOIS client.
        dns_client:   Injectable DNS client.
        ssl_client:   Injectable SSL metadata client.
    """

    name = "domain"
    tier = 2

    def __init__(
        self,
        whois_client: WhoisClient | None = None,
        dns_client: DnsClient | None = None,
        ssl_client: SslClient | None = None,
    ) -> None:
        self._whois = (
            whois_client if whois_client is not None else _default_whois_client()
        )
        self._dns = dns_client if dns_client is not None else _default_dns_client()
        self._ssl = ssl_client if ssl_client is not None else _default_ssl_client()

    def fetch(self, context: AdapterContext) -> AdapterResult:
        """Gather domain/infrastructure signals and return Evidence.

        Returns:
            AdapterSuccess with evidence rows (may be empty if domain is missing).
            AdapterFailure(kind='unavailable') if all lookups fail.
            AdapterFailure(kind='not_found') if domain is absent from context.
        """
        domain = context.domain
        if not domain:
            return AdapterFailure(
                kind="not_found",
                message="No domain in context — cannot analyze infrastructure",
            )

        try:
            return self._gather(context, domain)
        except Exception as exc:
            logger.warning("DomainAdapter unhandled error: %s", exc)
            return AdapterFailure(kind="unavailable", message=str(exc))

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _gather(self, context: AdapterContext, domain: str) -> AdapterResult:
        evidence: list[Evidence] = []
        fetched_at = datetime.now(tz=timezone.utc)
        had_any_data = False

        attribution = {"provider": "domain", "domain": domain}

        def _ev(
            field: str,
            raw: str | None,
            normalized: str | None = None,
            confidence: float = 0.75,
            extra_payload: dict | None = None,
        ) -> Evidence:
            return Evidence(
                verification_run_id=context.run_id,
                source=self.name,
                tier=self.tier,
                field=field,
                raw_value=raw,
                normalized_value=normalized if normalized is not None else raw,
                confidence=confidence,
                raw_payload=extra_payload or {"domain": domain},
                attribution=attribution,
                fetched_at=fetched_at,
            )

        # --- WHOIS ---
        whois_data: dict[str, Any] = {}
        whois_available = False
        try:
            whois_data = self._whois.query(domain)
            whois_available = True
            had_any_data = True
        except Exception as exc:
            logger.debug("WHOIS unavailable for %s: %s", domain, exc)
            evidence.append(_ev("whois_status", "unavailable", confidence=0.0))

        if whois_available:
            creation_date = whois_data.get("creation_date")
            registrar = whois_data.get("registrar")
            expiry = whois_data.get("expiration_date")

            if registrar:
                evidence.append(_ev("domain_registrar", str(registrar)))

            if creation_date:
                creation_str = (
                    creation_date.isoformat()
                    if hasattr(creation_date, "isoformat")
                    else str(creation_date)
                )
                evidence.append(_ev("domain_creation_date", creation_str))

                # Compute domain age in days.
                try:
                    if (
                        hasattr(creation_date, "tzinfo")
                        and creation_date.tzinfo is None
                    ):
                        creation_date = creation_date.replace(tzinfo=timezone.utc)
                    age_days = (datetime.now(tz=timezone.utc) - creation_date).days
                    evidence.append(
                        _ev("domain_age_days", str(age_days), confidence=0.8)
                    )
                    if age_days < _RECENTLY_REGISTERED_DAYS:
                        evidence.append(
                            _ev(
                                "recently_registered",
                                "true",
                                confidence=0.9,
                                extra_payload={
                                    "domain": domain,
                                    "age_days": age_days,
                                    "threshold_days": _RECENTLY_REGISTERED_DAYS,
                                },
                            )
                        )
                except Exception as exc:
                    logger.debug("Could not compute domain age: %s", exc)

            if expiry:
                expiry_str = (
                    expiry.isoformat() if hasattr(expiry, "isoformat") else str(expiry)
                )
                evidence.append(_ev("domain_expiry_date", expiry_str))

        # --- DNS / MX ---
        mx_records: list[str] = []
        try:
            mx_records = self._dns.query_mx(domain)
            had_any_data = True
            mx_value = mx_records[0] if mx_records else "none"
            confidence = 0.9 if mx_records else 0.85
            evidence.append(_ev("mx_records", mx_value, confidence=confidence))

            if not mx_records:
                evidence.append(
                    _ev(
                        "no_mx",
                        "true",
                        confidence=0.9,
                        extra_payload={"domain": domain, "mx_records": []},
                    )
                )
        except Exception as exc:
            logger.debug("MX lookup unavailable for %s: %s", domain, exc)

        # --- SPF ---
        try:
            txt_records = self._dns.query_txt(domain)
            had_any_data = True
            spf = next((r for r in txt_records if "v=spf1" in r.lower()), None)
            evidence.append(
                _ev(
                    "spf_record",
                    spf if spf else "none",
                    confidence=0.85 if spf else 0.7,
                )
            )
        except Exception as exc:
            logger.debug("SPF/TXT lookup unavailable for %s: %s", domain, exc)

        # --- DKIM (presence probe only — common selector "_domainkey") ---
        try:
            dkim_probe_domain = f"_domainkey.{domain}"
            dkim_records = self._dns.query_txt(dkim_probe_domain)
            had_any_data = True
            has_dkim = bool(dkim_records)
            evidence.append(
                _ev(
                    "dkim_present",
                    "true" if has_dkim else "false",
                    confidence=0.7,
                )
            )
        except Exception as exc:
            logger.debug("DKIM probe unavailable for %s: %s", domain, exc)

        # --- SSL ---
        try:
            cert = self._ssl.get_cert(domain)
            had_any_data = True
            if cert.get("issuer"):
                evidence.append(_ev("ssl_issuer", cert["issuer"]))
            if cert.get("subject"):
                evidence.append(_ev("ssl_subject", cert["subject"]))
            if cert.get("not_before"):
                evidence.append(_ev("ssl_not_before", str(cert["not_before"])))
            if cert.get("not_after"):
                evidence.append(_ev("ssl_not_after", str(cert["not_after"])))
        except Exception as exc:
            logger.debug("SSL unavailable for %s: %s", domain, exc)

        if not had_any_data and not evidence:
            return AdapterFailure(
                kind="unavailable",
                message=f"All domain lookups failed for {domain!r}",
            )

        return AdapterSuccess(evidence=evidence)


# ---------------------------------------------------------------------------
# Pipeline stage wrapper
# ---------------------------------------------------------------------------


class AnalyzeDomainStage:
    """Pipeline stage: analyze domain infrastructure (stage 4, ARCHITECTURE § 2).

    Wraps the DomainAdapter as a pipeline stage.  Persists returned Evidence
    rows and stores a summary in context["domain_signals"].

    Clients are injectable so tests can pass fakes.
    """

    name = "analyze_domain"

    def __init__(
        self,
        whois_client: WhoisClient | None = None,
        dns_client: DnsClient | None = None,
        ssl_client: SslClient | None = None,
    ) -> None:
        self._adapter = DomainAdapter(
            whois_client=whois_client,
            dns_client=dns_client,
            ssl_client=ssl_client,
        )

    def run(self, run_id: str, db: Any, context: dict) -> dict:
        from app.adapters.base import AdapterSuccess  # noqa: PLC0415

        normalized = context.get("normalized", {})
        adapter_context = AdapterContext(
            run_id=run_id,
            domain=normalized.get("domain"),
            company_name=normalized.get("company_name"),
            country_iso=normalized.get("country_iso"),
        )

        result = self._adapter.fetch(adapter_context)

        if isinstance(result, AdapterSuccess):
            for ev in result.evidence:
                db.add(ev)
            db.commit()
            domain_context = {
                "status": "complete",
                "evidence_count": len(result.evidence),
            }
        else:
            domain_context = {
                "status": result.kind,
                "message": result.message,
            }

        return {**context, "domain_signals": domain_context}
