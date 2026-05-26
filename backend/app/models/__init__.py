"""ORM model registry.

Import all models here so that:
  1. Alembic autogenerate can discover them via Base.metadata.
  2. Application code has a single convenient import path.
"""

from app.models.audit_event import AuditEvent
from app.models.entity import Entity
from app.models.evidence import Evidence
from app.models.field_comparison import FieldComparison
from app.models.operator import Operator
from app.models.report import Report
from app.models.review import Review
from app.models.risk_assessment import RiskAssessment, risk_assessment_evidence
from app.models.submission import Submission
from app.models.verification_run import VerificationRun

__all__ = [
    "AuditEvent",
    "Entity",
    "Evidence",
    "FieldComparison",
    "Operator",
    "Report",
    "Review",
    "RiskAssessment",
    "risk_assessment_evidence",
    "Submission",
    "VerificationRun",
]
