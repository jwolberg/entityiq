"""Report model — queryable composite of a verification run.

Serves both the operator FE and external API consumers.  A report is
assembled after the risk assessment is complete and is updated as partial
results arrive (status field reflects run completeness).
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Report(Base):
    __tablename__ = "report"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    verification_run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("verification_run.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    risk_assessment_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("risk_assessment.id"), nullable=True, index=True
    )

    # "pending" | "partial" | "complete" | "failed"
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")

    # Per-section statuses: {section_name: "pending"|"complete"|"unavailable"}
    section_statuses: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Denormalized summary payload for fast API reads.
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    verification_run: Mapped["VerificationRun"] = relationship(  # noqa: F821
        "VerificationRun", back_populates="report"
    )
    risk_assessment: Mapped["RiskAssessment | None"] = relationship(  # noqa: F821
        "RiskAssessment", back_populates="report"
    )

    def __repr__(self) -> str:
        return f"<Report id={self.id} status={self.status}>"
