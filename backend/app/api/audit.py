"""Audit trail read endpoints (P3-T2).

  GET /audit/runs/{run_id}  — timeline for one run (and its submission), oldest
                              first.  Any signed-in operator: it is the history
                              of the case they are looking at.
  GET /audit/events         — global log, newest first.  Lead only (USERS § 3:
                              the compliance lead reviews the audit log).

Read-only: the audit table is append-only (app/audit/recorder.py) and nothing
here mutates it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.auth.operator import get_current_operator, require_lead_or_examiner
from app.db.session import SessionLocal
from app.models.api_client import ApiClient
from app.models.audit_event import AuditEvent
from app.models.verification_run import VerificationRun

router = APIRouter(prefix="/audit", tags=["audit"])


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class AuditEventSchema(BaseModel):
    id: str
    event_type: str
    actor: str  # operator email, integrating-system name, or "system"
    description: str | None = None
    occurred_at: str
    run_id: str | None = None
    payload: dict | None = None


class AuditEventList(BaseModel):
    events: list[AuditEventSchema]


def _serialize(events: list[AuditEvent], db: Session) -> AuditEventList:
    client_ids = {e.api_client_id for e in events if e.api_client_id}
    client_names = (
        dict(
            db.query(ApiClient.id, ApiClient.name)
            .filter(ApiClient.id.in_(client_ids))
            .all()
        )
        if client_ids
        else {}
    )

    def actor(e: AuditEvent) -> str:
        if e.operator is not None:
            return e.operator.email
        if e.api_client_id:
            return client_names.get(e.api_client_id, "integration")
        return "system"

    return AuditEventList(
        events=[
            AuditEventSchema(
                id=e.id,
                event_type=e.event_type,
                actor=actor(e),
                description=e.description,
                occurred_at=e.occurred_at.isoformat(),
                run_id=e.verification_run_id,
                payload=e.payload,
            )
            for e in events
        ]
    )


@router.get(
    "/runs/{run_id}",
    response_model=AuditEventList,
    summary="Audit timeline for one verification run",
)
def run_timeline(
    run_id: str,
    db: Session = Depends(_get_db),
    _operator=Depends(get_current_operator),
) -> AuditEventList:
    run = db.get(VerificationRun, run_id)
    conditions = [AuditEvent.verification_run_id == run_id]
    if run is not None:
        conditions.append(AuditEvent.submission_id == run.submission_id)
    events = (
        db.query(AuditEvent)
        .filter(or_(*conditions))
        .order_by(AuditEvent.occurred_at.asc())
        .all()
    )
    return _serialize(events, db)


@router.get(
    "/events",
    response_model=AuditEventList,
    summary="Global audit log (lead or examiner)",
)
def global_log(
    limit: int = Query(default=100, ge=1, le=500),
    event_type: str | None = None,
    db: Session = Depends(_get_db),
    _reader=Depends(require_lead_or_examiner),
) -> AuditEventList:
    query = db.query(AuditEvent)
    if event_type:
        query = query.filter(AuditEvent.event_type == event_type)
    events = query.order_by(AuditEvent.occurred_at.desc()).limit(limit).all()
    return _serialize(events, db)
