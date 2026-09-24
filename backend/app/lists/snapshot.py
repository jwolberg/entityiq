"""Record versioned list snapshots."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.models.list_snapshot import ListSnapshot

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def content_hash(content: str | bytes) -> str:
    """SHA-256 hex digest of the raw list content."""
    data = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(data).hexdigest()


def record_snapshot(
    db: "Session", *, source: str, content: str | bytes, record_count: int
) -> ListSnapshot:
    """Persist one ingest of ``source`` and return the committed row."""
    snapshot = ListSnapshot(
        source=source,
        retrieved_at=datetime.now(tz=timezone.utc),
        content_sha256=content_hash(content),
        record_count=record_count,
    )
    db.add(snapshot)
    db.commit()
    return snapshot
