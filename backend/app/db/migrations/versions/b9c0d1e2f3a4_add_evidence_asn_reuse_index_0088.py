"""add partial index for cross-submission ASN reuse (ticket 0088)

Indexes evidence(normalized_value, created_at) WHERE field = 'ip_asn' so the
reuse signal no longer scans the whole evidence table. Additive only.

Revision ID: b9c0d1e2f3a4
Revises: a7b8c9d0e1f2
Create Date: 2026-09-25 23:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b9c0d1e2f3a4"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_WHERE = sa.text("field = 'ip_asn'")


def upgrade() -> None:
    op.create_index(
        "ix_evidence_ip_asn_reuse",
        "evidence",
        ["normalized_value", "created_at"],
        postgresql_where=_WHERE,
        sqlite_where=_WHERE,
    )


def downgrade() -> None:
    op.drop_index("ix_evidence_ip_asn_reuse", table_name="evidence")
