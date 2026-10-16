"""Tier-1 public-records stage: CourtListener + SAM.gov exclusions (ticket 0092).

Plan: docs/plans/2026-10-16-001-feat-public-records-sources-plan.md (U3).
Contracts were verified live on 2026-10-16 (docs/implementation-notes.md).

Two sources, each succeeding or failing on its own (KTD5):

- **CourtListener** (REST v4, token from ``COURTLISTENER_API_TOKEN``). The free
  tier allows 125 requests a day, so the budget is explicit: one bankruptcy
  search and one open-civil search per US submission, plus at most
  ``MAX_FRESHNESS_CHECKS`` docket-detail calls, and no automatic retries. A
  search returns one page (20 results); the true ``count`` is reported and
  ``truncated`` set when it's larger. The ``party`` list on a docket includes
  counsel and creditors, so a debtor is identified by ``caseName`` only. Civil
  cases carry no party role in search results, so they're stored unscored
  (R11 fallback).
- **SAM exclusions** (local snapshot from ``app.lists.sam``). Matched on the
  core name, or a Cross-Reference alias, plus a known US state (KTD10).

Evidence (``source="public_records"``, tier 1):

- ``public_records_status`` — one row per source. ``raw_value`` is searched |
  no_records | stale | unavailable | timeout | rate_limited | not_applicable;
  ``raw_payload`` holds ``source`` and optional ``notes``/``truncated``.
- ``public_records_summary`` — true and stored counts per kind.
- ``public_record`` — one row per record. ``raw_value`` is the kind
  (bankruptcy | federal_civil | federal_exclusion). ``normalized_value`` is
  strong | weak, or ``unscored`` for civil cases. Strength is decided here and
  never re-derived by scoring (KTD7). Only strong records may score (R6).

Status and summary rows carry no confidence: they are bookkeeping, not facts
about the company. Evidence copies each matched record; nothing references
SAM rows or snapshots, so snapshot pruning keeps working (ADR-0007).
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Protocol

from app.adapters.base import (
    AdapterContext,
    AdapterFailure,
    AdapterResult,
    AdapterSuccess,
)
from app.adapters.retry import RetryingAdapter
from app.adapters.tax_id import (
    ProviderNotConfigured,
    ProviderRateLimited,
    core_name,
    names_match,
)
from app.models.evidence import Evidence

logger = logging.getLogger(__name__)

SOURCE = "public_records"
TIER = 1
COURTLISTENER = "courtlistener"
SAM = "sam_exclusions"

COURTLISTENER_API = "https://www.courtlistener.com/api/rest/v4"
COURTLISTENER_SITE = "https://www.courtlistener.com"

MAX_RECORDS_PER_KIND = 50
MAX_FRESHNESS_CHECKS = 3
MAX_ALIAS_CANDIDATES = 500
BANKRUPTCY_WINDOW = timedelta(days=36 * 30)  # R8: filed within 36 months
FRESHNESS_WINDOW = timedelta(days=365)  # KTD13: refreshed within 12 months
SAM_STALE_AFTER = timedelta(days=7)
FAILURE_KINDS = frozenset({"unavailable", "timeout", "rate_limited"})

# USPS codes for states, DC and territories; anything else isn't a US state.
US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO "
    "MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY "
    "DC PR GU VI AS MP".split()
)
US_COUNTRY_CODES = frozenset({"", "US", "USA"})

# Two-letter code before a ZIP, e.g. "Austin, TX 78701".
_ADDRESS_STATE = re.compile(r"\b([A-Z]{2})\s+\d{5}(?:-\d{4})?\b")
_ALIAS_NOTE = re.compile(r"\([^)]*\)")


# ---------------------------------------------------------------------------
# CourtListener client
# ---------------------------------------------------------------------------


class CourtListenerClient(Protocol):
    name: str

    def search(self, query: str, **params: Any) -> dict: ...

    def docket(self, docket_id: int) -> dict: ...


class UnconfiguredCourtListener:
    """Production default without a token: the source reports unavailable."""

    name = "unconfigured"

    def search(self, query: str, **params: Any) -> dict:
        raise ProviderNotConfigured("COURTLISTENER_API_TOKEN is not set")

    def docket(self, docket_id: int) -> dict:
        raise ProviderNotConfigured("COURTLISTENER_API_TOKEN is not set")


class HttpCourtListener:
    """REST v4 client. Free accounts allow 5/min, 50/hour and 125/day."""

    name = COURTLISTENER

    def __init__(self, token: str, timeout: float = 20.0) -> None:
        self._headers = {"Authorization": f"Token {token}"}
        self._timeout = timeout

    def _get(self, path: str, params: dict | None = None) -> dict:
        import httpx  # noqa: PLC0415

        try:
            response = httpx.get(
                f"{COURTLISTENER_API}{path}",
                params=params,
                headers=self._headers,
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise TimeoutError("CourtListener timed out") from exc
        if response.status_code == 429:
            raise ProviderRateLimited("CourtListener rate limit reached")
        if response.status_code != 200:
            raise RuntimeError(f"CourtListener returned HTTP {response.status_code}")
        return response.json()

    def search(self, query: str, **params: Any) -> dict:
        return self._get("/search/", {"type": "d", "q": query, **params})

    def docket(self, docket_id: int) -> dict:
        return self._get(f"/dockets/{int(docket_id)}/")


def client_from_env() -> CourtListenerClient:
    token = (os.environ.get("COURTLISTENER_API_TOKEN") or "").strip()
    return HttpCourtListener(token) if token else UnconfiguredCourtListener()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return str(value)[:10]


def _parse_date(value: Any) -> date | None:
    text = _iso(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _phrase(name: str) -> str:
    return " ".join(name.replace('"', " ").replace("\\", " ").split())[:200]


def _docket_url(docket_id: Any) -> str:
    return f"{COURTLISTENER_SITE}/docket/{int(docket_id)}/"


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def submission_state(db: Any, run_id: str, billing_address: str | None) -> str | None:
    """Registry jurisdiction first (KTD10), then the billing-address state."""
    rows = (
        db.query(Evidence)
        .filter(
            Evidence.verification_run_id == run_id,
            Evidence.field.in_(("jurisdiction", "tax_id_jurisdiction")),
        )
        .all()
    )
    for row in rows:
        value = (row.normalized_value or row.raw_value or "").upper()
        match = re.fullmatch(r"US[_\-]([A-Z]{2})", value)
        if match and match.group(1) in US_STATES:
            return match.group(1)
    if billing_address:
        found = [
            s for s in _ADDRESS_STATE.findall(billing_address.upper()) if s in US_STATES
        ]
        if found:
            return found[-1]
    return None


# ---------------------------------------------------------------------------
# CourtListener adapter
# ---------------------------------------------------------------------------


@dataclass
class CourtListenerFindings:
    records: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    truncated: bool = False
    notes: list[str] = field(default_factory=list)


class CourtListenerAdapter:
    name = COURTLISTENER
    tier = TIER

    def __init__(self, client: CourtListenerClient, today: Callable[[], date]) -> None:
        self._client = client
        self._today = today

    def fetch(self, context: AdapterContext) -> AdapterResult:
        try:
            return AdapterSuccess(evidence=[self._findings(context.company_name or "")])
        except ProviderNotConfigured as exc:
            return AdapterFailure(kind="unavailable", message=str(exc))
        except TimeoutError as exc:
            return AdapterFailure(kind="timeout", message=str(exc))
        except ProviderRateLimited as exc:
            return AdapterFailure(kind="rate_limited", message=str(exc))
        except Exception as exc:
            logger.warning("CourtListener error: %s", exc)
            return AdapterFailure(kind="unavailable", message=str(exc))

    def _findings(self, name: str) -> CourtListenerFindings:
        party = f'party:"{_phrase(name)}"'
        bankrupt = self._client.search(
            f"{party} AND chapter:[* TO *]", order_by="dateFiled desc"
        )
        civil = self._client.search(
            f"{party} AND -chapter:[* TO *] AND -dateTerminated:[* TO *]",
            order_by="dateFiled desc",
        )
        found = CourtListenerFindings()
        bk_results = bankrupt.get("results") or []
        cv_results = sorted(
            civil.get("results") or [],
            key=lambda r: r.get("dateFiled") or "",
            reverse=True,
        )[:MAX_RECORDS_PER_KIND]
        found.records = self._bankruptcies(name, bk_results, found.notes)
        found.records += [self._civil(name, r) for r in cv_results]
        bk_total = int(bankrupt.get("count") or 0)
        cv_total = int(civil.get("count") or 0)
        found.summary = {
            # ``total`` is the search hit count; not every hit is this
            # company's case, so ``stored`` counts the debtor dockets kept.
            "bankruptcy": {
                "total": bk_total,
                "stored": sum(1 for r in found.records if r["kind"] == "bankruptcy"),
            },
            "federal_civil": {"total": cv_total, "stored": len(cv_results)},
        }
        found.truncated = bk_total > len(bk_results) or cv_total > len(cv_results)
        return found

    def _bankruptcies(
        self, name: str, results: list[dict], notes: list[str]
    ) -> list[dict]:
        key = core_name(name)
        out: list[dict] = []
        checks = 0
        for r in sorted(results, key=lambda x: x.get("dateFiled") or "", reverse=True):
            case = r.get("caseName") or ""
            if names_match(name, case):
                match = "strong"
            elif key and key in core_name(case):
                match = "weak"
            else:
                # The company is only creditor/counsel on someone else's case.
                continue
            terminated = r.get("dateTerminated")
            record = {
                "kind": "bankruptcy",
                "match": match,
                "status": "closed" if terminated else "open",
                "chapter": str(r.get("chapter") or "") or None,
                "court": r.get("court"),
                "court_id": r.get("court_id"),
                "docket_number": r.get("docketNumber"),
                "docket_id": r.get("docket_id"),
                "filed_date": _iso(r.get("dateFiled")),
                "terminated_date": _iso(terminated),
                "party_name": case,
                "party_role": "debtor",
                "freshness": "not_needed",
                "out_of_date": False,
                "last_filing_date": None,
                "source_url": _docket_url(r["docket_id"]),
            }
            if (
                match == "strong"
                and record["status"] == "open"
                and self._recent(record)
            ):
                if checks < MAX_FRESHNESS_CHECKS:
                    checks += 1
                    self._check_freshness(record, notes)
                else:  # budget spent: can't confirm it's open, so it can't score
                    record.update(match="weak", freshness="not_checked")
            out.append(record)
            if len(out) >= MAX_RECORDS_PER_KIND:
                break
        return out

    def _recent(self, record: dict) -> bool:
        filed = _parse_date(record["filed_date"])
        return filed is not None and self._today() - filed <= BANKRUPTCY_WINDOW

    def _check_freshness(self, record: dict, notes: list[str]) -> None:
        """KTD13: an "open" docket must have a filing within the last 12 months.

        Only ``date_last_filing`` counts. ``date_modified`` changes whenever
        CourtListener touches the row, including for our own lookup.
        """
        docket_id = record["docket_id"]
        try:
            detail = self._client.docket(docket_id)
        except ProviderRateLimited:
            notes.append(f"docket {docket_id} detail rate_limited")
            record.update(match="weak", freshness="unknown")
            return
        except Exception as exc:  # can't confirm → don't let it score
            logger.info("CourtListener docket %s detail failed: %s", docket_id, exc)
            notes.append(f"docket {docket_id} detail unavailable")
            record.update(match="weak", freshness="unknown")
            return
        if detail.get("date_terminated"):
            record.update(
                status="closed", terminated_date=_iso(detail["date_terminated"])
            )
        last = _parse_date(detail.get("date_last_filing"))
        record["last_filing_date"] = _iso(last)
        if record["status"] == "closed":
            record["freshness"] = "not_needed"
        elif last is not None and self._today() - last <= FRESHNESS_WINDOW:
            record["freshness"] = "fresh"
        else:
            record.update(match="weak", out_of_date=True, freshness="out_of_date")

    def _civil(self, name: str, r: dict) -> dict:
        parties = r.get("party") or []
        strong = names_match(name, r.get("caseName")) or any(
            names_match(name, p) for p in parties
        )
        return {
            "kind": "federal_civil",
            "match": "strong" if strong else "weak",
            "status": "open",
            "court": r.get("court"),
            "court_id": r.get("court_id"),
            "docket_number": r.get("docketNumber"),
            "docket_id": r.get("docket_id"),
            "filed_date": _iso(r.get("dateFiled")),
            "terminated_date": None,
            "suit_nature": r.get("suitNature") or None,
            "party_name": r.get("caseName") or "",
            "party_role": "unknown",
            "scored": False,
            "source_url": _docket_url(r["docket_id"]),
        }


# ---------------------------------------------------------------------------
# SAM exclusions lookup
# ---------------------------------------------------------------------------


def _aliases(cross_reference: str) -> list[str]:
    parts = re.split(r"[;|]", cross_reference or "")
    return [core_name(_ALIAS_NOTE.sub(" ", p)) for p in parts if p.strip()]


def sam_records(
    db: Any, name: str, state: str | None, today: date
) -> tuple[str, list[dict], dict]:
    """Return (status, records, info) for the latest SAM snapshot."""
    from sqlalchemy import or_  # noqa: PLC0415

    from app.lists import sam  # noqa: PLC0415
    from app.models.sam_exclusion_record import SamExclusionRecord  # noqa: PLC0415

    snapshot = sam.latest_snapshot(db)
    if snapshot is None:
        return "unavailable", [], {}
    retrieved = snapshot.retrieved_at
    if retrieved.tzinfo is None:  # SQLite returns naive values; we write UTC
        retrieved = retrieved.replace(tzinfo=timezone.utc)
    info: dict[str, Any] = {"snapshot_retrieved_at": retrieved.isoformat()}
    stale = datetime.now(tz=timezone.utc) - retrieved > SAM_STALE_AFTER

    key = core_name(name)
    conditions = [SamExclusionRecord.core_name == key]
    first = key.split()[0] if key else ""
    if len(first) >= 4:  # short first words ("abc", "the") match too much
        conditions.append(
            SamExclusionRecord.cross_reference.ilike(
                f"%{_like_escape(first)}%", escape="\\"
            )
        )
    rows = (
        db.query(SamExclusionRecord)
        .filter(SamExclusionRecord.snapshot_id == snapshot.id)
        .filter(or_(*conditions))
        .limit(MAX_ALIAS_CANDIDATES)
        .all()
    )
    records = []
    for row in rows:
        if row.core_name == key:
            matched_on = "name"
        elif key in _aliases(row.cross_reference):
            matched_on = "alias"
        else:
            continue
        row_state = (row.state or "").upper()
        us_row = (row.country or "").upper() in US_COUNTRY_CODES
        conflict = bool(state and row_state and row_state != state)
        strong = (
            row.classification != "Individual"
            and state is not None
            and row_state == state
            and us_row
        )
        terminated = row.termination_date is not None and row.termination_date < today
        party = row.name or " ".join(p for p in (row.first, row.last) if p)
        records.append(
            {
                "kind": "federal_exclusion",
                "match": "strong" if strong else "weak",
                "matched_on": matched_on,
                "status": "terminated" if terminated else "active",
                "classification": row.classification,
                "party_name": party,
                "agency": row.excluding_agency,
                "exclusion_type": row.exclusion_type,
                "exclusion_program": row.exclusion_program,
                "active_date": _iso(row.active_date),
                "termination_date": "Indefinite"
                if row.termination_indefinite
                else _iso(row.termination_date),
                "state": row.state or None,
                "state_conflict": conflict,
                "uei": row.uei or None,
                "cage": row.cage or None,
                "sam_number": row.sam_number,
                "source_url": None,  # no verified per-record SAM.gov URL
            }
        )

    def newest_first(items: list[dict]) -> list[dict]:
        return sorted(items, key=lambda r: r["active_date"] or "", reverse=True)

    ordered = newest_first([r for r in records if r["match"] == "strong"])
    ordered += newest_first([r for r in records if r["match"] != "strong"])
    info["total"] = len(ordered)
    kept = ordered[:MAX_RECORDS_PER_KIND]
    if stale:
        return "stale", kept, info
    return ("searched" if kept else "no_records"), kept, info


# ---------------------------------------------------------------------------
# Stage
# ---------------------------------------------------------------------------


class _EvidenceWriter:
    """Builds this stage's Evidence rows with one shape per row type."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.fetched_at = datetime.now(tz=timezone.utc)
        self.rows: list[Evidence] = []

    def _add(
        self,
        field_name: str,
        raw: str,
        normalized: str | None,
        payload: dict,
        provider: str,
        *,
        url: str | None = None,
        confidence: float | None = None,
    ) -> None:
        attribution = {"provider": provider}
        if url:
            attribution["source_url"] = url
        self.rows.append(
            Evidence(
                verification_run_id=self.run_id,
                source=SOURCE,
                tier=TIER,
                field=field_name,
                raw_value=raw,
                normalized_value=normalized,
                confidence=confidence,
                raw_payload=payload,
                attribution=attribution,
                fetched_at=self.fetched_at,
            )
        )

    def status(self, source: str, value: str, **extra: Any) -> None:
        payload = {"source": source, **{k: v for k, v in extra.items() if v}}
        self._add("public_records_status", value, value, payload, source)

    def summary(self, summary: dict) -> None:
        self._add("public_records_summary", "summary", None, summary, SOURCE)

    def record(self, rec: dict, provider: str) -> None:
        # R11: civil cases are never scoreable in v1, whatever their match.
        normalized = "unscored" if rec["kind"] == "federal_civil" else rec["match"]
        self._add(
            "public_record",
            rec["kind"],
            normalized,
            rec,
            provider,
            url=rec.get("source_url"),
            confidence=0.9 if rec["match"] == "strong" else 0.5,
        )


class PublicRecordsStage:
    """Pipeline stage 3e: after screen_people, before analyze_domain (KTD9)."""

    name = "public_records"

    def __init__(
        self,
        client: CourtListenerClient | None = None,
        today: Callable[[], date] | None = None,
        retry_attempts: int = 1,
    ) -> None:
        # No automatic retries by default: a retry repeats every search, and
        # the free CourtListener tier allows 125 requests a day.
        self._client = client
        self._today = today or (lambda: datetime.now(tz=timezone.utc).date())
        self._retry_attempts = retry_attempts

    def run(self, run_id: str, db: Any, context: dict) -> dict:
        normalized = context.get("normalized", {})
        name = normalized.get("company_name") or ""
        writer = _EvidenceWriter(run_id)

        if (normalized.get("country_iso") or "").upper() != "US":
            sources = {COURTLISTENER: "not_applicable", SAM: "not_applicable"}
            for source, value in sources.items():
                writer.status(source, value)
            return self._finish(db, writer, context, sources)

        sources: dict[str, str] = {}
        summary: dict[str, dict] = {}
        self._courtlistener(run_id, name, writer, sources, summary)

        try:
            state = submission_state(db, run_id, normalized.get("billing_address"))
            sam_status, sam_found, info = sam_records(db, name, state, self._today())
        except Exception as exc:  # KTD5: SAM trouble never hides CourtListener
            logger.warning("SAM exclusions lookup failed: %s", exc)
            db.rollback()
            sam_status, sam_found, info = "unavailable", [], {}
        for rec in sam_found:
            writer.record(rec, SAM)
        sources[SAM] = sam_status
        summary["federal_exclusion"] = {
            "total": info.pop("total", len(sam_found)),
            "stored": len(sam_found),
        }
        writer.status(SAM, sam_status, **info)

        writer.summary(summary)
        return self._finish(db, writer, context, sources)

    def _courtlistener(
        self,
        run_id: str,
        name: str,
        writer: _EvidenceWriter,
        sources: dict,
        summary: dict,
    ) -> None:
        if not core_name(name):
            sources[COURTLISTENER] = "not_applicable"
            writer.status(COURTLISTENER, "not_applicable", notes=["no company name"])
            return
        adapter = RetryingAdapter(
            CourtListenerAdapter(self._client or client_from_env(), self._today),
            max_attempts=self._retry_attempts,
        )
        result = adapter.fetch(
            AdapterContext(run_id=run_id, company_name=name, country_iso="US")
        )
        if not isinstance(result, AdapterSuccess):
            sources[COURTLISTENER] = result.kind
            writer.status(COURTLISTENER, result.kind)
            return
        found: CourtListenerFindings = result.evidence[0]
        for rec in found.records:
            writer.record(rec, COURTLISTENER)
        summary.update(found.summary)
        sources[COURTLISTENER] = "searched" if found.records else "no_records"
        writer.status(
            COURTLISTENER,
            sources[COURTLISTENER],
            truncated=found.truncated,
            notes=found.notes,
        )

    def _finish(
        self, db: Any, writer: _EvidenceWriter, context: dict, sources: dict
    ) -> dict:
        for row in writer.rows:
            db.add(row)
        db.commit()
        outcomes = list(sources.values())
        if outcomes and all(o in FAILURE_KINDS for o in outcomes):
            overall = outcomes[0]  # every source down: the orchestrator marks it
        else:
            overall = "complete"
        return {**context, "public_records": {"status": overall, "sources": sources}}
