"""Migrations match the models and run up/down on SQLite.

Guards every schema change (individual screening adds several): the migrated
schema must contain exactly the tables the ORM declares, and a full downgrade
must succeed.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

import app.models  # noqa: F401
from app.db.session import Base

_BACKEND = Path(__file__).resolve().parents[1]


def _config(url: str, monkeypatch) -> Config:
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config(str(_BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND / "app/db/migrations"))
    return cfg


def test_upgrade_head_creates_every_model_table_and_downgrades(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = _config(url, monkeypatch)

    command.upgrade(cfg, "head")
    tables = set(inspect(create_engine(url)).get_table_names()) - {"alembic_version"}
    assert tables == set(Base.metadata.tables)

    command.downgrade(cfg, "base")
    left = set(inspect(create_engine(url)).get_table_names()) - {"alembic_version"}
    assert left == set()


def test_single_migration_head(monkeypatch, tmp_path):
    from alembic.script import ScriptDirectory

    heads = ScriptDirectory.from_config(
        _config(f"sqlite:///{tmp_path / 'h.db'}", monkeypatch)
    ).get_heads()
    assert len(heads) == 1, heads


def test_head_adds_company_person_and_declared_people(tmp_path, monkeypatch):
    """Officer screening (ticket 0078): link table + declared people column."""
    url = f"sqlite:///{tmp_path / 'p.db'}"
    command.upgrade(_config(url, monkeypatch), "head")
    insp = inspect(create_engine(url))

    assert "declared_people" in {c["name"] for c in insp.get_columns("submission")}
    cols = {c["name"]: c for c in insp.get_columns("company_person")}
    assert {
        "id",
        "entity_id",
        "first_run_id",
        "screening_subject_id",
        "relationship",
        "role",
        "sources",
        "ownership_pct",
        "created_at",
    } <= set(cols)
    # No plaintext name: it lives only in the encrypted screening subject.
    assert not {"name", "full_name"} & set(cols)
