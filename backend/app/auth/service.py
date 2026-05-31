"""Service-credential authentication for integrating systems (P2-T12).

Integrating systems authenticate at the API boundary with an API key
(ARCHITECTURE § 5).  Keys are presented in the `X-API-Key` header — kept
distinct from operator `Authorization: Bearer` session tokens so the two
auth schemes never collide.

Key format:
  eiq_<urlsafe-random>           (the full secret, shown to the caller ONCE)
  key_prefix = first 12 chars    (non-secret; stored + indexed for lookup)

Storage:
  Only the PBKDF2-HMAC-SHA256 hash of the full key is persisted (reusing the
  operator password hashing primitives — no new dependency).  Verification
  looks the row up by prefix, then constant-time-compares the full key.

Dependencies exposed:
  get_current_api_client()  → require a valid, active API key (integrating systems)
  get_principal()           → accept EITHER an operator Bearer token OR an API key,
                              returning a Principal for attribution (used on the
                              machine-readable report export, which both operators
                              and integrating systems may call).

Provisioning:
  create_api_client(name, db) mints a key and returns (ApiClient, plaintext_key).
  The plaintext is returned once and never stored.  A provisioning UI/endpoint
  is out of scope for this ticket.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.operator import (
    _extract_bearer_token,
    _get_operator_id_from_token,
    hash_password,
    verify_password,
)
from app.db.session import SessionLocal

if TYPE_CHECKING:
    from app.models.api_client import ApiClient
    from app.models.operator import Operator

# ---------------------------------------------------------------------------
# Key generation / hashing
# ---------------------------------------------------------------------------

_KEY_PREFIX = "eiq_"
_PREFIX_LEN = 12  # "eiq_" + 8 random chars — non-secret lookup handle


def generate_api_key() -> tuple[str, str]:
    """Mint a new API key.

    Returns:
        (full_key, key_prefix) — full_key is the secret (show once);
        key_prefix is the non-secret lookup handle (first 12 chars).
    """
    full_key = _KEY_PREFIX + secrets.token_urlsafe(32)
    return full_key, full_key[:_PREFIX_LEN]


def hash_api_key(full_key: str) -> str:
    """Hash a full API key (PBKDF2-HMAC-SHA256, reusing the password primitive)."""
    return hash_password(full_key)


def verify_api_key(full_key: str, stored_hash: str) -> bool:
    """Constant-time-verify a full API key against its stored hash."""
    return verify_password(full_key, stored_hash)


def create_api_client(
    name: str,
    db: Session,
    *,
    commit: bool = True,
) -> tuple["ApiClient", str]:
    """Create and persist a new ApiClient, returning (client, plaintext_key).

    The plaintext key is returned ONCE and is not recoverable afterwards.
    """
    from app.models.api_client import ApiClient  # noqa: PLC0415

    full_key, key_prefix = generate_api_key()
    client = ApiClient(
        name=name,
        key_prefix=key_prefix,
        key_hash=hash_api_key(full_key),
        active=True,
    )
    db.add(client)
    if commit:
        db.commit()
    else:
        db.flush()
    return client, full_key


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
# API-client authentication
# ---------------------------------------------------------------------------

_INVALID_KEY_EXC = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or missing API key. Provide a valid X-API-Key header.",
    headers={"WWW-Authenticate": "ApiKey"},
)


def _resolve_api_client(api_key: str | None, db: Session) -> "ApiClient | None":
    """Validate an API key and return the active ApiClient, or None.

    Looks the row up by prefix, then constant-time-verifies the full key.
    Updates last_used_at on success.  Returns None for any failure
    (missing key, unknown prefix, bad key, or revoked client).
    """
    from app.models.api_client import ApiClient  # noqa: PLC0415

    if not api_key or len(api_key) <= _PREFIX_LEN:
        return None

    prefix = api_key[:_PREFIX_LEN]
    client = db.query(ApiClient).filter(ApiClient.key_prefix == prefix).first()
    if client is None or not client.active:
        # Still spend a verify to keep timing roughly uniform.
        verify_api_key(api_key, "pbkdf2_sha256:260000:aa:bb")
        return None

    if not verify_api_key(api_key, client.key_hash):
        return None

    client.last_used_at = datetime.now(tz=timezone.utc)
    db.commit()
    return client


def get_current_api_client(
    x_api_key: str | None = Header(default=None),
    db: Session = Depends(_get_db),
) -> "ApiClient":
    """FastAPI dependency: require a valid, active API key.

    Raises HTTP 401 if the key is missing, unknown, malformed, or revoked.
    """
    client = _resolve_api_client(x_api_key, db)
    if client is None:
        raise _INVALID_KEY_EXC
    return client


# ---------------------------------------------------------------------------
# Principal (operator OR integrating system) — for shared endpoints
# ---------------------------------------------------------------------------


@dataclass
class Principal:
    """The authenticated caller of a shared endpoint, for audit attribution."""

    kind: str  # "operator" | "system"
    id: str
    name: str
    # Populated for operators only; None for systems.
    operator_id: str | None = None
    api_client_id: str | None = None


def get_principal(
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    db: Session = Depends(_get_db),
) -> Principal:
    """FastAPI dependency: accept an API key OR an operator Bearer token.

    API key takes precedence when both are present.  Used on endpoints that
    both integrating systems and operators may call (e.g. report export), so
    every call is attributable.

    Raises HTTP 401 if neither a valid API key nor a valid operator session
    token is supplied.
    """
    # 1) Integrating system via X-API-Key.
    if x_api_key:
        client = _resolve_api_client(x_api_key, db)
        if client is None:
            raise _INVALID_KEY_EXC
        return Principal(
            kind="system",
            id=client.id,
            name=client.name,
            api_client_id=client.id,
        )

    # 2) Operator via Authorization: Bearer <session token>.
    token = _extract_bearer_token(authorization)
    if token is not None:
        operator_id = _get_operator_id_from_token(token)
        if operator_id is not None:
            from app.models.operator import Operator  # noqa: PLC0415

            operator: Operator | None = db.get(Operator, operator_id)
            if operator is not None:
                return Principal(
                    kind="operator",
                    id=operator.id,
                    name=operator.email,
                    operator_id=operator.id,
                )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=(
            "Authentication required. Provide an X-API-Key (integrating system) "
            "or an operator Bearer token."
        ),
        headers={"WWW-Authenticate": "ApiKey"},
    )
