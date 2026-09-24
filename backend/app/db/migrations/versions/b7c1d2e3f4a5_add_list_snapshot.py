"""add list_snapshot (IS1-T1, ticket 0029)

Versioned record of each watchlist ingest, shared by business verification and
individual screening.

Revision ID: b7c1d2e3f4a5
Revises: b3f7a2c9d4e1
Create Date: 2026-09-24 22:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7c1d2e3f4a5"
down_revision: Union[str, None] = "b3f7a2c9d4e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "list_snapshot",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_list_snapshot_source", "list_snapshot", ["source"])
    op.create_index(
        "ix_list_snapshot_content_sha256", "list_snapshot", ["content_sha256"]
    )


def downgrade() -> None:
    op.drop_index("ix_list_snapshot_content_sha256", table_name="list_snapshot")
    op.drop_index("ix_list_snapshot_source", table_name="list_snapshot")
    op.drop_table("list_snapshot")
