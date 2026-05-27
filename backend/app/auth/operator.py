"""Operator authentication + RBAC (P1-T9).

Minimal session-based auth for the MVP.  Designed behind an interface so
OIDC/SSO (ARCHITECTURE § 5) can replace it without touching route code.

Session mechanism (MVP):
  - Operator accounts are stored in the `operator` table with a hashed password.
  - Sign-in validates the password and issues a random opaque session token
    (stored in an in-memory dict — appropriate for a single-process MVP; a Redis
    session store or JWT would replace this for multi-process/OIDC).
  - The token is returned in the response and expected as a Bearer token in
    subsequent requests.

RBAC roles (ARCHITECTURE § 5):
  - "operator" — may review runs (mark-reviewed, add notes)
  - "lead"     — operator privileges + oversight and audit access

The FastAPI dependencies in this module enforce authentication + role:
  - get_current_operator()  → any authenticated operator
  - require_lead()          → lead role only (raises 403 for operator role)

Password hashing:
  Uses PBKDF2-HMAC-SHA256 via Python stdlib `hashlib` — no new dependencies.
  The hash format is "pbkdf2_sha256:<iterations>:<salt_hex>:<digest_hex>".
  This is intentionally simple for the MVP; swap for argon2 / bcrypt in P3.

Replacing the session mechanism (OIDC/SSO):
  1. Replace `_session_store` + `sign_in()` with OIDC token exchange.
  2. Keep `get_current_operator()` signature unchanged — it reads from the
     DB regardless of auth mechanism.
  3. The FastAPI routes do not need to change.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import SessionLocal

if TYPE_CHECKING:
    from app.models.operator import Operator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Password hashing (stdlib only — no new deps)
# ---------------------------------------------------------------------------

_ITERATIONS = 260_000  # NIST SP 800-132 recommendation for PBKDF2-HMAC-SHA256


def hash_password(password: str) -> str:
    """Hash a plaintext password with PBKDF2-HMAC-SHA256.

    Returns a string: "pbkdf2_sha256:<iterations>:<salt_hex>:<digest_hex>"
    """
    salt = secrets.token_bytes(32)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _ITERATIONS,
    )
    return f"pbkdf2_sha256:{_ITERATIONS}:{salt.hex()}:{digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a plaintext password against a stored hash.

    Returns True if the password matches, False otherwise.
    """
    try:
        parts = stored_hash.split(":")
        if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
            return False
        _, iterations_str, salt_hex, digest_hex = parts
        iterations = int(iterations_str)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, AttributeError):
        return False

    candidate = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    # Constant-time comparison to resist timing attacks
    return hmac.compare_digest(candidate, expected)


# ---------------------------------------------------------------------------
# In-memory session store (MVP — single-process)
#
# Maps session_token (str) → operator_id (str).
# Replacement path: swap this dict for a Redis-backed store or JWT validation.
# ---------------------------------------------------------------------------

_session_store: dict[str, str] = {}


def _new_session_token(operator_id: str) -> str:
    """Generate and store a new session token for the given operator."""
    token = secrets.token_urlsafe(32)
    _session_store[token] = operator_id
    return token


def _get_operator_id_from_token(token: str) -> str | None:
    """Look up an operator_id from a session token.  Returns None if unknown."""
    return _session_store.get(token)


def invalidate_session(token: str) -> None:
    """Remove a session token (sign-out)."""
    _session_store.pop(token, None)


def _clear_all_sessions() -> None:
    """For testing only: clear all sessions."""
    _session_store.clear()


# ---------------------------------------------------------------------------
# Sign-in
# ---------------------------------------------------------------------------


def sign_in(email: str, password: str, db: Session) -> str | None:
    """Validate operator credentials and return a session token.

    Returns:
        A session token string on success.
        None if credentials are invalid (email not found or wrong password).
    """
    from app.models.operator import Operator  # noqa: PLC0415

    operator = db.query(Operator).filter(Operator.email == email).first()
    if operator is None:
        # Constant-time to avoid enumeration via timing
        verify_password("__no_op__", "pbkdf2_sha256:260000:aa:bb")
        return None

    if not operator.password_hash:
        # Account exists but has no password set (e.g. OIDC-only account)
        return None

    if not verify_password(password, operator.password_hash):
        return None

    return _new_session_token(operator.id)


# ---------------------------------------------------------------------------
# FastAPI DB dependency
# ---------------------------------------------------------------------------


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Authentication dependency
# ---------------------------------------------------------------------------


def _extract_bearer_token(authorization: str | None) -> str | None:
    """Extract a Bearer token from an Authorization header value."""
    if not authorization:
        return None
    parts = authorization.strip().split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None


def get_current_operator(
    authorization: str | None = Header(default=None),
    db: Session = Depends(_get_db),
) -> "Operator":
    """FastAPI dependency: return the authenticated Operator or raise 401.

    Reads the Bearer token from the Authorization header, looks up the session,
    and returns the Operator ORM object.

    Raises:
        HTTP 401 Unauthorized — no token, invalid token, or token unknown.
    """
    from app.models.operator import Operator  # noqa: PLC0415

    token = _extract_bearer_token(authorization)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Provide a Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    operator_id = _get_operator_id_from_token(token)
    if operator_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    operator = db.get(Operator, operator_id)
    if operator is None:
        # Token pointed to a deleted operator
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Operator account not found.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return operator


# ---------------------------------------------------------------------------
# Role-based access control
# ---------------------------------------------------------------------------


def require_lead(
    operator: "Operator" = Depends(get_current_operator),
) -> "Operator":
    """FastAPI dependency: require the 'lead' role.

    Raises:
        HTTP 403 Forbidden — authenticated but not a lead.
    """
    if operator.role != "lead":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "This action requires the 'lead' role. "
                f"Your role is '{operator.role}'."
            ),
        )
    return operator


# ---------------------------------------------------------------------------
# Auth endpoint schemas (inline — small, no separate schema file needed)
# ---------------------------------------------------------------------------


from pydantic import BaseModel  # noqa: E402


class SignInRequest(BaseModel):
    email: str
    password: str


class SignInResponse(BaseModel):
    session_token: str
    operator_id: str
    role: str
    message: str


# ---------------------------------------------------------------------------
# Auth router (POST /auth/sign-in)
# ---------------------------------------------------------------------------

from fastapi import APIRouter  # noqa: E402

auth_router = APIRouter(prefix="/auth", tags=["auth"])


@auth_router.post(
    "/sign-in",
    response_model=SignInResponse,
    summary="Operator sign-in",
)
def operator_sign_in(
    body: SignInRequest,
    db: Session = Depends(_get_db),
) -> SignInResponse:
    """Authenticate an operator and return a session token.

    The returned token should be sent as `Authorization: Bearer <token>` on
    subsequent requests.

    Every sign-in attempt is recorded as an audit event (success or failure
    is not recorded separately — only successful sign-ins produce a token and
    an audit event).
    """
    from app.audit.recorder import record_event  # noqa: PLC0415
    from app.models.operator import Operator  # noqa: PLC0415

    token = sign_in(body.email, body.password, db)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    operator = db.query(Operator).filter(Operator.email == body.email).first()
    assert operator is not None  # sign_in returned a token so it must exist

    # Record sign-in audit event
    record_event(
        db=db,
        event_type="operator.sign_in",
        operator_id=operator.id,
        payload={
            "email": body.email,
            "occurred_at": datetime.now(tz=timezone.utc).isoformat(),
        },
        description=f"Operator {body.email!r} signed in.",
    )

    return SignInResponse(
        session_token=token,
        operator_id=operator.id,
        role=operator.role,
        message="Sign-in successful.",
    )
