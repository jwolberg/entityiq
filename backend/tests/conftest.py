"""Shared test fixtures for backend tests.

Provides a SQLite in-memory database and a FastAPI TestClient that uses it.
No live Postgres or Redis required.

SQLite in-memory caveat: each new connection to sqlite:///:memory: gets a
DIFFERENT empty database.  We use StaticPool to force all sessions/connections
to reuse the same in-memory database connection.
"""

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

    Overrides the _get_db dependency so no live Postgres is required.
    Each request gets its own session from the shared StaticPool connection.
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
    client = TestClient(app, raise_server_exceptions=True)
    yield client
    app.dependency_overrides.clear()
