"""Tests for the demo seed (P5-T2): accounts that can actually sign in, and
an idempotent re-run (the demo script seeds on every start)."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.auth.operator import verify_password
from app.db.session import Base
from app.models.api_client import ApiClient
from app.models.operator import Operator
from app.seed import DEMO_ACCOUNTS, DEMO_PASSWORD, seed


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess
    engine.dispose()


def test_seed_creates_operator_and_lead_that_can_sign_in(db):
    seed(db)
    for email, _name, role in DEMO_ACCOUNTS:
        op = db.query(Operator).filter(Operator.email == email).one()
        assert op.role == role
        assert verify_password(DEMO_PASSWORD, op.password_hash)
    assert {r for _, _, r in DEMO_ACCOUNTS} == {"operator", "lead", "examiner"}


def test_seed_mints_api_key_once(db):
    first = seed(db)
    assert first.api_key is not None and first.api_key.startswith("eiq_")
    second = seed(db)
    assert second.api_key is None  # plaintext is only ever shown at creation
    assert db.query(ApiClient).count() == 1


def test_seed_is_idempotent(db):
    seed(db)
    result = seed(db)
    assert result.created_accounts == []
    assert db.query(Operator).count() == len(DEMO_ACCOUNTS)


# The seed creates accounts with a publicly documented password, so it must
# refuse anything but a local SQLite database unless explicitly overridden.
@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://entityiq:pw@db.internal:5432/entityiq",
        "postgresql://u:p@localhost/entityiq",
    ],
)
def test_seed_refuses_non_sqlite_database(url, monkeypatch):
    from app.seed import UnsafeSeedTarget, check_seed_target

    monkeypatch.delenv("ENTITYIQ_ALLOW_DEMO_SEED", raising=False)
    with pytest.raises(UnsafeSeedTarget):
        check_seed_target(url)


def test_seed_allows_sqlite_and_explicit_override(monkeypatch):
    from app.seed import check_seed_target

    check_seed_target("sqlite:///./entityiq-demo.db")
    monkeypatch.setenv("ENTITYIQ_ALLOW_DEMO_SEED", "1")
    check_seed_target("postgresql://u:p@localhost/entityiq")
