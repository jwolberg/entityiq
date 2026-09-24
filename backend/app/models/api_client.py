"""ApiClient model — a service credential for an integrating system (P2-T12).

Integrating systems (the host platform's onboarding flow and other internal systems)
authenticate at the API boundary with an API key (ARCHITECTURE § 5).  Keys
are never stored in plaintext: only a PBKDF2 hash and a short non-secret
prefix (for identification in logs / UI) are persisted.

Submissions and report pulls made with a key are attributable to the calling
system for audit (the `api_client_id` columns on `submission` and
`audit_event`).
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ApiClient(Base):
    __tablename__ = "api_client"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    # Human-readable system name, e.g. "platform-onboarding".  Unique.
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)

    # Non-secret key prefix (e.g. "eiq_ab12cd34") used to look up the row
    # before verifying the full key against key_hash.  Indexed + unique.
    key_prefix: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True, index=True
    )

    # PBKDF2-HMAC-SHA256 hash of the full key (same format as operator passwords).
    key_hash: Mapped[str] = mapped_column(Text, nullable=False)

    # Inactive keys are rejected (soft revocation).
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Updated on each successful authentication (observability).
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"<ApiClient id={self.id} name={self.name!r} "
            f"prefix={self.key_prefix!r} active={self.active}>"
        )
