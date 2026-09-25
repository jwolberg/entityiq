"""Decision explanation for one screening run (ticket 0067; PLAN-IS-EVIDENCE).

``build_explanation`` is pure: stored rows in, dict out. It walks through the
decisions a run made (lists checked → blocking → term scoring → banding →
disposition → human actions) and cites the list entry and snapshot behind
every term.

It is derived only from stored rows and never decrypts the subject, so:
  - it still works after a crypto-shred (ADR-0004);
  - it never carries subject values (N5). Subject-side citations say which
    field was compared, not what it held.

Bands and the run disposition are recomputed with the same
``band_with_reason`` / ``run_reason`` that ``decide()`` uses, and compared
with what was stored (``consistent``), so a code change that would explain a
decision differently from how it was made is visible rather than silent.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from app.lists.ingest import SOURCE_NAMES, SOURCES
from app.screening.blocking import DEFAULT_CAP
from app.screening.dispose import band_with_reason, run_reason

TERM_LABELS = {
    "name_exact_normalized": "Name matches exactly (after normalization)",
    "name_token_reordered": "Same name, different word order",
    "name_translit_equivalent": "Name is a transliteration or spelling variant",
    "name_initials_compatible": "Name matches, with initials",
    "name_partial_overlap": "Part of the name matches",
    "dob_full_match": "Full date of birth matches",
    "dob_year_match": "Year (or year and month) of birth matches",
    "dob_within_range": "Birth year falls in the listed range",
    "dob_conflict": "Full dates of birth differ",
    "dob_partial_conflict": "Dates of birth disagree (at least one is partial)",
    "id_number_match": "ID document number matches",
    "id_number_conflict": "Same ID type and issuer, different number",
    "nationality_match": "Nationality matches",
    "nationality_conflict": "Nationality differs",
    "pob_match": "Place of birth matches",
    "common_name_penalty": "Common name, not corroborated",
}

BAND_REASONS = {
    "score_at_or_above_match": "Score {score} is at or above the match threshold "
    "({match_at}) → MATCH.",
    "between_thresholds": "Score {score} is at or above the clear threshold "
    "({clear_below}) and below the match threshold ({match_at}) → REVIEW.",
    "conflict_floor": "Score {score} is below the clear threshold ({clear_below}), "
    "but a name match with a conflicting date of birth or ID can't clear on its "
    "own (lists carry errors) → REVIEW.",
    "score_below_clear": "Score {score} is below the clear threshold "
    "({clear_below}) → CLEAR.",
}

RUN_REASONS = {
    "rollup_most_severe": "The run takes its most severe candidate band: "
    "{disposition}.",
    "auto_clear_allowed": "Every candidate is below the clear threshold and every "
    "required list was available and fresh, so the run closed as CLEAR without "
    "a human.",
    "no_candidates": "No list record matched any part of the subject's name, and "
    "every required list was available and fresh, so the run closed as CLEAR "
    "without a human.",
    "auto_clear_blocked_by_coverage": "The candidates alone would clear, but "
    "{gaps} was missing or stale, so the run can't auto-clear → REVIEW.",
}

# "ofac_sdn:1234@<snapshot>#dobs" and "screening_subject:<id>#dob"
_RECORD_LOCATOR = re.compile(
    r"^(?P<source>[^:]+):(?P<entry>.*)@(?P<snap>[^#]+)#(?P<field>.+)$"
)
_SUBJECT_LOCATOR = re.compile(r"^screening_subject:[^#]+#(?P<field>.+)$")


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


def _direction(weight: float) -> str:
    return "+" if weight > 0 else "-" if weight < 0 else "0"


def _record_ref(record) -> dict:
    return {
        "source": record.source,
        "display_name": SOURCE_NAMES.get(record.source, record.source),
        "entry_id": record.source_entry_id,
        "snapshot_id": record.snapshot_id,
        "primary_name": record.primary_name,
        "program": record.program,
    }


def _citation(claim, snapshots: dict) -> dict:
    if claim.about == "subject":
        m = _SUBJECT_LOCATOR.match(claim.locator)
        return {
            "about": "subject",
            "source": claim.source,
            "field": m.group("field") if m else None,
            "submitted_at": _iso(claim.retrieved_at),
        }
    m = _RECORD_LOCATOR.match(claim.locator)
    snap = snapshots.get(m.group("snap")) if m else None
    spec = SOURCES.get(claim.source)
    return {
        "about": "record",
        "source": claim.source,
        "display_name": SOURCE_NAMES.get(claim.source, claim.source),
        "entry_id": m.group("entry") if m else None,
        "field": m.group("field") if m else claim.field,
        "snapshot_id": m.group("snap") if m else None,
        # The list's retrieval time, from the snapshot row: older claims
        # stored the run's start time instead (gap G1).
        "snapshot_retrieved_at": _iso(snap.retrieved_at) if snap else None,
        "content_sha256": snap.content_sha256 if snap else None,
        "source_url": spec.url if spec else None,
        "locator": claim.locator,
    }


def _fmt(x: float) -> str:
    return f"{x:g}"


def build_explanation(
    *,
    run,
    subject,
    decision,
    rule_config: dict,
    rule_version: int | None,
    candidates: dict,
    records: dict,
    term_rows: list,
    claims: dict,
    snapshots: dict,
    dispositions: list,
    replays: list,
    show_operator_detail: bool,
    required_sources: list[str],
    max_list_age_days: float,
) -> dict:
    thresholds = decision.thresholds
    availability = run.source_availability or {}

    # [1] Lists checked: every required list, plus any other snapshot used.
    snaps_by_source = {
        snapshots[sid].source: snapshots[sid]
        for sid in decision.snapshot_ids
        if sid in snapshots
    }
    listed = list(dict.fromkeys([*required_sources, *sorted(snaps_by_source)]))
    lists = []
    for source in listed:
        snap = snaps_by_source.get(source)
        spec = SOURCES.get(source)
        lists.append(
            {
                "source": source,
                "display_name": SOURCE_NAMES.get(source, source),
                "required": source in required_sources,
                "status": availability.get(
                    f"list:{source}", "complete" if snap else "unavailable"
                ),
                "snapshot_id": snap.id if snap else None,
                "retrieved_at": _iso(snap.retrieved_at) if snap else None,
                "content_sha256": snap.content_sha256 if snap else None,
                "record_count": snap.record_count if snap else None,
                "source_url": spec.url if spec else None,
            }
        )

    claim_ids = {(t.candidate_id, t.name): t.claim_ids for t in term_rows}
    entries = decision.terms or []

    # [2] Blocking
    blocking = []
    # [3] Scoring and [4] Banding
    scoring = []
    banding = []
    cited: set[str] = set()
    for entry in entries:
        cand = candidates[entry["candidate_id"]]
        record = records[cand.watchlist_record_id]
        ref = _record_ref(record)
        blocking.append(
            {
                "candidate_id": cand.id,
                "record_ref": ref,
                "matched_keys": cand.blocking_keys,
            }
        )
        terms = []
        for t in entry["terms"]:
            cites = claim_ids.get((cand.id, t["name"]), [])
            cited.update(cites)
            terms.append(
                {
                    "name": t["name"],
                    "label": TERM_LABELS.get(t["name"], t["name"]),
                    "weight": t["weight"],
                    "direction": _direction(t["weight"]),
                    "record_field": t.get("record_field"),
                    "record_value": t.get("record_value"),
                    "citations": cites,
                }
            )
        scoring.append(
            {
                "candidate_id": cand.id,
                "record_ref": ref,
                "score": entry["score"],
                "terms": terms,
            }
        )
        band, reason = band_with_reason(entry["score"], entry["terms"], thresholds)
        banding.append(
            {
                "candidate_id": cand.id,
                "score": entry["score"],
                "band": entry["band"],
                "reason_code": reason,
                "reason_text": BAND_REASONS[reason].format(
                    score=_fmt(entry["score"]),
                    clear_below=_fmt(thresholds["clear_below"]),
                    match_at=_fmt(thresholds["match_at"]),
                ),
                "consistent": band == entry["band"],
            }
        )

    # [5] Run disposition (the same source_status the dispose stage saw)
    status = {k: v for k, v in availability.items() if k != "dispose"}
    disposition, auto_closed, reason = run_reason(entries, status)
    gaps = sorted(k for k, v in status.items() if v == "unavailable")
    frequency = next(
        (
            t["record_value"]
            for e in entries
            for t in e["terms"]
            if t["name"] == "common_name_penalty"
        ),
        None,
    )
    common = (
        {
            "frequency": frequency,
            "threshold": rule_config.get("common_name_threshold"),
            "applied": True,
        }
        if frequency is not None
        else None
    )

    # [6] Human actions
    human_dispositions = [
        {
            "disposition": d.disposition,
            "created_at": _iso(d.created_at),
            **(
                {"notes": d.notes, "operator_id": d.operator_id}
                if show_operator_detail
                else {}
            ),
        }
        for d in dispositions
    ]
    human_replays = [
        {
            "reproduced": (e.payload or {}).get("reproduced"),
            "shredded": (e.payload or {}).get("shredded"),
            "occurred_at": _iso(e.occurred_at),
            **({"operator_id": e.operator_id} if show_operator_detail else {}),
        }
        for e in replays
    ]

    return {
        "run_id": run.id,
        "decision_id": decision.id,
        "decided_at": _iso(decision.created_at),
        "rule_version": rule_version,
        "normalizer_version": decision.normalizer_version,
        "subject_shredded": subject.shredded_at is not None,
        "steps": [
            {
                "kind": "sources",
                "max_age_days": max_list_age_days,
                "lists": lists,
            },
            {
                "kind": "blocking",
                "candidate_count": len(blocking),
                "cap": DEFAULT_CAP,
                "candidates": blocking,
            },
            {"kind": "scoring", "candidates": scoring},
            {"kind": "banding", "thresholds": thresholds, "candidates": banding},
            {
                "kind": "disposition",
                "system_disposition": decision.system_disposition,
                "auto_closed": decision.auto_closed,
                "reason_code": reason,
                "reason_text": RUN_REASONS[reason].format(
                    disposition=disposition, gaps=", ".join(gaps) or "a list"
                ),
                "coverage_gaps": gaps,
                "common_name": common,
                "consistent": (disposition, auto_closed)
                == (decision.system_disposition, decision.auto_closed),
            },
            {
                "kind": "human",
                "dispositions": human_dispositions,
                "replays": human_replays,
            },
        ],
        "citations": {
            cid: _citation(claims[cid], snapshots)
            for cid in sorted(cited)
            if cid in claims
        },
    }
