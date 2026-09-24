"""P4-T6 — report reads require an authenticated principal.

Before P4-T6, GET /reports and GET /reports/{run_id} had no auth dependency:
anyone who could reach the API could list every company and read full
reports (evidence, contacts, scores).
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api.reports import _get_db as reports_get_db
from app.auth.operator import _clear_all_sessions, _new_session_token
from app.auth.service import _get_db as service_get_db
from app.main import app


@pytest.fixture
def client(sqlite_engine):
    SessionMaker = sessionmaker(bind=sqlite_engine, autocommit=False, autoflush=False)

    def override():
        db = SessionMaker()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[reports_get_db] = override
    app.dependency_overrides[service_get_db] = override
    yield TestClient(app)
    app.dependency_overrides.clear()
    _clear_all_sessions()


@pytest.mark.parametrize("path", ["/reports", "/reports/some-run-id"])
def test_report_reads_reject_anonymous(client, path):
    assert client.get(path).status_code == 401


def test_report_list_accepts_api_key(client, service_credential):
    resp = client.get("/reports", headers={"X-API-Key": service_credential["key"]})
    assert resp.status_code == 200


def test_report_detail_accepts_operator_token(client, sqlite_engine):
    import uuid

    from app.models.operator import Operator

    SessionMaker = sessionmaker(bind=sqlite_engine)
    db = SessionMaker()
    try:
        op = Operator(
            email=f"reader-{uuid.uuid4().hex[:8]}@example.com",
            full_name="Reader",
            role="operator",
        )
        db.add(op)
        db.commit()
        token = _new_session_token(op.id)
    finally:
        db.close()

    resp = client.get(
        "/reports/does-not-exist", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 404  # authenticated; the run just doesn't exist
