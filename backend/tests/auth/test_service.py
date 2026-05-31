"""Tests for P2-T12 — service-credential auth + per-system attribution.

Covers:
  - key generation / hashing / verification primitives
  - submission endpoint requires a valid X-API-Key (401 otherwise)
  - a successful submission is attributed to the calling system (FK + audit)
  - report export accepts an API key (system principal) and audits the pull
  - get_principal rejects callers with neither operator token nor API key
"""

from __future__ import annotations

import unittest.mock as mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.reports import _get_db as reports_get_db
from app.api.submissions import _get_db as submissions_get_db
from app.auth.service import (
    _get_db as service_get_db,
)
from app.auth.service import (
    create_api_client,
    generate_api_key,
    hash_api_key,
    verify_api_key,
)
from app.db.session import Base
from app.main import app
from app.models.api_client import ApiClient
from app.models.audit_event import AuditEvent
from app.models.submission import Submission

_VALID_PAYLOAD = {
    "company_name": "Acme Corp",
    "work_email": "cto@acme.example",
    "company_domain": "acme.example",
    "country": "US",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def db(engine):
    connection = engine.connect()
    transaction = connection.begin()
    sess = Session(bind=connection)
    yield sess
    sess.close()
    transaction.rollback()
    connection.close()


@pytest.fixture(scope="module")
def seeded_key(engine):
    """Seed an ApiClient once per module; return {'id','key','name'}."""
    SessionMaker = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    sess = SessionMaker()
    try:
        client, key = create_api_client("integration-test-system", sess)
        info = {"id": client.id, "key": key, "name": client.name}
    finally:
        sess.close()
    return info


@pytest.fixture
def client(engine):
    SessionMaker = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override_get_db():
        s = SessionMaker()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[submissions_get_db] = override_get_db
    app.dependency_overrides[reports_get_db] = override_get_db
    app.dependency_overrides[service_get_db] = override_get_db
    with mock.patch("app.pipeline.orchestrator.enqueue_run") as _enq:
        _enq.return_value = None
        yield TestClient(app, raise_server_exceptions=True)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Hashing primitives
# ---------------------------------------------------------------------------


def test_generated_key_has_prefix_and_verifies():
    full_key, prefix = generate_api_key()
    assert full_key.startswith("eiq_")
    assert prefix == full_key[:12]
    stored = hash_api_key(full_key)
    assert verify_api_key(full_key, stored) is True
    assert verify_api_key("eiq_wrong", stored) is False


def test_create_api_client_persists_hash_not_plaintext(db: Session):
    client, key = create_api_client("acme-onboarding", db)
    row = db.get(ApiClient, client.id)
    assert row is not None
    assert row.name == "acme-onboarding"
    assert row.active is True
    # The plaintext key is never stored.
    assert key not in row.key_hash
    assert verify_api_key(key, row.key_hash) is True


# ---------------------------------------------------------------------------
# Submission endpoint auth
# ---------------------------------------------------------------------------


def test_submission_without_api_key_is_401(client: TestClient):
    resp = client.post("/submissions", json=_VALID_PAYLOAD)
    assert resp.status_code == 401, resp.text


def test_submission_with_invalid_api_key_is_401(client: TestClient):
    resp = client.post(
        "/submissions",
        json=_VALID_PAYLOAD,
        headers={"X-API-Key": "eiq_not_a_real_key"},
    )
    assert resp.status_code == 401, resp.text


def test_submission_with_valid_key_is_attributed(
    client: TestClient, db: Session, seeded_key
):
    resp = client.post(
        "/submissions",
        json={**_VALID_PAYLOAD, "idempotency_key": "attrib-test-1"},
        headers={"X-API-Key": seeded_key["key"]},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()

    # Submission carries the api_client_id attribution.
    sub = db.get(Submission, body["submission_id"])
    assert sub is not None
    assert sub.api_client_id == seeded_key["id"]

    # An attributed audit event was recorded for the ingest.
    event = (
        db.query(AuditEvent)
        .filter(
            AuditEvent.event_type == "system.submission_received",
            AuditEvent.submission_id == body["submission_id"],
        )
        .first()
    )
    assert event is not None
    assert event.api_client_id == seeded_key["id"]
    assert event.operator_id is None


def test_revoked_key_is_rejected(client: TestClient, db: Session, engine):
    # Seed an inactive client directly.
    SessionMaker = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    s = SessionMaker()
    try:
        c, key = create_api_client("revoked-system", s, commit=False)
        c.active = False
        s.commit()
    finally:
        s.close()

    resp = client.post(
        "/submissions",
        json=_VALID_PAYLOAD,
        headers={"X-API-Key": key},
    )
    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# Report export auth (principal = system via API key)
# ---------------------------------------------------------------------------


def test_export_requires_a_principal(client: TestClient):
    resp = client.get("/reports/some-run/export")
    assert resp.status_code == 401, resp.text


def test_export_with_api_key_audits_the_pull(
    client: TestClient, db: Session, seeded_key
):
    # Build a run + assembled report via the scoring pipeline.
    from datetime import datetime, timezone  # noqa: PLC0415

    from app.models.entity import Entity  # noqa: PLC0415
    from app.models.evidence import Evidence  # noqa: PLC0415
    from app.models.verification_run import VerificationRun  # noqa: PLC0415
    from app.scoring.engine import ScoringStage  # noqa: PLC0415
    from app.scoring.report import StoreReportStage  # noqa: PLC0415

    entity = Entity(canonical_name="Pull Co", canonical_domain="pull.example")
    db.add(entity)
    db.flush()
    sub = Submission(
        company_name="Pull Co",
        domain="pull.example",
        work_email="ceo@pull.example",
        country="US",
        entity_id=entity.id,
    )
    db.add(sub)
    db.flush()
    run = VerificationRun(
        submission_id=sub.id,
        entity_id=entity.id,
        status="complete",
        source_availability={"scoring": "complete", "store_report": "complete"},
    )
    db.add(run)
    db.flush()
    db.add(
        Evidence(
            verification_run_id=run.id,
            source="opencorporates",
            tier=1,
            field="company_name",
            raw_value="Pull Co",
            normalized_value="Pull Co",
            confidence=0.9,
            fetched_at=datetime.now(tz=timezone.utc),
        )
    )
    db.commit()
    ScoringStage().run(run.id, db, {})
    StoreReportStage().run(run.id, db, {})
    run_id = run.id

    resp = client.get(
        f"/reports/{run_id}/export",
        headers={"X-API-Key": seeded_key["key"]},
    )
    assert resp.status_code == 200, resp.text

    event = (
        db.query(AuditEvent)
        .filter(
            AuditEvent.event_type == "report.exported",
            AuditEvent.verification_run_id == run_id,
        )
        .first()
    )
    assert event is not None
    assert event.api_client_id == seeded_key["id"]
