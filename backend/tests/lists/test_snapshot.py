"""Shared list ingestion with versioned snapshots (IS1-T1, ticket 0029)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.db.session import Base
from app.lists import ofac
from app.lists.snapshot import content_hash, record_snapshot
from app.models.list_snapshot import ListSnapshot

_CSV = (
    '36,"AEROCARIBBEAN AIRLINES","-0- ","CUBA","-0- ","-0- ","-0- ","-0- ","-0- ",'
    '"-0- ","-0- ","-0- "\n'
    '173,"ANGLO-CARIBBEAN CO., LTD.","-0- ","CUBA","-0- ","-0- ","-0- ","-0- ",'
    '"-0- ","-0- ","-0- ","-0- "\n'
)


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'lists.db'}")
    Base.metadata.create_all(engine)
    sess = Session(bind=engine)
    yield sess
    sess.close()
    engine.dispose()


def test_identical_content_hashes_identically():
    assert content_hash(_CSV) == content_hash(_CSV)
    assert content_hash(_CSV) != content_hash(_CSV + "\n")
    assert len(content_hash(_CSV)) == 64


def test_record_snapshot_writes_a_row_per_ingest(db):
    a = record_snapshot(db, source="ofac_sdn", content=_CSV, record_count=2)
    b = record_snapshot(db, source="ofac_sdn", content=_CSV, record_count=2)

    assert a.id != b.id
    assert a.content_sha256 == b.content_sha256
    assert db.query(ListSnapshot).count() == 2
    assert a.source == "ofac_sdn"
    assert a.record_count == 2
    assert a.retrieved_at is not None


def test_load_ofac_parses_and_records_a_snapshot(db):
    class _Fetcher:
        def fetch_csv(self) -> str:
            return _CSV

    snapshot, entries = ofac.load(db, fetcher=_Fetcher())

    assert [e["name"] for e in entries] == [
        "AEROCARIBBEAN AIRLINES",
        "ANGLO-CARIBBEAN CO., LTD.",
    ]
    assert snapshot.record_count == 2
    assert snapshot.content_sha256 == content_hash(_CSV)


def test_kyb_sanctions_adapter_uses_the_shared_ofac_module():
    """KYB behavior is unchanged: the adapter delegates to app.lists.ofac."""
    from app.adapters import sanctions

    assert sanctions._parse_sdn_csv is ofac.parse_sdn_csv
    assert sanctions.SdnListFetcher is ofac.SdnListFetcher
    assert sanctions._OFAC_SDN_URL == ofac.OFAC_SDN_URL
