"""List ingestion → snapshot → monitoring trigger (ticket 0051)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.db.session import Base
from app.lists import ingest
from app.models.list_snapshot import ListSnapshot
from app.models.watchlist_record import WatchlistRecord
from tests.lists.test_person_parsers import OFAC_CSV, UN_XML


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'ing.db'}")
    Base.metadata.create_all(engine)
    sess = Session(bind=engine)
    yield sess
    sess.close()
    engine.dispose()


def test_every_official_source_has_a_url_and_parser():
    assert set(ingest.SOURCES) == {"ofac_sdn", "un_consolidated", "eu_fsf", "uk_ofsi"}
    for spec in ingest.SOURCES.values():
        assert spec.url.startswith("https://")


def test_ingest_stores_snapshot_and_records_then_triggers_monitoring(db):
    triggered = []
    result = ingest.ingest_source(
        db, "ofac_sdn", fetch=lambda url: OFAC_CSV, on_new_snapshot=triggered.append
    )
    assert result.changed is True
    assert db.query(ListSnapshot).count() == 1
    assert db.query(WatchlistRecord).count() == 3  # individuals only
    assert triggered == [result.snapshot_id]


def test_unchanged_content_creates_no_snapshot_and_no_rescreen(db):
    triggered = []
    ingest.ingest_source(
        db, "un_consolidated", fetch=lambda u: UN_XML, on_new_snapshot=triggered.append
    )
    again = ingest.ingest_source(
        db, "un_consolidated", fetch=lambda u: UN_XML, on_new_snapshot=triggered.append
    )
    assert again.changed is False
    assert db.query(ListSnapshot).count() == 1
    assert len(triggered) == 1


def test_fetch_failure_is_reported_not_raised(db):
    def boom(url):
        raise RuntimeError("list endpoint down")

    result = ingest.ingest_source(
        db, "ofac_sdn", fetch=boom, on_new_snapshot=lambda s: None
    )
    assert result.changed is False
    assert "down" in (result.error or "")
    assert db.query(ListSnapshot).count() == 0
