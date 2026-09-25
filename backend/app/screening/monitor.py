"""Ongoing monitoring: re-screen subjects when a list changes (ticket 0051).

PRD-IDV F17. For a new snapshot, diff it against the previous snapshot of
the same source and block every active subject against only the added and
changed records. Subjects hit get a new monitoring run (never an edit of a
past decision), linked to their prior run and the triggering snapshot.
Shredded subjects and ended relationships are skipped. Removed records
trigger nothing: a delisting can only lower risk, and the next scheduled
screening reflects it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from app.audit.recorder import record_event
from app.lists.delta import diff_snapshots
from app.models.list_snapshot import ListSnapshot
from app.screening import crypto
from app.screening.blocking import BlockingIndex
from app.screening.models import ScreeningRun, ScreeningSubject
from app.screening.stages import subject_names

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _previous_snapshot_id(db: "Session", snapshot: ListSnapshot) -> str | None:
    prev = (
        db.query(ListSnapshot)
        .filter(
            ListSnapshot.source == snapshot.source,
            ListSnapshot.retrieved_at < snapshot.retrieved_at,
        )
        .order_by(ListSnapshot.retrieved_at.desc())
        .first()
    )
    return prev.id if prev else None


def rescreen_for_snapshot(
    db: "Session",
    snapshot_id: str,
    *,
    enqueue: Callable[[str], None] | None = None,
) -> list[str]:
    """Create monitoring runs for subjects hit by the snapshot's delta."""
    if enqueue is None:
        from app.screening.api import enqueue_screening as enqueue  # noqa: PLC0415

    snapshot = db.get(ListSnapshot, snapshot_id)
    delta = diff_snapshots(db, _previous_snapshot_id(db, snapshot), snapshot_id)
    index = BlockingIndex.build(delta.new_records)

    created: list[str] = []
    if delta.new_records:
        subjects = (
            db.query(ScreeningSubject)
            .filter(
                ScreeningSubject.shredded_at.is_(None),
                ScreeningSubject.relationship_ended_at.is_(None),
            )
            .all()
        )
        for subject in subjects:
            try:
                pii = crypto.get_subject_pii(db, subject)
            except (crypto.SubjectShredded, crypto.DecryptionError):
                continue
            if not any(index.candidates(n) for n in subject_names(pii)):
                continue
            prior = (
                db.query(ScreeningRun)
                .filter_by(subject_id=subject.id)
                .order_by(ScreeningRun.created_at.desc(), ScreeningRun.id.desc())
                .first()
            )
            run = ScreeningRun(
                subject_id=subject.id,
                trigger="monitoring",
                triggered_by_snapshot_id=snapshot_id,
                prior_run_id=prior.id if prior else None,
            )
            db.add(run)
            db.flush()
            created.append(run.id)

    record_event(
        db,
        "screening.monitoring_rescreen",
        payload={
            "snapshot_id": snapshot_id,
            "source": snapshot.source,
            "delta": delta.counts(),
            "rescreened": len(created),
        },
    )
    db.commit()
    for run_id in created:
        enqueue(run_id)
    return created
