"""OwnershipChallenge model — domain-ownership verification challenge (ticket 0003).

Tracks one issued challenge token for a submission's domain and its
verification outcome (DNS TXT / email / HTML meta-tag — same issue/verify
contract for all three methods). See PRD § Domain Ownership Verification and
USERS § 4.

INVARIANT: a verified challenge only ever contributes a bounded TRUST signal
(app.scoring.signals) — it never implies organizational authorization, and it
is attached to the run it was issued under (evidence_id), not retroactively
applied to other runs. An absent/incorrect token leaves the challenge
"pending" and contributes no signal at all.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class OwnershipChallenge(Base):
    __tablename__ = "ownership_challenge"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    # The run this challenge was issued under; verified evidence is attached
    # to this same run (Evidence.verification_run_id is non-nullable).
    verification_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("verification_run.id"), nullable=False, index=True
    )
    submission_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("submission.id"), nullable=False, index=True
    )
    # Denormalized from submission.domain at issue time (the submission is
    # immutable, so this never drifts) — avoids a join on every read.
    domain: Mapped[str] = mapped_column(String(255), nullable=False)

    # "dns_txt" | "email" | "html_meta"
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    token: Mapped[str] = mapped_column(String(128), nullable=False)
    # Method-specific target: the mailbox address for "email"; unused otherwise.
    target: Mapped[str | None] = mapped_column(String(320), nullable=True)

    # "pending" | "verified"
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")

    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Set once verified — the Evidence row carrying the bounded trust signal.
    evidence_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("evidence.id"), nullable=True
    )

    # Attribution — who issued the challenge (operator XOR integrating system).
    operator_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("operator.id"), nullable=True, index=True
    )
    api_client_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("api_client.id"), nullable=True, index=True
    )

    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    submission: Mapped["Submission"] = relationship("Submission")  # noqa: F821
    verification_run: Mapped["VerificationRun"] = relationship(  # noqa: F821
        "VerificationRun"
    )

    def __repr__(self) -> str:
        return (
            f"<OwnershipChallenge id={self.id} domain={self.domain!r}"
            f" method={self.method!r} status={self.status!r}>"
        )
