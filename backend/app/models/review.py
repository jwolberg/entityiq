"""Review model — operator decision on a verification run.

Records the operator's verdict: reviewed/approved/rejected/escalated,
freeform notes, and any submitted-field corrections.
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Review(Base):
    __tablename__ = "review"

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
    operator_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("operator.id"), nullable=False, index=True
    )

    # "reviewed" | "approved" | "rejected" | "escalated"
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="reviewed")

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Field-level corrections submitted by the operator: {field_name: corrected_value}
    corrections: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    verification_run: Mapped["VerificationRun"] = relationship(  # noqa: F821
        "VerificationRun", back_populates="review"
    )
    operator: Mapped["Operator"] = relationship(  # noqa: F821
        "Operator", back_populates="reviews"
    )

    def __repr__(self) -> str:
        return (
            f"<Review id={self.id} run={self.verification_run_id}"
            f" status={self.status}>"
        )
