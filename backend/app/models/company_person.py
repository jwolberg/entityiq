"""CompanyPerson — an officer or owner linked to a KYB entity (ticket 0078).

One row per person-relationship per entity, reused across re-runs. The person's
name and other attributes live only in the linked screening subject's
encrypted PII (ADR-0004), so crypto-shredding the subject leaves nothing
readable here. Per-run screening results are Evidence rows
(source "officer_screening") that point back at this row.
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

RELATIONSHIPS = ("officer", "owner")


class CompanyPerson(Base):
    __tablename__ = "company_person"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    entity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("entity.id"), nullable=False, index=True
    )
    # The run that first found this person (later runs reuse the row).
    first_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("verification_run.id"), nullable=True
    )
    screening_subject_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("screening_subject.id"), nullable=False, index=True
    )
    relationship: Mapped[str] = mapped_column(String(16), nullable=False)
    role: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Where the person came from: [{"source": "declared"|"registry", "locator": ...}]
    sources: Mapped[list | None] = mapped_column(JSON, nullable=True)
    ownership_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
