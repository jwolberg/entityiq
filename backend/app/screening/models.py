"""Individual screening data model (IS1-T3, ticket 0031; PRD-IDV §[15]).

Tables are prefixed ``screening_`` so they're namespaced away from KYB.

- Subject PII is never stored in plaintext. ``pii_ciphertext`` holds the
  subject's attributes encrypted with a per-subject data key (IS1-T5 /
  ticket 0033). The columns are nullable so the retention job can crypto-shred.
- ``screening_decision`` and ``screening_disposition`` are append-only
  (F14): enforced in code here (no update helpers) and at the database layer
  by IS1-T4 / ticket 0032.
- A decision freezes everything replay needs (F15/F16): the input bundle
  (encrypted, since it contains subject PII), list snapshot ids, rule
  version, every term and the thresholds applied.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.db.session import Base

# C1: screening's own vocabulary, separate from KYB's triage tiers.
SYSTEM_DISPOSITIONS = ("CLEAR", "REVIEW", "MATCH")
HUMAN_DISPOSITIONS = ("CLEAR", "MATCH")
RUN_TRIGGERS = ("intake", "monitoring", "kyb_officer")


def _uuid() -> str:
    return str(uuid.uuid4())


class ScreeningSubject(Base):
    """The person as submitted. Attributes live in the encrypted blob."""

    __tablename__ = "screening_subject"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    pii_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    pii_nonce: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # C9: optional, one-directional link to a KYB entity (officer/owner).
    kyb_entity_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("entity.id"), nullable=True, index=True
    )
    api_client_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("api_client.id"), nullable=True, index=True
    )
    created_by_operator_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("operator.id"), nullable=True
    )
    # Retention clock (C5): 5 years after the relationship ends.
    relationship_ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    shredded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ScreeningSubjectKey(Base):
    """A subject's data key, wrapped by the master key (ADR-0004).

    Not append-only: crypto-shredding sets ``wrapped_key`` to NULL.
    """

    __tablename__ = "screening_subject_key"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    subject_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("screening_subject.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    wrapped_key: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    destroyed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ScreeningRuleVersion(Base):
    """Versioned weights + thresholds (F13). Rows are never edited."""

    __tablename__ = "screening_rule_version"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    version: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    config: Mapped[dict] = mapped_column(JSON, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_operator_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("operator.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ScreeningRun(Base):
    """One screening of a subject against a set of list snapshots."""

    __tablename__ = "screening_run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    subject_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("screening_subject.id"), nullable=False, index=True
    )
    rule_version_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("screening_rule_version.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    trigger: Mapped[str] = mapped_column(String(32), nullable=False, default="intake")
    # Monitoring runs (F17) record the snapshot that triggered them.
    triggered_by_snapshot_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("list_snapshot.id"), nullable=True
    )
    prior_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("screening_run.id"), nullable=True
    )
    source_availability: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ScreeningCandidate(Base):
    """Blocking output: a watchlist record considered for this run."""

    __tablename__ = "screening_candidate"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("screening_run.id"), nullable=False, index=True
    )
    watchlist_record_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("watchlist_record.id"), nullable=False
    )
    blocking_keys: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)


class ScreeningClaim(Base):
    """A typed claim with provenance (F2). Claims without a source are dropped."""

    __tablename__ = "screening_claim"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("screening_run.id"), nullable=False, index=True
    )
    candidate_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("screening_candidate.id"), nullable=True
    )
    # "subject" or "record": which side of the comparison the claim is about.
    about: Mapped[str] = mapped_column(String(16), nullable=False)
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[dict | list | str | None] = mapped_column(JSON, nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    locator: Mapped[str] = mapped_column(Text, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ScreeningTerm(Base):
    """A named, weighted scoring term citing ≥1 claim (F7)."""

    __tablename__ = "screening_term"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("screening_run.id"), nullable=False, index=True
    )
    candidate_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("screening_candidate.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    claim_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    @validates("claim_ids")
    def _cites_a_claim(self, _key: str, value: list | None) -> list:
        if not value:
            raise ValueError("A screening term must cite at least one claim (F7).")
        return value


class ScreeningDecision(Base):
    """The frozen, replayable system decision for a run. Append-only."""

    __tablename__ = "screening_decision"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("screening_run.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    rule_version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("screening_rule_version.id"), nullable=False
    )
    system_disposition: Mapped[str] = mapped_column(String(16), nullable=False)
    # True when CLEAR closed the run with no human (C2).
    auto_closed: Mapped[bool] = mapped_column(nullable=False, default=False)
    snapshot_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    thresholds: Mapped[dict] = mapped_column(JSON, nullable=False)
    terms: Mapped[list] = mapped_column(JSON, nullable=False)
    top_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Frozen inputs contain subject PII, so they're encrypted with its key.
    frozen_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    frozen_nonce: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    normalizer_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ScreeningDisposition(Base):
    """A human disposition of a decision. Append-only; history is kept."""

    __tablename__ = "screening_disposition"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    decision_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("screening_decision.id"), nullable=False, index=True
    )
    disposition: Mapped[str] = mapped_column(String(16), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    operator_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("operator.id"), nullable=False
    )
    # Set in Python (microseconds) so history orders reliably even when two
    # dispositions land within the same second; server default as fallback.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(tz=timezone.utc),
        server_default=func.now(),
        nullable=False,
    )
