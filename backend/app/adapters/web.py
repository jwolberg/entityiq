"""Tier-3 public web evidence adapter (P2-T4).

Gathers public web evidence for a company: site content, contact pages,
directories, and press footprint.  Extracts contacts (names, phones, emails,
addresses) with source attribution.

Strategy: structured HTTP fetch first (httpx); Playwright is a lazy-imported
fallback for JavaScript-heavy sites.  Playwright is NEVER imported at the
module level — it is lazy-loaded inside _PlaywrightFetcher only, so tests
(and CI environments without browsers) can import this module freely.

Fetcher is injectable:
  - Tests inject FakeWebFetcher (returns canned HTML) — no network, no browser.
  - Production uses _HttpxFetcher (structured HTTP); falls back to
    _PlaywrightFetcher only if the structured fetch yields no useful content.

IMPORTANT: playwright must NOT be imported at module level.  It is only
imported inside _PlaywrightFetcher.__init__ so that:
  1. Tests and CI environments without browsers can import web.py freely.
  2. ``pip install -e ".[dev]"`` + ``pytest`` pass WITHOUT ``playwright install``.

Contact extraction:
  - Email: regex over page text.
  - Phone: regex over page text (international + US formats).
  - Address: heuristic patterns (street number + street name patterns).
  - Branding: og:site_name / title tag / h1 — first non-empty value.
  - Employee footprint: count of unique email domains on the page as a proxy
    signal; a thin/template site will have 0.
"""

from __future__ import annotations

import logging
import re
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

# ---------------------------------------------------------------------------
# Simple extraction regexes
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE,
)

_PHONE_RE = re.compile(
    r"(?:\+?\d[\d\s\-\.\(\)]{7,}\d)",
)

# Heuristic: digits + whitespace + word (e.g. "123 Main Street", "42 Baker Rd")
_ADDRESS_RE = re.compile(
    r"\b\d{1,5}\s+[A-Za-z][A-Za-z0-9\s,\.]{5,50}(?:Street|St|Avenue|Ave|"
    r"Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Court|Ct|Place|Pl|Way)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Injectable web fetcher protocol
# ---------------------------------------------------------------------------


class WebFetcher(Protocol):
    """Protocol for anything that can fetch a URL and return page text."""

    def fetch(self, url: str) -> "FetchResult":
        """Fetch the URL and return a FetchResult.

        On failure, raise any exception — the adapter catches and handles it.
        """
        ...


class FetchResult:
    """Result of a web fetch."""

    def __init__(
        self,
        url: str,
        text: str,
        status_code: int = 200,
    ) -> None:
        self.url = url
        self.text = text
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Production HTTP fetcher (httpx — no browser)
# ---------------------------------------------------------------------------


class _HttpxFetcher:
    """Structured HTTP fetcher using httpx (no JavaScript rendering)."""

    def __init__(self, timeout: float = 15.0) -> None:
        try:
            import httpx  # noqa: PLC0415

            self._client = httpx.Client(
                timeout=timeout,
                follow_redirects=True,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (compatible; EntityIQ/1.0; " "+https://skyfi.com)"
                    )
                },
            )
        except ImportError as exc:
            raise RuntimeError("httpx is required for the web adapter") from exc

    def fetch(self, url: str) -> FetchResult:
        response = self._client.get(url)
        return FetchResult(
            url=str(response.url),
            text=response.text,
            status_code=response.status_code,
        )


# ---------------------------------------------------------------------------
# Playwright fallback fetcher (lazy import — NEVER at module level)
# ---------------------------------------------------------------------------


class _PlaywrightFetcher:
    """Playwright-based fallback fetcher for JavaScript-heavy sites.

    Playwright is imported lazily inside __init__ so that:
    - Tests never import Playwright.
    - CI without ``playwright install`` passes freely.
    - Production with Playwright installed works transparently.
    """

    def __init__(self, timeout_ms: int = 15000) -> None:
        # Lazy import — ImportError if playwright is not installed.
        try:
            from playwright.sync_api import sync_playwright  # noqa: PLC0415

            self._sync_playwright = sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "playwright is not installed; run: pip install playwright && "
                "playwright install chromium"
            ) from exc
        self._timeout_ms = timeout_ms

    def fetch(self, url: str) -> FetchResult:
        with self._sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, timeout=self._timeout_ms, wait_until="domcontentloaded")
                text = page.content()
                return FetchResult(url=url, text=text, status_code=200)
            finally:
                browser.close()


def _default_web_fetcher() -> WebFetcher:
    """Return the production fetcher: httpx (Playwright fallback deferred)."""
    return _HttpxFetcher()


# ---------------------------------------------------------------------------
# HTML parsing helpers (stdlib only — no beautifulsoup dep)
# ---------------------------------------------------------------------------


def _extract_meta(html: str, property_name: str) -> str | None:
    """Extract og: meta tag content from raw HTML."""
    pattern = re.compile(
        r'<meta[^>]+(?:property|name)\s*=\s*["\']'
        + re.escape(property_name)
        + r'["\'][^>]+content\s*=\s*["\']([^"\']+)["\']',
        re.IGNORECASE,
    )
    m = pattern.search(html)
    if m:
        return m.group(1).strip()
    # Also try reversed attribute order: content first, then property.
    pattern2 = re.compile(
        r'<meta[^>]+content\s*=\s*["\']([^"\']+)["\'][^>]+'
        r'(?:property|name)\s*=\s*["\']' + re.escape(property_name) + r'["\']',
        re.IGNORECASE,
    )
    m2 = pattern2.search(html)
    return m2.group(1).strip() if m2 else None


def _extract_title(html: str) -> str | None:
    """Extract <title> text from raw HTML."""
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    return m.group(1).strip() if m else None


def _extract_h1(html: str) -> str | None:
    """Extract first <h1> text from raw HTML."""
    m = re.search(r"<h1[^>]*>([^<]+)</h1>", html, re.IGNORECASE)
    return m.group(1).strip() if m else None


def _strip_tags(html: str) -> str:
    """Very simple tag stripper — removes HTML tags, decodes common entities."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ").replace("&#160;", " ")
    return text


# ---------------------------------------------------------------------------
# Web adapter
# ---------------------------------------------------------------------------


class WebAdapter:
    """Tier-3 adapter: gather public web evidence for a company domain.

    Args:
        fetcher: Injectable web fetcher.  Defaults to httpx-based HTTP
                 fetcher.  Playwright fallback is NOT automatically used;
                 a caller can pass ``_PlaywrightFetcher()`` explicitly for
                 sites that require JavaScript.
    """

    name = "web"
    tier = 3

    def __init__(self, fetcher: WebFetcher | None = None) -> None:
        self._fetcher = fetcher if fetcher is not None else _default_web_fetcher()

    def fetch(self, context: AdapterContext) -> AdapterResult:
        """Fetch the company domain and extract public web evidence.

        Returns:
            AdapterSuccess with evidence rows.
            AdapterFailure(kind="not_found") if no domain in context.
            AdapterFailure(kind="unavailable") if the site is unreachable.
        """
        domain = context.domain
        if not domain:
            return AdapterFailure(
                kind="not_found",
                message="No domain in context — cannot gather web evidence",
            )

        url = f"https://{domain}"

        try:
            result = self._fetcher.fetch(url)
        except Exception as exc:
            exc_name = type(exc).__name__
            if "timeout" in exc_name.lower() or "timeout" in str(exc).lower():
                return AdapterFailure(kind="timeout", message=str(exc))
            logger.debug("WebAdapter: site %r unreachable: %s", domain, exc)
            return AdapterFailure(
                kind="unavailable",
                message=f"Site unreachable: {exc}",
            )

        if result.status_code >= 400:
            return AdapterFailure(
                kind="unavailable",
                message=f"HTTP {result.status_code} from {url}",
            )

        html = result.text
        plain_text = _strip_tags(html)

        return self._extract_evidence(context, domain, url, html, plain_text)

    # ------------------------------------------------------------------
    # Internal extraction
    # ------------------------------------------------------------------

    def _extract_evidence(
        self,
        context: AdapterContext,
        domain: str,
        url: str,
        html: str,
        plain_text: str,
    ) -> AdapterResult:
        fetched_at = datetime.now(tz=timezone.utc)
        attribution = {
            "provider": "web",
            "source_url": url,
            "domain": domain,
        }

        def _ev(
            field: str,
            raw: str | None,
            normalized: str | None = None,
            confidence: float = 0.6,
            payload: Any = None,
        ) -> Evidence:
            return Evidence(
                verification_run_id=context.run_id,
                source=self.name,
                tier=self.tier,
                field=field,
                raw_value=raw,
                normalized_value=normalized if normalized is not None else raw,
                confidence=confidence,
                raw_payload=payload or {"domain": domain, "url": url},
                attribution=attribution,
                fetched_at=fetched_at,
            )

        evidence: list[Evidence] = []

        # --- Branding signal ---
        brand = (
            _extract_meta(html, "og:site_name")
            or _extract_meta(html, "og:title")
            or _extract_title(html)
            or _extract_h1(html)
        )
        if brand:
            evidence.append(_ev("web_brand", brand, confidence=0.65))

        # --- Contact extraction ---
        emails_found = list({m.lower() for m in _EMAIL_RE.findall(plain_text)})
        phones_found = list({p.strip() for p in _PHONE_RE.findall(plain_text)})
        addresses_found = list({a.strip() for a in _ADDRESS_RE.findall(plain_text)})

        if emails_found:
            evidence.append(
                _ev(
                    "web_contacts_email",
                    emails_found[0],
                    confidence=0.7,
                    payload={"emails": emails_found[:10], "domain": domain},
                )
            )
        if phones_found:
            evidence.append(
                _ev(
                    "web_contacts_phone",
                    phones_found[0],
                    confidence=0.6,
                    payload={"phones": phones_found[:5], "domain": domain},
                )
            )
        if addresses_found:
            evidence.append(
                _ev(
                    "web_contacts_address",
                    addresses_found[0],
                    confidence=0.55,
                    payload={"addresses": addresses_found[:5], "domain": domain},
                )
            )

        # --- Employee footprint proxy ---
        # Count distinct email domains on the page (excluding the company's own domain).
        email_domains = {e.split("@")[1] for e in emails_found if "@" in e}
        external_email_domains = {d for d in email_domains if domain not in d}
        footprint_count = len(emails_found)

        if footprint_count == 0:
            evidence.append(
                _ev(
                    "web_thin_footprint",
                    "true",
                    confidence=0.55,
                    payload={
                        "domain": domain,
                        "email_count": 0,
                        "signal": "no_emails_found",
                    },
                )
            )
        else:
            evidence.append(
                _ev(
                    "web_employee_footprint",
                    str(footprint_count),
                    confidence=0.6,
                    payload={
                        "domain": domain,
                        "email_count": footprint_count,
                        "external_email_domains": list(external_email_domains),
                    },
                )
            )

        if not evidence:
            # Site reachable but yielded no extractable content.
            evidence.append(
                _ev(
                    "web_thin_footprint",
                    "true",
                    confidence=0.5,
                    payload={"domain": domain, "signal": "no_content_extracted"},
                )
            )

        return AdapterSuccess(evidence=evidence)


# ---------------------------------------------------------------------------
# Pipeline stage wrapper
# ---------------------------------------------------------------------------


class WebEvidenceStage:
    """Pipeline stage 6: gather Tier-3 public web evidence (P2-T4).

    Registered AFTER enrich_network_ip (stage 5) and BEFORE consistency_checks
    (stage 7) per ARCHITECTURE § 2.

    The fetcher is injectable so tests use a fake (no network, no browser).
    Playwright is lazy-imported inside _PlaywrightFetcher only — not here.
    """

    name = "web_evidence"

    def __init__(self, fetcher: WebFetcher | None = None) -> None:
        self._adapter = WebAdapter(fetcher=fetcher)

    def run(self, run_id: str, db: "Any", context: dict) -> dict:
        from app.adapters.base import AdapterSuccess  # noqa: PLC0415

        normalized = context.get("normalized", {})
        adapter_context = AdapterContext(
            run_id=run_id,
            company_name=normalized.get("company_name"),
            domain=normalized.get("domain"),
            country_iso=normalized.get("country_iso"),
        )

        result = self._adapter.fetch(adapter_context)

        if isinstance(result, AdapterSuccess):
            for ev in result.evidence:
                db.add(ev)
            db.commit()
            web_ctx: dict = {
                "status": "complete",
                "evidence_count": len(result.evidence),
            }
        else:
            web_ctx = {
                "status": result.kind,
                "message": result.message,
            }

        return {**context, "web_evidence": web_ctx}
