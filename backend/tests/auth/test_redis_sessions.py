"""Tests for ticket 0024 — operator sessions backed by Redis.

No live Redis is used or required.  `_FakeRedis` below implements the small
slice of the redis-py client interface the session store needs
(`ping`, `setex`, `get`, `delete`, `scan_iter`), with an injectable clock so
TTL expiry is deterministic instead of sleeping in real time.  `redis` itself
is already a backend dependency (pyproject.toml); `fakeredis` is not
installed, per the project's "no new dependencies without approval" rule, so
this hand-rolled fake stands in for it.

Scenarios:
  1. A session created via one RedisSessionStore is visible via a second
     instance backed by the same Redis — proves both "survives an API
     restart" (a fresh store object, same backing data) and "shared across
     instances" (two store objects, same backing data) at once.
  2. Sign-out (delete) on one instance revokes the session as seen by another.
  3. TTL (SESSION_TTL_HOURS, in seconds here) is enforced by Redis key expiry.
  4. build_session_store() returns a RedisSessionStore when Redis is reachable.
  5. build_session_store() falls back to InMemorySessionStore and logs a
     warning when Redis is unreachable.
"""

from __future__ import annotations

import logging

from app.auth.session_store import (
    InMemorySessionStore,
    RedisSessionStore,
    build_session_store,
)

# ---------------------------------------------------------------------------
# Fake Redis client (minimal slice of the redis-py interface)
# ---------------------------------------------------------------------------


class _FakeRedis:
    """In-process stand-in for redis.Redis — enough for RedisSessionStore.

    Supports an injectable clock so TTL expiry can be advanced deterministically
    instead of sleeping.  Two _FakeRedis *instances* are distinct backends;
    tests that want to simulate "shared Redis" pass the SAME instance to
    multiple RedisSessionStore objects (that's the point of the test).
    """

    def __init__(self, now_func=None):
        import time as _time

        self._now = now_func or _time.time
        self._data: dict[str, tuple[bytes, float | None]] = {}

    def ping(self) -> bool:
        return True

    def setex(self, key: str, ttl_seconds: int, value: str) -> None:
        self._data[key] = (
            value.encode() if isinstance(value, str) else value,
            self._now() + ttl_seconds,
        )

    def get(self, key: str) -> bytes | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at is not None and self._now() >= expires_at:
            self._data.pop(key, None)
            return None
        return value

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def scan_iter(self, match: str):
        # Minimal glob support: only the "<prefix>*" pattern the store uses.
        assert match.endswith("*")
        prefix = match[:-1]
        return [k for k in list(self._data) if k.startswith(prefix)]


class _UnreachableRedis:
    """Simulates a Redis client whose connection is down."""

    def ping(self) -> bool:
        raise ConnectionError("Connection refused")


# ---------------------------------------------------------------------------
# 1-2. Shared backend: survives "restart", shared across instances, sign-out
#      revokes everywhere.
# ---------------------------------------------------------------------------


def test_session_visible_from_a_second_store_instance():
    """A second RedisSessionStore (simulating another API process, or the
    same process after a restart) sees a session created by the first."""
    backend = _FakeRedis()
    store_a = RedisSessionStore(backend)
    store_b = RedisSessionStore(backend)

    token = store_a.create("op-1", ttl_seconds=3600)

    assert store_b.get(token) == "op-1"


def test_sign_out_on_one_instance_revokes_on_another():
    backend = _FakeRedis()
    store_a = RedisSessionStore(backend)
    store_b = RedisSessionStore(backend)

    token = store_a.create("op-1", ttl_seconds=3600)
    assert store_b.get(token) == "op-1"

    store_a.delete(token)  # sign-out via instance A

    assert store_b.get(token) is None  # revoked everywhere


# ---------------------------------------------------------------------------
# 3. TTL enforcement
# ---------------------------------------------------------------------------


def test_ttl_is_enforced_by_redis_expiry():
    clock = [0.0]
    backend = _FakeRedis(now_func=lambda: clock[0])
    store = RedisSessionStore(backend)

    token = store.create("op-1", ttl_seconds=5)
    assert store.get(token) == "op-1"

    clock[0] += 6  # past the TTL

    assert store.get(token) is None


def test_ttl_not_yet_expired_still_returns_operator():
    clock = [0.0]
    backend = _FakeRedis(now_func=lambda: clock[0])
    store = RedisSessionStore(backend)

    token = store.create("op-1", ttl_seconds=5)
    clock[0] += 4  # under the TTL

    assert store.get(token) == "op-1"


# ---------------------------------------------------------------------------
# 4-5. build_session_store(): Redis when reachable, in-memory + warning when not
# ---------------------------------------------------------------------------


def test_build_session_store_uses_redis_when_reachable():
    backend = _FakeRedis()
    store = build_session_store("redis://fake/0", client_factory=lambda url: backend)
    assert isinstance(store, RedisSessionStore)

    # Sanity: the returned store actually talks to our fake backend.
    token = store.create("op-1", ttl_seconds=60)
    assert backend.get(f"entityiq:session:{token}") is not None


def test_build_session_store_falls_back_to_in_memory_with_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="app.auth.session_store"):
        store = build_session_store(
            "redis://unreachable-host:6379/0",
            client_factory=lambda url: _UnreachableRedis(),
        )

    assert isinstance(store, InMemorySessionStore)
    assert any(
        "redis" in record.message.lower() and "unreachable-host" in record.message
        for record in caplog.records
    ), [r.message for r in caplog.records]


def test_in_memory_store_still_works_as_the_fallback():
    """Sanity: the fallback store is fully functional on its own (dev/demo
    without Redis)."""
    store = InMemorySessionStore()
    token = store.create("op-1", ttl_seconds=60)
    assert store.get(token) == "op-1"
    store.delete(token)
    assert store.get(token) is None


# ---------------------------------------------------------------------------
# RedisSessionStore.clear_all() only touches this app's own keys
# ---------------------------------------------------------------------------


def test_clear_all_only_removes_session_keys_not_unrelated_redis_keys():
    """The store shares its Redis instance with other tools (e.g. Celery);
    clear_all() must not be a blunt FLUSHDB."""
    backend = _FakeRedis()
    backend.setex("some:other:app:key", 3600, "unrelated")
    store = RedisSessionStore(backend)
    store.create("op-1", ttl_seconds=3600)

    store.clear_all()

    assert backend.get("some:other:app:key") == b"unrelated"


def test_fallback_warning_never_logs_redis_credentials(caplog):
    """Review finding: an auth-protected REDIS_URL must not reach the logs."""
    import logging

    from app.auth.session_store import InMemorySessionStore, build_session_store

    with caplog.at_level(logging.WARNING):
        store = build_session_store("redis://:s3cr3t-pass@127.0.0.1:1/0")
    assert isinstance(store, InMemorySessionStore)
    assert "s3cr3t-pass" not in caplog.text
    assert "127.0.0.1" in caplog.text
