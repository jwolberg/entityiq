"""append-only triggers on audit and decision tables (IS1-T4, ticket 0032)

PRD-IDV F14 / C4: UPDATE and DELETE are rejected by the database itself on
audit_event (both offerings), screening_decision and screening_disposition.
The retention jobs never touch these tables (ADR-0002; crypto-shredding
destroys keys, not rows).

Revision ID: e1f2a3b4c5d6
Revises: d9e3f4a5b6c7
Create Date: 2026-09-24 23:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, None] = "d9e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("audit_event", "screening_decision", "screening_disposition")


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE OR REPLACE FUNCTION entityiq_reject_append_only_change()
            RETURNS trigger AS $$
            BEGIN
              RAISE EXCEPTION 'append-only table %: % not allowed',
                TG_TABLE_NAME, TG_OP;
            END;
            $$ LANGUAGE plpgsql;
            """
        )
        for table in TABLES:
            op.execute(
                f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE "
                f"ON {table} FOR EACH ROW "
                "EXECUTE FUNCTION entityiq_reject_append_only_change()"
            )
    else:
        for table in TABLES:
            for action in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER {table}_append_only_{action.lower()} "
                    f"BEFORE {action} ON {table} "
                    f"BEGIN SELECT RAISE(ABORT, 'append-only table {table}: "
                    f"{action} not allowed'); END"
                )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        for table in TABLES:
            op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
        op.execute("DROP FUNCTION IF EXISTS entityiq_reject_append_only_change()")
    else:
        for table in TABLES:
            for action in ("update", "delete"):
                op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only_{action}")
