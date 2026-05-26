"""Entity model — the canonical company under analysis.

Links submissions and verification runs for the same real-world business.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Entity(Base):
    __tablename__ = "entity"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # Canonical display name derived from submissions / registry lookups.
    canonical_name: Mapped[str] = mapped_column(String(512), nullable=False)
    # Primary domain considered authoritative for this entity.
    canonical_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    submissions: Mapped[list["Submission"]] = relationship(  # noqa: F821
        "Submission", back_populates="entity"
    )
    verification_runs: Mapped[list["VerificationRun"]] = relationship(  # noqa: F821
        "VerificationRun", back_populates="entity"
    )

    def __repr__(self) -> str:
        return f"<Entity id={self.id} name={self.canonical_name!r}>"
