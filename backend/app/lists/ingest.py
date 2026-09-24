"""Fetch official sanctions lists, snapshot them, trigger monitoring (0051).

    python -m app.lists.ingest                 # all sources
    python -m app.lists.ingest ofac_sdn uk_ofsi

Run it on a schedule (daily is typical). A new snapshot is only stored when
the list's content hash changed. Each new snapshot triggers screening
monitoring (``app.screening.monitor.rescreen_for_snapshot``). A fetch
failure is logged and reported; it never deletes or edits earlier
snapshots, so screening keeps using the last good one.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.lists.persons import (
    PersonRecord,
    parse_eu_persons,
    parse_ofac_individuals,
    parse_uk_individuals,
    parse_un_individuals,
    store_records,
)
from app.lists.snapshot import content_hash, record_snapshot
from app.models.list_snapshot import ListSnapshot

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceSpec:
    url: str
    parse: Callable[[str], list[PersonRecord]]


SOURCES: dict[str, SourceSpec] = {
    "ofac_sdn": SourceSpec(
        "https://www.treasury.gov/ofac/downloads/sdn.csv", parse_ofac_individuals
    ),
    "un_consolidated": SourceSpec(
        "https://scsanctions.un.org/resources/xml/en/consolidated.xml",
        parse_un_individuals,
    ),
    "eu_fsf": SourceSpec(
        "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1"
        "/content?token=dG9rZW4tMjAxNw",
        parse_eu_persons,
    ),
    # OFSI's 2022-format consolidated list. Its "Last Updated" header was
    # 03/06/2026 when checked (2026-09-24); if OFSI retires it in favour of the
    # UK Sanctions List, add a parser for that format.
    "uk_ofsi": SourceSpec(
        "https://ofsistorage.blob.core.windows.net/publishlive/2022format/ConList.csv",
        parse_uk_individuals,
    ),
}


@dataclass
class IngestResult:
    source: str
    changed: bool
    snapshot_id: str | None = None
    records: int = 0
    error: str | None = None


def _http_fetch(url: str) -> str:
    import httpx  # noqa: PLC0415

    response = httpx.get(url, timeout=120.0, follow_redirects=True)
    response.raise_for_status()
    return response.content.decode("utf-8-sig", errors="replace")


def ingest_source(
    db: "Session",
    source: str,
    *,
    fetch: Callable[[str], str] = _http_fetch,
    on_new_snapshot: Callable[[str], object] | None = None,
) -> IngestResult:
    spec = SOURCES[source]
    try:
        text = fetch(spec.url)
    except Exception as exc:
        logger.warning("List ingest %s failed: %s", source, exc)
        return IngestResult(source=source, changed=False, error=str(exc))

    latest = (
        db.query(ListSnapshot)
        .filter_by(source=source)
        .order_by(ListSnapshot.retrieved_at.desc())
        .first()
    )
    if latest is not None and latest.content_sha256 == content_hash(text):
        return IngestResult(source=source, changed=False, snapshot_id=latest.id)

    records = spec.parse(text)
    snapshot = record_snapshot(
        db, source=source, content=text, record_count=len(records)
    )
    store_records(db, snapshot, records)
    if on_new_snapshot is not None:
        on_new_snapshot(snapshot.id)
    return IngestResult(
        source=source, changed=True, snapshot_id=snapshot.id, records=len(records)
    )


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI wrapper
    from app.db.session import SessionLocal  # noqa: PLC0415
    from app.screening.monitor import rescreen_for_snapshot  # noqa: PLC0415

    sources = (sys.argv[1:] if argv is None else argv) or list(SOURCES)
    db = SessionLocal()
    failed = 0
    try:
        for source in sources:
            result = ingest_source(
                db, source, on_new_snapshot=lambda sid: rescreen_for_snapshot(db, sid)
            )
            failed += result.error is not None
            print(
                f"{source}: "
                + (
                    f"FAILED ({result.error})"
                    if result.error
                    else f"{'new snapshot' if result.changed else 'unchanged'}"
                    f" {result.records or ''}".rstrip()
                )
            )
    finally:
        db.close()
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
