"""WatchlistRecord — one natural person from one list snapshot (IS1-T2, 0030).

Public list data, not subject PII. The shape is documented in
app/lists/persons.py. Rows are never edited: a new list version is a new
snapshot with new rows.
"""

import uuid

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class WatchlistRecord(Base):
    __tablename__ = "watchlist_record"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    snapshot_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("list_snapshot.id"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_entry_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    primary_name: Mapped[str] = mapped_column(Text, nullable=False)
    names: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    dobs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    pobs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    nationalities: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    documents: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    gender: Mapped[str | None] = mapped_column(String(32), nullable=True)
    program: Mapped[str | None] = mapped_column(String(128), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<WatchlistRecord {self.source}:{self.source_entry_id} "
            f"{self.primary_name!r}>"
        )
