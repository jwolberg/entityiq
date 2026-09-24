"""Tier-3 LinkedIn presence adapter (IC1-T4, ticket 0010).

Resolves the company's LinkedIn Company Page, directly from ``linkedin_url``
when supplied, otherwise by name + domain search. It emits presence, footprint
and website evidence plus a best-effort requester ↔ company association
(feature PRD docs/PRD-identity-corroboration.md §6).

Tier 3 is fallback enrichment: it never overrides a Tier-1 result.

Provider access is Open Decision #5 / IC0-T2 (ticket 0006). Scraping is out of
scope (PRD §6, §15), so this module has no HTTP client at all. The provider sits
behind a protocol:

  - ``UnconfiguredLinkedInProvider`` is the production default. The source
    reports *unavailable*, so there's no signal and no penalty.
  - ``StubLinkedInProvider`` is deterministic, backed by an in-memory page
    list. Used by tests and the demo, or opt in with
    ENTITYIQ_LINKEDIN_PROVIDER=stub.

Resolution outcomes:
  - Page found → ``linkedin_presence=found`` plus footprint evidence.
  - Submitted URL doesn't resolve → ``linkedin_presence=not_found``. That's a
    finding: the applicant pointed at a page that isn't there.
  - No URL and the search finds nothing → unavailable (no penalty). Many
    legitimate firms have no page (PRD §6 calibration note).
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


class ProviderRateLimited(Exception):
    """Raised by a provider when it rejects the call for quota/rate reasons."""


class ProviderNotConfigured(Exception):
    """Raised when no LinkedIn data provider has been selected (IC0-T2)."""


class LinkedInProvider(Protocol):
    """Returns a company-page record dict or None.

    Record keys: url, name, employee_count, followers, founded_year, website,
    page_created (ISO date), associated_people (list of names, optional).
    """

    name: str

    def by_url(self, url: str) -> dict | None: ...

    def search(self, company_name: str | None, domain: str | None) -> dict | None: ...


def canonical_page_url(url: str | None) -> str | None:
    """``linkedin.com/company/<slug>`` in a stable form, or None."""
    if not url:
        return None
    m = re.search(r"linkedin\.com/company/([^/?#\s]+)", url, re.IGNORECASE)
    if not m:
        return None
    return f"https://www.linkedin.com/company/{m.group(1).lower()}"


def bare_domain(value: str | None) -> str | None:
    """Host without scheme, ``www.``, path or port, lowercased."""
    if not value:
        return None
    host = re.sub(r"^[a-z]+://", "", value.strip().lower()).split("/")[0]
    host = host.split(":")[0]
    return host[4:] if host.startswith("www.") else host or None


def _person_key(name: str | None) -> str:
    return " ".join((name or "").lower().split())


class UnconfiguredLinkedInProvider:
    """Production default until IC0-T2 selects a compliant provider."""

    name = "unconfigured"

    def by_url(self, url: str) -> dict | None:
        raise ProviderNotConfigured("No LinkedIn data provider configured")

    def search(self, company_name: str | None, domain: str | None) -> dict | None:
        raise ProviderNotConfigured("No LinkedIn data provider configured")


# Deterministic demo/test pages. Fictional companies only.
DEFAULT_STUB_PAGES: list[dict] = [
    {
        "url": "https://www.linkedin.com/company/acme-corp",
        "name": "Acme Corporation",
        "employee_count": 1200,
        "followers": 45000,
        "founded_year": 1998,
        "website": "https://www.acme.com",
        "page_created": "2011-03-01",
        "associated_people": ["Jane Smith"],
    },
]


class StubLinkedInProvider:
    """Deterministic provider backed by an in-memory list of pages."""

    name = "stub"

    def __init__(self, pages: list[dict] | None = None) -> None:
        self._pages = list(pages if pages is not None else DEFAULT_STUB_PAGES)

    def by_url(self, url: str) -> dict | None:
        wanted = canonical_page_url(url)
        return next(
            (p for p in self._pages if canonical_page_url(p.get("url")) == wanted),
            None,
        )

    def search(self, company_name: str | None, domain: str | None) -> dict | None:
        wanted = bare_domain(domain)
        if not wanted:
            return None
        # Search is keyed on the website domain: a name alone is too ambiguous.
        return next(
            (p for p in self._pages if bare_domain(p.get("website")) == wanted),
            None,
        )


def provider_from_env() -> LinkedInProvider:
    """Select the provider from ENTITYIQ_LINKEDIN_PROVIDER (default: none)."""
    choice = (os.environ.get("ENTITYIQ_LINKEDIN_PROVIDER") or "").strip().lower()
    if choice in ("", "none", "unconfigured"):
        return UnconfiguredLinkedInProvider()
    if choice == "stub":
        return StubLinkedInProvider()
    raise ValueError(f"Unknown ENTITYIQ_LINKEDIN_PROVIDER: {choice!r}")


class LinkedInAdapter:
    """Tier-3 adapter: company-page presence and footprint."""

    name = "linkedin"
    tier = 3

    def __init__(self, provider: LinkedInProvider | None = None) -> None:
        self._provider = provider if provider is not None else provider_from_env()

    def fetch(self, context: AdapterContext) -> AdapterResult:
        submitted_url = canonical_page_url(context.linkedin_url)
        try:
            if submitted_url:
                page, resolved_by = self._provider.by_url(submitted_url), "url"
            else:
                page = self._provider.search(context.company_name, context.domain)
                resolved_by = "search"
        except ProviderNotConfigured as exc:
            return AdapterFailure(kind="unavailable", message=str(exc))
        except TimeoutError as exc:
            return AdapterFailure(kind="timeout", message=str(exc))
        except ProviderRateLimited as exc:
            return AdapterFailure(kind="rate_limited", message=str(exc))
        except Exception as exc:
            logger.warning("LinkedIn provider error: %s", exc)
            return AdapterFailure(kind="unavailable", message=str(exc))

        fetched_at = datetime.now(tz=timezone.utc)
        if page is None:
            if not submitted_url:
                return AdapterFailure(
                    kind="unavailable",
                    message="No LinkedIn company page found by name + domain",
                )
            return AdapterSuccess(
                evidence=[
                    self._ev(
                        context,
                        fetched_at,
                        "linkedin_presence",
                        "not_found",
                        source_url=submitted_url,
                        payload={"resolved_by": "url", "submitted_url": submitted_url},
                    )
                ]
            )

        page_url = canonical_page_url(page.get("url")) or page.get("url")
        payload = {**page, "resolved_by": resolved_by}
        rows = [
            self._ev(
                context,
                fetched_at,
                "linkedin_presence",
                "found",
                source_url=page_url,
                payload=payload,
            ),
            self._ev(
                context,
                fetched_at,
                "linkedin_company_url",
                page_url,
                source_url=page_url,
            ),
        ]
        simple = (
            ("name", "linkedin_company_name"),
            ("employee_count", "linkedin_employee_count"),
            ("followers", "linkedin_followers"),
            ("founded_year", "linkedin_founded_year"),
            ("page_created", "linkedin_page_created"),
        )
        for key, field in simple:
            if page.get(key) is not None:
                rows.append(
                    self._ev(
                        context, fetched_at, field, str(page[key]), source_url=page_url
                    )
                )
        if page.get("website"):
            rows.append(
                self._ev(
                    context,
                    fetched_at,
                    "linkedin_website",
                    bare_domain(page["website"]),
                    raw=page["website"],
                    source_url=page_url,
                )
            )
        people = page.get("associated_people")
        if context.requester_full_name and people is not None:
            wanted = _person_key(context.requester_full_name)
            matched = any(_person_key(p) == wanted for p in people)
            rows.append(
                self._ev(
                    context,
                    fetched_at,
                    "linkedin_requester_match",
                    "true" if matched else "false",
                    source_url=page_url,
                    confidence=0.6,
                )
            )
        return AdapterSuccess(evidence=rows)

    def _ev(
        self,
        context: AdapterContext,
        fetched_at: datetime,
        field: str,
        normalized: str | None,
        *,
        raw: str | None = None,
        source_url: str | None = None,
        payload: dict | None = None,
        confidence: float = 0.7,
    ) -> Evidence:
        return Evidence(
            verification_run_id=context.run_id,
            source=self.name,
            tier=self.tier,
            field=field,
            raw_value=raw if raw is not None else normalized,
            normalized_value=normalized,
            confidence=confidence,
            raw_payload=payload or {},
            attribution={"provider": self._provider.name, "source_url": source_url},
            fetched_at=fetched_at,
        )


class VerifyLinkedInStage:
    """Pipeline stage: LinkedIn presence (Tier-3 group, after web evidence)."""

    name = "verify_linkedin"

    def __init__(self, adapter: LinkedInAdapter | None = None) -> None:
        self._adapter = RetryingAdapter(
            adapter if adapter is not None else LinkedInAdapter()
        )

    def run(self, run_id: str, db: Any, context: dict) -> dict:
        normalized = context.get("normalized", {})
        adapter_context = AdapterContext(
            run_id=run_id,
            company_name=normalized.get("company_name"),
            domain=normalized.get("domain"),
            linkedin_url=normalized.get("linkedin_url"),
            requester_full_name=normalized.get("requester_full_name"),
        )

        result = self._adapter.fetch(adapter_context)

        if isinstance(result, AdapterSuccess):
            for ev in result.evidence:
                db.add(ev)
            db.commit()
            presence = next(
                (
                    e.normalized_value
                    for e in result.evidence
                    if e.field == "linkedin_presence"
                ),
                None,
            )
            summary = {
                "status": "complete",
                "presence": presence,
                "evidence_count": len(result.evidence),
            }
        else:
            summary = {"status": result.kind, "message": result.message}

        return {**context, "linkedin": summary}
