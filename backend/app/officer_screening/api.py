"""People behind a company run (ticket 0083, ADR-0006).

GET /reports/{run_id}/people   — the officers/owners screened in a KYB run,
                                 with dispositions; names for operator
                                 sessions only (operator, lead, examiner).
GET /officer-screening/entities/{entity_id}
                               — the company behind an officer screening,
                                 so the Individuals view can link back.

Names come from the encrypted screening subjects and are never stored on the
KYB side. Integration API keys get no personal data (ADR-0002), so these
routes take operator sessions only. Every people view is audited by ids.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.audit.recorder import record_event
from app.auth.operator import get_current_operator
from app.db.session import SessionLocal
from app.models.entity import Entity
from app.models.evidence import Evidence
from app.models.operator import Operator
from app.models.verification_run import VerificationRun
from app.officer_screening.screen import FIELD, SOURCE
from app.screening import crypto
from app.screening.models import (
    ScreeningDecision,
    ScreeningDisposition,
    ScreeningSubject,
)

router = APIRouter(tags=["officer screening"])


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _names(db: Session, subject_ids: list[str]) -> dict[str, dict | None]:
    """subject id → PII (None when shredded); {} when screening isn't set up."""
    if not subject_ids or not crypto.is_configured():
        return {}
    subjects = (
        db.query(ScreeningSubject).filter(ScreeningSubject.id.in_(subject_ids)).all()
    )
    return crypto.get_subjects_pii(db, subjects)


def _human_dispositions(db: Session, screening_run_ids: list[str]) -> dict[str, str]:
    """screening run id → latest human disposition."""
    if not screening_run_ids:
        return {}
    decisions = {
        d.id: d.run_id
        for d in db.query(ScreeningDecision).filter(
            ScreeningDecision.run_id.in_(screening_run_ids)
        )
    }
    latest: dict[str, str] = {}
    rows = (
        db.query(ScreeningDisposition)
        .filter(ScreeningDisposition.decision_id.in_(list(decisions)))
        .order_by(ScreeningDisposition.created_at.desc(), ScreeningDisposition.id)
    )
    for row in rows:
        latest.setdefault(decisions[row.decision_id], row.disposition)
    return latest


@router.get("/reports/{run_id}/people")
def run_people(
    run_id: str,
    db: Session = Depends(_get_db),
    operator: Operator = Depends(get_current_operator),
) -> dict:
    run = db.get(VerificationRun, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Verification run {run_id!r} not found.",
        )
    rows = (
        db.query(Evidence)
        .filter_by(verification_run_id=run_id, source=SOURCE, field=FIELD)
        .order_by(Evidence.fetched_at, Evidence.id)
        .all()
    )
    payloads = [r.raw_payload or {} for r in rows]
    subject_ids = [p.get("screening_subject_id") for p in payloads]
    pii = _names(db, [s for s in subject_ids if s])
    human = _human_dispositions(
        db, [p["screening_run_id"] for p in payloads if p.get("screening_run_id")]
    )

    people = []
    for row, payload in zip(rows, payloads, strict=True):
        subject_id = payload.get("screening_subject_id")
        shredded = subject_id in pii and pii[subject_id] is None
        people.append(
            {
                "name": (pii.get(subject_id) or {}).get("name"),
                "shredded": shredded,
                "relationships": payload.get("relationships", []),
                "roles": payload.get("roles", []),
                "sources": payload.get("sources", []),
                "ownership_pct": payload.get("ownership_pct"),
                "company_person_ids": payload.get("company_person_ids", []),
                "screening_subject_id": subject_id,
                "screening_run_id": payload.get("screening_run_id"),
                "disposition": row.normalized_value,
                "auto_closed": payload.get("auto_closed"),
                "top_score": payload.get("top_score"),
                "human_disposition": human.get(payload.get("screening_run_id")),
            }
        )

    record_event(
        db,
        "report.people_viewed",
        operator_id=operator.id,
        verification_run_id=run_id,
        payload={"screening_subject_ids": subject_ids, "count": len(people)},
    )
    availability = run.source_availability or {}
    return {
        "run_id": run_id,
        "sources": {
            "registry": availability.get("collect_people"),
            "screening": availability.get("screen_people"),
        },
        "people": people,
    }


@router.get("/officer-screening/entities/{entity_id}")
def entity_link(
    entity_id: str,
    db: Session = Depends(_get_db),
    operator: Operator = Depends(get_current_operator),
) -> dict:
    entity = db.get(Entity, entity_id)
    if entity is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Entity {entity_id!r} not found.",
        )
    latest = (
        db.query(VerificationRun)
        .filter_by(entity_id=entity_id)
        .order_by(VerificationRun.created_at.desc(), VerificationRun.id.desc())
        .first()
    )
    return {
        "entity_id": entity_id,
        "company_name": entity.canonical_name,
        "latest_run_id": latest.id if latest else None,
    }
