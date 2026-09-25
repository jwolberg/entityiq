"""add company_person and submission.declared_people (ticket 0078)

Officer/owner screening: link KYB entities to screening subjects, and keep
the officers/owners a submitter declared. Additive only.

Revision ID: a7b8c9d0e1f2
Revises: f2a3b4c5d6e7
Create Date: 2026-09-25 22:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("submission") as batch:
        batch.add_column(sa.Column("declared_people", sa.JSON(), nullable=True))
    op.create_table(
        "company_person",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("entity_id", sa.String(length=36), nullable=False),
        sa.Column("first_run_id", sa.String(length=36), nullable=True),
        sa.Column("screening_subject_id", sa.String(length=36), nullable=False),
        sa.Column("relationship", sa.String(length=16), nullable=False),
        sa.Column("role", sa.String(length=128), nullable=True),
        sa.Column("sources", sa.JSON(), nullable=True),
        sa.Column("ownership_pct", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["entity_id"], ["entity.id"]),
        sa.ForeignKeyConstraint(["first_run_id"], ["verification_run.id"]),
        sa.ForeignKeyConstraint(["screening_subject_id"], ["screening_subject.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_company_person_entity_id"), "company_person", ["entity_id"]
    )
    op.create_index(
        op.f("ix_company_person_screening_subject_id"),
        "company_person",
        ["screening_subject_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_company_person_screening_subject_id"), table_name="company_person"
    )
    op.drop_index(op.f("ix_company_person_entity_id"), table_name="company_person")
    op.drop_table("company_person")
    with op.batch_alter_table("submission") as batch:
        batch.drop_column("declared_people")
