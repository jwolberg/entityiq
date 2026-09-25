"""Disposition engine and frozen decision records (IS2-T4, ticket 0039).

``decide(bundle)`` is a pure function of the frozen input bundle:

    {"subject": {...}, "candidates": [{"candidate_id", "record": {...}, ...}],
     "rule": {...}, "normalizer_version": "n1", "source_status": {...}}

The pipeline stage and replay (ticket 0040) both call it, so a replayed
decision can't drift from the original. It needs no list or network access.

Rules (PRD-IDV F8–F12, C2):
  - band per candidate: score >= match_at → MATCH; < clear_below → CLEAR;
    otherwise REVIEW.
  - a name match with a DOB or ID conflict is floored at REVIEW: lists
    carry errors, so a conflict lowers the band but can't clear on its own.
  - the run's disposition is the most severe candidate band (no candidates
    → CLEAR).
  - guarded auto-CLEAR: CLEAR closes without a human only when every source
    answered; otherwise it becomes REVIEW.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.screening.names import NORMALIZER_VERSION
from app.screening.scoring import NAME_TERMS, score_pair

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_SEVERITY = {"CLEAR": 0, "REVIEW": 1, "MATCH": 2}
_FULL_NAME = (
    "name_exact_normalized",
    "name_token_reordered",
    "name_translit_equivalent",
)
_CONFLICTS = ("dob_conflict", "dob_partial_conflict", "id_number_conflict")


def band_with_reason(
    score: float, terms: list[dict], thresholds: dict
) -> tuple[str, str]:
    """A candidate's band and the rule that put it there.

    Shared by ``decide()`` and the decision explanation (ticket 0067), so the
    explanation can't drift from the verdict. Needs only stored term names,
    the score and the thresholds, never subject values.
    """
    if score >= thresholds["match_at"]:
        return "MATCH", "score_at_or_above_match"
    if score >= thresholds["clear_below"]:
        return "REVIEW", "between_thresholds"
    names = {t["name"] for t in terms}
    if names & set(NAME_TERMS[:4]) and names & set(_CONFLICTS):
        return "REVIEW", "conflict_floor"
    return "CLEAR", "score_below_clear"


def run_reason(candidates: list[dict], source_status: dict) -> tuple[str, bool, str]:
    """The run's disposition, whether it auto-closed, and why (C2, N4).

    ``candidates`` need only a ``band``.
    """
    disposition = max(
        (c["band"] for c in candidates), key=_SEVERITY.__getitem__, default="CLEAR"
    )
    if disposition != "CLEAR":
        return disposition, False, "rollup_most_severe"
    if any(status == "unavailable" for status in source_status.values()):
        return "REVIEW", False, "auto_clear_blocked_by_coverage"
    return "CLEAR", True, "auto_clear_allowed" if candidates else "no_candidates"


def _public_terms(terms: list[dict]) -> list[dict]:
    """Terms without subject values: safe to store unencrypted for display."""
    return [{k: v for k, v in t.items() if k != "subject_value"} for t in terms]


def decide(bundle: dict) -> dict:
    rule = bundle["rule"]
    subject = bundle["subject"]
    cands = bundle["candidates"]

    scored = [(c, score_pair(subject, c["record"], rule)) for c in cands]
    frequency = sum(
        1 for _c, (_s, terms) in scored if any(t["name"] in _FULL_NAME for t in terms)
    )
    if frequency >= rule["common_name_threshold"]:
        scored = [
            (c, score_pair(subject, c["record"], rule, name_frequency=frequency))
            for c in cands
        ]

    results = []
    for cand, (score, terms) in scored:
        results.append(
            {
                "candidate_id": cand["candidate_id"],
                "record_id": cand["record"]["id"],
                "score": score,
                "band": band_with_reason(score, terms, rule["thresholds"])[0],
                "terms": _public_terms(terms),
            }
        )

    disposition, auto_closed, _reason = run_reason(
        results, bundle.get("source_status", {})
    )
    return {
        "disposition": disposition,
        "auto_closed": auto_closed,
        "top_score": max((r["score"] for r in results), default=None),
        "candidates": results,
    }


class DisposeStage:
    """Build the frozen bundle, decide, and write the append-only decision."""

    name = "dispose"
    # Runs even when the run budget is spent, so every run gets a decision.
    always_run = True

    def run(self, run_id: str, db: "Session", context: dict) -> dict:
        from app.audit.recorder import record_event  # noqa: PLC0415
        from app.models.watchlist_record import WatchlistRecord  # noqa: PLC0415
        from app.screening.crypto import (  # noqa: PLC0415
            encrypt_for_subject,
            get_subject_pii,
        )
        from app.screening.models import (  # noqa: PLC0415
            ScreeningCandidate,
            ScreeningDecision,
            ScreeningRuleVersion,
            ScreeningRun,
            ScreeningSubject,
        )
        from app.screening.rules import current_rule  # noqa: PLC0415
        from app.screening.stages import record_dict  # noqa: PLC0415

        run = db.get(ScreeningRun, run_id)
        rule = (
            db.get(ScreeningRuleVersion, run.rule_version_id)
            if run.rule_version_id
            else current_rule(db)
        )
        subject = db.get(ScreeningSubject, run.subject_id)
        candidates = (
            db.query(ScreeningCandidate)
            .filter_by(run_id=run_id)
            .order_by(ScreeningCandidate.watchlist_record_id)
            .all()
        )
        records = (
            {
                r.id: r
                for r in db.query(WatchlistRecord).filter(
                    WatchlistRecord.id.in_([c.watchlist_record_id for c in candidates])
                )
            }
            if candidates
            else {}
        )

        bundle = {
            "subject": get_subject_pii(db, subject),
            "candidates": [
                {
                    "candidate_id": c.id,
                    "record": record_dict(records[c.watchlist_record_id]),
                    "source": records[c.watchlist_record_id].source,
                    "source_entry_id": records[c.watchlist_record_id].source_entry_id,
                    "snapshot_id": records[c.watchlist_record_id].snapshot_id,
                    "blocking_keys": c.blocking_keys,
                }
                for c in candidates
            ],
            "rule": rule.config,
            "normalizer_version": NORMALIZER_VERSION,
            "source_status": {
                k: v
                for k, v in (run.source_availability or {}).items()
                if k != self.name
            },
        }
        result = decide(bundle)
        ciphertext, nonce = encrypt_for_subject(db, subject.id, bundle)
        snapshot_ids = (context.get("blocking") or {}).get("snapshot_ids") or []

        decision = ScreeningDecision(
            run_id=run_id,
            rule_version_id=rule.id,
            system_disposition=result["disposition"],
            auto_closed=result["auto_closed"],
            snapshot_ids=snapshot_ids,
            thresholds=rule.config["thresholds"],
            terms=[
                {k: v for k, v in c.items() if k != "terms"} | {"terms": c["terms"]}
                for c in result["candidates"]
            ],
            top_score=result["top_score"],
            frozen_ciphertext=ciphertext,
            frozen_nonce=nonce,
            normalizer_version=NORMALIZER_VERSION,
        )
        db.add(decision)
        db.flush()
        record_event(
            db,
            "screening.decided",
            payload={
                "run_id": run_id,
                "decision_id": decision.id,
                "disposition": result["disposition"],
                "auto_closed": result["auto_closed"],
                "rule_version": rule.version,
            },
        )
        db.commit()
        return {
            **context,
            "decision": {
                "status": "complete",
                "disposition": result["disposition"],
                "auto_closed": result["auto_closed"],
            },
        }
