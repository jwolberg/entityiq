"""Disposition engine, guarded auto-CLEAR, frozen decision (IS2-T4, ticket 0039)."""

from __future__ import annotations

import base64
import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.db.session import Base
from app.lists.persons import PersonRecord, store_records
from app.models.list_snapshot import ListSnapshot
from app.screening import crypto
from app.screening.dispose import decide
from app.screening.models import ScreeningDecision, ScreeningRun, ScreeningSubject
from app.screening.pipeline import run_screening
from app.screening.scoring import DEFAULT_RULE


def _cand(cid, name, **attrs):
    return {
        "candidate_id": cid,
        "record": {
            "id": cid,
            "names": [{"name": name, "kind": "primary"}],
            "dobs": attrs.get("dobs", []),
            "pobs": [],
            "nationalities": attrs.get("nat", []),
            "documents": attrs.get("docs", []),
        },
    }


def _bundle(subject, candidates, *, unavailable=()):
    return {
        "subject": subject,
        "candidates": candidates,
        "rule": DEFAULT_RULE,
        "normalizer_version": "n1",
        "source_status": {
            "block_candidates": "unavailable" if unavailable else "complete"
        },
    }


# ---------------------------------------------------------------------------
# decide(): pure
# ---------------------------------------------------------------------------


def test_no_candidates_is_an_auto_clear():
    result = decide(_bundle({"name": "Nobody Listed"}, []))
    assert result["disposition"] == "CLEAR"
    assert result["auto_closed"] is True


def test_strong_corroborated_match_is_match():
    result = decide(
        _bundle(
            {"name": "Teodor Vasilescu", "dob": "1962-08-30", "nationality": "RO"},
            [
                _cand(
                    "W1", "Teodor Vasilescu", dobs=[{"date": "1962-08-30"}], nat=["RO"]
                )
            ],
        )
    )
    assert result["disposition"] == "MATCH"
    assert result["auto_closed"] is False


def test_name_only_on_both_sides_is_review_never_clear():
    """F9: absence of corroborating data is not evidence of no risk."""
    result = decide(
        _bundle({"name": "Teodor Vasilescu"}, [_cand("W1", "Teodor Vasilescu")])
    )
    assert result["disposition"] == "REVIEW"


def test_weak_candidates_only_is_auto_clear():
    result = decide(
        _bundle(
            {"name": "Ahmed Morozko", "dob": "1990-01-01"},
            [_cand("W1", "Viktor Morozko", dobs=[{"date": "1950-05-05"}])],
        )
    )
    assert result["candidates"][0]["band"] == "CLEAR"
    assert result["disposition"] == "CLEAR"
    assert result["auto_closed"] is True


def test_unavailable_source_forces_review_instead_of_clear():
    """C2 guard: CLEAR only auto-closes when every source answered."""
    result = decide(_bundle({"name": "Nobody Listed"}, [], unavailable=True))
    assert result["disposition"] == "REVIEW"
    assert result["auto_closed"] is False


def test_conflict_cannot_clear_a_name_match_on_its_own():
    """F8/F10: lists carry errors, so a DOB conflict only lowers the band."""
    result = decide(
        _bundle(
            {"name": "Teodor Vasilescu", "dob": "1962-08-30"},
            [_cand("W1", "Teodor Vasilescu", dobs=[{"date": "1975-01-01"}])],
        )
    )
    cand = result["candidates"][0]
    assert cand["score"] < DEFAULT_RULE["thresholds"]["clear_below"]
    assert cand["band"] == "REVIEW"
    assert result["disposition"] == "REVIEW"


def test_run_takes_the_most_severe_candidate_band():
    result = decide(
        _bundle(
            {"name": "Teodor Vasilescu", "dob": "1962-08-30", "nationality": "RO"},
            [
                _cand("W1", "Viktor Morozko"),
                _cand(
                    "W2", "Teodor Vasilescu", dobs=[{"date": "1962-08-30"}], nat=["RO"]
                ),
            ],
        )
    )
    assert [c["band"] for c in result["candidates"]] == ["CLEAR", "MATCH"]
    assert result["disposition"] == "MATCH"


def test_decide_is_deterministic():
    b = _bundle(
        {"name": "Teodor Vasilescu", "dob": "1962-08-30"},
        [_cand("W1", "Teodor Vasilescu", dobs=[{"year": 1962}])],
    )
    assert decide(b) == decide(b)


# ---------------------------------------------------------------------------
# End to end: pipeline writes a frozen, encrypted, append-only decision
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def master_key(monkeypatch):
    monkeypatch.setenv(
        "ENTITYIQ_SCREENING_MASTER_KEY", base64.b64encode(os.urandom(32)).decode()
    )


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'd.db'}")
    Base.metadata.create_all(engine)
    sess = Session(bind=engine)
    yield sess
    sess.close()
    engine.dispose()


def _screen(db, pii, people):
    if people is not None:
        snap = ListSnapshot(
            source="ofac_sdn",
            retrieved_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            content_sha256="x",
            record_count=len(people),
        )
        db.add(snap)
        db.flush()
        store_records(db, snap, people)
    subject = ScreeningSubject()
    db.add(subject)
    db.flush()
    crypto.set_subject_pii(db, subject, pii)
    run = ScreeningRun(subject_id=subject.id)
    db.add(run)
    db.commit()
    run_screening(run.id, db)
    return run


def _person(name, **kw):
    return PersonRecord(
        source="ofac_sdn",
        source_entry_id=kw.pop("eid", "1"),
        primary_name=name,
        names=[{"name": name, "kind": "primary"}],
        **kw,
    )


def test_pipeline_writes_a_frozen_decision(db):
    pii = {"name": "Teodor Vasilescu", "dob": "1962-08-30"}
    run = _screen(db, pii, [_person("Teodor Vasilescu", dobs=[{"date": "1962-08-30"}])])

    decision = db.query(ScreeningDecision).filter_by(run_id=run.id).one()
    assert decision.system_disposition == "REVIEW"
    assert decision.normalizer_version == "n2"
    assert decision.snapshot_ids and decision.rule_version_id
    assert decision.thresholds == DEFAULT_RULE["thresholds"]
    bundle = crypto.decrypt_for_subject(
        db, run.subject_id, decision.frozen_ciphertext, decision.frozen_nonce
    )
    assert bundle["subject"] == pii
    assert bundle["candidates"][0]["record"]["names"][0]["name"] == "Teodor Vasilescu"
    assert bundle["candidates"][0]["source"] == "ofac_sdn"
    # Terms are stored in the clear for display, without subject values.
    terms = decision.terms[0]["terms"]
    assert {t["name"] for t in terms} == {"name_exact_normalized", "dob_full_match"}
    assert all("subject_value" not in t for t in terms)


def test_no_lists_loaded_is_review_not_auto_clear(db):
    run = _screen(db, {"name": "Anyone"}, None)
    decision = db.query(ScreeningDecision).filter_by(run_id=run.id).one()
    assert decision.system_disposition == "REVIEW"
    assert decision.auto_closed is False


def test_clean_subject_is_auto_cleared(db):
    run = _screen(db, {"name": "Ingrid Solheimsen"}, [_person("Chidi Okafor")])
    decision = db.query(ScreeningDecision).filter_by(run_id=run.id).one()
    assert decision.system_disposition == "CLEAR"
    assert decision.auto_closed is True
    db.refresh(run)
    assert run.status == "complete"


def test_decision_rows_cannot_be_updated_through_the_orm_helpers(db):
    """Code-level append-only: the module exposes no update helper."""
    import app.screening.dispose as mod

    assert not [n for n in dir(mod) if n.startswith(("update", "edit", "delete"))]
    _ = text  # DB-level enforcement is covered by test_append_only.py


def test_pipeline_works_on_the_isolated_stage_session_path(db):
    """Production runs with stage timeouts (each stage on its own session)."""
    snap = ListSnapshot(
        source="ofac_sdn",
        retrieved_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        content_sha256="y",
        record_count=1,
    )
    db.add(snap)
    db.flush()
    store_records(
        db, snap, [_person("Teodor Vasilescu", dobs=[{"date": "1962-08-30"}])]
    )
    subject = ScreeningSubject()
    db.add(subject)
    db.flush()
    crypto.set_subject_pii(
        db, subject, {"name": "Teodor Vasilescu", "dob": "1962-08-30"}
    )
    run = ScreeningRun(subject_id=subject.id)
    db.add(run)
    db.commit()

    run_screening(run.id, db, stage_timeout_seconds=10, run_timeout_seconds=30)

    db.expire_all()
    run = db.get(ScreeningRun, run.id)
    assert run.status == "complete"
    assert set(run.source_availability.values()) == {"complete"}
    assert (
        db.query(ScreeningDecision).filter_by(run_id=run.id).one().system_disposition
        == "REVIEW"
    )
