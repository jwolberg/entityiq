"""Append-only audit event recorder (P1-T9).

Every operator action MUST be recorded here before (or immediately after)
the action takes effect.  Audit events are never updated or deleted —
this is the append-only contract (ARCHITECTURE § 6).

Usage:
    from app.audit.recorder import record_event
    record_event(
        db=db,
        event_type="operator.mark_reviewed",
        operator_id=operator.id,
        verification_run_id=run_id,
        payload={"notes": "...", "status": "reviewed"},
        description="Operator marked run as reviewed.",
    )

AuditEvent rows:
  - Created via INSERT only.
  - This module exposes NO update or delete helpers.
  - The model class (app.models.audit_event.AuditEvent) also exposes no
    update/delete methods (ARCHITECTURE § 6).

Mutation protection:
  - record_event() is the only public function.
  - There is intentionally no update_event() or delete_event().
  - DB-level enforcement (triggers / RLS) is deferred to P3-T3.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def record_event(
    db: "Session",
    event_type: str,
    *,
    operator_id: str | None = None,
    verification_run_id: str | None = None,
    submission_id: str | None = None,
    payload: dict | None = None,
    description: str | None = None,
) -> str:
    """Persist a single AuditEvent row.

    Args:
        db:                  Open SQLAlchemy session.
        event_type:          Dotted event name, e.g. "operator.sign_in".
        operator_id:         FK to operator who triggered the event (None for system).
        verification_run_id: FK to related run (if applicable).
        submission_id:       FK to related submission (if applicable).
        payload:             Structured event-specific data (JSON).
        description:         Human-readable summary for log scanning.

    Returns:
        The new AuditEvent.id.

    This function ONLY inserts — no updates, no deletes.
    """
    from app.models.audit_event import AuditEvent  # noqa: PLC0415

    event = AuditEvent(
        event_type=event_type,
        operator_id=operator_id,
        verification_run_id=verification_run_id,
        submission_id=submission_id,
        payload=payload or {},
        description=description,
        occurred_at=datetime.now(tz=timezone.utc),
    )
    db.add(event)
    db.commit()

    logger.info(
        "audit: %s op=%s run=%s",
        event_type,
        operator_id,
        verification_run_id,
    )
    return event.id
