"""Evidence model — one finding from one source.

The unit of explainability: every score in RiskAssessment traces back to
evidence rows (ARCHITECTURE § 3).

Uses generic JSON (not JSONB) so the schema builds on SQLite in tests.
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    verification_run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("verification_run.id"),
        nullable=False,
        index=True,
    )

    # Source identifier (e.g. "opencorporates", "whois", "ipinfo").
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    # Tier 1 | 2 | 3 per ARCHITECTURE § 4.
    tier: Mapped[int] = mapped_column(nullable=False)
    # Which submitted field this evidence relates to (e.g. "company_name").
    field: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Raw value returned by the source.
    raw_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Normalized/canonicalized value after pipeline processing.
    normalized_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Confidence score [0.0, 1.0] for this piece of evidence.
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Full structured payload from the source (preserved for auditability).
    raw_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Source attribution metadata (e.g. URL, API endpoint, version).
    attribution: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    verification_run: Mapped["VerificationRun"] = relationship(  # noqa: F821
        "VerificationRun", back_populates="evidence"
    )
    field_comparisons: Mapped[list["FieldComparison"]] = relationship(  # noqa: F821
        "FieldComparison", back_populates="evidence"
    )
    risk_assessments: Mapped[list["RiskAssessment"]] = relationship(  # noqa: F821
        "RiskAssessment",
        secondary="risk_assessment_evidence",
        back_populates="evidence_items",
    )

    def __repr__(self) -> str:
        return (
            f"<Evidence id={self.id} source={self.source!r}"
            f" field={self.field!r} tier={self.tier}>"
        )
