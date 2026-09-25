"""Per-subject envelope encryption + crypto-shred retention (IS1-T5, ticket 0033)."""

from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.db.session import Base
from app.models.audit_event import AuditEvent
from app.screening import crypto
from app.screening.models import (
    ScreeningDecision,
    ScreeningRuleVersion,
    ScreeningRun,
    ScreeningSubject,
    ScreeningSubjectKey,
)
from app.screening.retention import shred_expired

_PII = {"name": "Teodor Vasilescu", "dob": "1962-08-30", "passport": "X1234567"}


@pytest.fixture(autouse=True)
def master_key(monkeypatch):
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("ENTITYIQ_SCREENING_MASTER_KEY", key)
    return key


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'crypto.db'}")
    Base.metadata.create_all(engine)
    sess = Session(bind=engine)
    yield sess
    sess.close()
    engine.dispose()


def _subject(db, pii=_PII) -> ScreeningSubject:
    subject = ScreeningSubject()
    db.add(subject)
    db.flush()
    crypto.set_subject_pii(db, subject, pii)
    db.commit()
    return subject


def test_pii_round_trips_and_is_unreadable_in_raw_rows(db):
    subject = _subject(db)

    assert crypto.get_subject_pii(db, subject) == _PII
    raw = db.execute(
        text("SELECT pii_ciphertext FROM screening_subject WHERE id = :i"),
        {"i": subject.id},
    ).scalar_one()
    for value in _PII.values():
        assert value.encode() not in raw
    wrapped = db.execute(
        text("SELECT wrapped_key FROM screening_subject_key WHERE subject_id = :i"),
        {"i": subject.id},
    ).scalar_one()
    assert len(wrapped) > 32  # nonce + ciphertext + tag, never the bare key


def test_each_subject_has_its_own_key(db):
    a, b = _subject(db), _subject(db, {"name": "Other Person"})
    ka = db.query(ScreeningSubjectKey).filter_by(subject_id=a.id).one()
    kb = db.query(ScreeningSubjectKey).filter_by(subject_id=b.id).one()
    assert ka.wrapped_key != kb.wrapped_key


def test_ciphertext_is_bound_to_its_subject(db):
    """Swapping blobs between subjects must fail, not decrypt."""
    a, b = _subject(db), _subject(db, {"name": "Other Person"})
    b.pii_ciphertext, b.pii_nonce = a.pii_ciphertext, a.pii_nonce
    db.commit()
    with pytest.raises(crypto.DecryptionError):
        crypto.get_subject_pii(db, b)


def test_missing_master_key_is_a_clear_error(db, monkeypatch):
    monkeypatch.delenv("ENTITYIQ_SCREENING_MASTER_KEY")
    with pytest.raises(crypto.CryptoNotConfigured):
        _subject(db)


def test_wrong_master_key_cannot_unwrap(db, monkeypatch):
    subject = _subject(db)
    monkeypatch.setenv(
        "ENTITYIQ_SCREENING_MASTER_KEY", base64.b64encode(os.urandom(32)).decode()
    )
    with pytest.raises(crypto.DecryptionError):
        crypto.get_subject_pii(db, subject)


def test_frozen_inputs_use_the_same_subject_key(db):
    subject = _subject(db)
    bundle = {"subject": _PII, "candidates": [{"id": "W001"}]}
    ct, nonce = crypto.encrypt_for_subject(db, subject.id, bundle)
    assert crypto.decrypt_for_subject(db, subject.id, ct, nonce) == bundle


# ---------------------------------------------------------------------------
# Retention: crypto-shred after the AML period
# ---------------------------------------------------------------------------


def _decision_for(db, subject) -> ScreeningDecision:
    rule = ScreeningRuleVersion(version=1, config={})
    db.add(rule)
    db.flush()
    run = ScreeningRun(subject_id=subject.id, rule_version_id=rule.id)
    db.add(run)
    db.flush()
    ct, nonce = crypto.encrypt_for_subject(db, subject.id, {"subject": _PII})
    decision = ScreeningDecision(
        run_id=run.id,
        rule_version_id=rule.id,
        system_disposition="REVIEW",
        snapshot_ids=[],
        thresholds={},
        terms=[],
        normalizer_version="v1",
        frozen_ciphertext=ct,
        frozen_nonce=nonce,
    )
    db.add(decision)
    db.commit()
    return decision


def test_shred_after_retention_makes_pii_and_frozen_inputs_unrecoverable(db):
    now = datetime(2032, 1, 1, tzinfo=timezone.utc)
    old = _subject(db)
    old.relationship_ended_at = now - timedelta(days=5 * 365 + 2)
    decision = _decision_for(db, old)
    before = (decision.frozen_ciphertext, decision.frozen_nonce, decision.terms)

    shredded = shred_expired(db, now=now)

    assert shredded == 1
    db.refresh(old)
    db.refresh(decision)
    assert old.shredded_at is not None
    assert old.pii_ciphertext is None
    with pytest.raises(crypto.SubjectShredded):
        crypto.decrypt_for_subject(
            db, old.id, decision.frozen_ciphertext, decision.frozen_nonce
        )
    # The decision row itself is untouched (append-only).
    assert (decision.frozen_ciphertext, decision.frozen_nonce, decision.terms) == before


def test_shred_skips_subjects_inside_the_window_or_still_active(db):
    now = datetime(2032, 1, 1, tzinfo=timezone.utc)
    recent = _subject(db)
    recent.relationship_ended_at = now - timedelta(days=30)
    active = _subject(db)  # relationship not ended
    db.commit()

    assert shred_expired(db, now=now) == 0
    assert crypto.get_subject_pii(db, recent) == _PII
    assert crypto.get_subject_pii(db, active) == _PII


def test_shred_is_idempotent_and_audited_with_counts(db):
    now = datetime(2032, 1, 1, tzinfo=timezone.utc)
    s = _subject(db)
    s.relationship_ended_at = now - timedelta(days=4000)
    db.commit()

    assert shred_expired(db, now=now) == 1
    assert shred_expired(db, now=now) == 0
    events = (
        db.query(AuditEvent).filter_by(event_type="screening.retention_shred").all()
    )
    assert [e.payload["shredded"] for e in events] == [1, 0]
    assert all("name" not in (e.payload or {}) for e in events)


def test_retention_window_is_configurable(db, monkeypatch):
    monkeypatch.setenv("ENTITYIQ_SCREENING_RETENTION_DAYS", "10")
    now = datetime(2032, 1, 1, tzinfo=timezone.utc)
    s = _subject(db)
    s.relationship_ended_at = now - timedelta(days=11)
    db.commit()
    assert shred_expired(db, now=now) == 1
