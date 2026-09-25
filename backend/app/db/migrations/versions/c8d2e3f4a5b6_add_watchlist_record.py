"""add watchlist_record (IS1-T2, ticket 0030)

Revision ID: c8d2e3f4a5b6
Revises: b7c1d2e3f4a5
Create Date: 2026-09-24 22:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c8d2e3f4a5b6"
down_revision: Union[str, None] = "b7c1d2e3f4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "watchlist_record",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_entry_id", sa.String(length=64), nullable=False),
        sa.Column("primary_name", sa.Text(), nullable=False),
        sa.Column("names", sa.JSON(), nullable=False),
        sa.Column("dobs", sa.JSON(), nullable=False),
        sa.Column("pobs", sa.JSON(), nullable=False),
        sa.Column("nationalities", sa.JSON(), nullable=False),
        sa.Column("documents", sa.JSON(), nullable=False),
        sa.Column("gender", sa.String(length=32), nullable=True),
        sa.Column("program", sa.String(length=128), nullable=True),
        sa.ForeignKeyConstraint(["snapshot_id"], ["list_snapshot.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_watchlist_record_snapshot_id", "watchlist_record", ["snapshot_id"]
    )
    op.create_index(
        "ix_watchlist_record_source_entry_id", "watchlist_record", ["source_entry_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_watchlist_record_source_entry_id", table_name="watchlist_record")
    op.drop_index("ix_watchlist_record_snapshot_id", table_name="watchlist_record")
    op.drop_table("watchlist_record")
