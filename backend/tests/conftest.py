"""Shared test fixtures for backend tests.

Provides a SQLite in-memory database and a FastAPI TestClient that uses it.
No live Postgres or Redis required.

SQLite in-memory caveat: each new connection to sqlite:///:memory: gets a
DIFFERENT empty database.  We use StaticPool to force all sessions/connections
to reuse the same in-memory database connection.

Celery / enqueue_run: the api_client fixture patches app.pipeline.orchestrator.
enqueue_run to a no-op so that the submission endpoint can call it without a
live broker or Postgres worker session.  The orchestrator tests call run_sync()
directly — they never go through the Celery path.
"""

import unittest.mock as mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers all ORM models with Base.metadata
from app.api.submissions import _get_db
from app.db.session import Base
from app.main import app


@pytest.fixture(scope="session")
def sqlite_engine():
    """Session-scoped in-memory SQLite engine with StaticPool.

    StaticPool ensures every session/connection object shares the same
    underlying sqlite3 connection, so tables created by create_all() are
    visible to all subsequent queries — including those from the TestClient.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def db_session(sqlite_engine):
    """Function-scoped transactional session that rolls back after each test.

    NOTE: because StaticPool shares one connection, nested transactions are
    used for rollback isolation.  This session is usable for reading data
    written by the API (after its commit) within the same test.
    """
    connection = sqlite_engine.connect()
    transaction = connection.begin()
    sess = Session(bind=connection)
    yield sess
    sess.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def api_client(sqlite_engine):
    """FastAPI TestClient backed by the SQLite in-memory database.

    Two overrides are applied:
    1. _get_db: replaced with SQLite-backed sessions (no live Postgres).
    2. enqueue_run: patched to a no-op so the submission endpoint does not
       attempt to connect to a Redis broker.  Orchestrator behaviour is tested
       separately in tests/pipeline/test_orchestrator.py via run_sync().
    """
    TestingSessionLocal = sessionmaker(
        bind=sqlite_engine,
        autocommit=False,
        autoflush=False,
    )

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[_get_db] = override_get_db
    with mock.patch("app.pipeline.orchestrator.enqueue_run") as _mock_enqueue:
        _mock_enqueue.return_value = None
        client = TestClient(app, raise_server_exceptions=True)
        yield client
    app.dependency_overrides.clear()
