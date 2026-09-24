"""Lead-only API-key provisioning (ticket 0026).

Integration API keys (app.models.api_client.ApiClient) authenticate
integrating systems at the API boundary (app.auth.service). Before this
ticket the only way to mint one was app.seed's demo script. This adds a
lead-gated API to create, list, and revoke keys, so a compliance lead can
provision integrations without a shell.

  POST   /api-clients             — create (full key shown once)
  GET    /api-clients             — list (prefix + metadata only, no hash)
  POST   /api-clients/{id}/revoke — revoke (soft; rejected on the next
                                    X-API-Key auth — app.auth.service)

All three require the 'lead' role (app.auth.operator.require_lead) —
operators get 403, and the denial is itself audited (centralized in
require_lead, ADR-0002 §2). Create and revoke are additionally audited here.
The key hash is never returned or logged — this reuses
app.auth.service.generate_api_key / hash_api_key, the same primitives the
demo seed script uses.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.audit.recorder import record_event
from app.auth.operator import require_lead
from app.auth.service import create_api_client
from app.db.session import SessionLocal
from app.models.api_client import ApiClient
from app.schemas.api_client import (
    ApiClientListItem,
    ApiClientListResponse,
    CreateApiClientRequest,
    CreateApiClientResponse,
    RevokeApiClientResponse,
)

router = APIRouter(prefix="/api-clients", tags=["api-clients"])


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
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=CreateApiClientResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an integration API key (lead only)",
)
def create_client(
    body: CreateApiClientRequest,
    db: Session = Depends(_get_db),
    lead=Depends(require_lead),
) -> CreateApiClientResponse:
    """Mint a new integration API key. The plaintext key is returned ONCE —
    it is not recoverable afterwards (only its hash is stored).

    Returns 422 for a blank name, 409 if the name is already in use
    (ApiClient.name is unique).
    """
    name = body.name.strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="name must not be empty.",
        )
    if db.query(ApiClient).filter(ApiClient.name == name).first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An API client named {name!r} already exists.",
        )

    client, full_key = create_api_client(name, db)

    record_event(
        db=db,
        event_type="operator.api_client_created",
        operator_id=lead.id,
        payload={"api_client_id": client.id, "name": client.name},
        description=f"Lead {lead.email!r} created API client {client.name!r}.",
    )

    return CreateApiClientResponse(
        id=client.id,
        name=client.name,
        key_prefix=client.key_prefix,
        api_key=full_key,
        active=client.active,
        created_at=client.created_at.isoformat(),
    )


@router.get(
    "",
    response_model=ApiClientListResponse,
    summary="List integration API keys (lead only)",
)
def list_clients(
    db: Session = Depends(_get_db),
    _lead=Depends(require_lead),
) -> ApiClientListResponse:
    """Prefix + metadata only — never the hash or the plaintext key."""
    clients = db.query(ApiClient).order_by(ApiClient.created_at.desc()).all()
    items = [
        ApiClientListItem(
            id=c.id,
            name=c.name,
            key_prefix=c.key_prefix,
            active=c.active,
            created_at=c.created_at.isoformat(),
            last_used_at=c.last_used_at.isoformat() if c.last_used_at else None,
        )
        for c in clients
    ]
    return ApiClientListResponse(items=items, total=len(items))


@router.post(
    "/{client_id}/revoke",
    response_model=RevokeApiClientResponse,
    summary="Revoke an integration API key (lead only)",
)
def revoke_client(
    client_id: str,
    db: Session = Depends(_get_db),
    lead=Depends(require_lead),
) -> RevokeApiClientResponse:
    """Soft-revoke: the key is rejected on its next authenticated request
    (app.auth.service._resolve_api_client checks ApiClient.active).

    Idempotent — revoking an already-revoked key just returns its state
    without writing a second audit event. Returns 404 if unknown.
    """
    client = db.get(ApiClient, client_id)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API client {client_id!r} not found.",
        )

    was_active = client.active
    client.active = False
    db.commit()

    if was_active:
        record_event(
            db=db,
            event_type="operator.api_client_revoked",
            operator_id=lead.id,
            payload={"api_client_id": client.id, "name": client.name},
            description=f"Lead {lead.email!r} revoked API client {client.name!r}.",
        )

    return RevokeApiClientResponse(
        id=client.id,
        name=client.name,
        active=client.active,
        message="API client revoked.",
    )
