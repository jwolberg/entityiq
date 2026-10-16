"""add sam_exclusion_record (ticket 0091)

SAM.gov public exclusions extract rows, one set per list_snapshot. Additive
only; runs on SQLite and Postgres.

Revision ID: b8c9d0e1f2a3
Revises: c1d2e3f4a5b6
Create Date: 2026-10-16 20:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sam_exclusion_record",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("sam_number", sa.String(length=64), nullable=False),
        sa.Column("classification", sa.String(length=64), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("first", sa.String(length=128), nullable=False),
        sa.Column("last", sa.String(length=128), nullable=False),
        sa.Column("core_name", sa.String(length=255), nullable=False),
        sa.Column("city", sa.String(length=150), nullable=False),
        sa.Column("state", sa.String(length=150), nullable=False),
        sa.Column("country", sa.String(length=8), nullable=False),
        sa.Column("uei", sa.String(length=32), nullable=False),
        sa.Column("cage", sa.String(length=16), nullable=False),
        sa.Column("exclusion_program", sa.String(length=64), nullable=False),
        sa.Column("excluding_agency", sa.String(length=64), nullable=False),
        sa.Column("exclusion_type", sa.String(length=128), nullable=False),
        sa.Column("active_date", sa.Date(), nullable=True),
        sa.Column("termination_date", sa.Date(), nullable=True),
        sa.Column("termination_indefinite", sa.Boolean(), nullable=False),
        sa.Column("cross_reference", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["list_snapshot.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sam_exclusion_record_snapshot_id", "sam_exclusion_record", ["snapshot_id"]
    )
    op.create_index(
        "ix_sam_exclusion_record_lookup",
        "sam_exclusion_record",
        ["snapshot_id", "core_name", "state"],
    )
    op.create_index(
        "ix_sam_exclusion_record_uei", "sam_exclusion_record", ["snapshot_id", "uei"]
    )


def downgrade() -> None:
    op.drop_index("ix_sam_exclusion_record_uei", table_name="sam_exclusion_record")
    op.drop_index("ix_sam_exclusion_record_lookup", table_name="sam_exclusion_record")
    op.drop_index(
        "ix_sam_exclusion_record_snapshot_id", table_name="sam_exclusion_record"
    )
    op.drop_table("sam_exclusion_record")
