"""Crypto-shred retention for screening subjects (IS1-T5, ticket 0033; ADR-0004).

Subjects whose relationship ended more than ENTITYIQ_SCREENING_RETENTION_DAYS
ago (default 1825, about 5 years, the common AML record-keeping period) are
crypto-shredded. Decision rows are never modified. Each run writes one audit
event with counts only.

Run it on a schedule: ``python -m app.screening.retention``.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from app.audit.recorder import record_event
from app.screening.crypto import shred_subject, utcnow
from app.screening.models import ScreeningSubject

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

DEFAULT_RETENTION_DAYS = 1825


def retention_days() -> int:
    return int(
        os.environ.get("ENTITYIQ_SCREENING_RETENTION_DAYS", DEFAULT_RETENTION_DAYS)
    )


def _aware(value: datetime) -> datetime:
    # SQLite returns naive datetimes; they are stored as UTC.
    from datetime import timezone  # noqa: PLC0415

    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def shred_expired(db: "Session", now: datetime | None = None) -> int:
    """Shred every subject past the retention window. Returns the count."""
    now = now or utcnow()
    cutoff = now - timedelta(days=retention_days())
    due = [
        s
        for s in db.query(ScreeningSubject)
        .filter(
            ScreeningSubject.shredded_at.is_(None),
            ScreeningSubject.relationship_ended_at.is_not(None),
        )
        .all()
        if _aware(s.relationship_ended_at) <= cutoff
    ]
    for subject in due:
        shred_subject(db, subject, now)
    record_event(
        db,
        "screening.retention_shred",
        payload={"shredded": len(due), "retention_days": retention_days()},
    )
    db.commit()
    return len(due)


def main() -> None:  # pragma: no cover - thin CLI wrapper
    from app.db.session import SessionLocal  # noqa: PLC0415

    db = SessionLocal()
    try:
        print(f"shredded {shred_expired(db)} subject(s)")
    finally:
        db.close()


if __name__ == "__main__":  # pragma: no cover
    main()
