"""Shared fixtures for screening API tests: one file DB, real auth, fake queue."""

from __future__ import annotations

import base64
import os
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.api.audit as audit_api
import app.auth.operator as operator_auth
import app.auth.service as service_auth
import app.models  # noqa: F401
from app.db.session import Base
from app.lists.persons import PersonRecord, store_records
from app.main import app
from app.models.list_snapshot import ListSnapshot
from app.models.operator import Operator
from app.screening import api as screening_api


@pytest.fixture
def api_env(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "ENTITYIQ_SCREENING_MASTER_KEY", base64.b64encode(os.urandom(32)).decode()
    )
    engine = create_engine(
        f"sqlite:///{tmp_path / 'api.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    for dep in (
        screening_api._get_db,
        operator_auth._get_db,
        service_auth._get_db,
        audit_api._get_db,
    ):
        app.dependency_overrides[dep] = get_db

    # Run the pipeline inline (no broker), using the same test database.
    def run_inline(run_id: str, background=None) -> None:
        from app.screening.pipeline import run_screening

        db = factory()
        try:
            run_screening(run_id, db)
        finally:
            db.close()

    monkeypatch.setattr(screening_api, "enqueue_screening", run_inline)

    db = factory()
    tokens = {}
    for role in ("operator", "lead", "examiner"):
        db.add(
            Operator(
                email=f"{role}@test.example",
                full_name=role.title(),
                role=role,
                password_hash=operator_auth.hash_password("pw"),
            )
        )
    db.commit()
    for role in ("operator", "lead", "examiner"):
        tokens[role] = operator_auth.sign_in(f"{role}@test.example", "pw", db)
    client_row, api_key = service_auth.create_api_client("screening-test", db)
    api_client_id = client_row.id
    db.close()

    yield {
        "client": TestClient(app),
        "factory": factory,
        "auth": {r: {"Authorization": f"Bearer {t}"} for r, t in tokens.items() if t},
        "api_key": {"X-API-Key": api_key},
        "api_client_id": api_client_id,
    }
    app.dependency_overrides.clear()
    engine.dispose()


def load_people(factory, people, source="ofac_sdn"):
    db = factory()
    snap = ListSnapshot(
        source=source,
        retrieved_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        content_sha256=f"{source}-fixture",
        record_count=len(people),
    )
    db.add(snap)
    db.flush()
    store_records(
        db,
        snap,
        [
            PersonRecord(
                source=source,
                source_entry_id=str(i),
                primary_name=p["name"],
                names=[{"name": p["name"], "kind": "primary"}],
                dobs=p.get("dobs", []),
                nationalities=p.get("nat", []),
            )
            for i, p in enumerate(people)
        ],
    )
    db.commit()
    db.close()
