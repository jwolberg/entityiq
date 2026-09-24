"""Individual screening API (tickets 0036, 0040, 0041; PRD-IDV §[16]).

Routes live under /screenings. Intake accepts an operator session or an
integration API key. Every call is audited without subject PII in the
audit payload.
"""

from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.audit.recorder import record_event
from app.auth.service import Principal, get_principal
from app.db.session import SessionLocal
from app.screening import crypto
from app.screening.models import ScreeningDecision, ScreeningRun, ScreeningSubject

router = APIRouter(prefix="/screenings", tags=["screenings"])


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def enqueue_screening(run_id: str) -> None:
    """Queue the run on the screening queue (patched to run inline in tests)."""
    from app.screening.tasks import run_screening_task  # noqa: PLC0415

    run_screening_task.delay(run_id)


# ---------------------------------------------------------------------------
# Intake (ticket 0036)
# ---------------------------------------------------------------------------

_DOB = re.compile(r"\d{4}(-(0[1-9]|1[0-2])(-(0[1-9]|[12]\d|3[01]))?)?")


class DocumentIn(BaseModel):
    type: Literal["passport", "national_id", "tax_id", "other"]
    number: str = Field(min_length=1, max_length=64)
    country: str | None = Field(default=None, max_length=64)

    @field_validator("number")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("number must not be blank")
        return v.strip()


class ScreeningIn(BaseModel):
    name: str = Field(min_length=1, max_length=512)
    original_script_name: str | None = Field(default=None, max_length=512)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    dob: str | None = None
    nationality: str | None = Field(default=None, max_length=64)
    country_of_residence: str | None = Field(default=None, max_length=64)
    pob: str | None = Field(default=None, max_length=256)
    gender: str | None = Field(default=None, max_length=32)
    documents: list[DocumentIn] = Field(default_factory=list, max_length=10)
    address: str | None = Field(default=None, max_length=1024)
    kyb_entity_id: str | None = Field(default=None, max_length=36)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name must not be blank")
        return v.strip()

    @field_validator("dob")
    @classmethod
    def _dob_shape(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _DOB.fullmatch(v.strip()):
            raise ValueError("dob must be YYYY, YYYY-MM or YYYY-MM-DD")
        return v.strip()


class ScreeningCreated(BaseModel):
    run_id: str
    status: str
    disposition: str | None = None
    auto_closed: bool | None = None


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ScreeningCreated)
def create_screening(
    body: ScreeningIn,
    db: Session = Depends(_get_db),
    principal: Principal = Depends(get_principal),
) -> ScreeningCreated:
    subject = ScreeningSubject(
        kyb_entity_id=body.kyb_entity_id,
        api_client_id=principal.api_client_id,
        created_by_operator_id=principal.operator_id,
    )
    db.add(subject)
    db.flush()
    pii = body.model_dump(exclude={"kyb_entity_id"}, exclude_none=True)
    try:
        crypto.set_subject_pii(db, subject, pii)
    except crypto.CryptoNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Screening is not configured on this server.",
        ) from exc
    run = ScreeningRun(subject_id=subject.id, trigger="intake")
    db.add(run)
    db.flush()
    record_event(
        db,
        "screening.submitted",
        operator_id=principal.operator_id,
        api_client_id=principal.api_client_id,
        payload={"run_id": run.id, "subject_id": subject.id},
    )
    db.commit()
    run_id = run.id

    enqueue_screening(run_id)

    db.expire_all()
    run = db.get(ScreeningRun, run_id)
    decision = db.query(ScreeningDecision).filter_by(run_id=run_id).one_or_none()
    return ScreeningCreated(
        run_id=run_id,
        status=run.status,
        disposition=decision.system_disposition if decision else None,
        auto_closed=decision.auto_closed if decision else None,
    )
