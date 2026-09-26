"""RiskAssessment model — scored output for one verification run.

Stores the four-layer confidence scores and the overall 0-100 risk score.
Links to the evidence rows that contributed to it via the association table
`risk_assessment_evidence`.

Four layers (ARCHITECTURE § 3 / PRD § Core Verification Philosophy):
  - entity_score         Entity legitimacy
  - infrastructure_score Infrastructure legitimacy
  - representation_score Representation confidence
  - risk_score           Fraud/staging risk
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, Float, ForeignKey, String, Table, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

# Association table — many risk_assessments <-> many evidence rows.
risk_assessment_evidence = Table(
    "risk_assessment_evidence",
    Base.metadata,
    Column(
        "risk_assessment_id",
        String(36),
        ForeignKey("risk_assessment.id"),
        primary_key=True,
    ),
    Column(
        "evidence_id",
        String(36),
        ForeignKey("evidence.id"),
        primary_key=True,
    ),
)


class RiskAssessment(Base):
    __tablename__ = "risk_assessment"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    verification_run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("verification_run.id"),
        nullable=False,
        index=True,
    )

    # Four-layer scores [0.0, 100.0]; nullable until computed.
    entity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    infrastructure_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    representation_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Overall 0-100 composite score.
    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Triage tier: "pre_clear" | "review" | "escalate"
    triage_tier: Mapped[str | None] = mapped_column(
        String(32), nullable=True, index=True
    )

    # List of contributing signal objects (each has name, weight, evidence_id).
    contributing_signals: Mapped[list | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    verification_run: Mapped["VerificationRun"] = relationship(  # noqa: F821
        "VerificationRun", back_populates="risk_assessments"
    )
    evidence_items: Mapped[list["Evidence"]] = relationship(  # noqa: F821
        "Evidence",
        secondary=risk_assessment_evidence,
        back_populates="risk_assessments",
    )
    report: Mapped["Report | None"] = relationship(  # noqa: F821
        "Report", back_populates="risk_assessment", uselist=False
    )

    def __repr__(self) -> str:
        return (
            f"<RiskAssessment id={self.id} overall={self.overall_score}"
            f" triage={self.triage_tier}>"
        )
