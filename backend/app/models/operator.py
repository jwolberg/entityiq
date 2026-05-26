"""Operator model — an operator or lead account.

Roles:
  operator — may review, correct, and re-run.
  lead     — operator privileges plus oversight and audit access.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Operator(Base):
    __tablename__ = "operator"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(
        String(32), nullable=False, default="operator"
    )  # "operator" | "lead"
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # back-populated by Review
    reviews: Mapped[list["Review"]] = relationship(  # noqa: F821
        "Review", back_populates="operator", cascade="all, delete-orphan"
    )
    # back-populated by AuditEvent
    audit_events: Mapped[list["AuditEvent"]] = relationship(  # noqa: F821
        "AuditEvent", back_populates="operator"
    )

    def __repr__(self) -> str:
        return f"<Operator id={self.id} email={self.email} role={self.role}>"
