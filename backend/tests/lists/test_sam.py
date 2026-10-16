"""SAM.gov public exclusions extract: fetch, parse, snapshot, prune (ticket 0091).

The extract is a ZIP holding one UTF-8 CSV with a 31-column header (verified
live 2026-10-16, docs/implementation-notes.md). Tests build ZIPs from the
fictional fixture in tests/fixtures/public_records and never touch the network.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.db.session import Base
from app.lists import sam
from app.models.list_snapshot import ListSnapshot
from app.models.sam_exclusion_record import SamExclusionRecord

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "public_records"
    / "sam_exclusions_extract.csv"
)


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'sam.db'}")
    Base.metadata.create_all(engine)
    sess = Session(bind=engine)
    yield sess
    sess.close()
    engine.dispose()


def _csv_text(rows: list[dict] | None = None, header: list[str] | None = None) -> str:
    with FIXTURE.open(newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = header or list(reader.fieldnames or [])
        base_rows = list(reader)
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows if rows is not None else base_rows:
        writer.writerow({k: row.get(k, "") for k in fieldnames})
    return out.getvalue()


def _zip(*members: tuple[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in members:
            zf.writestr(name, text)
    return buf.getvalue()


def _extract(text: str | None = None) -> bytes:
    return _zip(("SAM_Exclusions_Public_Extract_V2_26289.CSV", text or _csv_text()))


def _fixture_rows() -> list[dict]:
    with FIXTURE.open(newline="") as fh:
        return list(csv.DictReader(fh))


def _variant(n: int) -> bytes:
    """A distinct but valid extract (same rows, one comment changed)."""
    rows = _fixture_rows()
    rows[0]["Additional Comments"] = f"revision {n}"
    return _extract(_csv_text(rows))


# --- parsing ---------------------------------------------------------------


def test_parse_maps_fixture_rows_to_records():
    parsed = sam.parse_extract(_extract())
    assert parsed.skipped == 0
    assert len(parsed.records) == 4
    assert parsed.truncated == 0  # real-shaped values fit their columns
    firm = next(r for r in parsed.records if r["name"] == "Fabrikam Logistics Inc.")
    assert firm["classification"] == "Firm"
    assert firm["core_name"] == "fabrikam logistics"
    assert firm["state"] == "TX"
    assert firm["uei"] == "EXMPL0000001"
    assert firm["sam_number"] == "00000000-0000-4000-8000-000000000001"
    assert firm["excluding_agency"] == "GSA"
    assert firm["active_date"] == date(2025, 3, 14)
    assert firm["termination_date"] is None
    assert firm["termination_indefinite"] is True
    assert firm["cross_reference"].startswith("Fabrikam Transport")

    dated = next(r for r in parsed.records if r["name"] == "Northwind Builders LLC")
    assert dated["termination_date"] == date(2029, 1, 5)
    assert dated["termination_indefinite"] is False


def test_blank_active_date_is_kept_as_none():
    parsed = sam.parse_extract(_extract())
    blank = next(
        r
        for r in parsed.records
        if r["sam_number"] == "00000000-0000-4000-8000-000000000004"
    )
    assert blank["active_date"] is None


def test_unparseable_dates_become_none_not_errors():
    rows = _fixture_rows()
    rows[0]["Active Date"] = "sometime"
    parsed = sam.parse_extract(_extract(_csv_text(rows)))
    assert len(parsed.records) == 4
    first = next(
        r
        for r in parsed.records
        if r["sam_number"] == "00000000-0000-4000-8000-000000000001"
    )
    assert first["active_date"] is None


def test_individual_rows_take_their_name_from_first_and_last():
    parsed = sam.parse_extract(_extract())
    person = next(
        r for r in parsed.records if r["last"] == "Example" and r["first"] == "Jordan"
    )
    assert person["classification"] == "Individual"
    assert person["name"] == ""
    assert person["core_name"] == "jordan example"


def test_rows_with_no_name_at_all_are_skipped_and_counted():
    rows = _fixture_rows()
    rows.append({"Classification": "Firm", "State / Province": "CA"})
    parsed = sam.parse_extract(_extract(_csv_text(rows)))
    assert len(parsed.records) == 4
    assert parsed.skipped == 1


def test_header_is_matched_by_name_not_position():
    with FIXTURE.open(newline="") as fh:
        header = list(csv.DictReader(fh).fieldnames or [])
    shuffled = list(reversed(header))
    parsed = sam.parse_extract(_extract(_csv_text(header=shuffled)))
    assert {r["sam_number"] for r in parsed.records} == {
        "00000000-0000-4000-8000-000000000001",
        "00000000-0000-4000-8000-000000000002",
        "00000000-0000-4000-8000-000000000003",
        "00000000-0000-4000-8000-000000000004",
    }


def test_missing_required_column_is_rejected():
    with FIXTURE.open(newline="") as fh:
        header = [h for h in csv.DictReader(fh).fieldnames or [] if h != "SAM Number"]
    with pytest.raises(sam.ExtractError, match="SAM Number"):
        sam.parse_extract(_extract(_csv_text(header=header)))


def test_zip_must_hold_exactly_one_csv():
    two = _zip(("a.CSV", _csv_text()), ("b.CSV", _csv_text()))
    with pytest.raises(sam.ExtractError, match="one CSV"):
        sam.parse_extract(two)
    with pytest.raises(sam.ExtractError):
        sam.parse_extract(b"not a zip file")


def test_decompressed_size_is_capped():
    with pytest.raises(sam.ExtractError, match="too large"):
        sam.parse_extract(_extract(), max_uncompressed_bytes=100)


# --- ingest ----------------------------------------------------------------


def test_ingest_stores_snapshot_and_rows(db):
    result = sam.ingest(db, fetch=_extract)
    assert result.error is None and result.changed is True
    assert result.records == 4
    snap = db.query(ListSnapshot).one()
    assert snap.source == sam.SOURCE and snap.record_count == 4
    assert db.query(SamExclusionRecord).filter_by(snapshot_id=snap.id).count() == 4


def test_identical_content_is_not_stored_twice(db):
    sam.ingest(db, fetch=_extract)
    again = sam.ingest(db, fetch=_extract)
    assert again.changed is False and again.error is None
    assert db.query(ListSnapshot).count() == 1
    assert db.query(SamExclusionRecord).count() == 4


def test_changed_content_adds_a_snapshot_and_keeps_earlier_rows(db):
    first = sam.ingest(db, fetch=lambda: _variant(1))
    second = sam.ingest(db, fetch=lambda: _variant(2))
    assert second.changed is True and second.snapshot_id != first.snapshot_id
    assert (
        db.query(SamExclusionRecord).filter_by(snapshot_id=first.snapshot_id).count()
        == 4
    )
    assert (
        db.query(SamExclusionRecord).filter_by(snapshot_id=second.snapshot_id).count()
        == 4
    )


def test_fetch_failure_writes_nothing(db):
    def boom():
        raise RuntimeError("extract endpoint down")

    result = sam.ingest(db, fetch=boom)
    assert result.changed is False
    assert "extract endpoint down" in (result.error or "")
    assert db.query(ListSnapshot).count() == 0
    assert db.query(SamExclusionRecord).count() == 0


def test_bad_extract_keeps_the_last_good_snapshot(db):
    sam.ingest(db, fetch=_extract)
    result = sam.ingest(db, fetch=lambda: b"truncated")
    assert result.error and result.changed is False
    assert db.query(ListSnapshot).count() == 1


def test_row_count_collapse_is_rejected(db):
    sam.ingest(db, fetch=_extract)
    one_row = _extract(_csv_text(_fixture_rows()[:1]))
    result = sam.ingest(db, fetch=lambda: one_row)
    assert result.changed is False
    assert "row count" in (result.error or "")
    assert db.query(ListSnapshot).count() == 1


def test_only_the_newest_snapshots_are_kept(db):
    other = ListSnapshot(
        source="ofac_sdn",
        retrieved_at=sam._now(),
        content_sha256="0" * 64,
        record_count=0,
    )
    db.add(other)
    db.commit()

    ids = [
        sam.ingest(db, fetch=lambda n=n: _variant(n), keep=3).snapshot_id
        for n in range(5)
    ]

    kept = {s.id for s in db.query(ListSnapshot).filter_by(source=sam.SOURCE)}
    assert kept == set(ids[-3:])
    assert {r.snapshot_id for r in db.query(SamExclusionRecord)} == set(ids[-3:])
    assert db.query(ListSnapshot).filter_by(source="ofac_sdn").count() == 1


def test_latest_snapshot_is_what_lookups_read(db):
    sam.ingest(db, fetch=lambda: _variant(1))
    second = sam.ingest(db, fetch=lambda: _variant(2))
    latest = sam.latest_snapshot(db)
    assert latest is not None and latest.id == second.snapshot_id
    assert sam.latest_snapshot(Session(bind=db.get_bind())) is not None


# --- production fetcher ----------------------------------------------------


def test_default_fetcher_requires_an_api_key(monkeypatch):
    monkeypatch.delenv("SAM_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SAM_API_KEY"):
        sam.default_fetch()


def test_default_fetcher_never_leaks_the_key_in_errors(monkeypatch):
    secret = "SAM-0000-test-key-should-not-leak"
    monkeypatch.setenv("SAM_API_KEY", secret)

    class _Boom:
        def __init__(self, *args, **kwargs):
            raise RuntimeError(f"connect failed for {args} {kwargs}")

    monkeypatch.setattr(sam.httpx, "stream", _Boom)
    with pytest.raises(RuntimeError) as err:
        sam.default_fetch()
    assert secret not in str(err.value)
    assert sam.EXTRACT_URL.startswith("https://")


def test_default_fetcher_caps_the_download_size(monkeypatch):
    monkeypatch.setenv("SAM_API_KEY", "k")

    class _Resp:
        status_code = 200
        headers = {"content-type": "application/zip"}

        def iter_bytes(self):
            for _ in range(10):
                yield b"x" * 64

    class _Stream:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return _Resp()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(sam.httpx, "stream", _Stream)
    with pytest.raises(RuntimeError, match="too large"):
        sam.default_fetch(max_bytes=100)
    assert len(sam.default_fetch(max_bytes=10_000)) == 640


# --- review hardening (2026-10-16 fresh-context review) ---------------------


def test_sam_is_not_a_monitored_person_list():
    from app.lists import ingest as person_ingest

    assert sam.SOURCE not in person_ingest.SOURCES


def test_failed_row_insert_leaves_no_snapshot_behind(db, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("value too long for type character varying(32)")

    monkeypatch.setattr(sam, "_insert_rows", broken)
    result = sam.ingest(db, fetch=_extract)
    assert result.changed is False and "value too long" in (result.error or "")
    assert db.query(ListSnapshot).count() == 0
    assert db.query(SamExclusionRecord).count() == 0

    monkeypatch.undo()
    retry = sam.ingest(db, fetch=_extract)  # same content is not "unchanged"
    assert retry.changed is True and retry.records == 4


def test_skipped_rows_are_reported_on_the_result(db):
    rows = _fixture_rows()
    rows.append({**rows[0], "SAM Number": ""})  # blank id → skipped
    result = sam.ingest(db, fetch=lambda: _extract(_csv_text(rows)))
    assert result.records == 4 and result.skipped == 1


def test_mostly_unusable_extract_is_rejected():
    rows = _fixture_rows() + [{"Classification": "Firm"} for _ in range(10)]
    with pytest.raises(sam.ExtractError, match="unusable"):
        sam.parse_extract(_extract(_csv_text(rows)))


def test_nul_bytes_are_stripped():
    rows = _fixture_rows()
    rows[0]["Name"] = "Fabrikam\x00 Logistics Inc."
    parsed = sam.parse_extract(_extract(_csv_text(rows)))
    first = next(
        r
        for r in parsed.records
        if r["sam_number"] == "00000000-0000-4000-8000-000000000001"
    )
    assert "\x00" not in first["name"] and first["core_name"] == "fabrikam logistics"


def test_values_are_truncated_to_column_lengths():
    rows = _fixture_rows()
    rows[0]["Name"] = "Fabrikam " + "x" * 400
    rows[0]["Country"] = "UNITED STATES OF AMERICA"
    parsed = sam.parse_extract(_extract(_csv_text(rows)))
    first = next(
        r
        for r in parsed.records
        if r["sam_number"] == "00000000-0000-4000-8000-000000000001"
    )
    limits = sam.column_limits()
    assert len(first["core_name"]) <= limits["core_name"]
    assert len(first["country"]) <= limits["country"]
    assert parsed.truncated >= 2


def test_zip_with_lying_size_header_is_rejected():
    data = bytearray(_extract())
    # Patch the declared uncompressed size (local and central headers) to 10.
    for sig, offset in ((b"PK\x03\x04", 22), (b"PK\x01\x02", 24)):
        at = data.find(sig)
        data[at + offset : at + offset + 4] = (10).to_bytes(4, "little")
    with pytest.raises(sam.ExtractError):
        sam.parse_extract(bytes(data))


def test_invalid_utf8_is_an_extract_error():
    raw = _csv_text().encode("utf-8") + b"\xff\xfe broken\n"
    with pytest.raises(sam.ExtractError):
        sam.parse_extract(_zip_bytes(("x.CSV", raw)))


def _zip_bytes(*members: tuple[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, payload in members:
            zf.writestr(name, payload)
    return buf.getvalue()


def test_latest_snapshot_is_never_pruned_on_timestamp_ties(db):
    tie = sam._now()
    for n in range(3):
        db.add(
            ListSnapshot(
                source=sam.SOURCE,
                retrieved_at=tie,
                content_sha256=f"{n}" * 64,
                record_count=0,
            )
        )
    db.commit()
    latest = sam.latest_snapshot(db)
    sam._prune(db, keep=1)
    remaining = db.query(ListSnapshot).filter_by(source=sam.SOURCE).all()
    assert [s.id for s in remaining] == [latest.id]


def test_keep_below_one_still_keeps_the_newest(db):
    result = sam.ingest(db, fetch=_extract, keep=0)
    assert db.query(ListSnapshot).filter_by(id=result.snapshot_id).count() == 1


# --- fetcher hardening -----------------------------------------------------


class _FakeResponse:
    def __init__(self, status=200, headers=None, chunks=(b"zip",), url=None):
        self.status_code = status
        self.headers = headers or {}
        self._chunks = chunks
        self.url = url

    def iter_bytes(self):
        yield from self._chunks


def _fake_stream(responses, calls):
    class _Stream:
        def __init__(self, method, url, **kwargs):
            calls.append((url, kwargs))
            self._resp = responses.pop(0)

        def __enter__(self):
            return self._resp

        def __exit__(self, *exc):
            return False

    return _Stream


def test_redirect_to_plain_http_is_refused(monkeypatch):
    monkeypatch.setenv("SAM_API_KEY", "k")
    calls = []
    monkeypatch.setattr(
        sam.httpx,
        "stream",
        _fake_stream(
            [_FakeResponse(302, {"location": "http://files.example.test/x.zip"})],
            calls,
        ),
    )
    with pytest.raises(RuntimeError, match="https"):
        sam.default_fetch()
    assert len(calls) == 1


def test_redirect_to_another_host_does_not_resend_the_key(monkeypatch):
    monkeypatch.setenv("SAM_API_KEY", "secret-key")
    calls = []
    monkeypatch.setattr(
        sam.httpx,
        "stream",
        _fake_stream(
            [
                _FakeResponse(
                    302, {"location": "https://files.example.test/x.zip?sig=1"}
                ),
                _FakeResponse(200, chunks=(b"abc",)),
            ],
            calls,
        ),
    )
    assert sam.default_fetch() == b"abc"
    assert "secret-key" in str(calls[0][1].get("params"))
    assert "secret-key" not in str(calls[1])


def test_non_200_is_an_error_without_the_key(monkeypatch):
    monkeypatch.setenv("SAM_API_KEY", "secret-key")
    monkeypatch.setattr(sam.httpx, "stream", _fake_stream([_FakeResponse(403)], []))
    with pytest.raises(RuntimeError, match="HTTP 403") as err:
        sam.default_fetch()
    assert "secret-key" not in str(err.value)


def test_http_client_request_logs_never_carry_the_key(monkeypatch, caplog):
    import logging

    monkeypatch.setenv("SAM_API_KEY", "secret-key")

    class _Logs:
        def __init__(self, method, url, **kwargs):
            logging.getLogger("httpx").info(
                "HTTP Request: GET %s?api_key=%s", url, kwargs["params"]["api_key"]
            )

        def __enter__(self):
            return _FakeResponse(200, chunks=(b"ok",))

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(sam.httpx, "stream", _Logs)
    with caplog.at_level(logging.DEBUG):  # every logger, incl. httpx
        sam.default_fetch()
    assert "secret-key" not in caplog.text


def test_slow_download_hits_the_deadline(monkeypatch):
    monkeypatch.setenv("SAM_API_KEY", "k")
    monkeypatch.setattr(
        sam.httpx, "stream", _fake_stream([_FakeResponse(200, chunks=(b"a", b"b"))], [])
    )
    with pytest.raises(RuntimeError, match="deadline"):
        sam.default_fetch(deadline_s=-1)


# --- Postgres (CI sets TEST_POSTGRES_URL) ----------------------------------


def test_ingest_on_postgres_with_oversized_values(monkeypatch):
    import os

    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL not set")
    engine = create_engine(url)
    Base.metadata.create_all(
        engine, tables=[ListSnapshot.__table__, SamExclusionRecord.__table__]
    )
    rows = _fixture_rows()
    rows[0]["Name"] = "Fabrikam " + "x" * 400
    rows[0]["Unique Entity ID"] = "U" * 80
    rows[0]["Country"] = "UNITED STATES OF AMERICA"
    try:
        with Session(bind=engine) as pg:
            result = sam.ingest(pg, fetch=lambda: _extract(_csv_text(rows)))
            assert result.error is None and result.records == 4
    finally:
        SamExclusionRecord.__table__.drop(engine, checkfirst=True)
        ListSnapshot.__table__.drop(engine, checkfirst=True)
        engine.dispose()
