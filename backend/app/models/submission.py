"""Submission model — immutable record of a raw registration input.

Captures all submitted fields plus network metadata captured server-side
(source IP, user agent, timestamp, forwarded headers, endpoint).  Once
written, this row is never mutated.
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Submission(Base):
    __tablename__ = "submission"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    # Optional idempotency key supplied by the caller to tolerate retries.
    idempotency_key: Mapped[str | None] = mapped_column(
        String(128), nullable=True, unique=True, index=True
    )

    # Required submitted fields (ARCHITECTURE § 3 / PRD § Inputs).
    company_name: Mapped[str] = mapped_column(String(512), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    work_email: Mapped[str] = mapped_column(String(320), nullable=False)
    # ISO 3166-1 alpha-2
    country: Mapped[str] = mapped_column(String(2), nullable=False)

    # Optional submitted fields.
    tax_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    billing_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requester_full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Catch-all for additional submitted metadata (JSON object).
    extra_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Network metadata — captured server-side, not from the request body.
    source_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    forwarded_headers: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    endpoint: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # FK — entity resolved from this submission (nullable until resolved).
    entity_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("entity.id"), nullable=True, index=True
    )

    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    entity: Mapped["Entity | None"] = relationship(  # noqa: F821
        "Entity", back_populates="submissions"
    )
    verification_runs: Mapped[list["VerificationRun"]] = relationship(  # noqa: F821
        "VerificationRun", back_populates="submission"
    )

    def __repr__(self) -> str:
        return (
            f"<Submission id={self.id} company={self.company_name!r}"
            f" domain={self.domain!r}>"
        )
