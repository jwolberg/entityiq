"""public_records stage: CourtListener + SAM exclusions (ticket 0092).

Fixtures copy live response shapes with fictional parties
(tests/fixtures/public_records, guarded by test_public_records_fixtures.py).
No network: CourtListener is a fake client; SAM reads a snapshot ingested from
the fixture extract.
"""

from __future__ import annotations

import copy
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.adapters import public_records as pr
from app.adapters.tax_id import ProviderRateLimited
from app.auth.pii import REDACTED, filter_summary_for_viewer
from app.db.session import Base
from app.lists import sam
from app.models.entity import Entity
from app.models.evidence import Evidence
from app.models.list_snapshot import ListSnapshot
from app.models.submission import Submission
from app.models.verification_run import VerificationRun
from tests.lists.test_sam import _extract

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "public_records"
TODAY = date(2026, 10, 16)


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class FakeCourtListener:
    """Records calls; answers from fixtures. Search kind is read from the query."""

    def __init__(self, *, bankruptcy=None, civil=None, docket=None, error=None):
        self.bankruptcy = (
            bankruptcy if bankruptcy is not None else _load("search_bankruptcy.json")
        )
        self.civil = civil if civil is not None else _load("search_empty.json")
        self.docket_payload = (
            docket if docket is not None else _load("docket_fresh.json")
        )
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    name = "courtlistener"

    def search(self, query: str, **params) -> dict:
        self.calls.append(("search", {"q": query, **params}))
        if self.error:
            raise self.error
        return copy.deepcopy(
            self.bankruptcy
            if "chapter:[* TO *]" in query and "-chapter" not in query
            else self.civil
        )

    def docket(self, docket_id: int) -> dict:
        self.calls.append(("docket", {"id": docket_id}))
        return copy.deepcopy(self.docket_payload)


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sess = Session(bind=engine)
    yield sess
    sess.close()
    engine.dispose()


def _run(db: Session, name: str = "Contoso Freight LLC", country: str = "US") -> str:
    entity = Entity(canonical_name=name)
    db.add(entity)
    db.flush()
    sub = Submission(
        company_name=name,
        domain="contoso.example",
        work_email="a@contoso.example",
        country=country,
        entity_id=entity.id,
    )
    db.add(sub)
    db.flush()
    run = VerificationRun(submission_id=sub.id, entity_id=entity.id, status="pending")
    db.add(run)
    db.commit()
    return run.id


def _registry_state(db: Session, run_id: str, jurisdiction: str) -> None:
    db.add(
        Evidence(
            verification_run_id=run_id,
            source="opencorporates",
            tier=1,
            field="jurisdiction",
            raw_value=jurisdiction,
            normalized_value=jurisdiction.upper(),
            confidence=0.9,
        )
    )
    db.commit()


def _context(name="Contoso Freight LLC", country="US", billing=None) -> dict:
    return {
        "normalized": {
            "company_name": name,
            "country_iso": country,
            "billing_address": billing,
        }
    }


def _stage(client=None, **kwargs) -> pr.PublicRecordsStage:
    return pr.PublicRecordsStage(
        client=client if client is not None else FakeCourtListener(),
        today=lambda: TODAY,
        retry_attempts=1,
        **kwargs,
    )


def _rows(db: Session, run_id: str) -> list[Evidence]:
    return (
        db.query(Evidence)
        .filter(
            Evidence.verification_run_id == run_id, Evidence.source == "public_records"
        )
        .all()
    )


def _records(db, run_id, kind=None):
    out = [r for r in _rows(db, run_id) if r.field == "public_record"]
    return [r for r in out if kind is None or r.raw_value == kind]


def _status(db, run_id) -> dict[str, str]:
    return {
        r.raw_payload["source"]: r.raw_value
        for r in _rows(db, run_id)
        if r.field == "public_records_status"
    }


def _ingest_sam(db, retrieved_at=None):
    result = sam.ingest(db, fetch=_extract)
    if retrieved_at is not None:
        snap = db.get(ListSnapshot, result.snapshot_id)
        snap.retrieved_at = retrieved_at
        db.commit()
    return result


# --- unconfigured / not applicable ------------------------------------------


def test_unconfigured_sources_are_unavailable_with_no_records(db, monkeypatch):
    """Covers AE4: no token and no SAM snapshot → nothing but status rows."""
    monkeypatch.delenv("COURTLISTENER_API_TOKEN", raising=False)
    run_id = _run(db)
    out = pr.PublicRecordsStage(today=lambda: TODAY).run(run_id, db, _context())
    assert _status(db, run_id) == {
        "courtlistener": "unavailable",
        "sam_exclusions": "unavailable",
    }
    assert _records(db, run_id) == []
    assert out["public_records"]["status"] == "unavailable"


def test_non_us_submission_is_not_applicable_and_calls_nothing(db):
    run_id = _run(db, country="GB")
    client = FakeCourtListener()
    out = _stage(client).run(run_id, db, _context(country="GB"))
    assert client.calls == []
    assert set(_status(db, run_id).values()) == {"not_applicable"}
    assert out["public_records"]["status"] == "complete"


# --- CourtListener bankruptcy ------------------------------------------------


def test_open_chapter_11_is_a_strong_bankruptcy_record_with_citation(db):
    run_id = _run(db)
    _registry_state(db, run_id, "us_tx")
    _stage().run(run_id, db, _context())
    strong = [
        r
        for r in _records(db, run_id, "bankruptcy")
        if r.normalized_value == "strong" and r.raw_payload["status"] == "open"
    ]
    assert len(strong) == 1  # the 2019 case is the same debtor but closed
    rec = strong[0].raw_payload
    assert rec["docket_number"] == "25-10417"
    assert rec["court"] == "United States Bankruptcy Court, D. Delaware"
    assert rec["filed_date"] == "2025-08-12"
    assert rec["status"] == "open"
    assert rec["chapter"] == "11"
    assert (
        strong[0].attribution["source_url"]
        == "https://www.courtlistener.com/docket/900001/"
    )


def test_delaware_venue_for_a_texas_company_stays_strong(db):
    """Covers AE5 / KTD10: a court's district is never a state conflict."""
    run_id = _run(db)
    _registry_state(db, run_id, "us_tx")
    _stage().run(run_id, db, _context())
    rec = next(
        r
        for r in _records(db, run_id, "bankruptcy")
        if r.raw_payload["docket_number"] == "25-10417"
    )
    assert rec.normalized_value == "strong"


def test_name_only_in_party_list_never_matches_a_bankruptcy(db):
    """Contoso is creditor counsel-side in Fabrikam's case: not its bankruptcy."""
    run_id = _run(db)
    _stage().run(run_id, db, _context())
    assert all(
        "Fabrikam" not in r.raw_payload["party_name"]
        for r in _records(db, run_id, "bankruptcy")
    )


def test_closed_bankruptcy_is_shown_but_marked_closed(db):
    run_id = _run(db)
    _stage().run(run_id, db, _context())
    closed = next(
        r
        for r in _records(db, run_id, "bankruptcy")
        if r.raw_payload["docket_number"] == "19-30122"
    )
    assert closed.raw_payload["status"] == "closed"
    assert closed.raw_payload["terminated_date"] == "2020-11-30"


def test_stale_open_docket_is_weak_and_flagged_out_of_date(db):
    """KTD13: open only if refreshed within 12 months."""
    run_id = _run(db)
    _stage(FakeCourtListener(docket=_load("docket_stale.json"))).run(
        run_id, db, _context()
    )
    rec = next(
        r
        for r in _records(db, run_id, "bankruptcy")
        if r.raw_payload["docket_number"] == "25-10417"
    )
    assert rec.normalized_value == "weak"
    assert rec.raw_payload["out_of_date"] is True


def test_freshness_is_checked_only_for_strong_open_recent_bankruptcies(db):
    run_id = _run(db)
    client = FakeCourtListener()
    _stage(client).run(run_id, db, _context())
    assert [c for c in client.calls if c[0] == "docket"] == [("docket", {"id": 900001})]
    assert len([c for c in client.calls if c[0] == "search"]) == 2


# --- CourtListener civil -------------------------------------------------------


def test_civil_cases_are_stored_unscored_with_true_count(db):
    """R11 fallback: role unknown, never scored; summary keeps the true total."""
    run_id = _run(db)
    _stage(FakeCourtListener(civil=_load("search_civil.json"))).run(
        run_id, db, _context()
    )
    civil = _records(db, run_id, "federal_civil")
    assert len(civil) == 5
    assert all(r.raw_payload["party_role"] == "unknown" for r in civil)
    assert all(r.raw_payload["scored"] is False for r in civil)
    assert all(r.normalized_value == "unscored" for r in civil)  # never scoreable
    summary = next(r for r in _rows(db, run_id) if r.field == "public_records_summary")
    assert summary.raw_payload["federal_civil"] == {"total": 57, "stored": 5}


def test_records_are_capped_most_recent_first(db):
    many = _load("search_civil.json")
    template = many["results"][0]
    many["results"] = [
        {
            **template,
            "docket_id": 920000 + i,
            "docketNumber": f"1:26-cv-{2000 + i}",
            "dateFiled": f"2026-01-{1 + i % 28:02d}",
        }
        for i in range(60)
    ]
    many["count"] = 60
    run_id = _run(db)
    _stage(FakeCourtListener(civil=many)).run(run_id, db, _context())
    civil = _records(db, run_id, "federal_civil")
    assert len(civil) == pr.MAX_RECORDS_PER_KIND == 50
    filed = [r.raw_payload["filed_date"] for r in civil]
    assert filed == sorted(filed, reverse=True)


# --- CourtListener failures ------------------------------------------------


@pytest.mark.parametrize(
    "error, kind",
    [
        (TimeoutError("slow"), "timeout"),
        (ProviderRateLimited("429"), "rate_limited"),
        (RuntimeError("boom"), "unavailable"),
    ],
)
def test_courtlistener_failure_kinds_do_not_hide_a_sam_hit(db, error, kind):
    run_id = _run(db, name="Fabrikam Logistics Inc.")
    _registry_state(db, run_id, "us_tx")
    _ingest_sam(db)
    out = _stage(FakeCourtListener(error=error)).run(
        run_id, db, _context(name="Fabrikam Logistics Inc.")
    )
    assert _status(db, run_id)["courtlistener"] == kind
    assert [r.normalized_value for r in _records(db, run_id, "federal_exclusion")] == [
        "strong"
    ]
    assert out["public_records"]["status"] == "complete"
    assert out["public_records"]["sources"]["courtlistener"] == kind


def test_outbound_query_carries_only_the_name_and_filters(db):
    run_id = _run(db)
    client = FakeCourtListener()
    _stage(client).run(run_id, db, _context(billing="1 Main St, Austin, TX 78701"))
    for kind, params in client.calls:
        if kind == "search":
            blob = json.dumps(params)
            assert "Main St" not in blob and "78701" not in blob and "@" not in blob
            assert 'party:"Contoso Freight LLC"' in params["q"]


# --- SAM exclusions --------------------------------------------------------


def test_firm_matching_name_and_registry_state_is_strong(db):
    run_id = _run(db, name="Fabrikam Logistics Inc.")
    _registry_state(db, run_id, "us_tx")
    _ingest_sam(db)
    _stage(FakeCourtListener(bankruptcy=_load("search_empty.json"))).run(
        run_id, db, _context(name="Fabrikam Logistics Inc.")
    )
    (rec,) = _records(db, run_id, "federal_exclusion")
    assert rec.normalized_value == "strong"
    p = rec.raw_payload
    assert p["agency"] == "GSA" and p["active_date"] == "2025-03-14"
    assert p["status"] == "active" and p["classification"] == "Firm"
    assert p["sam_number"] == "00000000-0000-4000-8000-000000000001"


def test_sam_state_conflict_makes_the_match_weak(db):
    run_id = _run(db, name="Fabrikam Logistics Inc.")
    _registry_state(db, run_id, "us_ca")
    _ingest_sam(db)
    _stage().run(run_id, db, _context(name="Fabrikam Logistics Inc."))
    (rec,) = _records(db, run_id, "federal_exclusion")
    assert rec.normalized_value == "weak"


def test_billing_address_state_is_the_fallback(db):
    run_id = _run(db, name="Fabrikam Logistics Inc.")
    _ingest_sam(db)
    _stage().run(
        run_id,
        db,
        _context(
            name="Fabrikam Logistics Inc.", billing="100 Example Way, Austin, TX 78701"
        ),
    )
    (rec,) = _records(db, run_id, "federal_exclusion")
    assert rec.normalized_value == "strong"


def test_sam_match_without_any_known_state_stays_weak(db):
    run_id = _run(db, name="Fabrikam Logistics Inc.")
    _ingest_sam(db)
    _stage().run(run_id, db, _context(name="Fabrikam Logistics Inc."))
    (rec,) = _records(db, run_id, "federal_exclusion")
    assert rec.normalized_value == "weak"


def test_alias_in_cross_reference_matches(db):
    run_id = _run(db, name="Fabrikam Transport")
    _registry_state(db, run_id, "us_tx")
    _ingest_sam(db)
    _stage().run(run_id, db, _context(name="Fabrikam Transport"))
    (rec,) = _records(db, run_id, "federal_exclusion")
    assert rec.normalized_value == "strong"
    assert rec.raw_payload["matched_on"] == "alias"


def test_individual_exclusion_with_the_company_name_is_weak(db):
    """KTD12: individual rows never strong-match a business."""
    run_id = _run(db, name="Contoso Example")
    _registry_state(db, run_id, "us_tx")
    _ingest_sam(db)
    _stage().run(run_id, db, _context(name="Contoso Example"))
    (rec,) = _records(db, run_id, "federal_exclusion")
    assert rec.raw_payload["classification"] == "Individual"
    assert rec.normalized_value == "weak"


def test_terminated_exclusion_is_shown_inactive(db):
    run_id = _run(db, name="Northwind Builders LLC")
    _registry_state(db, run_id, "us_nv")
    _ingest_sam(db)
    pr.PublicRecordsStage(
        client=FakeCourtListener(), today=lambda: date(2030, 1, 1), retry_attempts=1
    ).run(run_id, db, _context(name="Northwind Builders LLC"))
    (rec,) = _records(db, run_id, "federal_exclusion")
    assert rec.raw_payload["status"] == "terminated"


def test_stale_snapshot_is_used_and_flagged(db):
    run_id = _run(db, name="Fabrikam Logistics Inc.")
    _registry_state(db, run_id, "us_tx")
    _ingest_sam(db, retrieved_at=datetime(2026, 10, 1, tzinfo=timezone.utc))
    _stage().run(run_id, db, _context(name="Fabrikam Logistics Inc."))
    assert _status(db, run_id)["sam_exclusions"] == "stale"
    assert len(_records(db, run_id, "federal_exclusion")) == 1


def test_no_matches_records_no_records_status(db):
    run_id = _run(db, name="Proseware Unrelated Co")
    _ingest_sam(db)
    _stage(FakeCourtListener(bankruptcy=_load("search_empty.json"))).run(
        run_id, db, _context(name="Proseware Unrelated Co")
    )
    assert _status(db, run_id) == {
        "courtlistener": "no_records",
        "sam_exclusions": "no_records",
    }


# --- ADR-0007 obligations -----------------------------------------------------


def test_evidence_does_not_reference_sam_rows_so_pruning_still_works(db):
    run_id = _run(db, name="Fabrikam Logistics Inc.")
    _registry_state(db, run_id, "us_tx")
    first = _ingest_sam(db)
    _stage().run(run_id, db, _context(name="Fabrikam Logistics Inc."))
    for n in range(sam.KEEP_SNAPSHOTS + 1):
        from tests.lists.test_sam import _variant

        sam.ingest(db, fetch=lambda n=n: _variant(n))
    assert db.get(ListSnapshot, first.snapshot_id) is None  # pruned
    fks = inspect(db.get_bind()).get_foreign_keys("evidence")
    assert all(
        fk["referred_table"] not in ("sam_exclusion_record", "list_snapshot")
        for fk in fks
    )
    (rec,) = _records(db, run_id, "federal_exclusion")
    assert rec.raw_payload["sam_number"]  # evidence kept its own copy


def test_api_key_viewers_get_person_names_redacted():
    summary = {
        "evidence": [
            {
                "source": "public_records",
                "field": "public_record",
                "raw_value": "federal_exclusion",
                "normalized_value": "weak",
                "raw_payload": {
                    "kind": "federal_exclusion",
                    "classification": "Individual",
                    "party_name": "Jordan Example",
                    "match": "weak",
                },
            },
            {
                "source": "public_records",
                "field": "public_record",
                "raw_value": "bankruptcy",
                "normalized_value": "strong",
                "raw_payload": {
                    "kind": "bankruptcy",
                    "classification": None,
                    "party_name": "Contoso Freight LLC",
                    "match": "strong",
                },
            },
            {
                "source": "public_records",
                "field": "public_record",
                "raw_value": "federal_civil",
                "normalized_value": "strong",
                "raw_payload": {
                    "kind": "federal_civil",
                    "party_name": "Jordan Example v. Contoso Freight LLC",
                    "match": "strong",
                },
            },
        ]
    }
    system = filter_summary_for_viewer(summary, "system")["evidence"]
    assert system[0]["raw_payload"]["party_name"] == REDACTED
    assert system[1]["raw_payload"]["party_name"] == "Contoso Freight LLC"
    assert (
        system[2]["raw_payload"]["party_name"] == REDACTED
    )  # other side may be a person
    weak_firm = {
        "evidence": [
            {
                "source": "public_records",
                "field": "public_record",
                "raw_payload": {
                    "kind": "federal_exclusion",
                    "classification": "Firm",
                    "match": "weak",
                    "party_name": "Jordan Example Trucking",  # sole proprietor
                },
            }
        ]
    }
    redacted = filter_summary_for_viewer(weak_firm, "system")["evidence"][0]
    assert redacted["raw_payload"]["party_name"] == REDACTED
    operator = filter_summary_for_viewer(summary, "operator")["evidence"]
    assert operator[0]["raw_payload"]["party_name"] == "Jordan Example"
    assert (
        summary["evidence"][0]["raw_payload"]["party_name"] == "Jordan Example"
    )  # not mutated


def test_report_summary_carries_public_records_payload(db):
    from app.scoring.report import _build_summary

    run_id = _run(db)
    _stage().run(run_id, db, _context())
    rows = _rows(db, run_id)
    built = _build_summary(rows, [], None, {})
    items = [e for e in built["evidence"] if e["field"] == "public_record"]
    assert items and all("raw_payload" in e for e in items)


def test_report_with_person_named_public_records_is_audited_as_pii():
    from app.auth.pii import report_contains_pii

    person = {
        "source": "public_records",
        "field": "public_record",
        "raw_payload": {"kind": "federal_exclusion", "classification": "Individual"},
    }
    company = {
        "source": "public_records",
        "field": "public_record",
        "raw_payload": {"kind": "bankruptcy", "match": "strong"},
    }
    assert report_contains_pii({"evidence": [person]}) is True
    assert report_contains_pii({"evidence": [company]}) is False


# --- review hardening (2026-10-16 fresh-context review of 0092) -------------


def test_summary_reports_search_totals_and_truncation(db):
    bk = _load("search_bankruptcy.json")
    bk["count"] = 41
    civil = _load("search_civil.json")
    run_id = _run(db)
    _stage(FakeCourtListener(bankruptcy=bk, civil=civil)).run(run_id, db, _context())
    summary = next(r for r in _rows(db, run_id) if r.field == "public_records_summary")
    assert summary.raw_payload["bankruptcy"]["total"] == 41
    assert summary.raw_payload["federal_civil"] == {"total": 57, "stored": 5}
    cl = next(
        r
        for r in _rows(db, run_id)
        if r.field == "public_records_status"
        and r.raw_payload["source"] == "courtlistener"
    )
    assert cl.raw_payload["truncated"] is True


def test_status_and_summary_rows_carry_no_confidence(db):
    run_id = _run(db)
    _stage().run(run_id, db, _context())
    meta = [r for r in _rows(db, run_id) if r.field != "public_record"]
    assert meta and all(r.confidence is None for r in meta)


def test_courtlistener_is_not_retried_by_default_to_protect_the_daily_budget(db):
    run_id = _run(db)
    client = FakeCourtListener(error=TimeoutError("slow"))
    pr.PublicRecordsStage(client=client, today=lambda: TODAY).run(
        run_id, db, _context()
    )
    assert len(client.calls) == 1


def test_docket_detail_calls_are_capped(db):
    bk = _load("search_bankruptcy.json")
    base = bk["results"][0]
    bk["results"] = [
        {**base, "docket_id": 930000 + i, "docketNumber": f"25-1{i:04d}"}
        for i in range(6)
    ]
    run_id = _run(db)
    client = FakeCourtListener(bankruptcy=bk)
    _stage(client).run(run_id, db, _context())
    assert len([c for c in client.calls if c[0] == "docket"]) == pr.MAX_FRESHNESS_CHECKS
    unchecked = [
        r
        for r in _records(db, run_id, "bankruptcy")
        if r.raw_payload["freshness"] == "not_checked"
    ]
    assert len(unchecked) == 6 - pr.MAX_FRESHNESS_CHECKS
    assert all(r.normalized_value == "weak" for r in unchecked)


def test_freshness_ignores_date_modified(db):
    detail = _load("docket_stale.json")
    detail["date_modified"] = "2026-10-15T10:00:00-07:00"  # touched by our own lookup
    run_id = _run(db)
    _stage(FakeCourtListener(docket=detail)).run(run_id, db, _context())
    rec = next(
        r
        for r in _records(db, run_id, "bankruptcy")
        if r.raw_payload["docket_number"] == "25-10417"
    )
    assert (
        rec.normalized_value == "weak" and rec.raw_payload["freshness"] == "out_of_date"
    )


def test_rate_limit_during_freshness_is_surfaced(db):
    class Limited(FakeCourtListener):
        def docket(self, docket_id):
            raise ProviderRateLimited("429")

    run_id = _run(db)
    _stage(Limited()).run(run_id, db, _context())
    rec = next(
        r
        for r in _records(db, run_id, "bankruptcy")
        if r.raw_payload["docket_number"] == "25-10417"
    )
    assert rec.normalized_value == "weak" and rec.raw_payload["freshness"] == "unknown"
    cl = next(
        r
        for r in _rows(db, run_id)
        if r.field == "public_records_status"
        and r.raw_payload["source"] == "courtlistener"
    )
    assert "rate_limited" in " ".join(cl.raw_payload.get("notes", []))


def test_sam_failure_does_not_lose_courtlistener_results(db, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("no such table: sam_exclusion_record")

    monkeypatch.setattr(pr, "sam_records", broken)
    run_id = _run(db)
    out = _stage().run(run_id, db, _context())
    assert _status(db, run_id)["sam_exclusions"] == "unavailable"
    assert _records(db, run_id, "bankruptcy")
    assert out["public_records"]["status"] == "complete"


def test_sam_firm_row_without_a_state_is_weak(db):
    run_id = _run(db, name="Fabrikam Logistics Inc.")
    _registry_state(db, run_id, "us_tx")
    from app.models.sam_exclusion_record import SamExclusionRecord

    _ingest_sam(db)
    db.query(SamExclusionRecord).filter_by(core_name="fabrikam logistics").update(
        {"state": ""}
    )
    db.commit()
    _stage().run(run_id, db, _context(name="Fabrikam Logistics Inc."))
    (rec,) = _records(db, run_id, "federal_exclusion")
    assert rec.normalized_value == "weak"


def test_alias_query_escapes_like_wildcards(db):
    run_id = _run(db, name="Fab_ikam Transport")
    _registry_state(db, run_id, "us_tx")
    _ingest_sam(db)
    _stage().run(run_id, db, _context(name="Fab_ikam Transport"))
    assert _records(db, run_id, "federal_exclusion") == []


def test_blank_company_name_spends_no_api_calls(db):
    run_id = _run(db)
    client = FakeCourtListener()
    _stage(client).run(run_id, db, _context(name="  "))
    assert client.calls == []


def test_non_state_two_letter_token_is_not_a_billing_state(db):
    run_id = _run(db)
    assert pr.submission_state(db, run_id, "Unit 4, Postfach ZZ 12345") is None
    assert pr.submission_state(db, run_id, "1 Main St, Austin, TX 78701") == "TX"


def test_redaction_round_trip_from_real_stage_output(db):
    from app.scoring.report import _build_summary

    run_id = _run(db, name="Contoso Example")
    _registry_state(db, run_id, "us_tx")
    _ingest_sam(db)
    _stage(FakeCourtListener(civil=_load("search_civil.json"))).run(
        run_id, db, _context(name="Contoso Example")
    )
    summary = _build_summary(_rows(db, run_id), [], None, {})
    system = [
        e
        for e in filter_summary_for_viewer(summary, "system")["evidence"]
        if e["field"] == "public_record"
    ]
    assert system
    for item in system:
        payload = item["raw_payload"]
        visible = (
            payload["match"] == "strong"
            and payload["kind"] in ("bankruptcy", "federal_exclusion")
            and payload.get("classification") != "Individual"
        )
        assert (payload["party_name"] == REDACTED) is (not visible), payload
    operator = filter_summary_for_viewer(summary, "operator")["evidence"]
    assert all(
        e["raw_payload"]["party_name"] != REDACTED
        for e in operator
        if e["field"] == "public_record"
    )


def test_http_client_maps_statuses_and_sends_only_search_params(monkeypatch):
    import httpx

    seen = {}

    class _Resp:
        def __init__(self, code, body=None):
            self.status_code = code
            self._body = body or {"count": 0, "results": []}

        def json(self):
            return self._body

    def fake_get(url, params=None, headers=None, timeout=None):
        seen.update(url=url, params=params, headers=headers)
        return responses.pop(0)

    monkeypatch.setattr(httpx, "get", fake_get)
    client = pr.HttpCourtListener("tok")

    responses = [_Resp(200)]
    client.search('party:"Contoso"', order_by="dateFiled desc")
    assert seen["url"] == "https://www.courtlistener.com/api/rest/v4/search/"
    assert seen["params"] == {
        "type": "d",
        "q": 'party:"Contoso"',
        "order_by": "dateFiled desc",
    }
    assert seen["headers"] == {"Authorization": "Token tok"}

    responses = [_Resp(429)]
    with pytest.raises(ProviderRateLimited):
        client.search("x")
    responses = [_Resp(500)]
    with pytest.raises(RuntimeError, match="500"):
        client.search("x")

    def timeout(*a, **k):
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(httpx, "get", timeout)
    with pytest.raises(TimeoutError):
        client.docket(1)


def test_non_positive_docket_ids_get_no_link(db):
    """Demo dockets use ids <= 0 so they never link to a real CourtListener case."""
    bk = _load("search_bankruptcy.json")
    for r in bk["results"]:
        r["docket_id"] = -r["docket_id"]
    run_id = _run(db)
    _stage(FakeCourtListener(bankruptcy=bk)).run(run_id, db, _context())
    recs = _records(db, run_id, "bankruptcy")
    assert recs and all(r.raw_payload["source_url"] is None for r in recs)
    assert all("source_url" not in (r.attribution or {}) for r in recs)


# --- live-data findings (2026-10-16 run against real CourtListener data) ------


def test_jointly_administered_et_al_case_name_is_the_debtor(db):
    """Real cases are often captioned "Debtor, LLC, et al."; that's still the debtor."""
    bk = _load("search_bankruptcy.json")
    bk["results"][0]["caseName"] = "Contoso Freight LLC, et al."
    run_id = _run(db)
    _stage(FakeCourtListener(bankruptcy=bk)).run(run_id, db, _context())
    rec = next(
        r
        for r in _records(db, run_id, "bankruptcy")
        if r.raw_payload["docket_number"] == "25-10417"
    )
    assert rec.normalized_value == "strong"


def test_duplicate_dockets_for_one_case_are_stored_once(db):
    """CourtListener can hold several docket ids for one court + case number."""
    civil = _load("search_civil.json")
    first = civil["results"][0]
    civil["results"] = [
        first,
        {**first, "docket_id": first["docket_id"] + 7},
        *civil["results"][1:],
    ]
    run_id = _run(db)
    _stage(FakeCourtListener(civil=civil)).run(run_id, db, _context())
    numbers = [
        r.raw_payload["docket_number"] for r in _records(db, run_id, "federal_civil")
    ]
    assert len(numbers) == len(set(numbers)) == 5
