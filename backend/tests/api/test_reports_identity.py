"""Report and export carry identity corroboration additively (IC1-T7, ticket 0013).

Runs the real pipeline (fakes only at the network edge, as in the e2e test),
then reads GET /reports/{id} and /reports/{id}/export. Covers populated,
unavailable and pending states for both new sources, plus a contract check
that no pre-existing response field was removed or renamed.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.adapters.linkedin import (
    LinkedInAdapter,
    UnconfiguredLinkedInProvider,
    VerifyLinkedInStage,
)
from app.adapters.tax_id import (
    TaxIdAdapter,
    UnconfiguredTaxIdProvider,
    VerifyTaxIdStage,
)
from app.api.reports import _get_db
from app.auth.service import Principal, get_principal
from app.db.session import Base
from app.main import app
from app.models.verification_run import VerificationRun
from app.pipeline.orchestrator import Orchestrator
from app.scoring.report import assemble_report
from tests.pipeline.test_pipeline_e2e import _ACME_REGISTRY, _stages, _submit

# Response shape as it was before identity corroboration. Every key here must
# still be present: new data may only be added.
_REPORT_KEYS = {
    "run_id",
    "report_id",
    "status",
    "section_statuses",
    "scores",
    "evidence",
    "mismatches",
    "sources",
    "generated_at",
    "review",
    "run",
}
_EVIDENCE_KEYS = {
    "id",
    "source",
    "tier",
    "field",
    "raw_value",
    "normalized_value",
    "confidence",
    "attribution",
    "fetched_at",
}
_MISMATCH_KEYS = {
    "id",
    "field_name",
    "submitted_value",
    "discovered_value",
    "match_status",
    "evidence_id",
}
_SOURCE_KEYS = {"source", "tier", "evidence_count", "attribution", "status"}
_SIGNAL_KEYS = {"name", "layer", "direction", "weight", "description", "evidence_ids"}


@pytest.fixture
def identity_engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'identity.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def identity_client(identity_engine):
    factory = sessionmaker(bind=identity_engine, autocommit=False, autoflush=False)

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[_get_db] = override_get_db
    app.dependency_overrides[get_principal] = lambda: Principal(
        kind="operator", id="op-test", name="test@example.com", operator_id="op-test"
    )
    yield TestClient(app, raise_server_exceptions=True)
    app.dependency_overrides.pop(_get_db, None)
    app.dependency_overrides.pop(get_principal, None)


def _pipeline(*, configured: bool = True):
    stages = _stages(
        age_days=4000,
        mx=["aspmx.l.google.com"],
        txt=["v=spf1 include:_spf.google.com ~all"],
        registry=_ACME_REGISTRY,
    )
    if configured:
        return stages
    swap = {
        "verify_tax_id": VerifyTaxIdStage(
            TaxIdAdapter(provider=UnconfiguredTaxIdProvider())
        ),
        "verify_linkedin": VerifyLinkedInStage(
            LinkedInAdapter(provider=UnconfiguredLinkedInProvider())
        ),
    }
    return [swap.get(s.name, s) for s in stages]


def _run_pipeline(engine, **kw) -> str:
    db = Session(bind=engine)
    try:
        run = _submit(db)
        Orchestrator(_pipeline(**kw)).run_sync(run.id, db)
        return run.id
    finally:
        db.close()


@pytest.mark.parametrize("path", ["/reports/{id}", "/reports/{id}/export"])
def test_populated_identity_data_flows_through_report_and_export(
    identity_engine, identity_client, path
):
    run_id = _run_pipeline(identity_engine)

    data = identity_client.get(path.format(id=run_id)).json()

    fields = {(e["source"], e["field"]) for e in data["evidence"]}
    assert ("tax_id", "tax_id_status") in fields
    assert ("tax_id", "tax_id_name_match") in fields
    assert ("linkedin", "linkedin_presence") in fields
    assert ("linkedin", "linkedin_website") in fields

    rows = {m["field_name"]: m["match_status"] for m in data["mismatches"]}
    assert rows["tax_id"] == "match"
    assert rows["tax_id_registered_name"] == "match"
    assert rows["linkedin_website"] == "match"

    signals = {s["name"] for s in data["scores"]["contributing_signals"]}
    assert {"tax_id_verified_active", "linkedin_established_presence"} <= signals

    sources = {s["source"]: s for s in data["sources"]}
    assert sources["tax_id"]["evidence_count"] > 0
    assert sources["tax_id"]["tier"] == 1
    assert sources["linkedin"]["tier"] == 3


def test_unconfigured_identity_sources_are_reported_unavailable(
    identity_engine, identity_client
):
    run_id = _run_pipeline(identity_engine, configured=False)

    data = identity_client.get(f"/reports/{run_id}").json()

    sources = {s["source"]: s for s in data["sources"]}
    assert sources["tax_id"]["status"] == "unavailable"
    assert sources["linkedin"]["status"] == "unavailable"
    assert data["run"]["stages"]["verify_tax_id"] == "unavailable"
    assert data["run"]["stages"]["verify_linkedin"] == "unavailable"
    assert not any(e["source"] in ("tax_id", "linkedin") for e in data["evidence"])
    signals = {s["name"] for s in data["scores"]["contributing_signals"]}
    assert not any(n.startswith(("tax_id", "linkedin")) for n in signals)
    rows = {m["field_name"]: m["match_status"] for m in data["mismatches"]}
    assert rows["tax_id"] == "unverified"
    assert rows["linkedin_website"] == "unverified"


def test_in_flight_run_shows_identity_stages_pending(identity_engine, identity_client):
    db = Session(bind=identity_engine)
    try:
        run = _submit(db)
        run.status = "running"
        run.source_availability = {
            "normalize_input": "complete",
            "query_registries": "complete",
            "verify_tax_id": "pending",
            "verify_linkedin": "pending",
            "scoring": "pending",
        }
        db.commit()
        run_id = run.id
        assemble_report(run_id, db)
        db.commit()
    finally:
        db.close()

    data = identity_client.get(f"/reports/{run_id}").json()

    assert data["run"]["stages"]["verify_tax_id"] == "pending"
    assert data["run"]["stages"]["verify_linkedin"] == "pending"
    assert not any(s["source"] in ("tax_id", "linkedin") for s in data["sources"])


def test_no_existing_response_field_was_removed_or_renamed(
    identity_engine, identity_client
):
    run_id = _run_pipeline(identity_engine)

    for path in (f"/reports/{run_id}", f"/reports/{run_id}/export"):
        data = identity_client.get(path).json()
        assert _REPORT_KEYS <= set(data)
        assert all(_EVIDENCE_KEYS <= set(e) for e in data["evidence"])
        assert all(_MISMATCH_KEYS <= set(m) for m in data["mismatches"])
        assert all(_SOURCE_KEYS <= set(s) for s in data["sources"])
        assert all(
            _SIGNAL_KEYS <= set(s) for s in data["scores"]["contributing_signals"]
        )


def test_run_row_is_unchanged_by_identity_stages(identity_engine):
    """Guard: the new stages write evidence only, never the run's own columns."""
    run_id = _run_pipeline(identity_engine)
    db = Session(bind=identity_engine)
    try:
        run = db.get(VerificationRun, run_id)
        assert run.status == "complete"
        assert run.failure_reason is None
    finally:
        db.close()
