"""AuditEvent model — append-only log of every auditable action.

This table is append-only by design (ARCHITECTURE § 6).  No application
code should issue UPDATE or DELETE statements against it.  Rows are created
for:
  - Operator actions (sign-in, mark-reviewed, re-run, corrections, exports)
  - Verification run lifecycle events
  - Risk-score changes
  - Evidence sources used
  - Re-analysis triggers

The model intentionally exposes no update/delete helpers.
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class AuditEvent(Base):
    __tablename__ = "audit_event"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    # What happened (e.g. "operator.sign_in", "run.started", "score.changed").
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    # Optional FK to the operator who triggered the event (null for system events).
    operator_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("operator.id"), nullable=True, index=True
    )
    # Optional FK to the verification run this event relates to.
    verification_run_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("verification_run.id"),
        nullable=True,
        index=True,
    )
    # Optional FK to the submission this event relates to.
    submission_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("submission.id"), nullable=True, index=True
    )

    # Structured event payload (event-type-specific fields).
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Human-readable description (optional, for quick log scanning).
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    operator: Mapped["Operator | None"] = relationship(  # noqa: F821
        "Operator", back_populates="audit_events"
    )
    verification_run: Mapped["VerificationRun | None"] = relationship(  # noqa: F821
        "VerificationRun", back_populates="audit_events"
    )

    def __repr__(self) -> str:
        return (
            f"<AuditEvent id={self.id} type={self.event_type!r}"
            f" at={self.occurred_at}>"
        )
