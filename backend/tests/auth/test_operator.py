"""Tests for P1-T9 — Operator auth + RBAC.

Unauthorized/forbidden tests are written FIRST per the ticket spec.

Scenarios (in order):
  1. [SECURITY] Unauthenticated request to protected route → 401
  2. [SECURITY] Operator attempts a lead-only action → 403
  3. [SECURITY] Bad token → 401
  4. Valid operator login → scoped session token returned
  5. Sign-in with wrong password → 401
  6. Sign-in with unknown email → 401
  7. Token from sign-in works for protected routes
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.reviews import _get_db as reviews_get_db
from app.auth.operator import (
    _clear_all_sessions,
    hash_password,
    require_lead,
    sign_in,
    verify_password,
)
from app.auth.operator import (
    _get_db as auth_get_db,
)
from app.db.session import Base
from app.main import app
from app.models.operator import Operator

# ---------------------------------------------------------------------------
# DB + client fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def auth_engine():
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
def db(auth_engine):
    connection = auth_engine.connect()
    transaction = connection.begin()
    sess = Session(bind=connection)
    yield sess
    sess.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def auth_client(auth_engine):
    """TestClient wired to the in-memory DB.  Clears sessions after each test."""
    TestingSessionLocal = sessionmaker(
        bind=auth_engine, autocommit=False, autoflush=False
    )

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[auth_get_db] = override_get_db
    app.dependency_overrides[reviews_get_db] = override_get_db
    client = TestClient(app, raise_server_exceptions=True)
    yield client
    app.dependency_overrides.pop(auth_get_db, None)
    app.dependency_overrides.pop(reviews_get_db, None)
    _clear_all_sessions()


# ---------------------------------------------------------------------------
# Helper: create operator + lead accounts
# ---------------------------------------------------------------------------


def _make_operator(
    db: Session,
    email: str = "op@acme.example",
    password: str = "correct-horse",
    role: str = "operator",
) -> Operator:
    op = Operator(
        email=email,
        full_name="Test Operator",
        role=role,
        password_hash=hash_password(password),
    )
    db.add(op)
    db.commit()
    return op


def _make_run(db: Session) -> str:
    """Create minimal Entity + Submission + VerificationRun; return run_id."""
    from app.models.entity import Entity  # noqa: PLC0415
    from app.models.submission import Submission  # noqa: PLC0415
    from app.models.verification_run import VerificationRun  # noqa: PLC0415

    entity = Entity(canonical_name="Auth Test Co", canonical_domain="authtest.example")
    db.add(entity)
    db.flush()
    sub = Submission(
        company_name="Auth Test Co",
        domain="authtest.example",
        work_email="ceo@authtest.example",
        country="US",
        entity_id=entity.id,
    )
    db.add(sub)
    db.flush()
    run = VerificationRun(
        submission_id=sub.id,
        entity_id=entity.id,
        status="complete",
    )
    db.add(run)
    db.commit()
    return run.id


# ============================================================================
# SECURITY TESTS — written first
# ============================================================================


class TestUnauthenticated:
    """Unauthenticated requests to protected routes must return 401."""

    def test_mark_reviewed_no_token_returns_401(self, auth_client, auth_engine):
        """POST /reviews/{run_id} with no Authorization header → 401."""
        TestingSessionLocal = sessionmaker(
            bind=auth_engine, autocommit=False, autoflush=False
        )
        setup_db = TestingSessionLocal()
        try:
            run_id = _make_run(setup_db)
        finally:
            setup_db.close()

        resp = auth_client.post(f"/reviews/{run_id}", json={})
        assert (
            resp.status_code == 401
        ), f"Expected 401 for unauthenticated request, got {resp.status_code}"

    def test_mark_reviewed_garbage_token_returns_401(self, auth_client, auth_engine):
        """POST /reviews/{run_id} with a bad Bearer token → 401."""
        TestingSessionLocal = sessionmaker(
            bind=auth_engine, autocommit=False, autoflush=False
        )
        setup_db = TestingSessionLocal()
        try:
            run_id = _make_run(setup_db)
        finally:
            setup_db.close()

        resp = auth_client.post(
            f"/reviews/{run_id}",
            json={},
            headers={"Authorization": "Bearer not-a-real-token"},
        )
        assert (
            resp.status_code == 401
        ), f"Expected 401 for invalid token, got {resp.status_code}"

    def test_mark_reviewed_malformed_auth_header_returns_401(
        self, auth_client, auth_engine
    ):
        """POST /reviews/{run_id} with malformed Authorization header → 401."""
        TestingSessionLocal = sessionmaker(
            bind=auth_engine, autocommit=False, autoflush=False
        )
        setup_db = TestingSessionLocal()
        try:
            run_id = _make_run(setup_db)
        finally:
            setup_db.close()

        # Not Bearer format
        resp = auth_client.post(
            f"/reviews/{run_id}",
            json={},
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
        assert resp.status_code == 401


class TestRBAC:
    """Role-based access control: operator attempting lead-only actions → 403."""

    def test_operator_cannot_call_lead_only_dependency(self, db: Session):
        """require_lead() raises 403 for an operator-role account."""

        op = _make_operator(db, email="rbac_op@test.example", role="operator")

        # Simulate calling require_lead with an operator-role account
        # by patching get_current_operator to return the operator
        import unittest.mock as mock  # noqa: PLC0415

        import pytest  # noqa: PLC0415

        with mock.patch("app.auth.operator.get_current_operator", return_value=op):
            from fastapi import HTTPException  # noqa: PLC0415

            with pytest.raises(HTTPException) as exc_info:
                require_lead(operator=op, db=db)
            assert exc_info.value.status_code == 403

    def test_lead_passes_require_lead(self, db: Session):
        """require_lead() passes for a lead-role account."""
        lead = _make_operator(db, email="lead_user@test.example", role="lead")
        result = require_lead(operator=lead, db=db)
        assert result.id == lead.id

    def test_denied_lead_only_action_is_audited(self, db: Session):
        """require_lead() 403s are recorded as an audit event (ADR-0002 §2;
        ticket 0002: "Unauthorized PII access is denied and the attempt is
        audited.")."""
        from fastapi import HTTPException

        from app.models.audit_event import AuditEvent

        op = _make_operator(db, email="audited_op@test.example", role="operator")

        with pytest.raises(HTTPException):
            require_lead(operator=op, db=db)

        events = (
            db.query(AuditEvent)
            .filter(AuditEvent.event_type == "operator.access_denied")
            .all()
        )
        assert len(events) == 1
        assert events[0].operator_id == op.id


# ============================================================================
# SIGN-IN TESTS
# ============================================================================


class TestSignIn:
    """Sign-in credential validation."""

    def test_valid_credentials_return_token(self, auth_client, auth_engine):
        """POST /auth/sign-in with correct credentials → session token."""
        TestingSessionLocal = sessionmaker(
            bind=auth_engine, autocommit=False, autoflush=False
        )
        setup_db = TestingSessionLocal()
        try:
            _make_operator(
                setup_db,
                email="signin_ok@acme.example",
                password="hunter2",
                role="operator",
            )
        finally:
            setup_db.close()

        resp = auth_client.post(
            "/auth/sign-in",
            json={"email": "signin_ok@acme.example", "password": "hunter2"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "session_token" in data
        assert len(data["session_token"]) > 20
        assert data["role"] == "operator"
        assert "operator_id" in data

    def test_wrong_password_returns_401(self, auth_client, auth_engine):
        """POST /auth/sign-in with wrong password → 401."""
        TestingSessionLocal = sessionmaker(
            bind=auth_engine, autocommit=False, autoflush=False
        )
        setup_db = TestingSessionLocal()
        try:
            _make_operator(setup_db, email="wrong_pw@acme.example", password="correct")
        finally:
            setup_db.close()

        resp = auth_client.post(
            "/auth/sign-in",
            json={"email": "wrong_pw@acme.example", "password": "wrong"},
        )
        assert resp.status_code == 401

    def test_unknown_email_returns_401(self, auth_client):
        """POST /auth/sign-in with unknown email → 401."""
        resp = auth_client.post(
            "/auth/sign-in",
            json={"email": "nobody@unknown.example", "password": "x"},
        )
        assert resp.status_code == 401

    def test_token_scoped_to_operator(self, auth_client, auth_engine):
        """Token returned by sign-in works for protected route."""
        TestingSessionLocal = sessionmaker(
            bind=auth_engine, autocommit=False, autoflush=False
        )
        setup_db = TestingSessionLocal()
        try:
            _make_operator(setup_db, email="token_test@acme.example", password="secret")
            run_id = _make_run(setup_db)
        finally:
            setup_db.close()

        # Sign in
        sign_in_resp = auth_client.post(
            "/auth/sign-in",
            json={"email": "token_test@acme.example", "password": "secret"},
        )
        assert sign_in_resp.status_code == 200
        token = sign_in_resp.json()["session_token"]

        # Use token on protected route
        review_resp = auth_client.post(
            f"/reviews/{run_id}",
            json={"review_status": "reviewed"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert review_resp.status_code == 201, review_resp.text


# ============================================================================
# PASSWORD HASHING UNIT TESTS
# ============================================================================


class TestPasswordHashing:
    """hash_password / verify_password — pure unit tests, no DB."""

    def test_hash_is_not_plaintext(self):
        pw = "my_secure_password"
        h = hash_password(pw)
        assert pw not in h
        assert h.startswith("pbkdf2_sha256:")

    def test_correct_password_verifies(self):
        pw = "correct-horse-battery-staple"
        h = hash_password(pw)
        assert verify_password(pw, h)

    def test_wrong_password_fails(self):
        h = hash_password("correct")
        assert not verify_password("wrong", h)

    def test_two_hashes_of_same_password_differ(self):
        pw = "same-password"
        h1 = hash_password(pw)
        h2 = hash_password(pw)
        assert h1 != h2  # Different salts

    def test_empty_password_hashes(self):
        pw = ""
        h = hash_password(pw)
        assert verify_password(pw, h)
        assert not verify_password("not-empty", h)


# ============================================================================
# SIGN-IN UNIT TESTS (direct sign_in() call)
# ============================================================================


class TestSignInUnit:
    """Direct sign_in() function tests."""

    def test_sign_in_returns_token(self, db: Session):
        _make_operator(db, email="unit_signin@test.example", password="pw1")
        token = sign_in("unit_signin@test.example", "pw1", db)
        assert token is not None
        assert len(token) > 20

    def test_sign_in_wrong_password_returns_none(self, db: Session):
        _make_operator(db, email="unit_bad_pw@test.example", password="correct")
        result = sign_in("unit_bad_pw@test.example", "wrong", db)
        assert result is None

    def test_sign_in_unknown_email_returns_none(self, db: Session):
        result = sign_in("nobody@nowhere.test", "x", db)
        assert result is None
