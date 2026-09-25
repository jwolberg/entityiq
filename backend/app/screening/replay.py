"""Replay a screening decision from its frozen inputs (IS2-T5, ticket 0040).

PRD-IDV F16/N1: decrypt the frozen bundle and run the same pure ``decide``
the pipeline used. This path reads no watchlists and makes no network call,
so a decision still reproduces after the lists change. A crypto-shredded
subject can't be replayed; that's reported, not treated as a mismatch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.screening.crypto import SubjectShredded, decrypt_for_subject
from app.screening.dispose import decide
from app.screening.models import ScreeningDecision, ScreeningRun

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _original(decision: ScreeningDecision) -> dict:
    return {
        "disposition": decision.system_disposition,
        "auto_closed": decision.auto_closed,
        "candidates": [
            {"candidate_id": c["candidate_id"], "score": c["score"], "band": c["band"]}
            for c in decision.terms
        ],
    }


def compare_results(original: dict, replayed: dict) -> list[dict]:
    diffs: list[dict] = []
    for field in ("disposition", "auto_closed"):
        if original.get(field) != replayed.get(field):
            diffs.append(
                {
                    "field": field,
                    "original": original.get(field),
                    "replayed": replayed.get(field),
                }
            )
    before = {c["candidate_id"]: c for c in original.get("candidates", [])}
    after = {c["candidate_id"]: c for c in replayed.get("candidates", [])}
    for cid in sorted(set(before) | set(after)):
        a, b = before.get(cid), after.get(cid)
        if a is None or b is None:
            diffs.append({"field": f"{cid}", "original": a, "replayed": b})
            continue
        for key in ("score", "band"):
            if a[key] != b[key]:
                diffs.append(
                    {"field": f"{cid}.{key}", "original": a[key], "replayed": b[key]}
                )
    return diffs


def replay_decision(db: "Session", decision: ScreeningDecision) -> dict:
    run = db.get(ScreeningRun, decision.run_id)
    original = _original(decision)
    try:
        bundle = decrypt_for_subject(
            db, run.subject_id, decision.frozen_ciphertext, decision.frozen_nonce
        )
    except SubjectShredded:
        return {
            "reproduced": None,
            "shredded": True,
            "original": original,
            "replayed": None,
            "differences": [],
        }
    result = decide(bundle)
    replayed = {
        "disposition": result["disposition"],
        "auto_closed": result["auto_closed"],
        "candidates": [
            {"candidate_id": c["candidate_id"], "score": c["score"], "band": c["band"]}
            for c in result["candidates"]
        ],
    }
    diffs = compare_results(original, replayed)
    return {
        "reproduced": not diffs,
        "shredded": False,
        "original": original,
        "replayed": replayed,
        "differences": diffs,
    }
