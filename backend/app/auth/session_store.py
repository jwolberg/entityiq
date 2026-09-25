"""Operator session storage backends (ticket 0024).

Two interchangeable stores behind the same tiny interface used by
app.auth.operator:

  - InMemorySessionStore — dict-based, single-process. The pre-0024 default
    and the fallback when Redis isn't reachable.
  - RedisSessionStore    — Redis-backed. Sessions survive an API restart and
    are shared across API instances, because the data lives in Redis rather
    than process memory. TTL is enforced by Redis's own key expiry (SETEX),
    so there's no separate purge loop to keep in sync with SESSION_TTL_HOURS.

build_session_store() tries to connect to and ping the configured Redis URL;
any failure (connection refused, timeout, auth, wrong scheme, ...) falls back
to InMemorySessionStore and logs a warning, so dev and the demo keep working
without Redis.

`redis` is already a backend dependency (pyproject.toml) — importing it here
adds nothing new.
"""

from __future__ import annotations

import logging
import secrets
import time
from typing import Callable, Protocol

logger = logging.getLogger(__name__)

# Namespaced so session keys never collide with Celery's own keys when both
# share one Redis instance/DB (RUNBOOK: CELERY_BROKER_URL also defaults to
# redis://localhost:6379/0).
_KEY_PREFIX = "entityiq:session:"


class SessionStore(Protocol):
    """Storage backend for operator session tokens."""

    def create(self, operator_id: str, ttl_seconds: int) -> str:
        """Create a new session for operator_id and return its token."""
        ...

    def get(self, token: str) -> str | None:
        """Return the operator_id for a valid, unexpired token, else None."""
        ...

    def delete(self, token: str) -> None:
        """Revoke a session token (sign-out). A no-op if unknown."""
        ...

    def clear_all(self) -> None:
        """Remove every session. Test/dev use only."""
        ...


# ---------------------------------------------------------------------------
# In-memory store (MVP default / fallback)
# ---------------------------------------------------------------------------


class InMemorySessionStore:
    """Dict-based session store — single process only.

    now_func is injectable (rather than calling time.time() directly) so
    tests can control expiry deterministically without sleeping.
    """

    def __init__(self, now_func: Callable[[], float] = time.time):
        self._now = now_func
        self._data: dict[str, tuple[str, float]] = {}

    def create(self, operator_id: str, ttl_seconds: int) -> str:
        token = secrets.token_urlsafe(32)
        self._data[token] = (operator_id, self._now() + ttl_seconds)
        return token

    def get(self, token: str) -> str | None:
        entry = self._data.get(token)
        if entry is None:
            return None
        operator_id, expires_at = entry
        if self._now() >= expires_at:
            self._data.pop(token, None)
            return None
        return operator_id

    def delete(self, token: str) -> None:
        self._data.pop(token, None)

    def clear_all(self) -> None:
        self._data.clear()


# ---------------------------------------------------------------------------
# Redis-backed store
# ---------------------------------------------------------------------------


class RedisSessionStore:
    """Redis-backed session store.

    Accepts any client exposing the small slice of the redis-py interface
    used here (ping/setex/get/delete/scan_iter) — production gets a real
    redis.Redis; tests inject a fake (no live network in tests).
    """

    def __init__(self, client) -> None:
        self._client = client

    def create(self, operator_id: str, ttl_seconds: int) -> str:
        token = secrets.token_urlsafe(32)
        self._client.setex(_KEY_PREFIX + token, ttl_seconds, operator_id)
        return token

    def get(self, token: str) -> str | None:
        value = self._client.get(_KEY_PREFIX + token)
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else value

    def delete(self, token: str) -> None:
        self._client.delete(_KEY_PREFIX + token)

    def clear_all(self) -> None:
        # Only this app's own session keys — never a blunt FLUSHDB, since the
        # Redis instance may be shared with the Celery broker.
        for key in list(self._client.scan_iter(f"{_KEY_PREFIX}*")):
            self._client.delete(key)


# ---------------------------------------------------------------------------
# Factory: Redis if reachable, else in-memory + warning
# ---------------------------------------------------------------------------


def _default_client_factory(url: str):
    import redis as redis_lib  # noqa: PLC0415 — only needed on this path

    return redis_lib.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)


def _redact(url: str) -> str:
    """Drop userinfo (credentials) from a Redis URL before logging it."""
    from urllib.parse import urlsplit, urlunsplit  # noqa: PLC0415

    parts = urlsplit(url)
    if not (parts.username or parts.password):
        return url
    host = parts.hostname or ""
    netloc = f"***@{host}" + (f":{parts.port}" if parts.port else "")
    return urlunsplit(parts._replace(netloc=netloc))


def build_session_store(
    redis_url: str,
    *,
    now_func: Callable[[], float] = time.time,
    client_factory: Callable[[str], object] | None = None,
) -> SessionStore:
    """Return a Redis-backed store if redis_url is reachable, else in-memory.

    Tries to build a client and ping it; any exception (connection refused,
    timeout, DNS failure, auth error, ...) is treated as "Redis unavailable"
    and logged as a warning before falling back.
    """
    factory = client_factory or _default_client_factory
    try:
        client = factory(redis_url)
        client.ping()
    except Exception as exc:  # noqa: BLE001 - any backend failure -> fallback
        logger.warning(
            "Operator sessions: Redis unreachable at %s (%s: %s). Falling "
            "back to in-memory sessions — they will NOT survive a restart "
            "or be shared across API instances until Redis is reachable.",
            _redact(redis_url),
            type(exc).__name__,
            exc,
        )
        return InMemorySessionStore(now_func=now_func)

    logger.info("Operator sessions: backed by Redis at %s", redis_url)
    return RedisSessionStore(client)
