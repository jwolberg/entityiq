"""add api_client table + per-system attribution columns (P2-T12)

Adds the `api_client` service-credential table and nullable `api_client_id`
foreign keys on `submission` and `audit_event` so submissions and audited
actions made by integrating systems are attributable.

Revision ID: a1b2c3d4e5f6
Revises: 4d2b0864b0af
Create Date: 2026-05-31 10:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "4d2b0864b0af"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_client",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("key_prefix", sa.String(length=32), nullable=False),
        sa.Column("key_hash", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("key_prefix"),
    )
    op.create_index(
        op.f("ix_api_client_key_prefix"), "api_client", ["key_prefix"], unique=True
    )

    # Batch mode so the ALTERs work on SQLite (copy-and-move) as well as Postgres.
    with op.batch_alter_table("submission") as batch_op:
        batch_op.add_column(
            sa.Column("api_client_id", sa.String(length=36), nullable=True)
        )
        batch_op.create_index(
            batch_op.f("ix_submission_api_client_id"),
            ["api_client_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            "fk_submission_api_client_id",
            "api_client",
            ["api_client_id"],
            ["id"],
        )

    with op.batch_alter_table("audit_event") as batch_op:
        batch_op.add_column(
            sa.Column("api_client_id", sa.String(length=36), nullable=True)
        )
        batch_op.create_index(
            batch_op.f("ix_audit_event_api_client_id"),
            ["api_client_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            "fk_audit_event_api_client_id",
            "api_client",
            ["api_client_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("audit_event") as batch_op:
        batch_op.drop_constraint("fk_audit_event_api_client_id", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_audit_event_api_client_id"))
        batch_op.drop_column("api_client_id")

    with op.batch_alter_table("submission") as batch_op:
        batch_op.drop_constraint("fk_submission_api_client_id", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_submission_api_client_id"))
        batch_op.drop_column("api_client_id")

    op.drop_index(op.f("ix_api_client_key_prefix"), table_name="api_client")
    op.drop_table("api_client")
