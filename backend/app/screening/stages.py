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


DEFAULT_REQUIRED_SOURCES = "ofac_sdn,un_consolidated,eu_fsf,uk_ofsi"


def required_sources() -> list[str]:
    import os  # noqa: PLC0415

    raw = os.environ.get(
        "ENTITYIQ_SCREENING_REQUIRED_SOURCES", DEFAULT_REQUIRED_SOURCES
    )
    return [s.strip() for s in raw.split(",") if s.strip()]


def max_list_age_days() -> float:
    import os  # noqa: PLC0415

    return float(os.environ.get("ENTITYIQ_SCREENING_MAX_LIST_AGE_DAYS", "7"))


def list_coverage(db: "Session", snapshot_ids: list[str]) -> dict[str, str]:
    """``{"list:<source>": "complete"|"unavailable"}`` for every required list.

    A required list with no snapshot, or whose latest snapshot is older than
    ENTITYIQ_SCREENING_MAX_LIST_AGE_DAYS, is unavailable, and an unavailable
    list blocks auto-CLEAR (C2, F9, N4).
    """
    from datetime import datetime, timedelta, timezone  # noqa: PLC0415

    now = datetime.now(tz=timezone.utc)
    latest = (
        {
            s.source: s
            for s in db.query(ListSnapshot).filter(ListSnapshot.id.in_(snapshot_ids))
        }
        if snapshot_ids
        else {}
    )
    coverage = {}
    for source in required_sources():
        snap = latest.get(source)
        fresh = False
        if snap is not None:
            retrieved = snap.retrieved_at
            if retrieved.tzinfo is None:
                retrieved = retrieved.replace(tzinfo=timezone.utc)
            fresh = now - retrieved <= timedelta(days=max_list_age_days())
        coverage[f"list:{source}"] = "complete" if fresh else "unavailable"
    return coverage


class BlockCandidatesStage:
    """Recall-first candidate generation against the current list snapshots.

    Also records per-list coverage on the run (``list:<source>`` entries in
    source_availability) so a missing or stale required list blocks
    auto-CLEAR and shows as a coverage gap.
    """

    name = "block_candidates"

    def run(self, run_id: str, db: "Session", context: dict) -> dict:
        snapshot_ids = current_snapshot_ids(db)
        run = db.get(ScreeningRun, run_id)
        run.source_availability = {
            **(run.source_availability or {}),
            **list_coverage(db, snapshot_ids),
        }
        db.commit()
        if not snapshot_ids:
            return {
                **context,
                "blocking": {"status": "unavailable", "message": "No lists loaded"},
            }

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


def record_dict(record: WatchlistRecord) -> dict:
    return {
        "id": record.id,
        "names": record.names,
        "dobs": record.dobs,
        "pobs": record.pobs,
        "nationalities": record.nationalities,
        "documents": record.documents,
    }


def _full_name_match(terms: list[dict]) -> bool:
    return any(
        t["name"]
        in ("name_exact_normalized", "name_token_reordered", "name_translit_equivalent")
        for t in terms
    )


class ScoreCandidatesStage:
    """Score every candidate with the current rule version (F7–F10, F13).

    Writes claims with provenance for both sides of every comparison and a
    term row citing them. Subject-side claims hold only a locator into the
    encrypted subject record, never the value (N5).
    """

    name = "score_candidates"

    def run(self, run_id: str, db: "Session", context: dict) -> dict:
        from datetime import datetime, timezone  # noqa: PLC0415

        from app.screening.models import ScreeningClaim, ScreeningTerm  # noqa: PLC0415
        from app.screening.rules import current_rule  # noqa: PLC0415
        from app.screening.scoring import score_pair  # noqa: PLC0415

        rule = current_rule(db)
        run = db.get(ScreeningRun, run_id)
        run.rule_version_id = rule.id
        subject = db.get(ScreeningSubject, run.subject_id)
        pii = get_subject_pii(db, subject)
        now = run.started_at or datetime.now(tz=timezone.utc)

        candidates = db.query(ScreeningCandidate).filter_by(run_id=run_id).all()
        records = (
            {
                r.id: r
                for r in db.query(WatchlistRecord)
                .filter(
                    WatchlistRecord.id.in_([c.watchlist_record_id for c in candidates])
                )
                .all()
            }
            if candidates
            else {}
        )

        scored = [
            (
                c,
                score_pair(
                    pii, record_dict(records[c.watchlist_record_id]), rule.config
                ),
            )
            for c in candidates
        ]
        # Common-name frequency: how many candidates match the whole name.
        frequency = sum(1 for _c, (_s, terms) in scored if _full_name_match(terms))
        if frequency >= rule.config["common_name_threshold"]:
            scored = [
                (
                    c,
                    score_pair(
                        pii,
                        record_dict(records[c.watchlist_record_id]),
                        rule.config,
                        name_frequency=frequency,
                    ),
                )
                for c in candidates
            ]

        subject_claims: dict[str, ScreeningClaim] = {}
        top = None
        for cand, (score, terms) in scored:
            record = records[cand.watchlist_record_id]
            cand.score = score
            top = score if top is None else max(top, score)
            for term in terms:
                sfield = term["subject_field"]
                if sfield not in subject_claims:
                    subject_claims[sfield] = ScreeningClaim(
                        run_id=run_id,
                        about="subject",
                        field=sfield,
                        value=None,
                        source="subject_submission",
                        locator=f"screening_subject:{subject.id}#{sfield}",
                        retrieved_at=now,
                    )
                    db.add(subject_claims[sfield])
                rclaim = ScreeningClaim(
                    run_id=run_id,
                    candidate_id=cand.id,
                    about="record",
                    field=term["record_field"],
                    value=term["record_value"],
                    source=record.source,
                    locator=(
                        f"{record.source}:{record.source_entry_id}"
                        f"@{record.snapshot_id}#{term['record_field']}"
                    ),
                    retrieved_at=now,
                )
                db.add(rclaim)
                db.flush()
                db.add(
                    ScreeningTerm(
                        run_id=run_id,
                        candidate_id=cand.id,
                        name=term["name"],
                        weight=term["weight"],
                        claim_ids=[subject_claims[sfield].id, rclaim.id],
                    )
                )
        db.commit()
        return {
            **context,
            "scoring": {
                "status": "complete",
                "rule_version": rule.version,
                "candidates": len(candidates),
                "top_score": top,
            },
        }
