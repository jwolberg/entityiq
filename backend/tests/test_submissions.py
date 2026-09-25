"""Tests for P1-T1 — Submission endpoint + network-metadata capture.

All tests run against SQLite in-memory — no live Postgres or Redis required.
"""

import unittest.mock as mock

import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api.submissions import _get_db
from app.main import app
from app.models.submission import Submission
from app.models.verification_run import VerificationRun

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_PAYLOAD = {
    "company_name": "Acme Corp",
    "work_email": "cto@acme.example",
    "company_domain": "acme.example",
    "country": "US",
}


def test_submission_passes_background_tasks_to_enqueue(api_client: TestClient):
    """The endpoint hands enqueue a BackgroundTasks so eager runs defer (0074)."""
    with mock.patch("app.pipeline.orchestrator.enqueue_run") as enq:
        resp = api_client.post("/submissions", json=_VALID_PAYLOAD)
    assert resp.status_code == 202, resp.text
    run_id, background = enq.call_args.args
    assert run_id == resp.json()["run_id"]
    assert isinstance(background, BackgroundTasks)


# ---------------------------------------------------------------------------
# Test: valid submission persists, returns 202, and includes run_id
# ---------------------------------------------------------------------------


def test_valid_submission_returns_202(api_client: TestClient):
    resp = api_client.post("/submissions", json=_VALID_PAYLOAD)
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert "submission_id" in body
    assert "run_id" in body
    assert body["submission_id"]
    assert body["run_id"]


def test_valid_submission_persists_to_db(api_client: TestClient, db_session):
    resp = api_client.post("/submissions", json=_VALID_PAYLOAD)
    assert resp.status_code == 202
    body = resp.json()
    sub = db_session.get(Submission, body["submission_id"])
    assert sub is not None
    assert sub.company_name == "Acme Corp"
    assert sub.domain == "acme.example"
    assert sub.work_email == "cto@acme.example"
    assert sub.country == "US"


def test_valid_submission_creates_verification_run(api_client: TestClient, db_session):
    resp = api_client.post("/submissions", json=_VALID_PAYLOAD)
    assert resp.status_code == 202
    body = resp.json()
    run = db_session.get(VerificationRun, body["run_id"])
    assert run is not None
    assert run.status == "pending"
    assert run.submission_id == body["submission_id"]


# ---------------------------------------------------------------------------
# Test: missing required fields → 422
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing_field",
    ["company_name", "work_email", "company_domain", "country"],
)
def test_missing_required_field_returns_422(api_client: TestClient, missing_field: str):
    payload = dict(_VALID_PAYLOAD)
    del payload[missing_field]
    resp = api_client.post("/submissions", json=payload)
    assert resp.status_code == 422, f"Expected 422 when {missing_field!r} is missing"


# ---------------------------------------------------------------------------
# Test: trusted-IP invariant — X-Forwarded-For is NOT the source_ip
# ---------------------------------------------------------------------------


def test_spoofed_x_forwarded_for_is_not_used_as_source_ip(
    api_client: TestClient, db_session
):
    """The source_ip must be the connection peer, not the spoofed header."""
    resp = api_client.post(
        "/submissions",
        json={**_VALID_PAYLOAD, "idempotency_key": "xff-spoof-test"},
        headers={"X-Forwarded-For": "1.2.3.4, 5.6.7.8"},
    )
    assert resp.status_code == 202
    body = resp.json()
    sub = db_session.get(Submission, body["submission_id"])
    assert sub is not None
    # TestClient sends requests from 127.0.0.1 (loopback) — the peer address.
    # It must NOT be the spoofed 1.2.3.4 value from X-Forwarded-For.
    assert sub.source_ip != "1.2.3.4"
    assert sub.source_ip != "5.6.7.8"


def test_forwarded_headers_are_stored_separately(api_client: TestClient, db_session):
    """Forwarded headers are captured for context but are not the source_ip."""
    resp = api_client.post(
        "/submissions",
        json={**_VALID_PAYLOAD, "idempotency_key": "fwd-header-context"},
        headers={"X-Forwarded-For": "203.0.113.99"},
    )
    assert resp.status_code == 202
    body = resp.json()
    sub = db_session.get(Submission, body["submission_id"])
    assert sub is not None
    # The forwarded header IS stored for context.
    assert sub.forwarded_headers is not None
    assert "x-forwarded-for" in sub.forwarded_headers
    assert sub.forwarded_headers["x-forwarded-for"] == "203.0.113.99"
    # But source_ip is still the trusted peer.
    assert sub.source_ip != "203.0.113.99"


# ---------------------------------------------------------------------------
# Test: idempotency — duplicate key does not create a second run
# ---------------------------------------------------------------------------


def test_idempotent_submission_does_not_create_duplicate(
    api_client: TestClient, db_session
):
    """Two requests with the same idempotency_key return the same run_id."""
    payload = {**_VALID_PAYLOAD, "idempotency_key": "idem-key-dedup-test-001"}
    resp1 = api_client.post("/submissions", json=payload)
    resp2 = api_client.post("/submissions", json=payload)
    assert resp1.status_code == 202
    assert resp2.status_code == 202
    body1, body2 = resp1.json(), resp2.json()
    assert body1["submission_id"] == body2["submission_id"]
    assert body1["run_id"] == body2["run_id"]

    # Confirm only one Submission row exists for this idempotency key
    from sqlalchemy import text

    count = db_session.execute(
        text("SELECT COUNT(*) FROM submission WHERE idempotency_key = :k"),
        {"k": "idem-key-dedup-test-001"},
    ).scalar()
    assert count == 1


# ---------------------------------------------------------------------------
# Test: free/disposable email domain accepted but flagged
# ---------------------------------------------------------------------------


def test_free_email_domain_accepted_but_flagged(api_client: TestClient):
    """Gmail etc. are accepted (not rejected) but flagged in the response."""
    payload = {
        "company_name": "Freelancer Inc",
        "work_email": "freelancer@gmail.com",
        "company_domain": "freelancer.example",
        "country": "US",
    }
    resp = api_client.post("/submissions", json=payload)
    assert resp.status_code == 202
    body = resp.json()
    assert body["is_free_email_domain"] is True


def test_work_email_domain_not_flagged(api_client: TestClient):
    """A corporate email is accepted and NOT flagged as free."""
    resp = api_client.post("/submissions", json=_VALID_PAYLOAD)
    assert resp.status_code == 202
    body = resp.json()
    assert body["is_free_email_domain"] is False


# ---------------------------------------------------------------------------
# Test: optional fields are persisted when provided
# ---------------------------------------------------------------------------


def test_optional_fields_persisted(api_client: TestClient, db_session):
    payload = {
        **_VALID_PAYLOAD,
        "tax_id": "12-3456789",
        "billing_address": "123 Main St, Springfield, USA",
        "phone": "+1-555-0100",
        "requester_full_name": "Jane Doe",
        "idempotency_key": "optional-fields-test-001",
    }
    resp = api_client.post("/submissions", json=payload)
    assert resp.status_code == 202
    body = resp.json()
    sub = db_session.get(Submission, body["submission_id"])
    assert sub is not None
    assert sub.tax_id == "12-3456789"
    assert sub.billing_address == "123 Main St, Springfield, USA"
    assert sub.phone == "+1-555-0100"
    assert sub.requester_full_name == "Jane Doe"


# ---------------------------------------------------------------------------
# Test: domain normalization (strip scheme and path before persisting)
# ---------------------------------------------------------------------------


def test_domain_with_https_scheme_is_normalized(api_client: TestClient, db_session):
    payload = {
        **_VALID_PAYLOAD,
        "company_domain": "https://acme.example/about",
        "idempotency_key": "domain-norm-test-001",
    }
    resp = api_client.post("/submissions", json=payload)
    assert resp.status_code == 202
    body = resp.json()
    sub = db_session.get(Submission, body["submission_id"])
    assert sub is not None
    assert sub.domain == "acme.example"


# ---------------------------------------------------------------------------
# Test: operators may submit via Bearer token (no API key), attributed to them
# ---------------------------------------------------------------------------


def test_operator_can_submit_with_bearer_token(sqlite_engine, db_session):
    """An operator (Bearer token, no X-API-Key) can submit a registration.

    /submissions accepts an operator OR an integrating system (get_principal).
    The submission has no api_client_id and is attributed to the operator.
    """
    from app.auth.operator import _clear_all_sessions, hash_password, sign_in
    from app.auth.operator import _get_db as operator_get_db
    from app.auth.service import _get_db as service_get_db
    from app.models.operator import Operator

    SessionMaker = sessionmaker(bind=sqlite_engine, autocommit=False, autoflush=False)

    def override_get_db():
        db = SessionMaker()
        try:
            yield db
        finally:
            db.close()

    # Seed an operator and mint a session token.
    seed = SessionMaker()
    try:
        seed.add(
            Operator(
                email="submitter@acme.example",
                full_name="Submitter Op",
                role="operator",
                password_hash=hash_password("pw"),
            )
        )
        seed.commit()
        token = sign_in("submitter@acme.example", "pw", seed)
    finally:
        seed.close()
    assert token is not None

    app.dependency_overrides[_get_db] = override_get_db
    app.dependency_overrides[service_get_db] = override_get_db
    app.dependency_overrides[operator_get_db] = override_get_db
    try:
        with mock.patch("app.pipeline.orchestrator.enqueue_run") as _enq:
            _enq.return_value = None
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.post(
                "/submissions",
                json={**_VALID_PAYLOAD, "idempotency_key": "operator-submit-001"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        sub = db_session.get(Submission, body["submission_id"])
        assert sub is not None
        assert sub.api_client_id is None  # operator submission, not a system
    finally:
        app.dependency_overrides.clear()
        _clear_all_sessions()


def test_submission_without_auth_returns_401(sqlite_engine):
    """No API key and no Bearer token → 401."""
    from app.auth.service import _get_db as service_get_db

    SessionMaker = sessionmaker(bind=sqlite_engine, autocommit=False, autoflush=False)

    def override_get_db():
        db = SessionMaker()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[_get_db] = override_get_db
    app.dependency_overrides[service_get_db] = override_get_db
    try:
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.post("/submissions", json=_VALID_PAYLOAD)
        assert resp.status_code == 401, resp.text
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Declared officers / owners (ticket 0079)
# ---------------------------------------------------------------------------


def test_declared_people_are_stored(api_client: TestClient, db_session):
    people = [
        {"name": "  Ann Lee ", "relationship": "officer", "role": "Director"},
        {
            "name": "Bo Chen",
            "relationship": "owner",
            "ownership_pct": 60,
            "dob": "1971-04",
            "nationality": "SG",
        },
    ]
    resp = api_client.post("/submissions", json={**_VALID_PAYLOAD, "people": people})
    assert resp.status_code == 202, resp.text
    sub = db_session.get(Submission, resp.json()["submission_id"])
    assert sub.declared_people == [
        {"name": "Ann Lee", "relationship": "officer", "role": "Director"},
        {
            "name": "Bo Chen",
            "relationship": "owner",
            "ownership_pct": 60.0,
            "dob": "1971-04",
            "nationality": "SG",
        },
    ]


def test_people_are_optional(api_client: TestClient, db_session):
    resp = api_client.post("/submissions", json=_VALID_PAYLOAD)
    assert resp.status_code == 202, resp.text
    sub = db_session.get(Submission, resp.json()["submission_id"])
    assert sub.declared_people is None


@pytest.mark.parametrize(
    "person",
    [
        {"relationship": "officer"},  # no name
        {"name": "   ", "relationship": "officer"},  # blank name
        {"name": "Ann Lee", "relationship": "cousin"},  # unknown relationship
        {"name": "Ann Lee", "relationship": "owner", "ownership_pct": 150},
        {"name": "Ann Lee", "relationship": "owner", "ownership_pct": -1},
        {"name": "Ann Lee", "relationship": "officer", "dob": "1990-13"},
        {"name": "Ann Lee", "relationship": "officer", "dob": "04/1990"},
    ],
)
def test_invalid_declared_person_is_422(api_client: TestClient, person):
    resp = api_client.post("/submissions", json={**_VALID_PAYLOAD, "people": [person]})
    assert resp.status_code == 422, resp.text


def test_too_many_declared_people_is_422(api_client: TestClient):
    people = [{"name": f"Person {i}", "relationship": "officer"} for i in range(51)]
    resp = api_client.post("/submissions", json={**_VALID_PAYLOAD, "people": people})
    assert resp.status_code == 422, resp.text
