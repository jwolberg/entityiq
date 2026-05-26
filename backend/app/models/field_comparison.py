"""FieldComparison model — submitted vs. discovered value for a field.

Match status values: "match" | "mismatch" | "unverified"
Used in the operator UI's diff view (ARCHITECTURE § 3).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class FieldComparison(Base):
    __tablename__ = "field_comparison"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    verification_run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("verification_run.id"),
        nullable=False,
        index=True,
    )
    # The evidence row that provided the discovered value.
    evidence_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("evidence.id"), nullable=True, index=True
    )

    # Which field is being compared (e.g. "company_name", "address").
    field_name: Mapped[str] = mapped_column(String(128), nullable=False)

    submitted_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    discovered_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    # "match" | "mismatch" | "unverified"
    match_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unverified"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    verification_run: Mapped["VerificationRun"] = relationship(  # noqa: F821
        "VerificationRun", back_populates="field_comparisons"
    )
    evidence: Mapped["Evidence | None"] = relationship(  # noqa: F821
        "Evidence", back_populates="field_comparisons"
    )

    def __repr__(self) -> str:
        return (
            f"<FieldComparison id={self.id} field={self.field_name!r}"
            f" status={self.match_status}>"
        )
