"""PII retention job (ADR-0002; tickets 0002, 0020).

Anonymizes rows in place once they pass their retention window. Never
hard-deletes — foreign keys, scores and audit history stay intact
(docs/decisions/0002-pii-retention-policy.md §2). The audit log itself is
append-only and is never touched by this job (ARCHITECTURE § 6,
app/audit/recorder.py) — anonymized rows are referred to by id, never
rewritten in the audit trail.

Run as a script/module entry point:

    python -m app.db.retention

Or as a Celery task (``entityiq.run_retention`` in app.worker) — this ticket
wires the task only; scheduling it on a beat/cron is a deployment concern.

Windows (env-configurable, ADR-0002 §2):
  ENTITYIQ_RETENTION_NETWORK_DAYS     Submission network metadata (default 90)
  ENTITYIQ_RETENTION_REVIEWED_DAYS    Reviewed submissions' PII (default 1825)
  ENTITYIQ_RETENTION_UNREVIEWED_DAYS  Never-reviewed submissions' PII (default 180)

Anonymization rules (ADR-0002 §2):
  - Network metadata: truncate source_ip to /24 (IPv4) or /48 (IPv6); null
    user_agent and forwarded_headers.
  - Submitted PII (tax_id, billing_address, phone, requester_full_name,
    linkedin_url): nulled. work_email is NOT NULL in the schema, so it is
    replaced with ANONYMIZED_EMAIL instead of nulled — the one deliberate
    deviation from "null the PII fields" (see docs/implementation-notes.md).
  - company_name, domain, country and all scores/evidence/triage data are
    left untouched.
  - Requester-association evidence (ticket 0020): the LinkedIn adapter's
    ``associated_people`` list (a list of real names) is stripped from the
    linked ``linkedin_presence`` Evidence row's raw_payload, on the same
    schedule as the submission's own PII. The company-level parts of that
    payload (name, employee_count, ...) and the linkedin_requester_match
    true/false evidence (a derived fact, not a name) are left alone.

Idempotency: rather than a separate "anonymized" flag column, we detect
already-done rows structurally:
  - network metadata: user_agent and forwarded_headers are already None AND
    source_ip (if present) already carries a "/" CIDR suffix.
  - submitted PII: work_email already equals ANONYMIZED_EMAIL.
Both checks are cheap and mean re-running the job is always safe.

Cross-dialect note: SQLite's DateTime(timezone=True) columns come back
naive (Postgres returns them tz-aware already) — cutoff comparisons are done
in Python, after normalizing every loaded datetime to aware-UTC via
``_as_aware_utc``, rather than at the SQL layer, so the comparisons are
dialect-independent (and the job runs infrequently enough that a full-table
Python-side filter is not a scaling concern for this MVP).

Writes exactly one audit event per run, with the row counts per category —
and only when something was actually anonymized, so idempotent no-op runs
don't spam the audit log:
  event_type = "retention.anonymized"
  payload = {"network_metadata": N, "reviewed_pii": N, "unreviewed_pii": N,
             "requester_association": N}
"""

from __future__ import annotations

import ipaddress
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Placeholder for the one PII field that's NOT NULL in the schema
# (Submission.work_email) — anonymization replaces the value rather than
# nulling it, since the column can't hold NULL.
ANONYMIZED_EMAIL = "redacted@anonymized.invalid"

# Submission columns that hold the submitter's own PII — everything ADR-0002
# §1 lists as personal data except work_email (handled separately below,
# since it's NOT NULL) and company_name/domain/country, which ADR-0002 §2
# explicitly keeps (company-level, not personal).
_SUBMITTED_PII_FIELDS = (
    "tax_id",
    "billing_address",
    "phone",
    "requester_full_name",
    "linkedin_url",
)

# The LinkedIn evidence field whose raw_payload carries a list of real names
# (ticket 0020) — the requester-association evidence that follows the
# submitted-PII retention schedule.
_REQUESTER_ASSOCIATION_EVIDENCE_FIELD = "linkedin_presence"
_REQUESTER_ASSOCIATION_PAYLOAD_KEY = "associated_people"


def _network_days() -> int:
    return int(os.environ.get("ENTITYIQ_RETENTION_NETWORK_DAYS", "90"))


def _reviewed_days() -> int:
    return int(os.environ.get("ENTITYIQ_RETENTION_REVIEWED_DAYS", "1825"))


def _unreviewed_days() -> int:
    return int(os.environ.get("ENTITYIQ_RETENTION_UNREVIEWED_DAYS", "180"))


def _as_aware_utc(dt: datetime) -> datetime:
    """Normalize a DB-loaded datetime to aware UTC.

    SQLite drops tzinfo on read even for DateTime(timezone=True) columns;
    Postgres preserves it. Every value this app writes is UTC, so a naive
    value is assumed to already be UTC.
    """
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _truncate_ip(ip: str) -> str | None:
    """IPv4 -> /24, IPv6 -> /48 (ADR-0002 §2). None for an unparsable value."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    prefix = 24 if addr.version == 4 else 48
    network = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
    return str(network)


def _network_already_anonymized(sub) -> bool:
    if sub.user_agent is not None or sub.forwarded_headers is not None:
        return False
    if sub.source_ip is not None and "/" not in sub.source_ip:
        return False
    return True


def _anonymize_network_metadata(db: "Session", *, now: datetime) -> int:
    from sqlalchemy import or_  # noqa: PLC0415

    from app.models.submission import Submission  # noqa: PLC0415

    cutoff = now - timedelta(days=_network_days())
    candidates = (
        db.query(Submission)
        .filter(
            or_(
                Submission.source_ip.isnot(None),
                Submission.user_agent.isnot(None),
                Submission.forwarded_headers.isnot(None),
            )
        )
        .all()
    )

    count = 0
    for sub in candidates:
        if _as_aware_utc(sub.submitted_at) > cutoff:
            continue
        if _network_already_anonymized(sub):
            continue
        if sub.source_ip is not None:
            # An unparsable value can't be truncated, so drop it entirely
            # rather than keep the raw string (and re-flag it every pass).
            sub.source_ip = _truncate_ip(sub.source_ip)
        sub.user_agent = None
        sub.forwarded_headers = None
        count += 1
    return count


def _latest_review_decision(db: "Session", submission_id: str) -> datetime | None:
    """Most recent review decision across all of this submission's runs.

    None means the submission has never been reviewed (any of its runs).
    """
    from app.models.review import Review  # noqa: PLC0415
    from app.models.verification_run import VerificationRun  # noqa: PLC0415

    latest = (
        db.query(Review.decided_at)
        .join(VerificationRun, Review.verification_run_id == VerificationRun.id)
        .filter(VerificationRun.submission_id == submission_id)
        .filter(Review.decided_at.isnot(None))
        .order_by(Review.decided_at.desc())
        .first()
    )
    return _as_aware_utc(latest[0]) if latest else None


def _anonymize_submission_pii(sub) -> None:
    for field in _SUBMITTED_PII_FIELDS:
        setattr(sub, field, None)
    sub.work_email = ANONYMIZED_EMAIL


def _anonymize_requester_association(
    db: "Session", *, submission_ids: list[str]
) -> int:
    """Strip ``associated_people`` from linked LinkedIn evidence (ticket 0020).

    Called for every submission whose PII is anonymized (this pass or earlier),
    so evidence created by a later re-analysis is scrubbed too. Idempotent:
    rows without the key are skipped.
    """
    if not submission_ids:
        return 0

    from app.models.evidence import Evidence  # noqa: PLC0415
    from app.models.verification_run import VerificationRun  # noqa: PLC0415

    run_ids = [
        row[0]
        for row in db.query(VerificationRun.id)
        .filter(VerificationRun.submission_id.in_(submission_ids))
        .all()
    ]
    if not run_ids:
        return 0

    rows = (
        db.query(Evidence)
        .filter(
            Evidence.verification_run_id.in_(run_ids),
            Evidence.field == _REQUESTER_ASSOCIATION_EVIDENCE_FIELD,
        )
        .all()
    )

    count = 0
    for ev in rows:
        payload = ev.raw_payload or {}
        if _REQUESTER_ASSOCIATION_PAYLOAD_KEY not in payload:
            continue
        # Reassign (not in-place mutation) — JSON columns aren't tracked for
        # dirty-checking on in-place dict mutation without MutableDict.
        ev.raw_payload = {
            k: v for k, v in payload.items() if k != _REQUESTER_ASSOCIATION_PAYLOAD_KEY
        }
        count += 1
    return count


def run_retention(db: "Session", *, now: datetime | None = None) -> dict[str, int]:
    """Run one retention pass. Idempotent.

    Returns the same counts dict written to the audit event's payload.
    """
    from app.models.submission import Submission  # noqa: PLC0415

    now = now or datetime.now(tz=timezone.utc)

    network_count = _anonymize_network_metadata(db, now=now)

    reviewed_cutoff = now - timedelta(days=_reviewed_days())
    unreviewed_cutoff = now - timedelta(days=_unreviewed_days())

    reviewed_count = 0
    unreviewed_count = 0
    anonymized_submission_ids: list[str] = []

    pii_candidates = (
        db.query(Submission).filter(Submission.work_email != ANONYMIZED_EMAIL).all()
    )
    for sub in pii_candidates:
        decided_at = _latest_review_decision(db, sub.id)
        if decided_at is not None:
            if decided_at <= reviewed_cutoff:
                _anonymize_submission_pii(sub)
                reviewed_count += 1
                anonymized_submission_ids.append(sub.id)
        elif _as_aware_utc(sub.submitted_at) <= unreviewed_cutoff:
            _anonymize_submission_pii(sub)
            unreviewed_count += 1
            anonymized_submission_ids.append(sub.id)

    # Sweep every anonymized submission, not only this pass's: re-analysis
    # after anonymization can create fresh LinkedIn evidence with names.
    already_anonymized = [
        row[0]
        for row in db.query(Submission.id)
        .filter(Submission.work_email == ANONYMIZED_EMAIL)
        .all()
    ]
    requester_association_count = _anonymize_requester_association(
        db,
        submission_ids=sorted(set(anonymized_submission_ids) | set(already_anonymized)),
    )

    db.commit()

    counts = {
        "network_metadata": network_count,
        "reviewed_pii": reviewed_count,
        "unreviewed_pii": unreviewed_count,
        "requester_association": requester_association_count,
    }

    if any(counts.values()):
        from app.audit.recorder import record_event  # noqa: PLC0415

        record_event(
            db=db,
            event_type="retention.anonymized",
            payload=counts,
            description=(
                f"Retention job anonymized {network_count} network-metadata "
                f"row(s), {reviewed_count} reviewed-PII row(s), "
                f"{unreviewed_count} unreviewed-PII row(s), and "
                f"{requester_association_count} requester-association "
                "evidence row(s)."
            ),
        )

    return counts


def main() -> None:
    """CLI/module entry point: ``python -m app.db.retention``."""
    logging.basicConfig(level=logging.INFO)
    from app.db.session import SessionLocal  # noqa: PLC0415

    db = SessionLocal()
    try:
        counts = run_retention(db)
        logger.info("retention job complete: %s", counts)
    finally:
        db.close()


if __name__ == "__main__":
    main()
