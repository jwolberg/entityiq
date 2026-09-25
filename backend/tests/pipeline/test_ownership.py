"""Tests for ticket 0003 — domain-ownership verification core logic.

Covers the injectable proof checks (DNS TXT / HTML meta-tag / email — all
offline, no live network) and the DB-backed issue/verify orchestration in
app.pipeline.ownership. API-level contract tests live in
tests/api/test_ownership.py; scoring-signal tests live in
tests/scoring/test_signals.py.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers all ORM models
from app.db.session import Base
from app.models.entity import Entity
from app.models.evidence import Evidence
from app.models.ownership_challenge import OwnershipChallenge
from app.models.submission import Submission
from app.models.verification_run import VerificationRun
from app.pipeline.ownership import (
    CHALLENGE_METHODS,
    UnknownChallengeMethod,
    attempt_verification,
    check_dns_txt,
    check_email_token,
    check_html_meta,
    dns_txt_record_value,
    generate_challenge_token,
    html_meta_snippet,
    issue_challenge,
)

# ---------------------------------------------------------------------------
# Fake injectable clients — no live network
# ---------------------------------------------------------------------------


class FakeDnsTxtClient:
    def __init__(self, records: dict[str, list[str]] | None = None) -> None:
        self._records = records or {}

    def query_txt(self, domain: str) -> list[str]:
        return self._records.get(domain, [])


class RaisingDnsTxtClient:
    def query_txt(self, domain: str) -> list[str]:
        raise RuntimeError("DNS resolution failed")


class FakeHttpPageFetcher:
    def __init__(self, pages: dict[str, str] | None = None) -> None:
        self._pages = pages or {}

    def fetch_text(self, url: str) -> str:
        if url not in self._pages:
            raise RuntimeError(f"404: {url}")
        return self._pages[url]


# ---------------------------------------------------------------------------
# Pure verification-check functions
# ---------------------------------------------------------------------------


def test_generate_challenge_token_is_unique_and_nonempty():
    a = generate_challenge_token()
    b = generate_challenge_token()
    assert a and b
    assert a != b


def test_check_dns_txt_correct_token_verifies():
    token = generate_challenge_token()
    client = FakeDnsTxtClient({"acme.example": [dns_txt_record_value(token)]})
    assert check_dns_txt("acme.example", token, client) is True


def test_check_dns_txt_absent_record_is_unverified():
    token = generate_challenge_token()
    client = FakeDnsTxtClient({"acme.example": ["v=spf1 include:_spf.google.com ~all"]})
    assert check_dns_txt("acme.example", token, client) is False


def test_check_dns_txt_wrong_token_is_unverified():
    token = generate_challenge_token()
    other = generate_challenge_token()
    client = FakeDnsTxtClient({"acme.example": [dns_txt_record_value(other)]})
    assert check_dns_txt("acme.example", token, client) is False


def test_check_dns_txt_client_error_is_unverified_not_raised():
    token = generate_challenge_token()
    assert check_dns_txt("acme.example", token, RaisingDnsTxtClient()) is False


def test_check_html_meta_correct_tag_verifies():
    token = generate_challenge_token()
    html = f"<html><head>{html_meta_snippet(token)}</head><body></body></html>"
    fetcher = FakeHttpPageFetcher({"https://acme.example/": html})
    assert check_html_meta("acme.example", token, fetcher) is True


def test_check_html_meta_missing_tag_is_unverified():
    fetcher = FakeHttpPageFetcher(
        {"https://acme.example/": "<html><body>hi</body></html>"}
    )
    assert check_html_meta("acme.example", generate_challenge_token(), fetcher) is False


def test_check_html_meta_wrong_token_is_unverified():
    token = generate_challenge_token()
    other = generate_challenge_token()
    html = f"<html><head>{html_meta_snippet(other)}</head></html>"
    fetcher = FakeHttpPageFetcher({"https://acme.example/": html})
    assert check_html_meta("acme.example", token, fetcher) is False


def test_check_html_meta_fetch_error_is_unverified_not_raised():
    fetcher = FakeHttpPageFetcher({})  # raises for any URL
    assert check_html_meta("acme.example", generate_challenge_token(), fetcher) is False


def test_check_email_token_match():
    token = generate_challenge_token()
    assert check_email_token(token, token) is True


@pytest.mark.parametrize("submitted", [None, "", "wrong-token"])
def test_check_email_token_absent_or_wrong(submitted):
    token = generate_challenge_token()
    assert check_email_token(submitted, token) is False


# ---------------------------------------------------------------------------
# DB fixtures
# ---------------------------------------------------------------------------


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


def _run(db: Session, domain: str = "acme.example") -> str:
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
    return run.id


# ---------------------------------------------------------------------------
# issue_challenge()
# ---------------------------------------------------------------------------


class TestIssueChallenge:
    def test_issues_challenge_for_submissions_domain(self, db: Session):
        run_id = _run(db)
        challenge = issue_challenge(db, run_id=run_id, method="dns_txt")

        assert challenge.id is not None
        assert challenge.domain == "acme.example"
        assert challenge.method == "dns_txt"
        assert challenge.status == "pending"
        assert challenge.token
        assert challenge.verified_at is None

        stored = db.get(OwnershipChallenge, challenge.id)
        assert stored is not None
        assert stored.token == challenge.token

    def test_all_three_methods_are_issuable(self, db: Session):
        run_id = _run(db)
        for method in CHALLENGE_METHODS:
            target = "verify@acme.example" if method == "email" else None
            challenge = issue_challenge(db, run_id=run_id, method=method, target=target)
            assert challenge.method == method
            assert challenge.status == "pending"

    def test_unknown_method_raises(self, db: Session):
        run_id = _run(db)
        with pytest.raises(UnknownChallengeMethod):
            issue_challenge(db, run_id=run_id, method="carrier_pigeon")

    def test_email_method_requires_target(self, db: Session):
        run_id = _run(db)
        with pytest.raises(ValueError):
            issue_challenge(db, run_id=run_id, method="email", target=None)

    def test_unknown_run_raises(self, db: Session):
        with pytest.raises(ValueError):
            issue_challenge(
                db, run_id="00000000-0000-0000-0000-000000000000", method="dns_txt"
            )


# ---------------------------------------------------------------------------
# attempt_verification()
# ---------------------------------------------------------------------------


class TestAttemptVerification:
    def test_correct_dns_txt_verifies_and_persists_evidence(self, db: Session):
        run_id = _run(db)
        challenge = issue_challenge(db, run_id=run_id, method="dns_txt")
        dns_client = FakeDnsTxtClient(
            {challenge.domain: [dns_txt_record_value(challenge.token)]}
        )

        verified = attempt_verification(db, challenge, dns_client=dns_client)

        assert verified is True
        assert challenge.status == "verified"
        assert challenge.verified_at is not None
        assert challenge.evidence_id is not None

        ev = db.get(Evidence, challenge.evidence_id)
        assert ev is not None
        assert ev.source == "ownership"
        assert ev.field == "domain_ownership_verified"
        assert ev.normalized_value == "true"
        assert ev.verification_run_id == run_id

    def test_absent_dns_txt_record_leaves_unverified_no_evidence(self, db: Session):
        run_id = _run(db)
        challenge = issue_challenge(db, run_id=run_id, method="dns_txt")
        dns_client = FakeDnsTxtClient({challenge.domain: []})

        verified = attempt_verification(db, challenge, dns_client=dns_client)

        assert verified is False
        assert challenge.status == "pending"
        assert challenge.evidence_id is None
        assert db.query(Evidence).count() == 0

    def test_incorrect_dns_txt_token_leaves_unverified_no_evidence(self, db: Session):
        run_id = _run(db)
        challenge = issue_challenge(db, run_id=run_id, method="dns_txt")
        wrong_token = generate_challenge_token()
        dns_client = FakeDnsTxtClient(
            {challenge.domain: [dns_txt_record_value(wrong_token)]}
        )

        verified = attempt_verification(db, challenge, dns_client=dns_client)

        assert verified is False
        assert challenge.status == "pending"
        assert db.query(Evidence).count() == 0

    def test_html_meta_same_contract(self, db: Session):
        run_id = _run(db)
        challenge = issue_challenge(db, run_id=run_id, method="html_meta")
        html = f"<html><head>{html_meta_snippet(challenge.token)}</head></html>"
        fetcher = FakeHttpPageFetcher({f"https://{challenge.domain}/": html})

        verified = attempt_verification(db, challenge, http_fetcher=fetcher)

        assert verified is True
        assert challenge.status == "verified"
        ev = db.get(Evidence, challenge.evidence_id)
        assert ev.source == "ownership"
        assert ev.field == "domain_ownership_verified"

    def test_email_same_contract(self, db: Session):
        run_id = _run(db)
        challenge = issue_challenge(
            db, run_id=run_id, method="email", target="verify@acme.example"
        )

        verified = attempt_verification(db, challenge, submitted_token=challenge.token)

        assert verified is True
        assert challenge.status == "verified"
        ev = db.get(Evidence, challenge.evidence_id)
        assert ev.source == "ownership"
        assert ev.field == "domain_ownership_verified"

    def test_email_wrong_submitted_token_is_unverified(self, db: Session):
        run_id = _run(db)
        challenge = issue_challenge(
            db, run_id=run_id, method="email", target="verify@acme.example"
        )

        verified = attempt_verification(db, challenge, submitted_token="not-the-token")

        assert verified is False
        assert challenge.status == "pending"
        assert db.query(Evidence).count() == 0

    def test_already_verified_challenge_is_idempotent(self, db: Session):
        run_id = _run(db)
        challenge = issue_challenge(db, run_id=run_id, method="dns_txt")
        dns_client = FakeDnsTxtClient(
            {challenge.domain: [dns_txt_record_value(challenge.token)]}
        )
        assert attempt_verification(db, challenge, dns_client=dns_client) is True
        evidence_id_first = challenge.evidence_id

        # Second call, even with a client that would now fail, is a no-op success.
        assert (
            attempt_verification(db, challenge, dns_client=FakeDnsTxtClient()) is True
        )
        assert challenge.evidence_id == evidence_id_first
        assert db.query(Evidence).count() == 1
