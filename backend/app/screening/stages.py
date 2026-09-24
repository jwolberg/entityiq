"""Screening pipeline stages (tickets 0037–0039).

Each stage follows the shared PipelineStage contract (app/pipeline/base.py):
it returns a new context dict and never calls ``db.rollback()`` itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func

from app.models.list_snapshot import ListSnapshot
from app.models.watchlist_record import WatchlistRecord
from app.screening.blocking import BlockingIndex
from app.screening.crypto import get_subject_pii
from app.screening.models import ScreeningCandidate, ScreeningRun, ScreeningSubject

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def current_snapshot_ids(db: "Session") -> list[str]:
    """Latest snapshot per source that actually has person records."""
    with_records = (
        db.query(ListSnapshot.source, func.max(ListSnapshot.retrieved_at))
        .join(WatchlistRecord, WatchlistRecord.snapshot_id == ListSnapshot.id)
        .group_by(ListSnapshot.source)
        .all()
    )
    ids: list[str] = []
    for source, latest in with_records:
        snap = (
            db.query(ListSnapshot)
            .filter(ListSnapshot.source == source, ListSnapshot.retrieved_at == latest)
            .order_by(ListSnapshot.id)
            .first()
        )
        if snap is not None:
            ids.append(snap.id)
    return sorted(ids)


_INDEX_CACHE: dict[tuple, BlockingIndex] = {}


def _load_index(db: "Session", snapshot_ids: list[str]) -> BlockingIndex:
    key = (id(db.get_bind()), tuple(snapshot_ids))
    index = _INDEX_CACHE.get(key)
    if index is None:
        records = (
            db.query(WatchlistRecord)
            .filter(WatchlistRecord.snapshot_id.in_(snapshot_ids))
            .all()
        )
        index = BlockingIndex.build(records)
        if len(_INDEX_CACHE) >= 4:
            _INDEX_CACHE.pop(next(iter(_INDEX_CACHE)))
        _INDEX_CACHE[key] = index
    return index


def subject_names(pii: dict) -> list[str]:
    names = [pii.get("name"), pii.get("original_script_name"), *pii.get("aliases", [])]
    return [n for n in names if n]


class BlockCandidatesStage:
    """Recall-first candidate generation against the current list snapshots."""

    name = "block_candidates"

    def run(self, run_id: str, db: "Session", context: dict) -> dict:
        snapshot_ids = current_snapshot_ids(db)
        if not snapshot_ids:
            return {
                **context,
                "blocking": {"status": "unavailable", "message": "No lists loaded"},
            }

        run = db.get(ScreeningRun, run_id)
        subject = db.get(ScreeningSubject, run.subject_id)
        pii = get_subject_pii(db, subject)
        index = _load_index(db, snapshot_ids)

        matched: dict[str, set[str]] = {}
        for name in subject_names(pii):
            for cand in index.candidates(name):
                matched.setdefault(cand.record_id, set()).update(cand.matched_keys)
        for record_id, keys in sorted(matched.items()):
            db.add(
                ScreeningCandidate(
                    run_id=run_id,
                    watchlist_record_id=record_id,
                    blocking_keys=sorted(keys),
                )
            )
        db.commit()
        return {
            **context,
            "blocking": {
                "status": "complete",
                "snapshot_ids": snapshot_ids,
                "candidate_count": len(matched),
            },
        }
