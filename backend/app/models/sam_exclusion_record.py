"""SamExclusionRecord — one active SAM.gov exclusion from one snapshot (0091).

Public list data from the GSA public exclusions extract. Rows are never
edited: a new extract is a new ``list_snapshot`` with new rows. Only the
newest snapshots are kept (plan KTD12); older ones are pruned with their rows.
Individual-classification rows name people; they are stored but never
strong-match a business (plan R6), and their names are redacted for
integration API keys (ADR-0007).
"""

import uuid
from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class SamExclusionRecord(Base):
    __tablename__ = "sam_exclusion_record"
    __table_args__ = (
        Index("ix_sam_exclusion_record_lookup", "snapshot_id", "core_name", "state"),
        Index("ix_sam_exclusion_record_uei", "snapshot_id", "uei"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    snapshot_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("list_snapshot.id"), nullable=False, index=True
    )
    # A 36-char UUID in live files (the 2017 layout documents 9 chars).
    sam_number: Mapped[str] = mapped_column(String(64), nullable=False)
    # Firm | Individual | Special Entity Designation | Vessel
    classification: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    first: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    last: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    # tax_id.core_name of the display name, precomputed for indexed lookups.
    core_name: Mapped[str] = mapped_column(String(255), nullable=False)
    city: Mapped[str] = mapped_column(String(150), nullable=False, default="")
    state: Mapped[str] = mapped_column(String(150), nullable=False, default="")
    country: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    uei: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    cage: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    exclusion_program: Mapped[str] = mapped_column(
        String(64), nullable=False, default=""
    )
    excluding_agency: Mapped[str] = mapped_column(
        String(64), nullable=False, default=""
    )
    exclusion_type: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    active_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    termination_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    termination_indefinite: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    cross_reference: Mapped[str] = mapped_column(Text, nullable=False, default="")

    def __repr__(self) -> str:
        return f"<SamExclusionRecord {self.sam_number} {self.classification}>"
