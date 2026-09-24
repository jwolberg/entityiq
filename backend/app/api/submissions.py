"""Submission endpoint — POST /submissions.

Accepts required + optional fields, captures network metadata server-side,
persists a Submission row + VerificationRun, and enqueues the run.

TRUSTED-IP INVARIANT (ARCHITECTURE § 5):
  The authoritative client IP is request.client.host — the peer address assigned
  by the trusted edge/proxy.  X-Forwarded-For and similar headers are client-
  spoofable and must NOT be used as the source IP.  We capture them separately
  (forwarded_headers) for context only.
"""

import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.audit.recorder import record_event
from app.auth.service import Principal, get_principal
from app.db.session import SessionLocal
from app.models.entity import Entity
from app.models.submission import Submission
from app.models.verification_run import VerificationRun
from app.pipeline.normalize import is_free_email_domain as _is_free_email_domain
from app.schemas.submission import SubmissionRequest, SubmissionResponse

router = APIRouter(prefix="/submissions", tags=["submissions"])

# ---------------------------------------------------------------------------
# Free / disposable email domain detection
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Trusted-IP extraction
# ---------------------------------------------------------------------------

# TRUSTED_PROXY_DEPTH controls how many proxy hops the edge adds.
# Setting it to 0 (default) means we always use the connection peer (the
# edge-assigned IP).  If behind a load balancer that adds one hop, set to 1
# and we'll use the last element of X-Forwarded-For — but this is off by
# default to prevent spoofing.
_TRUSTED_PROXY_DEPTH: int = int(os.environ.get("TRUSTED_PROXY_DEPTH", "0"))


def _get_trusted_client_ip(request: Request) -> str | None:
    """Return the authoritative client IP.

    Default (TRUSTED_PROXY_DEPTH=0): the direct connection peer — the IP
    assigned by the trusted edge.  This is NOT the raw X-Forwarded-For value.

    If TRUSTED_PROXY_DEPTH > 0 the operator has explicitly configured the
    number of trusted proxy hops; we walk back that many entries from the
    right side of X-Forwarded-For (which the trusted proxy appended).

    Either way, unauthenticated client-supplied headers cannot override the
    edge-assigned value at depth 0.
    """
    if _TRUSTED_PROXY_DEPTH == 0:
        # Most secure default: use the TCP connection peer address.
        return request.client.host if request.client else None

    # Operator-configured depth: pick the (depth)th entry from the right of
    # X-Forwarded-For.  The trusted edge appends the real client IP last.
    xff = request.headers.get("x-forwarded-for", "")
    parts = [p.strip() for p in xff.split(",") if p.strip()]
    if len(parts) >= _TRUSTED_PROXY_DEPTH:
        return parts[-_TRUSTED_PROXY_DEPTH]
    # Fallback to peer if the header is shorter than expected.
    return request.client.host if request.client else None


def _collect_forwarded_headers(request: Request) -> dict:
    """Collect forwarded/proxy headers for context (not used as source_ip)."""
    header_names = [
        "x-forwarded-for",
        "x-real-ip",
        "x-forwarded-host",
        "x-forwarded-proto",
        "forwarded",
        "cf-connecting-ip",
        "true-client-ip",
    ]
    return {h: request.headers[h] for h in header_names if h in request.headers}


# ---------------------------------------------------------------------------
# DB dependency
# ---------------------------------------------------------------------------


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Submission endpoint
# ---------------------------------------------------------------------------


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SubmissionResponse,
    summary="Submit an enterprise registration for verification",
)
def submit(
    body: SubmissionRequest,
    request: Request,
    db: Session = Depends(_get_db),
    principal: Principal = Depends(get_principal),
) -> SubmissionResponse:
    """Accept a registration submission and enqueue a verification run.

    Requires an authenticated principal — an integrating system (X-API-Key) or
    an operator (Bearer token), matching the report-export endpoint.  The
    submission and a `submission_received` audit event are attributed to the
    calling principal (P2-T12).

    Returns 202 with the submission_id, run_id, and a pending status.
    Idempotent on idempotency_key: a duplicate key returns the original run
    without creating a new one.
    """
    # --- Idempotency check ---
    if body.idempotency_key:
        existing_sub = (
            db.query(Submission)
            .filter(Submission.idempotency_key == body.idempotency_key)
            .first()
        )
        if existing_sub is not None:
            # Find the most recent run for this submission
            existing_run = (
                db.query(VerificationRun)
                .filter(VerificationRun.submission_id == existing_sub.id)
                .order_by(VerificationRun.created_at.desc())
                .first()
            )
            return SubmissionResponse(
                submission_id=existing_sub.id,
                run_id=existing_run.id if existing_run else "",
                status=existing_run.status if existing_run else "pending",
                is_free_email_domain=_is_free_email_domain(str(body.work_email)),
                message="Duplicate idempotency key — returning existing run.",
            )

    # --- Capture network metadata (server-side only — not from request body) ---
    source_ip = _get_trusted_client_ip(request)
    user_agent = request.headers.get("user-agent")
    forwarded_headers = _collect_forwarded_headers(request)
    endpoint = str(request.url.path)
    submitted_at = datetime.now(tz=timezone.utc)

    # --- Free-email detection ---
    is_free = _is_free_email_domain(str(body.work_email))

    # --- Persist entity (minimal — entity resolution is a later stage) ---
    entity = Entity(
        canonical_name=body.company_name,
        canonical_domain=body.company_domain,
    )
    db.add(entity)
    db.flush()  # assigns entity.id without committing

    # --- Persist submission ---
    submission = Submission(
        idempotency_key=body.idempotency_key,
        company_name=body.company_name,
        domain=body.company_domain,
        work_email=str(body.work_email),
        country=body.country,
        tax_id=body.tax_id or body.registration_number,
        billing_address=body.billing_address,
        phone=body.phone,
        requester_full_name=body.requester_full_name,
        linkedin_url=str(body.linkedin_url) if body.linkedin_url else None,
        extra_metadata=body.extra_metadata,
        entity_id=entity.id,
        api_client_id=principal.api_client_id,
        source_ip=source_ip,
        user_agent=user_agent,
        forwarded_headers=forwarded_headers if forwarded_headers else None,
        endpoint=endpoint,
        submitted_at=submitted_at,
    )
    db.add(submission)
    db.flush()

    # --- Create verification run (pending) ---
    run = VerificationRun(
        submission_id=submission.id,
        entity_id=entity.id,
        status="pending",
    )
    db.add(run)
    db.flush()

    db.commit()

    # --- Attribute the ingest to the calling principal (P2-T12) ---
    record_event(
        db=db,
        event_type=f"{principal.kind}.submission_received",
        operator_id=principal.operator_id,
        api_client_id=principal.api_client_id,
        submission_id=submission.id,
        verification_run_id=run.id,
        payload={
            principal.kind: principal.name,
            "endpoint": endpoint,
            "submitted_at": submitted_at.isoformat(),
        },
        description=(
            f"{principal.kind.capitalize()} {principal.name!r} submitted "
            f"registration {submission.id!r}; run {run.id!r} enqueued."
        ),
    )

    # --- Enqueue the run ---
    # Import here to avoid circular imports; the enqueue function is wired in
    # P1-T2 and is a no-op stub until then.
    from app.pipeline.orchestrator import enqueue_run  # noqa: PLC0415

    enqueue_run(run.id)

    return SubmissionResponse(
        submission_id=submission.id,
        run_id=run.id,
        status="pending",
        is_free_email_domain=is_free,
        message="Submission accepted. Verification run enqueued.",
    )
