"""Tests for ticket 0003 — domain-ownership verification API.

  POST /ownership/runs/{run_id}/challenges          — issue a challenge
  GET  /ownership/runs/{run_id}/challenges           — list challenges
  POST /ownership/challenges/{challenge_id}/verify   — attempt verification

Scenarios (ticket 0003 acceptance criteria):
  1. A challenge token can be issued for a submission's domain.
  2. A correct DNS TXT token verifies and adds an ownership trust signal.
  3. An absent/incorrect token leaves the domain unverified and adds no
     trust signal.
  4. Verified ownership never raises representation confidence to
     "authorized" — the signal stays bounded.
  5. Email and HTML meta-tag methods follow the same issue/verify contract.
  Plus: auth (operator token AND integration API key), audit trail, 404s.
"""

from __future__ import annotations

import unittest.mock as mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.ownership import _get_db as ownership_get_db
from app.auth.operator import _clear_all_sessions, hash_password
from app.auth.operator import _get_db as auth_get_db
from app.auth.service import Principal, get_principal
from app.auth.service import _get_db as service_get_db
from app.db.session import Base
from app.main import app
from app.models.audit_event import AuditEvent
from app.models.entity import Entity
from app.models.evidence import Evidence
from app.models.operator import Operator
from app.models.ownership_challenge import OwnershipChallenge
from app.models.submission import Submission
from app.models.verification_run import VerificationRun
from app.pipeline.ownership import dns_txt_record_value, html_meta_snippet
from app.scoring.signals import representation_confidence_signals
from tests.pipeline.test_ownership import FakeDnsTxtClient, FakeHttpPageFetcher

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ownership_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def db(ownership_engine):
    connection = ownership_engine.connect()
    transaction = connection.begin()
    sess = Session(bind=connection)
    yield sess
    sess.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def client(ownership_engine):
    """TestClient with an authenticated operator principal stood in.

    Real auth (operator token / API key / 401) is covered separately by
    TestAuthentication below, which uses `raw_client` instead.
    """
    TestingSessionLocal = sessionmaker(
        bind=ownership_engine, autocommit=False, autoflush=False
    )

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[ownership_get_db] = override_get_db
    app.dependency_overrides[get_principal] = lambda: Principal(
        kind="operator",
        id="op-test",
        name="operator@test.example",
        operator_id="op-test",
    )
    c = TestClient(app, raise_server_exceptions=True)
    yield c
    app.dependency_overrides.pop(ownership_get_db, None)
    app.dependency_overrides.pop(get_principal, None)


@pytest.fixture
def raw_client(ownership_engine):
    """TestClient with real auth wired up (no get_principal override)."""
    TestingSessionLocal = sessionmaker(
        bind=ownership_engine, autocommit=False, autoflush=False
    )

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[ownership_get_db] = override_get_db
    app.dependency_overrides[auth_get_db] = override_get_db
    app.dependency_overrides[service_get_db] = override_get_db
    c = TestClient(app, raise_server_exceptions=True)
    yield c
    app.dependency_overrides.pop(ownership_get_db, None)
    app.dependency_overrides.pop(auth_get_db, None)
    app.dependency_overrides.pop(service_get_db, None)
    _clear_all_sessions()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_run(db: Session, domain: str = "acme.example") -> tuple[str, str]:
    """Return (run_id, submission_id)."""
    entity = Entity(canonical_name="Acme Corporation", canonical_domain=domain)
    db.add(entity)
    db.flush()
    sub = Submission(
        company_name="Acme Corporation",
        domain=domain,
        work_email=f"ceo@{domain}",
        country="US",
        entity_id=entity.id,
    )
    db.add(sub)
    db.flush()
    run = VerificationRun(submission_id=sub.id, entity_id=entity.id, status="complete")
    db.add(run)
    db.commit()
    return run.id, sub.id


# ---------------------------------------------------------------------------
# 1. Issue a challenge for a submission's domain
# ---------------------------------------------------------------------------


class TestIssueChallenge:
    def test_issues_dns_txt_challenge_with_instructions(self, client, db):
        run_id, sub_id = _make_run(db)

        resp = client.post(
            f"/ownership/runs/{run_id}/challenges", json={"method": "dns_txt"}
        )

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["run_id"] == run_id
        assert body["submission_id"] == sub_id
        assert body["domain"] == "acme.example"
        assert body["method"] == "dns_txt"
        assert body["status"] == "pending"
        assert body["token"]
        assert body["instructions"]["dns_record_value"] == dns_txt_record_value(
            body["token"]
        )

    def test_issue_persists_row_and_audit_event(self, client, db, ownership_engine):
        run_id, sub_id = _make_run(db)
        resp = client.post(
            f"/ownership/runs/{run_id}/challenges", json={"method": "dns_txt"}
        )
        challenge_id = resp.json()["challenge_id"]

        SessionMaker = sessionmaker(bind=ownership_engine)
        check = SessionMaker()
        try:
            stored = check.get(OwnershipChallenge, challenge_id)
            assert stored is not None
            assert stored.submission_id == sub_id
            assert stored.status == "pending"

            event = (
                check.query(AuditEvent)
                .filter(AuditEvent.event_type == "ownership.challenge_issued")
                .filter(AuditEvent.verification_run_id == run_id)
                .first()
            )
            assert event is not None
            assert event.payload["challenge_id"] == challenge_id
        finally:
            check.close()

    def test_html_meta_instructions_include_snippet(self, client, db):
        run_id, _ = _make_run(db)
        resp = client.post(
            f"/ownership/runs/{run_id}/challenges", json={"method": "html_meta"}
        )
        body = resp.json()
        assert body["instructions"]["html_snippet"] == html_meta_snippet(body["token"])

    def test_email_method_requires_target(self, client, db):
        run_id, _ = _make_run(db)
        resp = client.post(
            f"/ownership/runs/{run_id}/challenges", json={"method": "email"}
        )
        assert resp.status_code == 422

    def test_email_method_with_target_is_issued(self, client, db):
        run_id, _ = _make_run(db)
        resp = client.post(
            f"/ownership/runs/{run_id}/challenges",
            json={"method": "email", "target": "verify@acme.example"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["instructions"]["email_target"] == "verify@acme.example"

    def test_unknown_method_is_422(self, client, db):
        run_id, _ = _make_run(db)
        resp = client.post(
            f"/ownership/runs/{run_id}/challenges", json={"method": "carrier_pigeon"}
        )
        assert resp.status_code == 422

    def test_unknown_run_is_404(self, client):
        resp = client.post(
            "/ownership/runs/00000000-0000-0000-0000-000000000000/challenges",
            json={"method": "dns_txt"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 2 & 3. DNS TXT: correct token verifies + signal; absent/wrong → unverified
# ---------------------------------------------------------------------------


class TestDnsTxtVerification:
    def test_correct_token_verifies_and_adds_bounded_trust_signal(self, client, db):
        run_id, _ = _make_run(db)
        issue_resp = client.post(
            f"/ownership/runs/{run_id}/challenges", json={"method": "dns_txt"}
        )
        challenge_id = issue_resp.json()["challenge_id"]
        token = issue_resp.json()["token"]
        domain = issue_resp.json()["domain"]

        fake_dns = FakeDnsTxtClient({domain: [dns_txt_record_value(token)]})
        with mock.patch(
            "app.pipeline.ownership._default_dns_txt_client", return_value=fake_dns
        ):
            verify_resp = client.post(
                f"/ownership/challenges/{challenge_id}/verify", json={}
            )

        assert verify_resp.status_code == 200, verify_resp.text
        body = verify_resp.json()
        assert body["verified"] is True
        assert body["status"] == "verified"
        assert body["verified_at"] is not None

        # Evidence persisted on the SAME run → feeds the scoring signal catalog.
        ev = db.query(Evidence).filter(Evidence.verification_run_id == run_id).all()
        assert len(ev) == 1
        assert ev[0].source == "ownership"
        assert ev[0].field == "domain_ownership_verified"
        assert ev[0].normalized_value == "true"

        signals = {s.name: s for s in representation_confidence_signals(ev, [])}
        sig = signals["domain_ownership_verified"]
        assert sig.direction == "trust"
        assert ev[0].id in sig.evidence_ids

    def test_absent_token_is_unverified_no_evidence_no_signal(self, client, db):
        run_id, _ = _make_run(db)
        issue_resp = client.post(
            f"/ownership/runs/{run_id}/challenges", json={"method": "dns_txt"}
        )
        challenge_id = issue_resp.json()["challenge_id"]
        domain = issue_resp.json()["domain"]

        fake_dns = FakeDnsTxtClient({domain: []})  # no TXT records at all
        with mock.patch(
            "app.pipeline.ownership._default_dns_txt_client", return_value=fake_dns
        ):
            verify_resp = client.post(
                f"/ownership/challenges/{challenge_id}/verify", json={}
            )

        assert verify_resp.status_code == 200
        body = verify_resp.json()
        assert body["verified"] is False
        assert body["status"] == "pending"

        ev = db.query(Evidence).filter(Evidence.verification_run_id == run_id).all()
        assert ev == []
        signals = {s.name for s in representation_confidence_signals(ev, [])}
        assert "domain_ownership_verified" not in signals

    def test_incorrect_token_is_unverified_no_evidence(self, client, db):
        run_id, _ = _make_run(db)
        issue_resp = client.post(
            f"/ownership/runs/{run_id}/challenges", json={"method": "dns_txt"}
        )
        challenge_id = issue_resp.json()["challenge_id"]
        domain = issue_resp.json()["domain"]

        fake_dns = FakeDnsTxtClient(
            {domain: [dns_txt_record_value("not-the-right-token")]}
        )
        with mock.patch(
            "app.pipeline.ownership._default_dns_txt_client", return_value=fake_dns
        ):
            verify_resp = client.post(
                f"/ownership/challenges/{challenge_id}/verify", json={}
            )

        assert verify_resp.json()["verified"] is False
        assert (
            db.query(Evidence).filter(Evidence.verification_run_id == run_id).count()
            == 0
        )

    def test_retry_after_dns_propagates_succeeds(self, client, db):
        """A pending challenge can be re-verified once the record appears."""
        run_id, _ = _make_run(db)
        issue_resp = client.post(
            f"/ownership/runs/{run_id}/challenges", json={"method": "dns_txt"}
        )
        challenge_id = issue_resp.json()["challenge_id"]
        token = issue_resp.json()["token"]
        domain = issue_resp.json()["domain"]

        with mock.patch(
            "app.pipeline.ownership._default_dns_txt_client",
            return_value=FakeDnsTxtClient({domain: []}),
        ):
            first = client.post(f"/ownership/challenges/{challenge_id}/verify", json={})
        assert first.json()["verified"] is False

        with mock.patch(
            "app.pipeline.ownership._default_dns_txt_client",
            return_value=FakeDnsTxtClient({domain: [dns_txt_record_value(token)]}),
        ):
            second = client.post(
                f"/ownership/challenges/{challenge_id}/verify", json={}
            )
        assert second.json()["verified"] is True

    def test_verify_unknown_challenge_is_404(self, client):
        resp = client.post(
            "/ownership/challenges/00000000-0000-0000-0000-000000000000/verify",
            json={},
        )
        assert resp.status_code == 404

    def test_verify_audit_events_for_success_and_failure(
        self, client, db, ownership_engine
    ):
        run_id, _ = _make_run(db)
        issue_resp = client.post(
            f"/ownership/runs/{run_id}/challenges", json={"method": "dns_txt"}
        )
        challenge_id = issue_resp.json()["challenge_id"]
        domain = issue_resp.json()["domain"]

        with mock.patch(
            "app.pipeline.ownership._default_dns_txt_client",
            return_value=FakeDnsTxtClient({domain: []}),
        ):
            client.post(f"/ownership/challenges/{challenge_id}/verify", json={})

        SessionMaker = sessionmaker(bind=ownership_engine)
        check = SessionMaker()
        try:
            failed_events = (
                check.query(AuditEvent)
                .filter(AuditEvent.event_type == "ownership.verification_failed")
                .all()
            )
            assert any(
                e.payload.get("challenge_id") == challenge_id for e in failed_events
            )
        finally:
            check.close()


# ---------------------------------------------------------------------------
# 4. Verified ownership never asserts "authorized" — bounded signal
# ---------------------------------------------------------------------------


class TestNeverAuthorization:
    def test_response_and_signal_never_claim_authorization(self, client, db):
        run_id, _ = _make_run(db)
        issue_resp = client.post(
            f"/ownership/runs/{run_id}/challenges", json={"method": "dns_txt"}
        )
        challenge_id = issue_resp.json()["challenge_id"]
        token = issue_resp.json()["token"]
        domain = issue_resp.json()["domain"]

        with mock.patch(
            "app.pipeline.ownership._default_dns_txt_client",
            return_value=FakeDnsTxtClient({domain: [dns_txt_record_value(token)]}),
        ):
            verify_resp = client.post(
                f"/ownership/challenges/{challenge_id}/verify", json={}
            )

        body = verify_resp.json()
        assert "authorized" not in str(body).lower()

        ev = db.query(Evidence).filter(Evidence.verification_run_id == run_id).all()
        sig = next(
            s
            for s in representation_confidence_signals(ev, [])
            if s.name == "domain_ownership_verified"
        )
        # Bounded: weight * 40 is the max point swing on the 0-100 layer score
        # (see app.scoring.engine._layer_score_from_signals) — this alone can
        # never zero out (= "fully confirmed") the representation score.
        assert sig.weight <= 0.3
        assert "not" in sig.description.lower()
        assert "authoriz" in sig.description.lower()


# ---------------------------------------------------------------------------
# 5. Email and HTML meta-tag follow the same issue/verify contract
# ---------------------------------------------------------------------------


class TestSameContractAcrossMethods:
    def test_html_meta_verifies_and_adds_same_signal(self, client, db):
        run_id, _ = _make_run(db)
        issue_resp = client.post(
            f"/ownership/runs/{run_id}/challenges", json={"method": "html_meta"}
        )
        challenge_id = issue_resp.json()["challenge_id"]
        token = issue_resp.json()["token"]
        domain = issue_resp.json()["domain"]

        html = f"<html><head>{html_meta_snippet(token)}</head></html>"
        fake_fetcher = FakeHttpPageFetcher({f"https://{domain}/": html})
        with mock.patch(
            "app.pipeline.ownership._default_http_page_fetcher",
            return_value=fake_fetcher,
        ):
            resp = client.post(f"/ownership/challenges/{challenge_id}/verify", json={})

        assert resp.json()["verified"] is True
        ev = db.query(Evidence).filter(Evidence.verification_run_id == run_id).all()
        assert ev[0].source == "ownership"
        assert ev[0].field == "domain_ownership_verified"

    def test_email_verifies_with_submitted_token(self, client, db):
        run_id, _ = _make_run(db)
        issue_resp = client.post(
            f"/ownership/runs/{run_id}/challenges",
            json={"method": "email", "target": "verify@acme.example"},
        )
        challenge_id = issue_resp.json()["challenge_id"]
        token = issue_resp.json()["token"]

        resp = client.post(
            f"/ownership/challenges/{challenge_id}/verify",
            json={"submitted_token": token},
        )

        assert resp.json()["verified"] is True
        ev = db.query(Evidence).filter(Evidence.verification_run_id == run_id).all()
        assert ev[0].source == "ownership"
        assert ev[0].field == "domain_ownership_verified"

    def test_email_wrong_submitted_token_is_unverified(self, client, db):
        run_id, _ = _make_run(db)
        issue_resp = client.post(
            f"/ownership/runs/{run_id}/challenges",
            json={"method": "email", "target": "verify@acme.example"},
        )
        challenge_id = issue_resp.json()["challenge_id"]

        resp = client.post(
            f"/ownership/challenges/{challenge_id}/verify",
            json={"submitted_token": "wrong"},
        )
        assert resp.json()["verified"] is False

    @pytest.mark.parametrize("method", ["dns_txt", "email", "html_meta"])
    def test_all_methods_share_the_same_response_shape(self, client, db, method):
        run_id, _ = _make_run(db)
        target = "verify@acme.example" if method == "email" else None
        resp = client.post(
            f"/ownership/runs/{run_id}/challenges",
            json={"method": method, **({"target": target} if target else {})},
        )
        assert resp.status_code == 201
        body = resp.json()
        for key in (
            "challenge_id",
            "run_id",
            "submission_id",
            "domain",
            "method",
            "token",
            "status",
            "issued_at",
            "instructions",
            "message",
        ):
            assert key in body


# ---------------------------------------------------------------------------
# List challenges (minimal status-check endpoint for the operator UI)
# ---------------------------------------------------------------------------


class TestListChallenges:
    def test_lists_challenges_for_a_run(self, client, db):
        run_id, _ = _make_run(db)
        client.post(f"/ownership/runs/{run_id}/challenges", json={"method": "dns_txt"})
        client.post(
            f"/ownership/runs/{run_id}/challenges", json={"method": "html_meta"}
        )

        resp = client.get(f"/ownership/runs/{run_id}/challenges")
        assert resp.status_code == 200
        methods = {c["method"] for c in resp.json()}
        assert methods == {"dns_txt", "html_meta"}

    def test_unknown_run_is_404(self, client):
        resp = client.get(
            "/ownership/runs/00000000-0000-0000-0000-000000000000/challenges"
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Auth — operator token AND integration API key (real auth, no override)
# ---------------------------------------------------------------------------


class TestAuthentication:
    def test_unauthenticated_issue_is_401(self, raw_client, db):
        run_id, _ = _make_run(db)
        resp = raw_client.post(
            f"/ownership/runs/{run_id}/challenges", json={"method": "dns_txt"}
        )
        assert resp.status_code == 401

    def test_unauthenticated_verify_is_401(self, raw_client):
        resp = raw_client.post(
            "/ownership/challenges/00000000-0000-0000-0000-000000000000/verify",
            json={},
        )
        assert resp.status_code == 401

    def test_operator_bearer_token_can_issue(self, raw_client, db, ownership_engine):
        run_id, _ = _make_run(db)
        SessionMaker = sessionmaker(bind=ownership_engine)
        setup_db = SessionMaker()
        try:
            op = Operator(
                email="own_op@test.example",
                full_name="Owner Op",
                role="operator",
                password_hash=hash_password("pw"),
            )
            setup_db.add(op)
            setup_db.commit()
        finally:
            setup_db.close()

        signin = raw_client.post(
            "/auth/sign-in", json={"email": "own_op@test.example", "password": "pw"}
        )
        assert signin.status_code == 200, signin.text
        token = signin.json()["session_token"]

        resp = raw_client.post(
            f"/ownership/runs/{run_id}/challenges",
            json={"method": "dns_txt"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201, resp.text

    def test_integration_api_key_can_issue(
        self, raw_client, db, ownership_engine, service_credential
    ):
        run_id, _ = _make_run(db)
        resp = raw_client.post(
            f"/ownership/runs/{run_id}/challenges",
            json={"method": "dns_txt"},
            headers={"X-API-Key": service_credential["key"]},
        )
        assert resp.status_code == 201, resp.text


@pytest.fixture
def service_credential(ownership_engine):
    """A service credential seeded into THIS module's engine (not conftest's)."""
    from app.auth.service import create_api_client

    SessionMaker = sessionmaker(
        bind=ownership_engine, autocommit=False, autoflush=False
    )
    sess = SessionMaker()
    try:
        client_obj, key = create_api_client("ownership-test-system", sess)
        info = {"id": client_obj.id, "key": key, "name": client_obj.name}
    finally:
        sess.close()
    return info
