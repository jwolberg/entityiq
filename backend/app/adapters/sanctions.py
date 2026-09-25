"""Tier-1 sanctions/watchlist screening adapter (P2-T3).

Screens a company name (and optionally a requester name) against the OFAC
Specially Designated Nationals and Blocked Persons (SDN) list — a free,
publicly available sanctions list published by the US Treasury Department.

Source: https://www.treasury.gov/ofac/downloads/sdn.csv

Design notes
------------
- The SDN CSV fetcher is injectable so tests can pass a small deterministic
  fixture.  The production fetcher downloads from the OFAC URL above.
- On a match:  high-signal Evidence rows + a ``sanctions_hit`` risk flag at
  confidence 0.95 are emitted.  The hit list entry is preserved in
  raw_payload for auditability.
- On no match: a single ``sanctions_screened`` Evidence row is emitted at
  confidence 1.0 (the source was reachable and the name was checked).
- On unavailable list: AdapterFailure(kind="unavailable") is returned;
  the run continues per ARCHITECTURE § 2 graceful degradation.

Name matching
-------------
- Exact (case-insensitive) match on any SDN name or alias.
- Common legal-suffix stripping (Ltd, Inc, LLC, Corp, GmbH, S.A., Co, PLC)
  is applied to both the query and each list entry before comparison so that
  "ACME Corp" matches "ACME" in the list.

Government business registry integration is explicitly deferred
(Open Decision #5 — per-country licensing/variance). This adapter covers
the sanctions/watchlist piece of P2-T3 only; gov-registry adapters are a
follow-on ticket once the licensing question is resolved.

OFAC SDN CSV columns (1-indexed):
    1  - Ent_Num  (entity number / list ID)
    2  - SDN_Name (primary name)
    3  - SDN_Type (person / entity / vessel / aircraft / etc.)
    4  - Program  (e.g. SDGT, IRAN, ...)
    5  - Title
    6  - Call_Sign
    7  - Vess_type
    8  - Tonnage
    9  - GRT
    10 - Vess_flag
    11 - Vess_owner
    12 - Remarks

Aliases appear in a separate ``alt.csv``.  For MVP we focus on sdn.csv
primary names + ``-0- <alias>`` remarks entries where visible.  Full
alt.csv integration is a follow-on.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from app.adapters.base import (
    AdapterContext,
    AdapterFailure,
    AdapterResult,
    AdapterSuccess,
)
from app.adapters.retry import RetryingAdapter

# OFAC fetching and parsing live in the shared list module (IS1-T1); these
# names stay importable from here for existing callers and tests.
from app.lists.ofac import OFAC_SDN_URL as _OFAC_SDN_URL
from app.lists.ofac import SdnListFetcher
from app.lists.ofac import default_fetcher as _default_sdn_fetcher
from app.lists.ofac import parse_sdn_csv as _parse_sdn_csv
from app.models.evidence import Evidence

logger = logging.getLogger(__name__)

# Legal suffixes to strip before name comparison.  Applied to both query and
# list entry.  Sorted longest-first so "private limited" is tried before
# "limited".
_LEGAL_SUFFIXES = re.compile(
    r"\b("
    r"private limited|public limited company|"
    r"incorporated|corporation|limited liability company|"
    r"gesellschaft mit beschr[aä]nkter haftung|"
    r"sociedad an[oó]nima|"
    r"limited partnership|"
    r"ltd\.?|llc\.?|inc\.?|corp\.?|plc\.?|"
    r"gmbh\.?|s\.?a\.?|co\.?|lp\.?|llp\.?"
    r")\s*$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# SDN list parser
# ---------------------------------------------------------------------------


def _strip_legal_suffix(name: str) -> str:
    """Return name with trailing legal suffixes removed (case-insensitive)."""
    return _LEGAL_SUFFIXES.sub("", name).strip().rstrip(",").strip()


def _normalize_name(name: str) -> str:
    """Lowercase and strip whitespace/punctuation for comparison."""
    return " ".join(_strip_legal_suffix(name).lower().split())


# ---------------------------------------------------------------------------
# Sanctions adapter
# ---------------------------------------------------------------------------


class SanctionsAdapter:
    """Tier-1 adapter: screen names against the OFAC SDN list.

    Args:
        fetcher:  Injectable SDN list fetcher.  Defaults to the production
                  OFAC HTTP fetcher.
        timeout:  Unused in the base implementation (fetcher-level concern);
                  kept for interface parity with other adapters.
    """

    name = "sanctions"
    tier = 1

    def __init__(
        self,
        fetcher: SdnListFetcher | None = None,
    ) -> None:
        self._fetcher = fetcher if fetcher is not None else _default_sdn_fetcher()
        # Cached parsed list — fetched once per adapter instance.
        self._sdn_entries: list[dict[str, str]] | None = None

    # ------------------------------------------------------------------
    # Public: SourceAdapter.fetch (uses AdapterContext)
    # ------------------------------------------------------------------

    def fetch(self, context: AdapterContext) -> AdapterResult:
        """Screen names in context against the SDN list.

        Returns:
            AdapterSuccess with evidence rows.
            AdapterFailure(kind="unavailable") if the list cannot be loaded.
        """
        names_to_check: list[str] = []
        if context.company_name:
            names_to_check.append(context.company_name)

        # requester_name is not on AdapterContext (not needed by other adapters).
        # We screen whatever names we have from context.
        if not names_to_check:
            return AdapterFailure(
                kind="not_found",
                message="No names to screen — company_name is absent",
            )

        try:
            entries = self._load_sdn_list()
        except Exception as exc:
            logger.warning("SanctionsAdapter: could not load SDN list: %s", exc)
            return AdapterFailure(kind="unavailable", message=str(exc))

        try:
            return self._screen(context, names_to_check, entries)
        except Exception as exc:
            logger.warning("SanctionsAdapter unhandled error: %s", exc)
            return AdapterFailure(kind="unavailable", message=str(exc))

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_sdn_list(self) -> list[dict[str, str]]:
        """Load and cache the SDN list (fetched once per adapter instance)."""
        if self._sdn_entries is None:
            csv_text = self._fetcher.fetch_csv()
            self._sdn_entries = _parse_sdn_csv(csv_text)
        return self._sdn_entries

    def _screen(
        self,
        context: AdapterContext,
        names: list[str],
        entries: list[dict[str, str]],
    ) -> AdapterResult:
        """Check each name against the SDN entries and emit Evidence."""
        fetched_at = datetime.now(tz=timezone.utc)
        attribution = {
            "provider": "ofac_sdn",
            "source_url": _OFAC_SDN_URL,
            "list": "OFAC Specially Designated Nationals (SDN)",
        }

        def _ev(
            field: str,
            raw: str | None,
            normalized: str | None = None,
            confidence: float = 0.95,
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
                raw_payload=payload or {},
                attribution=attribution,
                fetched_at=fetched_at,
            )

        evidence: list[Evidence] = []
        hit_found = False

        for query_name in names:
            query_norm = _normalize_name(query_name)
            matches = _find_matches(query_norm, entries)

            if matches:
                hit_found = True
                for match in matches:
                    evidence.append(
                        _ev(
                            "sanctions_hit",
                            match["name"],
                            normalized=match["name"].upper(),
                            confidence=0.95,
                            payload={
                                "query": query_name,
                                "matched_entry": match["name"],
                                "sdn_id": match["id"],
                                "list": "OFAC SDN",
                            },
                        )
                    )
                # Strong risk flag for any sanctions hit.
                evidence.append(
                    _ev(
                        "sanctions_risk_flag",
                        "true",
                        confidence=0.95,
                        payload={
                            "query": query_name,
                            "hit_count": len(matches),
                            "list": "OFAC SDN",
                        },
                    )
                )

        if not hit_found:
            # Emit a clean "screened, no hit" evidence row.
            screened_names = ", ".join(names)
            evidence.append(
                _ev(
                    "sanctions_screened",
                    "no_hit",
                    confidence=1.0,
                    payload={"screened": names, "list": "OFAC SDN"},
                )
            )
            logger.debug("SanctionsAdapter: screened %r — no SDN hits", screened_names)
        else:
            logger.warning(
                "SanctionsAdapter: SDN HIT for names %r — %d evidence rows emitted",
                names,
                len(evidence),
            )

        return AdapterSuccess(evidence=evidence)


def _find_matches(
    query_norm: str, entries: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Return SDN entries that match query_norm (case-insensitive, suffix-stripped).

    Exact match after normalisation is the primary strategy.  This avoids
    false positives from partial-name or substring matching.
    """
    matches: list[dict[str, str]] = []
    for entry in entries:
        entry_norm = _normalize_name(entry["name"])
        if entry_norm == query_norm:
            matches.append(entry)
    return matches


# ---------------------------------------------------------------------------
# Pipeline stage wrapper
# ---------------------------------------------------------------------------


class SanctionsScreeningStage:
    """Pipeline stage: screen company name against sanctions lists (P2-T3).

    Registered AFTER query_registries (stage 3) and BEFORE analyze_domain
    (stage 4) per ARCHITECTURE § 2 (Tier-1 authoritative sources: registries
    and sanctions together form stage 3).

    The fetcher is injectable so tests use a deterministic fixture without
    downloading the live OFAC list.
    """

    name = "sanctions_screening"

    def __init__(self, fetcher: SdnListFetcher | None = None) -> None:
        self._adapter = RetryingAdapter(SanctionsAdapter(fetcher=fetcher))

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
        )

        result = self._adapter.fetch(adapter_context)

        if isinstance(result, AdapterSuccess):
            for ev in result.evidence:
                db.add(ev)
            db.commit()
            sanctions_ctx: dict = {
                "status": "complete",
                "evidence_count": len(result.evidence),
            }
        else:
            sanctions_ctx = {
                "status": result.kind,
                "message": result.message,
            }

        return {**context, "sanctions": sanctions_ctx}
