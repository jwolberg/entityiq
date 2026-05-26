"""VerificationRun model — one analysis execution for a submission/entity.

A new run supersedes the prior one for the same entity.  Prior runs are
retained for audit history and score-change tracking (ARCHITECTURE § 2,
§ 6).  The `supersedes_id` self-reference carries the chain.
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class VerificationRun(Base):
    __tablename__ = "verification_run"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    # FKs to the submission that triggered this run and the entity being analysed.
    submission_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("submission.id"), nullable=False, index=True
    )
    entity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("entity.id"), nullable=False, index=True
    )

    # Self-referential link to the run this one supersedes (nullable for first run).
    supersedes_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("verification_run.id"),
        nullable=True,
        index=True,
    )

    # Overall run lifecycle status.
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )  # pending | running | complete | failed

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Per-source availability summary: {source_name: "complete"|"unavailable"|...}
    source_availability: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Optional human-readable reason for failure.
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- relationships ---
    submission: Mapped["Submission"] = relationship(  # noqa: F821
        "Submission", back_populates="verification_runs"
    )
    entity: Mapped["Entity"] = relationship(  # noqa: F821
        "Entity", back_populates="verification_runs"
    )

    # Self-reference: the run superseded by this one (many superseded by one).
    superseded_run: Mapped["VerificationRun | None"] = relationship(
        "VerificationRun",
        foreign_keys=[supersedes_id],
        remote_side="VerificationRun.id",
        back_populates="superseding_run",
    )
    # The run that supersedes this one (one supersedes many is unlikely in practice,
    # but the relationship is modelled as a list to be safe at the DB level).
    superseding_run: Mapped["VerificationRun | None"] = relationship(
        "VerificationRun",
        foreign_keys=[supersedes_id],
        back_populates="superseded_run",
        uselist=False,
    )

    evidence: Mapped[list["Evidence"]] = relationship(  # noqa: F821
        "Evidence", back_populates="verification_run", cascade="all, delete-orphan"
    )
    field_comparisons: Mapped[list["FieldComparison"]] = relationship(  # noqa: F821
        "FieldComparison",
        back_populates="verification_run",
        cascade="all, delete-orphan",
    )
    risk_assessments: Mapped[list["RiskAssessment"]] = relationship(  # noqa: F821
        "RiskAssessment",
        back_populates="verification_run",
        cascade="all, delete-orphan",
    )
    report: Mapped["Report | None"] = relationship(  # noqa: F821
        "Report", back_populates="verification_run", uselist=False
    )
    review: Mapped["Review | None"] = relationship(  # noqa: F821
        "Review", back_populates="verification_run", uselist=False
    )
    audit_events: Mapped[list["AuditEvent"]] = relationship(  # noqa: F821
        "AuditEvent", back_populates="verification_run"
    )

    def __repr__(self) -> str:
        return (
            f"<VerificationRun id={self.id} entity={self.entity_id}"
            f" status={self.status}>"
        )
