"""ListSnapshot — one ingest of one watchlist (IS1-T1, ticket 0029).

Content is identified by its SHA-256, so identical content across ingests is
recognizable (delta detection, replay). A row is written per ingest.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ListSnapshot(Base):
    __tablename__ = "list_snapshot"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # e.g. "ofac_sdn", "un_consolidated", "eu_fsf", "uk_ofsi".
    source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<ListSnapshot {self.source} {self.content_sha256[:12]} "
            f"n={self.record_count}>"
        )
