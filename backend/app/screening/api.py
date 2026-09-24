"""Individual screening API (tickets 0036, 0040, 0041; PRD-IDV §[16]).

Routes live under /screenings. Intake accepts an operator session or an
integration API key. Every call is audited without subject PII in the
audit payload.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import case
from sqlalchemy.orm import Session

from app.audit.recorder import record_event
from app.auth.operator import get_current_operator
from app.auth.service import Principal, get_principal
from app.db.session import SessionLocal
from app.models.operator import Operator
from app.models.watchlist_record import WatchlistRecord
from app.screening import crypto
from app.screening.models import (
    HUMAN_DISPOSITIONS,
    ScreeningCandidate,
    ScreeningClaim,
    ScreeningDecision,
    ScreeningDisposition,
    ScreeningRuleVersion,
    ScreeningRun,
    ScreeningSubject,
    ScreeningTerm,
)

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


# ---------------------------------------------------------------------------
# Read + human disposition (ticket 0041)
# ---------------------------------------------------------------------------

_SEVERITY = {"MATCH": 0, "REVIEW": 1, "CLEAR": 2, None: 3}
# Roles allowed to record a human disposition (examiners are read-only).
_DISPOSING_ROLES = ("operator", "lead")


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return _aware(dt).isoformat() if dt else None


def _latest_dispositions(
    db: Session, decision_ids: list[str]
) -> dict[str, ScreeningDisposition]:
    """Latest human disposition per decision, in one query."""
    latest: dict[str, ScreeningDisposition] = {}
    if not decision_ids:
        return latest
    rows = (
        db.query(ScreeningDisposition)
        .filter(ScreeningDisposition.decision_id.in_(decision_ids))
        .order_by(ScreeningDisposition.created_at.desc(), ScreeningDisposition.id)
    )
    for row in rows:
        latest.setdefault(row.decision_id, row)
    return latest


@router.get("")
def list_screenings(
    disposition: Literal["CLEAR", "REVIEW", "MATCH"] | None = None,
    list_source: str | None = None,
    older_than_days: int | None = None,
    trigger: Literal["intake", "monitoring"] | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(_get_db),
    operator: Operator = Depends(get_current_operator),
) -> dict:
    query = db.query(ScreeningRun, ScreeningDecision).outerjoin(
        ScreeningDecision, ScreeningDecision.run_id == ScreeningRun.id
    )
    if disposition:
        query = query.filter(ScreeningDecision.system_disposition == disposition)
    if trigger:
        query = query.filter(ScreeningRun.trigger == trigger)
    if older_than_days is not None:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=older_than_days)
        query = query.filter(ScreeningRun.created_at <= cutoff)
    if list_source:
        query = query.filter(
            db.query(ScreeningCandidate.id)
            .join(
                WatchlistRecord,
                ScreeningCandidate.watchlist_record_id == WatchlistRecord.id,
            )
            .filter(
                ScreeningCandidate.run_id == ScreeningRun.id,
                WatchlistRecord.source == list_source,
            )
            .exists()
        )
    total = query.count()
    severity = case(
        *(
            (ScreeningDecision.system_disposition == d, rank)
            for d, rank in _SEVERITY.items()
            if d is not None
        ),
        else_=_SEVERITY[None],
    )
    page = (
        query.order_by(severity, ScreeningRun.created_at.desc(), ScreeningRun.id)
        .limit(limit)
        .offset(offset)
        .all()
    )

    pii = crypto.get_subjects_pii(
        db,
        db.query(ScreeningSubject)
        .filter(ScreeningSubject.id.in_([run.subject_id for run, _ in page]))
        .all(),
    )
    latest = _latest_dispositions(db, [d.id for _, d in page if d])
    items = []
    for run, decision in page:
        human = latest.get(decision.id) if decision else None
        items.append(
            {
                "run_id": run.id,
                "subject_name": (pii.get(run.subject_id) or {}).get("name"),
                "status": run.status,
                "trigger": run.trigger,
                "system_disposition": decision.system_disposition if decision else None,
                "auto_closed": decision.auto_closed if decision else None,
                "top_score": decision.top_score if decision else None,
                "human_disposition": human.disposition if human else None,
                "created_at": _iso(run.created_at),
            }
        )
    record_event(
        db,
        "screening.queue_viewed",
        operator_id=operator.id,
        payload={
            "run_ids": [i["run_id"] for i in items],
            "total": total,
            "offset": offset,
            "filters": {
                k: v
                for k, v in {
                    "disposition": disposition,
                    "list_source": list_source,
                    "older_than_days": older_than_days,
                    "trigger": trigger,
                }.items()
                if v is not None
            },
        },
    )
    db.commit()
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def _load_run(db: Session, run_id: str) -> ScreeningRun:
    run = db.get(ScreeningRun, run_id)
    if run is None:
        raise HTTPException(
            status_code=404, detail=f"Screening run {run_id!r} not found."
        )
    return run


@router.get("/{run_id}")
def get_screening(
    run_id: str,
    db: Session = Depends(_get_db),
    principal: Principal = Depends(get_principal),
) -> dict:
    """Operators see any run; an integration key only runs it submitted."""
    run = _load_run(db, run_id)
    subject = db.get(ScreeningSubject, run.subject_id)
    is_operator = principal.operator_id is not None
    if not is_operator and subject.api_client_id != principal.api_client_id:
        # Same answer as a missing run: don't reveal other clients' runs.
        raise HTTPException(
            status_code=404, detail=f"Screening run {run_id!r} not found."
        )
    try:
        pii: dict | None = crypto.get_subject_pii(db, subject)
    except crypto.SubjectShredded:
        pii = None
    decision = db.query(ScreeningDecision).filter_by(run_id=run_id).one_or_none()
    rule = db.get(ScreeningRuleVersion, decision.rule_version_id) if decision else None

    claims = {
        c.id: {
            "about": c.about,
            "field": c.field,
            "value": c.value,
            "source": c.source,
            "locator": c.locator,
            "retrieved_at": _iso(c.retrieved_at),
        }
        for c in db.query(ScreeningClaim).filter_by(run_id=run_id)
    }
    term_rows: dict[str, list[ScreeningTerm]] = {}
    for t in db.query(ScreeningTerm).filter_by(run_id=run_id):
        term_rows.setdefault(t.candidate_id, []).append(t)

    entries = decision.terms if decision else []
    cands = {
        c.id: c
        for c in db.query(ScreeningCandidate).filter(
            ScreeningCandidate.id.in_([e["candidate_id"] for e in entries])
        )
    }
    records = {
        r.id: r
        for r in db.query(WatchlistRecord).filter(
            WatchlistRecord.id.in_([c.watchlist_record_id for c in cands.values()])
        )
    }
    candidates = []
    for entry in entries:
        cand = cands[entry["candidate_id"]]
        record = records[cand.watchlist_record_id]
        claim_ids_by_name = {t.name: t.claim_ids for t in term_rows.get(cand.id, [])}
        terms = [
            {**t, "claim_ids": claim_ids_by_name.get(t["name"], [])}
            for t in entry["terms"]
        ]
        cited = {cid for t in terms for cid in t["claim_ids"]}
        candidates.append(
            {
                "candidate_id": cand.id,
                "score": entry["score"],
                "band": entry["band"],
                "blocking_keys": cand.blocking_keys,
                "record": {
                    "id": record.id,
                    "source": record.source,
                    "source_entry_id": record.source_entry_id,
                    "snapshot_id": record.snapshot_id,
                    "primary_name": record.primary_name,
                    "names": record.names,
                    "dobs": record.dobs,
                    "pobs": record.pobs,
                    "nationalities": record.nationalities,
                    "documents": record.documents,
                    "program": record.program,
                },
                "terms": terms,
                "claims": {cid: claims[cid] for cid in cited if cid in claims},
            }
        )

    dispositions = (
        db.query(ScreeningDisposition)
        .filter_by(decision_id=decision.id)
        .order_by(ScreeningDisposition.created_at, ScreeningDisposition.id)
        .all()
        if decision
        else []
    )
    monitoring = _monitoring_block(db, run, candidates)
    started, finished = _aware(run.started_at), _aware(run.finished_at)
    record_event(
        db,
        "screening.viewed",
        operator_id=principal.operator_id,
        api_client_id=principal.api_client_id,
        payload={"run_id": run_id, "pii_shown": pii is not None},
    )
    db.commit()
    return {
        "run_id": run_id,
        "subject": pii,
        "subject_shredded": pii is None,
        "kyb_entity_id": subject.kyb_entity_id,
        "run": {
            "status": run.status,
            "trigger": run.trigger,
            "started_at": _iso(started),
            "finished_at": _iso(finished),
            "duration_seconds": (finished - started).total_seconds()
            if started and finished
            else None,
            "stages": run.source_availability or {},
        },
        "decision": {
            "decision_id": decision.id,
            "system_disposition": decision.system_disposition,
            "auto_closed": decision.auto_closed,
            "top_score": decision.top_score,
            "rule_version": rule.version if rule else None,
            "thresholds": decision.thresholds,
            "snapshot_ids": decision.snapshot_ids,
            "normalizer_version": decision.normalizer_version,
            "created_at": _iso(decision.created_at),
        }
        if decision
        else None,
        "candidates": candidates,
        "monitoring": monitoring,
        "dispositions": [
            {
                "disposition": d.disposition,
                "created_at": _iso(d.created_at),
                # Analyst notes and identities stay internal to operators.
                **(
                    {"notes": d.notes, "operator_id": d.operator_id}
                    if is_operator
                    else {}
                ),
            }
            for d in dispositions
        ],
    }


def _monitoring_block(
    db: Session, run: ScreeningRun, candidates: list[dict]
) -> dict | None:
    """Why a monitoring run exists: the snapshot, what changed, the prior run."""
    if run.trigger != "monitoring" or not run.triggered_by_snapshot_id:
        return None
    from app.lists.delta import diff_snapshots  # noqa: PLC0415
    from app.models.list_snapshot import ListSnapshot  # noqa: PLC0415

    snapshot = db.get(ListSnapshot, run.triggered_by_snapshot_id)
    previous = (
        db.query(ListSnapshot)
        .filter(
            ListSnapshot.source == snapshot.source,
            ListSnapshot.retrieved_at < snapshot.retrieved_at,
        )
        .order_by(ListSnapshot.retrieved_at.desc())
        .first()
    )
    delta = diff_snapshots(db, previous.id if previous else None, snapshot.id)
    changed = set(delta.added_entry_ids) | set(delta.changed_entry_ids)
    hit = sorted(
        c["record"]["source_entry_id"]
        for c in candidates
        if c["record"]["source"] == snapshot.source
        and c["record"]["source_entry_id"] in changed
    )
    prior = (
        db.query(ScreeningDecision).filter_by(run_id=run.prior_run_id).one_or_none()
        if run.prior_run_id
        else None
    )
    return {
        "snapshot_id": snapshot.id,
        "source": snapshot.source,
        "retrieved_at": _iso(snapshot.retrieved_at),
        "changed_entry_ids": hit,
        "prior_run_id": run.prior_run_id,
        "prior_disposition": prior.system_disposition if prior else None,
    }


class DispositionIn(BaseModel):
    disposition: Literal[HUMAN_DISPOSITIONS]  # type: ignore[valid-type]
    notes: str | None = Field(default=None, max_length=4000)


@router.post("/{run_id}/disposition", status_code=status.HTTP_201_CREATED)
def dispose_screening(
    run_id: str,
    body: DispositionIn,
    db: Session = Depends(_get_db),
    operator: Operator = Depends(get_current_operator),
) -> dict:
    if operator.role not in _DISPOSING_ROLES:
        raise HTTPException(
            status_code=403,
            detail=f"Role '{operator.role}' cannot record dispositions.",
        )
    _load_run(db, run_id)
    decision = db.query(ScreeningDecision).filter_by(run_id=run_id).one_or_none()
    if decision is None:
        raise HTTPException(status_code=409, detail="The run has no decision yet.")
    row = ScreeningDisposition(
        decision_id=decision.id,
        disposition=body.disposition,
        notes=body.notes,
        operator_id=operator.id,
    )
    db.add(row)
    db.flush()
    record_event(
        db,
        "screening.dispositioned",
        operator_id=operator.id,
        payload={
            "run_id": run_id,
            "decision_id": decision.id,
            "disposition": body.disposition,
            "system_disposition": decision.system_disposition,
        },
    )
    db.commit()
    return {"disposition_id": row.id, "disposition": row.disposition}


# ---------------------------------------------------------------------------
# Replay (ticket 0040)
# ---------------------------------------------------------------------------

# Leads and examiners verify decisions; operators disposition them.
_REPLAY_ROLES = ("lead", "examiner")


@router.post("/{run_id}/replay")
def replay_screening(
    run_id: str,
    db: Session = Depends(_get_db),
    operator: Operator = Depends(get_current_operator),
) -> dict:
    from app.screening.replay import replay_decision  # noqa: PLC0415

    if operator.role not in _REPLAY_ROLES:
        raise HTTPException(
            status_code=403, detail=f"Role '{operator.role}' cannot replay decisions."
        )
    _load_run(db, run_id)
    decision = db.query(ScreeningDecision).filter_by(run_id=run_id).one_or_none()
    if decision is None:
        raise HTTPException(status_code=409, detail="The run has no decision yet.")
    result = replay_decision(db, decision)
    record_event(
        db,
        "screening.replayed",
        operator_id=operator.id,
        payload={
            "run_id": run_id,
            "reproduced": result["reproduced"],
            "shredded": result["shredded"],
        },
    )
    db.commit()
    return result
