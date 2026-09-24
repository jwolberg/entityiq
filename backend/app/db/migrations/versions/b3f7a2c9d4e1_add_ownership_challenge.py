"""add ownership_challenge table (ticket 0003)

Adds the `ownership_challenge` table for optional domain-ownership
verification (DNS TXT / email / HTML meta-tag). A challenge is issued under
a specific verification run; on success its Evidence row (linked via
`evidence_id`) is attached to that same run and contributes a bounded trust
signal — never an authorization decision.

Revision ID: b3f7a2c9d4e1
Revises: a1b2c3d4e5f6
Create Date: 2026-09-24 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3f7a2c9d4e1"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ownership_challenge",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("verification_run_id", sa.String(length=36), nullable=False),
        sa.Column("submission_id", sa.String(length=36), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("method", sa.String(length=32), nullable=False),
        sa.Column("token", sa.String(length=128), nullable=False),
        sa.Column("target", sa.String(length=320), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence_id", sa.String(length=36), nullable=True),
        sa.Column("operator_id", sa.String(length=36), nullable=True),
        sa.Column("api_client_id", sa.String(length=36), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["verification_run_id"],
            ["verification_run.id"],
            name="fk_ownership_challenge_verification_run_id",
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["submission.id"],
            name="fk_ownership_challenge_submission_id",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"], ["evidence.id"], name="fk_ownership_challenge_evidence_id"
        ),
        sa.ForeignKeyConstraint(
            ["operator_id"], ["operator.id"], name="fk_ownership_challenge_operator_id"
        ),
        sa.ForeignKeyConstraint(
            ["api_client_id"],
            ["api_client.id"],
            name="fk_ownership_challenge_api_client_id",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_ownership_challenge_verification_run_id"),
        "ownership_challenge",
        ["verification_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ownership_challenge_submission_id"),
        "ownership_challenge",
        ["submission_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ownership_challenge_operator_id"),
        "ownership_challenge",
        ["operator_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ownership_challenge_api_client_id"),
        "ownership_challenge",
        ["api_client_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_ownership_challenge_api_client_id"), table_name="ownership_challenge"
    )
    op.drop_index(
        op.f("ix_ownership_challenge_operator_id"), table_name="ownership_challenge"
    )
    op.drop_index(
        op.f("ix_ownership_challenge_submission_id"), table_name="ownership_challenge"
    )
    op.drop_index(
        op.f("ix_ownership_challenge_verification_run_id"),
        table_name="ownership_challenge",
    )
    op.drop_table("ownership_challenge")
