"""screen_people stage: screen each officer and owner (ticket 0081, ADR-0006).

For every person collected by ``collect_people``: reuse the entity's screening
subject for the same person (decrypted, matched on normalized name and
compatible date of birth) or create one, link it with ``company_person`` rows
(one per relationship), run a ``kyb_officer`` screening inline, and record the
outcome as KYB evidence.

Evidence (``source="officer_screening"``, tier 1, one row per person):
  officer_screening_result = CLEAR | REVIEW | MATCH | unavailable
  raw_payload: company_person_ids, screening run/subject ids, relationships,
  roles, sources, ownership_pct, auto_closed, top_score. Never the name.

Runs inline on the stage's session: under a stage timeout that session is
isolated, so either every person in the run is screened or none are.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.audit.recorder import record_event
from app.models.company_person import CompanyPerson
from app.models.evidence import Evidence
from app.models.verification_run import VerificationRun
from app.officer_screening.people import dob_compatible, person_key
from app.scoring.signals import OFFICER_SCREENING_FIELD, OFFICER_SCREENING_SOURCE
from app.screening import crypto
from app.screening.models import ScreeningDecision, ScreeningRun, ScreeningSubject
from app.screening.pipeline import run_screening

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

SOURCE = OFFICER_SCREENING_SOURCE
FIELD = OFFICER_SCREENING_FIELD
TRIGGER = "kyb_officer"


def _subject_pii(person: dict) -> dict:
    pii = {"name": person["name"]}
    for attr in ("dob", "nationality"):
        if person.get(attr):
            pii[attr] = person[attr]
    return pii


def _known_subjects(db: "Session", entity_id: str) -> list[tuple]:
    """(subject, pii) for the entity's linked, readable subjects."""
    ids = {
        link.screening_subject_id
        for link in db.query(CompanyPerson).filter_by(entity_id=entity_id)
    }
    if not ids:
        return []
    known = []
    for subject in db.query(ScreeningSubject).filter(ScreeningSubject.id.in_(ids)):
        try:
            known.append((subject, crypto.get_subject_pii(db, subject)))
        except (crypto.SubjectShredded, crypto.DecryptionError):
            continue
    return known


def _subject_for(
    db: "Session", entity_id: str, person: dict, known: list
) -> ScreeningSubject:
    key = person_key(person["name"])
    for subject, pii in known:
        if person_key(pii.get("name")) == key and dob_compatible(
            pii.get("dob"), person.get("dob")
        ):
            merged = {**_subject_pii(person), **pii}  # what we knew first wins
            if merged != pii:
                crypto.set_subject_pii(db, subject, merged)
            return subject
    subject = ScreeningSubject(kyb_entity_id=entity_id)
    db.add(subject)
    db.flush()
    pii = _subject_pii(person)
    crypto.set_subject_pii(db, subject, pii)
    known.append((subject, pii))
    return subject


def _link(
    db: "Session", entity_id: str, run_id: str, subject: ScreeningSubject, person: dict
) -> list[str]:
    ids = []
    for relationship in person["relationships"]:
        link = (
            db.query(CompanyPerson)
            .filter_by(
                entity_id=entity_id,
                screening_subject_id=subject.id,
                relationship=relationship,
            )
            .first()
        )
        if link is None:
            link = CompanyPerson(
                entity_id=entity_id,
                first_run_id=run_id,
                screening_subject_id=subject.id,
                relationship=relationship,
            )
            db.add(link)
        link.role = person["roles"][0] if person["roles"] else link.role
        link.sources = person["sources"]
        if relationship == "owner" and person.get("ownership_pct") is not None:
            link.ownership_pct = person["ownership_pct"]
        db.flush()
        ids.append(link.id)
    return ids


class ScreenPeopleStage:
    """Pipeline stage: screen the people collect_people found (ticket 0081)."""

    name = "screen_people"

    def run(self, run_id: str, db: "Session", context: dict) -> dict:
        people = (context.get("people") or {}).get("people") or []
        if not people:
            return {**context, "screened_people": {"status": "complete", "count": 0}}
        if not crypto.is_configured():
            return {
                **context,
                "screened_people": {
                    "status": "unavailable",
                    "message": "Individual screening is not configured",
                },
            }

        run = db.get(VerificationRun, run_id)
        entity_id = run.entity_id
        known = _known_subjects(db, entity_id)
        for person in people:
            subject = _subject_for(db, entity_id, person, known)
            link_ids = _link(db, entity_id, run_id, subject, person)
            screening = ScreeningRun(subject_id=subject.id, trigger=TRIGGER)
            db.add(screening)
            db.flush()
            record_event(
                db,
                "screening.submitted",
                verification_run_id=run_id,
                payload={
                    "run_id": screening.id,
                    "subject_id": subject.id,
                    "trigger": TRIGGER,
                    "verification_run_id": run_id,
                    "entity_id": entity_id,
                },
            )
            db.commit()
            run_screening(screening.id, db)

            db.refresh(screening)
            decision = (
                db.query(ScreeningDecision).filter_by(run_id=screening.id).one_or_none()
            )
            disposition = decision.system_disposition if decision else "unavailable"
            db.add(
                Evidence(
                    verification_run_id=run_id,
                    source=SOURCE,
                    tier=1,
                    field=FIELD,
                    raw_value=disposition,
                    normalized_value=disposition,
                    confidence=0.9,
                    raw_payload={
                        "company_person_ids": link_ids,
                        "screening_run_id": screening.id,
                        "screening_subject_id": subject.id,
                        "screening_status": screening.status,
                        "relationships": person["relationships"],
                        "roles": person["roles"],
                        "sources": person["sources"],
                        "ownership_pct": person.get("ownership_pct"),
                        "auto_closed": decision.auto_closed if decision else None,
                        "top_score": decision.top_score if decision else None,
                    },
                    attribution={
                        "provider": "entityiq_individual_screening",
                        "screening_run_id": screening.id,
                    },
                    fetched_at=datetime.now(tz=timezone.utc),
                )
            )
            db.commit()

        return {
            **context,
            "screened_people": {"status": "complete", "count": len(people)},
        }
