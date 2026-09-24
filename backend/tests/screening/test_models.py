"""Screening data model (IS1-T3, ticket 0031)."""

from __future__ import annotations

import ast
from pathlib import Path

from sqlalchemy import inspect as sa_inspect

import app.models  # noqa: F401
from app.db.session import Base
from app.screening import models as sm

_APP = Path(__file__).resolve().parents[2] / "app"

SCREENING_TABLES = {
    "screening_subject",
    "screening_rule_version",
    "screening_run",
    "screening_candidate",
    "screening_claim",
    "screening_term",
    "screening_decision",
    "screening_disposition",
}


def test_all_screening_tables_are_registered():
    assert SCREENING_TABLES <= set(Base.metadata.tables)


def test_subject_pii_columns_are_nullable_for_retention():
    cols = {c.name: c for c in sa_inspect(sm.ScreeningSubject).columns}
    for name in ("pii_ciphertext", "pii_nonce"):
        assert cols[name].nullable, name
    # There is no plaintext PII column on the subject.
    assert not {"full_name", "name", "dob", "date_of_birth"} & set(cols)


def test_decision_references_subject_only_through_the_run():
    cols = {c.name for c in sa_inspect(sm.ScreeningDecision).columns}
    assert "run_id" in cols
    assert not {"full_name", "name", "dob"} & cols


def test_disposition_vocabulary_is_separate_from_kyb():
    """C1: CLEAR / REVIEW / MATCH for screening; KYB keeps its own tiers."""
    assert sm.SYSTEM_DISPOSITIONS == ("CLEAR", "REVIEW", "MATCH")
    assert sm.HUMAN_DISPOSITIONS == ("CLEAR", "MATCH")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
        elif isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
    return mods


def test_kyb_code_never_imports_screening():
    for sub in ("adapters", "pipeline", "scoring", "api", "audit"):
        for path in (_APP / sub).rglob("*.py"):
            bad = {m for m in _imports(path) if m.startswith("app.screening")}
            assert not bad, f"{path} imports {bad}"


# Shared platform modules the screening offering may use.
_ALLOWED_SHARED = (
    "app.db",
    "app.lists",
    "app.auth",
    "app.audit",
    "app.pipeline.orchestrator",
    "app.pipeline.base",
    "app.adapters.base",
    "app.adapters.retry",
    "app.models.list_snapshot",
    "app.models.watchlist_record",
    "app.models.operator",
    "app.models.api_client",
    "app.models.audit_event",
    "app.models.entity",
    "app.worker",
)


def test_screening_imports_only_shared_platform_modules():
    for path in (_APP / "screening").rglob("*.py"):
        for mod in _imports(path):
            if not mod.startswith("app.") or mod.startswith("app.screening"):
                continue
            assert mod.startswith(_ALLOWED_SHARED), f"{path} imports {mod}"
