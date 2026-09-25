"""Append-only enforced by the database (IS1-T4, ticket 0032; PRD-IDV F14, C4).

Runs against a SQLite database built by the real migrations, and against
Postgres when TEST_POSTGRES_URL is set (CI provides it).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.models.audit_event import AuditEvent
from app.models.operator import Operator
from app.screening.models import (
    ScreeningDecision,
    ScreeningDisposition,
    ScreeningRuleVersion,
    ScreeningRun,
    ScreeningSubject,
)

_BACKEND = Path(__file__).resolve().parents[2]
_TABLES = ("audit_event", "screening_decision", "screening_disposition")


def _migrated(url: str, monkeypatch) -> Config:
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config(str(_BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND / "app/db/migrations"))
    command.upgrade(cfg, "head")
    return cfg


def _urls(tmp_path) -> list[str]:
    urls = [f"sqlite:///{tmp_path / 'append.db'}"]
    if os.environ.get("TEST_POSTGRES_URL"):
        urls.append(os.environ["TEST_POSTGRES_URL"])
    return urls


def _seed(db: Session) -> dict[str, str]:
    op = Operator(
        email=f"{uuid.uuid4()}@example.test",
        full_name="Lead",
        role="lead",
        password_hash="x",
    )
    db.add(op)
    db.flush()
    subject = ScreeningSubject()
    rule = ScreeningRuleVersion(version=int(uuid.uuid4().int % 10**9), config={})
    db.add_all([subject, rule])
    db.flush()
    run = ScreeningRun(subject_id=subject.id, rule_version_id=rule.id)
    db.add(run)
    db.flush()
    decision = ScreeningDecision(
        run_id=run.id,
        rule_version_id=rule.id,
        system_disposition="REVIEW",
        snapshot_ids=[],
        thresholds={},
        terms=[],
        normalizer_version="v1",
    )
    db.add(decision)
    db.flush()
    disposition = ScreeningDisposition(
        decision_id=decision.id, disposition="CLEAR", operator_id=op.id
    )
    event = AuditEvent(event_type="test.append_only", operator_id=op.id)
    db.add_all([disposition, event])
    db.commit()
    return {
        "audit_event": event.id,
        "screening_decision": decision.id,
        "screening_disposition": disposition.id,
    }


@pytest.mark.parametrize("which", ["sqlite", "postgres"])
def test_update_and_delete_are_rejected_inserts_still_work(
    which, tmp_path, monkeypatch
):
    urls = _urls(tmp_path)
    if which == "postgres" and len(urls) < 2:
        pytest.skip("TEST_POSTGRES_URL not set")
    url = urls[0] if which == "sqlite" else urls[1]
    cfg = _migrated(url, monkeypatch)
    engine = create_engine(url)
    try:
        with Session(engine) as db:
            ids = _seed(db)  # inserts succeed

        for table in _TABLES:
            for stmt in (
                f"UPDATE {table} SET id = id WHERE id = :id",
                f"DELETE FROM {table} WHERE id = :id",
            ):
                with engine.begin() as conn, pytest.raises(DBAPIError) as err:
                    conn.execute(text(stmt), {"id": ids[table]})
                assert "append-only" in str(err.value).lower(), (table, stmt)
    finally:
        engine.dispose()
        if which == "postgres":
            command.downgrade(cfg, "base")


def test_triggers_are_removed_on_downgrade(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'down.db'}"
    cfg = _migrated(url, monkeypatch)
    command.downgrade(cfg, "d9e3f4a5b6c7")  # the revision before the triggers
    engine = create_engine(url)
    with engine.connect() as conn:
        names = {
            r[0]
            for r in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            )
        }
    engine.dispose()
    assert not any("append_only" in n for n in names)


@pytest.mark.parametrize("which", ["sqlite", "postgres"])
def test_retention_jobs_run_cleanly_with_the_triggers_in_place(
    which, tmp_path, monkeypatch
):
    """KYB retention and crypto-shredding never UPDATE/DELETE append-only rows.

    If either did, the trigger would abort the whole retention pass in prod.
    """
    from datetime import datetime, timedelta, timezone

    from app.db.retention import run_retention
    from app.screening.retention import shred_expired
    from tests.test_retention import (
        _entity,
        _linkedin_evidence,
        _review,
        _run,
        _submission,
    )

    urls = _urls(tmp_path)
    if which == "postgres" and len(urls) < 2:
        pytest.skip("TEST_POSTGRES_URL not set")
    url = urls[0] if which == "sqlite" else urls[1]
    cfg = _migrated(url, monkeypatch)
    engine = create_engine(url)
    now = datetime(2032, 1, 1, tzinfo=timezone.utc)
    try:
        with Session(engine) as db:
            ids = _seed(db)
            long_ago = now - timedelta(days=4000)
            sub = _submission(
                db, entity=_entity(db, name="Acme"), submitted_at=long_ago
            )
            run = _run(db, submission=sub)
            _review(db, run=run, decided_at=long_ago)
            _linkedin_evidence(db, run=run)
            subject = db.get(
                ScreeningSubject,
                db.get(
                    ScreeningRun,
                    db.get(ScreeningDecision, ids["screening_decision"]).run_id,
                ).subject_id,
            )
            subject.relationship_ended_at = long_ago
            db.commit()

            def snapshot():
                return {
                    t: db.execute(
                        text(f"SELECT * FROM {t} WHERE id = :id"), {"id": ids[t]}
                    ).one()
                    for t in _TABLES
                }

            before = snapshot()
            counts = run_retention(db, now=now)
            shredded = shred_expired(db, now=now)
            db.commit()
            db.expire_all()
            assert counts["reviewed_pii"] == 1 and shredded == 1
            assert snapshot() == before
    finally:
        engine.dispose()
        if which == "postgres":
            command.downgrade(cfg, "base")
