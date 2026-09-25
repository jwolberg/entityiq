"""Scoring stage, claims with provenance, rule versions (IS2-T3, ticket 0038)."""

from __future__ import annotations

import base64
import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.db.session import Base
from app.lists.persons import PersonRecord, store_records
from app.models.list_snapshot import ListSnapshot
from app.screening import crypto
from app.screening.models import (
    ScreeningCandidate,
    ScreeningClaim,
    ScreeningRuleVersion,
    ScreeningRun,
    ScreeningSubject,
    ScreeningTerm,
)
from app.screening.rules import current_rule, new_rule_version
from app.screening.stages import BlockCandidatesStage, ScoreCandidatesStage

PII = {"name": "Teodor Vasilescu", "dob": "1962-08-30", "nationality": "Romania"}


@pytest.fixture(autouse=True)
def master_key(monkeypatch):
    monkeypatch.setenv(
        "ENTITYIQ_SCREENING_MASTER_KEY", base64.b64encode(os.urandom(32)).decode()
    )


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 's.db'}")
    Base.metadata.create_all(engine)
    sess = Session(bind=engine)
    yield sess
    sess.close()
    engine.dispose()


def _load(db, people):
    snap = ListSnapshot(
        source="ofac_sdn",
        retrieved_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        content_sha256="x",
        record_count=len(people),
    )
    db.add(snap)
    db.flush()
    store_records(db, snap, people)
    db.commit()
    return snap


def _scored_run(db):
    _load(
        db,
        [
            PersonRecord(
                source="ofac_sdn",
                source_entry_id="9001",
                primary_name="Teodor Vasilescu",
                names=[{"name": "Teodor Vasilescu", "kind": "primary"}],
                dobs=[{"date": "1962-08-30"}],
                nationalities=["RO"],
            ),
        ],
    )
    subject = ScreeningSubject()
    db.add(subject)
    db.flush()
    crypto.set_subject_pii(db, subject, PII)
    run = ScreeningRun(subject_id=subject.id, started_at=datetime.now(timezone.utc))
    db.add(run)
    db.commit()
    ctx = BlockCandidatesStage().run(run.id, db, {})
    ctx = ScoreCandidatesStage().run(run.id, db, ctx)
    return run, ctx


def test_default_rule_version_is_created_once(db):
    a = current_rule(db)
    b = current_rule(db)
    assert a.id == b.id and a.version == 1
    assert db.query(ScreeningRuleVersion).count() == 1


def test_threshold_change_is_a_new_version_not_an_edit(db):
    v1 = current_rule(db)
    config = {**v1.config, "thresholds": {"clear_below": 0.3, "match_at": 0.95}}
    v2 = new_rule_version(db, config, note="tighten match")
    assert v2.version == 2
    db.refresh(v1)
    assert v1.config["thresholds"]["clear_below"] == 0.35  # untouched
    assert current_rule(db).id == v2.id


def test_stage_scores_candidates_and_records_rule_version(db):
    run, ctx = _scored_run(db)
    cand = db.query(ScreeningCandidate).filter_by(run_id=run.id).one()
    assert cand.score == pytest.approx(0.5 + 0.3 + 0.1)
    db.refresh(run)
    assert run.rule_version_id == current_rule(db).id
    assert ctx["scoring"]["status"] == "complete"
    assert ctx["scoring"]["top_score"] == pytest.approx(0.9)


def test_every_term_cites_a_subject_claim_and_a_record_claim(db):
    run, _ = _scored_run(db)
    claims = {c.id: c for c in db.query(ScreeningClaim).filter_by(run_id=run.id)}
    terms = db.query(ScreeningTerm).filter_by(run_id=run.id).all()
    assert {t.name for t in terms} == {
        "name_exact_normalized",
        "dob_full_match",
        "nationality_match",
    }
    for term in terms:
        sides = {claims[cid].about for cid in term.claim_ids}
        assert sides == {"subject", "record"}, term.name
        for cid in term.claim_ids:
            assert (
                claims[cid].source and claims[cid].locator and claims[cid].retrieved_at
            )


def test_record_claims_carry_list_provenance(db):
    run, _ = _scored_run(db)
    rec_claims = db.query(ScreeningClaim).filter_by(run_id=run.id, about="record").all()
    assert rec_claims
    for c in rec_claims:
        assert c.source == "ofac_sdn"
        assert c.locator.startswith("ofac_sdn:9001@")


def test_subject_pii_never_lands_in_claims_or_terms_in_plaintext(db):
    run, _ = _scored_run(db)
    for c in db.query(ScreeningClaim).filter_by(run_id=run.id, about="subject"):
        assert c.value is None
        assert c.locator.startswith("screening_subject:")
    blob = " ".join(
        str(t.detail) + str(t.claim_ids)
        for t in db.query(ScreeningTerm).filter_by(run_id=run.id)
    )
    for value in PII.values():
        assert value not in blob


def test_no_candidates_scores_nothing(db):
    _load(db, [])
    subject = ScreeningSubject()
    db.add(subject)
    db.flush()
    crypto.set_subject_pii(db, subject, {"name": "Nobody Listed"})
    run = ScreeningRun(subject_id=subject.id)
    db.add(run)
    db.commit()
    ctx = ScoreCandidatesStage().run(run.id, db, {"blocking": {"status": "complete"}})
    assert ctx["scoring"] == {
        "status": "complete",
        "rule_version": 1,
        "candidates": 0,
        "top_score": None,
    }
