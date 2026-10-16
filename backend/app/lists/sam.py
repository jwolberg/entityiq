"""SAM.gov public exclusions extract: fetch, parse, snapshot, prune (ticket 0091).

    python -m app.lists.sam          # needs SAM_API_KEY; run daily after 7 AM ET

The daily extract is a ZIP holding one UTF-8 CSV of every *active* exclusion
(a full list, not a delta). Its real header has 31 columns and ISO dates, and
differs from the 2017 published layout, so columns are matched by header name
(docs/implementation-notes.md, 2026-10-16).

This is deliberately separate from ``app.lists.ingest``: that registry parses
natural persons into ``watchlist_record`` and triggers individual-screening
monitoring, which SAM rows must not do (plan KTD3/KTD4).

Guards, so a bad download can never replace the last good snapshot:

- fetch: HTTPS only (redirects are followed by hand and must stay https; the
  API key is never re-sent to another host), capped size, wall-clock deadline,
  and the key is kept out of errors and of httpx's request logging;
- parse: exactly one CSV member, streamed with a decompressed-size cap,
  required columns by name, NUL bytes stripped, values truncated to their
  column lengths, and a file that is mostly unusable rows is rejected;
- store: one transaction per ingest (snapshot + rows), so a failed insert
  leaves nothing behind; a new snapshot may not shrink below half the
  previous row count; only the newest ``KEEP_SNAPSHOTS`` are kept (KTD12).
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import os
import sys
import time
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING
from urllib.parse import quote, urljoin, urlsplit

import httpx
from sqlalchemy import String, insert

from app.adapters.tax_id import core_name
from app.lists.ingest import IngestResult
from app.models.list_snapshot import ListSnapshot
from app.models.sam_exclusion_record import SamExclusionRecord

if TYPE_CHECKING:
    from sqlalchemy.orm import Query, Session

logger = logging.getLogger(__name__)

SOURCE = "sam_exclusions"
SOURCE_NAME = "SAM.gov Exclusions"
EXTRACT_URL = "https://api.sam.gov/data-services/v1/extracts"

KEEP_SNAPSHOTS = 7
MAX_DOWNLOAD_BYTES = 50_000_000  # live extract was 11.9 MB zipped
MAX_UNCOMPRESSED_BYTES = 300_000_000  # and 78.6 MB unzipped
DOWNLOAD_DEADLINE_S = 600.0
MAX_REDIRECTS = 3
MIN_ROW_FRACTION = 0.5  # vs. the previous snapshot
MAX_SKIPPED_FRACTION = 0.05  # live file: 0 skipped of 169,025
MIN_SKIPPED_TO_REJECT = 5  # a handful of bad rows is tolerated on any size
INSERT_CHUNK = 5_000

REQUIRED_COLUMNS = (
    "Classification",
    "Name",
    "First",
    "Last",
    "City",
    "State / Province",
    "Country",
    "Unique Entity ID",
    "Exclusion Program",
    "Excluding Agency",
    "Exclusion Type",
    "Active Date",
    "Termination Date",
    "Cross-Reference",
    "SAM Number",
    "CAGE",
)


class ExtractError(ValueError):
    """The downloaded extract is not a usable SAM exclusions file."""


@dataclass
class ParsedExtract:
    records: list[dict] = field(default_factory=list)
    skipped: int = 0
    truncated: int = 0
    sha256: str = ""


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def column_limits() -> dict[str, int]:
    """Max lengths of the bounded string columns (Postgres enforces them)."""
    return {
        col.name: col.type.length
        for col in SamExclusionRecord.__table__.columns
        if isinstance(col.type, String) and col.type.length
    }


def _date(value: str) -> date | None:
    """Live files use ISO dates; the 2017 layout documents MM/DD/YYYY."""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _row(raw: dict[str, str], limits: dict[str, int]) -> tuple[dict | None, int]:
    """Map one CSV row. Returns (record or None if unusable, truncations)."""

    def get(key: str) -> str:
        return (raw.get(key) or "").replace("\x00", "").strip()

    name = get("Name")
    first, last = get("First"), get("Last")
    display = name or " ".join(part for part in (first, last) if part)
    key = core_name(display)
    if not key or not get("SAM Number"):
        return None, 0
    termination = get("Termination Date")
    record = {
        "sam_number": get("SAM Number"),
        "classification": get("Classification"),
        "name": name,
        "first": first,
        "last": last,
        "core_name": key,
        "city": get("City"),
        "state": get("State / Province"),
        "country": get("Country"),
        "uei": get("Unique Entity ID"),
        "cage": get("CAGE"),
        "exclusion_program": get("Exclusion Program"),
        "excluding_agency": get("Excluding Agency"),
        "exclusion_type": get("Exclusion Type"),
        "active_date": _date(get("Active Date")),
        "termination_date": _date(termination),
        "termination_indefinite": termination.lower() == "indefinite",
        "cross_reference": get("Cross-Reference"),
    }
    truncated = 0
    for column, limit in limits.items():
        value = record.get(column)
        if isinstance(value, str) and len(value) > limit:
            record[column] = value[:limit]
            truncated += 1
    return record, truncated


class _CappedHashingReader(io.RawIOBase):
    """Reads the ZIP member, hashing as it goes and refusing to exceed a cap."""

    def __init__(self, inner, cap: int) -> None:
        self._inner, self._cap, self._read = inner, cap, 0
        self.digest = hashlib.sha256()

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        chunk = self._inner.read(len(buffer))
        self._read += len(chunk)
        if self._read > self._cap:
            raise ExtractError("extract CSV too large")
        self.digest.update(chunk)
        buffer[: len(chunk)] = chunk
        return len(chunk)


def parse_extract(
    data: bytes, *, max_uncompressed_bytes: int = MAX_UNCOMPRESSED_BYTES
) -> ParsedExtract:
    """Parse the extract ZIP. Raises ExtractError on anything unexpected."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ExtractError("extract is not a ZIP file") from exc
    members = archive.infolist()
    if len(members) != 1 or not members[0].filename.lower().endswith(".csv"):
        raise ExtractError(
            f"expected one CSV in the extract, found {[m.filename for m in members]}"
        )
    member = members[0]
    if member.file_size > max_uncompressed_bytes:
        raise ExtractError(f"extract CSV too large ({member.file_size} bytes)")

    limits = column_limits()
    parsed = ParsedExtract()
    try:
        with archive.open(member) as fh:
            raw = _CappedHashingReader(fh, max_uncompressed_bytes)
            text = io.TextIOWrapper(io.BufferedReader(raw), "utf-8-sig", newline="")
            reader = csv.DictReader(text)
            header = set(reader.fieldnames or [])
            missing = [col for col in REQUIRED_COLUMNS if col not in header]
            if missing:
                raise ExtractError(f"extract is missing columns: {', '.join(missing)}")
            for row in reader:
                record, truncated = _row(row, limits)
                if record is None:
                    parsed.skipped += 1
                else:
                    parsed.records.append(record)
                    parsed.truncated += truncated
            parsed.sha256 = raw.digest.hexdigest()
    except ExtractError:
        raise
    except (zipfile.BadZipFile, UnicodeDecodeError, csv.Error, EOFError) as exc:
        raise ExtractError(f"extract could not be read: {exc}") from exc

    total = len(parsed.records) + parsed.skipped
    if not parsed.records:
        raise ExtractError("extract has no usable rows")
    if (
        parsed.skipped > MIN_SKIPPED_TO_REJECT
        and parsed.skipped > total * MAX_SKIPPED_FRACTION
    ):
        raise ExtractError(
            f"{parsed.skipped} of {total} rows are unusable; refusing the extract"
        )
    return parsed


@contextmanager
def _quiet_http_logging() -> Iterator[None]:
    """httpx logs every request URL at INFO, and the key is a query param."""
    loggers = [logging.getLogger(name) for name in ("httpx", "httpcore")]
    previous = [lg.level for lg in loggers]
    for lg in loggers:
        lg.setLevel(logging.WARNING)
    try:
        yield
    finally:
        for lg, level in zip(loggers, previous, strict=True):
            lg.setLevel(level)


def default_fetch(
    *, max_bytes: int = MAX_DOWNLOAD_BYTES, deadline_s: float = DOWNLOAD_DEADLINE_S
) -> bytes:
    """Download the latest daily extract. The API key never appears in errors."""
    key = os.environ.get("SAM_API_KEY", "").strip()
    if not key:
        raise RuntimeError("SAM_API_KEY is not set")
    started = time.monotonic()
    url, params = EXTRACT_URL, {"api_key": key, "fileType": "EXCLUSION"}
    origin = urlsplit(EXTRACT_URL).netloc
    try:
        with _quiet_http_logging():
            for _ in range(MAX_REDIRECTS + 1):
                if urlsplit(url).scheme != "https":
                    raise RuntimeError("refusing a non-https extract URL")
                kwargs = {"timeout": 120.0, "follow_redirects": False}
                if params is not None:
                    kwargs["params"] = params
                with httpx.stream("GET", url, **kwargs) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        url = urljoin(url, response.headers.get("location", ""))
                        if urlsplit(url).netloc != origin:
                            params = None  # never send the key to another host
                        continue
                    if response.status_code != 200:
                        raise RuntimeError(f"HTTP {response.status_code}")
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        if time.monotonic() - started > deadline_s:
                            raise RuntimeError("download passed its deadline")
                        size += len(chunk)
                        if size > max_bytes:
                            raise RuntimeError(
                                f"download too large (> {max_bytes} bytes)"
                            )
                        chunks.append(chunk)
                    return b"".join(chunks)
            raise RuntimeError("too many redirects")
    except Exception as exc:
        message = str(exc).replace(key, "***").replace(quote(key, safe=""), "***")
        raise RuntimeError(f"SAM extract fetch failed: {message}") from None


def _newest_first(db: "Session") -> "Query":
    # ``id`` breaks exact timestamp ties so latest_snapshot and _prune agree.
    return (
        db.query(ListSnapshot)
        .filter_by(source=SOURCE)
        .order_by(
            ListSnapshot.retrieved_at.desc(),
            ListSnapshot.created_at.desc(),
            ListSnapshot.id.desc(),
        )
    )


def latest_snapshot(db: "Session") -> ListSnapshot | None:
    return _newest_first(db).first()


def _prune(db: "Session", keep: int) -> None:
    for old in _newest_first(db).all()[max(1, keep) :]:
        db.query(SamExclusionRecord).filter_by(snapshot_id=old.id).delete(
            synchronize_session=False
        )
        db.delete(old)
    db.commit()


def _insert_rows(db: "Session", snapshot_id: str, records: list[dict]) -> None:
    table = SamExclusionRecord.__table__
    for start in range(0, len(records), INSERT_CHUNK):
        chunk = records[start : start + INSERT_CHUNK]
        db.execute(
            insert(table),
            [{**row, "id": _uuid(), "snapshot_id": snapshot_id} for row in chunk],
        )


def _uuid() -> str:
    import uuid  # noqa: PLC0415

    return str(uuid.uuid4())


def ingest(
    db: "Session",
    *,
    fetch: Callable[[], bytes] = default_fetch,
    keep: int = KEEP_SNAPSHOTS,
) -> IngestResult:
    """Fetch, validate and store one extract. Never raises."""
    try:
        parsed = parse_extract(fetch())
    except Exception as exc:  # fetch or parse: keep the last good snapshot
        logger.warning("SAM ingest failed: %s", exc)
        return IngestResult(source=SOURCE, changed=False, error=str(exc))

    latest = latest_snapshot(db)
    if latest is not None and latest.content_sha256 == parsed.sha256:
        return IngestResult(source=SOURCE, changed=False, snapshot_id=latest.id)
    if (
        latest is not None
        and len(parsed.records) < latest.record_count * MIN_ROW_FRACTION
    ):
        error = (
            f"row count collapsed from {latest.record_count} to {len(parsed.records)}; "
            "keeping the previous snapshot"
        )
        logger.warning("SAM ingest rejected: %s", error)
        return IngestResult(source=SOURCE, changed=False, error=error)

    try:  # snapshot + rows in one transaction
        snapshot = ListSnapshot(
            source=SOURCE,
            retrieved_at=_now(),
            content_sha256=parsed.sha256,
            record_count=len(parsed.records),
        )
        db.add(snapshot)
        db.flush()
        _insert_rows(db, snapshot.id, parsed.records)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("SAM ingest could not store the snapshot: %s", exc)
        return IngestResult(source=SOURCE, changed=False, error=f"store failed: {exc}")

    try:
        _prune(db, keep)
    except Exception as exc:  # the new snapshot is stored; pruning retries next run
        db.rollback()
        logger.warning("SAM ingest could not prune old snapshots: %s", exc)
    if parsed.skipped or parsed.truncated:
        logger.info(
            "SAM ingest skipped %d rows, truncated %d values",
            parsed.skipped,
            parsed.truncated,
        )
    return IngestResult(
        source=SOURCE,
        changed=True,
        snapshot_id=snapshot.id,
        records=len(parsed.records),
        skipped=parsed.skipped,
    )


def main() -> int:  # pragma: no cover - CLI wrapper
    from app.db.session import SessionLocal  # noqa: PLC0415

    db = SessionLocal()
    try:
        result = ingest(db)
    finally:
        db.close()
    if result.error:
        print(f"{SOURCE}: FAILED ({result.error})")
        return 1
    state = "new snapshot" if result.changed else "unchanged"
    skipped = f" ({result.skipped} rows skipped)" if result.skipped else ""
    print(f"{SOURCE}: {state} {result.records or ''}{skipped}".rstrip())
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
