"""Domain-ownership verification endpoints (ticket 0003, plan unit U24).

  POST /ownership/runs/{run_id}/challenges          — issue a challenge
  GET  /ownership/runs/{run_id}/challenges           — list challenges for a run
  POST /ownership/challenges/{challenge_id}/verify   — attempt verification

Protected routes: any authenticated principal — an operator (Bearer session
token) OR an integrating system (X-API-Key) — following the same shared-
endpoint pattern as GET /reports (app.auth.service.get_principal). This lets
either an operator reviewing a case, or the host platform's own onboarding
flow, issue and check a challenge on a registrant's behalf; registrants
themselves never call EntityIQ's API directly (USERS § 4).

Every issue and verify attempt (success or failure) is recorded as an
attributable audit event.

INVARIANT (PRD § Domain Ownership Verification, USERS § 4): a verified
challenge only ever contributes a small, bounded trust signal — see
app.scoring.signals._append_ownership_signals. It never implies the
registrant is authorized to represent the organization, and this router does
not expose any "authorize" or "approve" action.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.audit.recorder import record_event
from app.auth.service import Principal, get_principal
from app.db.session import SessionLocal
from app.models.ownership_challenge import OwnershipChallenge
from app.models.verification_run import VerificationRun
from app.pipeline.ownership import (
    UnknownChallengeMethod,
    attempt_verification,
    dns_txt_record_value,
    html_meta_snippet,
    issue_challenge,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ownership", tags=["ownership"])


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
# Request / response schemas
# ---------------------------------------------------------------------------


class IssueChallengeRequest(BaseModel):
    """Body for POST /ownership/runs/{run_id}/challenges."""

    method: str  # "dns_txt" | "email" | "html_meta"
    # Required for method == "email"; ignored otherwise.
    target: str | None = None


class ChallengeInstructions(BaseModel):
    """Method-specific instructions the registrant needs to complete the challenge."""

    method: str
    # dns_txt
    dns_record_type: str | None = None
    dns_record_name: str | None = None
    dns_record_value: str | None = None
    # html_meta
    html_snippet: str | None = None
    # email
    email_target: str | None = None


class ChallengeResponse(BaseModel):
    challenge_id: str
    run_id: str
    submission_id: str
    domain: str
    method: str
    # None for the email method: the token goes only to the emailed address;
    # returning it would let the issuer verify without the registrant.
    token: str | None
    status: str
    issued_at: str
    verified_at: str | None = None
    instructions: ChallengeInstructions
    message: str


class VerifyChallengeRequest(BaseModel):
    """Body for POST /ownership/challenges/{challenge_id}/verify.

    submitted_token is required for method == "email" (the token the
    registrant got back from the email, e.g. via a link); dns_txt and
    html_meta are re-checked live against the domain and ignore this field.
    """

    submitted_token: str | None = None


class VerifyChallengeResponse(BaseModel):
    challenge_id: str
    method: str
    verified: bool
    status: str
    verified_at: str | None = None
    message: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def email_sender_factory():
    """Email backend for email challenges (patched in tests).

    Unconfigured in production: sending fails loudly and the challenge stays
    pending with ``last_error`` set. The token is never returned to the caller.
    """
    from app.pipeline.ownership import _default_email_sender  # noqa: PLC0415

    return _default_email_sender()


def _instructions_for(challenge: OwnershipChallenge) -> ChallengeInstructions:
    if challenge.method == "dns_txt":
        return ChallengeInstructions(
            method="dns_txt",
            dns_record_type="TXT",
            dns_record_name=challenge.domain,
            dns_record_value=dns_txt_record_value(challenge.token),
        )
    if challenge.method == "html_meta":
        return ChallengeInstructions(
            method="html_meta",
            html_snippet=html_meta_snippet(challenge.token),
        )
    return ChallengeInstructions(method="email", email_target=challenge.target)


def _to_response(challenge: OwnershipChallenge, message: str = "") -> ChallengeResponse:
    return ChallengeResponse(
        challenge_id=challenge.id,
        run_id=challenge.verification_run_id,
        submission_id=challenge.submission_id,
        domain=challenge.domain,
        method=challenge.method,
        token=None if challenge.method == "email" else challenge.token,
        status=challenge.status,
        issued_at=challenge.issued_at.isoformat(),
        verified_at=(
            challenge.verified_at.isoformat() if challenge.verified_at else None
        ),
        instructions=_instructions_for(challenge),
        message=message,
    )


def _principal_ids(principal: Principal) -> tuple[str | None, str | None]:
    """Return (operator_id, api_client_id) for audit attribution."""
    return principal.operator_id, principal.api_client_id


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/runs/{run_id}/challenges",
    response_model=ChallengeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Issue a domain-ownership challenge for a run's submission",
)
def issue_challenge_endpoint(
    run_id: str,
    body: IssueChallengeRequest,
    db: Session = Depends(_get_db),
    principal: Principal = Depends(get_principal),
) -> ChallengeResponse:
    """Issue a challenge token for the domain of the submission behind `run_id`.

    Returns 404 if the run is unknown, 422 for an unknown method or a missing
    email target. The token and method-specific instructions are returned so
    an operator (or the calling integration) can relay them to the
    registrant — registrants never call this API directly.
    """
    operator_id, api_client_id = _principal_ids(principal)

    try:
        challenge = issue_challenge(
            db,
            run_id=run_id,
            method=body.method,
            target=body.target,
            operator_id=operator_id,
            api_client_id=api_client_id,
            email_sender=email_sender_factory(),
        )
    except UnknownChallengeMethod as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except ValueError as exc:
        message = str(exc)
        if "not found" in message:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=message
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message
        ) from exc

    record_event(
        db=db,
        event_type="ownership.challenge_issued",
        operator_id=operator_id,
        api_client_id=api_client_id,
        verification_run_id=challenge.verification_run_id,
        submission_id=challenge.submission_id,
        payload={
            "challenge_id": challenge.id,
            "method": challenge.method,
            "domain": challenge.domain,
        },
        description=(
            f"{principal.name!r} issued a {challenge.method} domain-ownership "
            f"challenge for {challenge.domain!r}."
        ),
    )

    return _to_response(
        challenge,
        message=(
            f"Challenge issued. Relay the {challenge.method} instructions "
            "to the registrant."
        ),
    )


@router.get(
    "/runs/{run_id}/challenges",
    response_model=list[ChallengeResponse],
    summary="List domain-ownership challenges issued for a run",
)
def list_challenges_endpoint(
    run_id: str,
    db: Session = Depends(_get_db),
    _principal: Principal = Depends(get_principal),
) -> list[ChallengeResponse]:
    """List all challenges issued under `run_id`, most recent first."""
    run = db.get(VerificationRun, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Verification run {run_id!r} not found.",
        )

    rows = (
        db.query(OwnershipChallenge)
        .filter(OwnershipChallenge.verification_run_id == run_id)
        .order_by(OwnershipChallenge.issued_at.desc())
        .all()
    )
    return [_to_response(c) for c in rows]


@router.post(
    "/challenges/{challenge_id}/verify",
    response_model=VerifyChallengeResponse,
    summary="Attempt to verify a domain-ownership challenge",
)
def verify_challenge_endpoint(
    challenge_id: str,
    body: VerifyChallengeRequest,
    db: Session = Depends(_get_db),
    principal: Principal = Depends(get_principal),
) -> VerifyChallengeResponse:
    """Run the method-specific proof check for one challenge.

    Always returns 200 with `verified` reflecting the outcome — an
    absent/incorrect token is a normal "not yet" result (e.g. DNS still
    propagating), not a client error, so it can be safely retried.

    Returns 404 if the challenge is unknown, 422 if method == "email" and no
    submitted_token was given.
    """
    challenge = db.get(OwnershipChallenge, challenge_id)
    if challenge is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ownership challenge {challenge_id!r} not found.",
        )

    if (
        challenge.method == "email"
        and challenge.status != "verified"
        and not body.submitted_token
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="submitted_token is required to verify an email challenge.",
        )

    verified = attempt_verification(db, challenge, submitted_token=body.submitted_token)

    operator_id, api_client_id = _principal_ids(principal)
    record_event(
        db=db,
        event_type=(
            "ownership.challenge_verified"
            if verified
            else "ownership.verification_failed"
        ),
        operator_id=operator_id,
        api_client_id=api_client_id,
        verification_run_id=challenge.verification_run_id,
        submission_id=challenge.submission_id,
        payload={
            "challenge_id": challenge.id,
            "method": challenge.method,
            "verified": verified,
        },
        description=(
            f"{principal.name!r} {'verified' if verified else 'attempted to verify'} "
            f"domain ownership for {challenge.domain!r} via {challenge.method}."
        ),
    )

    return VerifyChallengeResponse(
        challenge_id=challenge.id,
        method=challenge.method,
        verified=verified,
        status=challenge.status,
        verified_at=(
            challenge.verified_at.isoformat() if challenge.verified_at else None
        ),
        message=(
            "Domain ownership verified — added as a bounded confidence signal."
            if verified
            else (
                "Not verified yet — token not found or incorrect. No signal "
                "added; retry once ready."
            )
        ),
    )
