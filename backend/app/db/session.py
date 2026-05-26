"""Database engine, session factory, and declarative base.

Reads DATABASE_URL from the environment.  Defaults to a local Postgres URL
for convenience in local development; the production deployment sets the real
URL via an env variable.

Using generic SQLAlchemy types (JSON, Text, etc.) throughout so the schema
builds on both Postgres (production) and SQLite (tests / CI, no live DB
required).
"""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# Sensible local-dev default; override via DATABASE_URL env var in production.
_DEFAULT_URL = "postgresql+psycopg://entityiq:entityiq@localhost:5432/entityiq"

DATABASE_URL: str = os.environ.get("DATABASE_URL", _DEFAULT_URL)

engine = create_engine(
    DATABASE_URL,
    # pool_pre_ping keeps idle connections from going stale across process
    # restarts without needing an explicit health-check on every checkout.
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
