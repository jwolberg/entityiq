"""Pydantic schemas for lead-only API-key provisioning (ticket 0026).

The full key is only ever present in CreateApiClientResponse — the one-time
response to POST /api-clients. Every other shape (list, revoke) carries only
non-secret metadata: id, name, key_prefix, active, timestamps. The hash is
never serialized anywhere (app.models.api_client.ApiClient.key_hash has no
schema field at all).
"""

from __future__ import annotations

from pydantic import BaseModel


class CreateApiClientRequest(BaseModel):
    name: str


class CreateApiClientResponse(BaseModel):
    id: str
    name: str
    key_prefix: str
    # Plaintext — shown once, never persisted or logged (app.auth.service).
    api_key: str
    active: bool
    created_at: str


class ApiClientListItem(BaseModel):
    id: str
    name: str
    key_prefix: str
    active: bool
    created_at: str
    last_used_at: str | None = None


class ApiClientListResponse(BaseModel):
    items: list[ApiClientListItem]
    total: int


class RevokeApiClientResponse(BaseModel):
    id: str
    name: str
    active: bool
    message: str
