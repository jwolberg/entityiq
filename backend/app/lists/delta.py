"""Diff two snapshots of the same list (IS5-T1, ticket 0051; PRD-IDV F17).

Records are matched across snapshots by their source entry id. A record is
"changed" when any attribute screening compares (names, DOBs, places of
birth, nationalities, documents) differs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.models.watchlist_record import WatchlistRecord

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_COMPARED = ("names", "dobs", "pobs", "nationalities", "documents")


@dataclass
class SnapshotDelta:
    added_entry_ids: list[str] = field(default_factory=list)
    changed_entry_ids: list[str] = field(default_factory=list)
    removed_entry_ids: list[str] = field(default_factory=list)
    # Records in the new snapshot that are added or changed.
    new_records: list[WatchlistRecord] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {
            "added": len(self.added_entry_ids),
            "changed": len(self.changed_entry_ids),
            "removed": len(self.removed_entry_ids),
        }


def _by_entry(db: "Session", snapshot_id: str | None) -> dict[str, WatchlistRecord]:
    if snapshot_id is None:
        return {}
    return {
        r.source_entry_id: r
        for r in db.query(WatchlistRecord).filter_by(snapshot_id=snapshot_id)
    }


def diff_snapshots(
    db: "Session", old_snapshot_id: str | None, new_snapshot_id: str
) -> SnapshotDelta:
    old = _by_entry(db, old_snapshot_id)
    new = _by_entry(db, new_snapshot_id)
    delta = SnapshotDelta()
    for entry_id in sorted(new):
        if entry_id not in old:
            delta.added_entry_ids.append(entry_id)
            delta.new_records.append(new[entry_id])
        elif any(
            getattr(new[entry_id], f) != getattr(old[entry_id], f) for f in _COMPARED
        ):
            delta.changed_entry_ids.append(entry_id)
            delta.new_records.append(new[entry_id])
    delta.removed_entry_ids = sorted(set(old) - set(new))
    return delta
