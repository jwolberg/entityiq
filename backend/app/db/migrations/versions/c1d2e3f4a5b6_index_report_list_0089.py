"""index the report list's tier filter and newest-first order (ticket 0089)

GET /reports filters by risk_assessment.triage_tier and pages by
report.created_at DESC. Additive only.

Revision ID: c1d2e3f4a5b6
Revises: b9c0d1e2f3a4
Create Date: 2026-09-25 23:30:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, None] = "b9c0d1e2f3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_risk_assessment_triage_tier", "risk_assessment", ["triage_tier"]
    )
    op.create_index("ix_report_created_at", "report", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_report_created_at", table_name="report")
    op.drop_index("ix_risk_assessment_triage_tier", table_name="risk_assessment")
