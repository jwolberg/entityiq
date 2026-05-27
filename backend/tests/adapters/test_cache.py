"""Tests for P2-T5 — AdapterCache: in-process caching with per-source TTL.

Test scenarios (per ticket AC):
  - Repeated lookup within TTL served from cache (no second provider call).
  - Lookup after TTL expiry triggers a new fetch.
  - Different lookup keys are cached independently.
  - Different sources are cached independently.
  - Cache max_size evicts the oldest entry (FIFO).
  - Cache.clear() removes all entries.
  - CachedAdapter wraps an adapter transparently; hit avoids second fetch.
  - CachedAdapter: cache miss triggers fetch; result stored.
"""

from __future__ import annotations

import time

from app.adapters.base import AdapterContext, AdapterSuccess
from app.adapters.cache import AdapterCache, CachedAdapter

# ---------------------------------------------------------------------------
# Fake adapter helpers
# ---------------------------------------------------------------------------


class _CountingAdapter:
    """Records how many times fetch() was called."""

    name = "counting"
    tier = 1

    def __init__(self, result=None) -> None:
        self.call_count = 0
        self._result = result or AdapterSuccess(evidence=[])

    def fetch(self, context: AdapterContext):
        self.call_count += 1
        return self._result


def _make_context(
    company_name: str = "Acme Corp",
    domain: str = "acme.com",
) -> AdapterContext:
    return AdapterContext(
        run_id="run-cache-test",
        company_name=company_name,
        domain=domain,
    )


# ---------------------------------------------------------------------------
# AdapterCache unit tests
# ---------------------------------------------------------------------------


class TestAdapterCacheBasic:
    def test_cache_miss_returns_none(self) -> None:
        cache = AdapterCache()
        result = cache.get("opencorporates", "acme corp|acme.com")
        assert result is None

    def test_set_then_get_returns_result(self) -> None:
        cache = AdapterCache()
        result = AdapterSuccess(evidence=[])
        cache.set("opencorporates", "acme corp|acme.com", result)
        fetched = cache.get("opencorporates", "acme corp|acme.com")
        assert fetched is result

    def test_different_sources_cached_independently(self) -> None:
        cache = AdapterCache()
        r1 = AdapterSuccess(evidence=[])
        r2 = AdapterSuccess(evidence=[])
        cache.set("opencorporates", "key1", r1)
        cache.set("sanctions", "key1", r2)

        assert cache.get("opencorporates", "key1") is r1
        assert cache.get("sanctions", "key1") is r2

    def test_different_keys_cached_independently(self) -> None:
        cache = AdapterCache()
        r1 = AdapterSuccess(evidence=[])
        r2 = AdapterSuccess(evidence=[])
        cache.set("opencorporates", "acme corp|acme.com", r1)
        cache.set("opencorporates", "evil corp|evil.com", r2)

        assert cache.get("opencorporates", "acme corp|acme.com") is r1
        assert cache.get("opencorporates", "evil corp|evil.com") is r2


class TestAdapterCacheTTL:
    def test_expired_entry_returns_none(self) -> None:
        # Use a source name not in DEFAULT_TTLS so default_ttl=0 applies.
        cache = AdapterCache(default_ttl=0)
        result = AdapterSuccess(evidence=[])
        cache.set("custom_source_xyz", "key", result)
        # Immediately expired — should miss.
        # Sleep a tiny bit to ensure monotonic clock advances.
        time.sleep(0.01)
        assert cache.get("custom_source_xyz", "key") is None

    def test_non_expired_entry_returned(self) -> None:
        cache = AdapterCache(default_ttl=3600)
        result = AdapterSuccess(evidence=[])
        cache.set("opencorporates", "key", result)
        assert cache.get("opencorporates", "key") is result

    def test_per_source_ttl_used(self) -> None:
        cache = AdapterCache(ttls={"mystore": 0})
        result = AdapterSuccess(evidence=[])
        cache.set("mystore", "key", result)
        time.sleep(0.01)
        # Source-specific TTL of 0 → expired.
        assert cache.get("mystore", "key") is None


class TestAdapterCacheCapacity:
    def test_evicts_oldest_entry_when_full(self) -> None:
        cache = AdapterCache(max_size=2, default_ttl=3600)
        r1 = AdapterSuccess(evidence=[])
        r2 = AdapterSuccess(evidence=[])
        r3 = AdapterSuccess(evidence=[])

        cache.set("s", "key1", r1)
        cache.set("s", "key2", r2)
        cache.set("s", "key3", r3)  # Should evict key1 (FIFO)

        assert cache.get("s", "key1") is None  # evicted
        assert cache.get("s", "key2") is r2
        assert cache.get("s", "key3") is r3

    def test_size_property(self) -> None:
        cache = AdapterCache(default_ttl=3600)
        assert cache.size == 0
        cache.set("s", "k1", AdapterSuccess(evidence=[]))
        assert cache.size == 1
        cache.set("s", "k2", AdapterSuccess(evidence=[]))
        assert cache.size == 2


class TestAdapterCacheClear:
    def test_clear_removes_all_entries(self) -> None:
        cache = AdapterCache(default_ttl=3600)
        cache.set("s1", "k1", AdapterSuccess(evidence=[]))
        cache.set("s2", "k2", AdapterSuccess(evidence=[]))
        cache.clear()
        assert cache.size == 0
        assert cache.get("s1", "k1") is None

    def test_invalidate_removes_specific_entry(self) -> None:
        cache = AdapterCache(default_ttl=3600)
        r1 = AdapterSuccess(evidence=[])
        r2 = AdapterSuccess(evidence=[])
        cache.set("s", "k1", r1)
        cache.set("s", "k2", r2)
        cache.invalidate("s", "k1")
        assert cache.get("s", "k1") is None
        assert cache.get("s", "k2") is r2


# ---------------------------------------------------------------------------
# CachedAdapter tests
# ---------------------------------------------------------------------------


class TestCachedAdapter:
    def test_first_call_hits_underlying_adapter(self) -> None:
        cache = AdapterCache(default_ttl=3600)
        inner = _CountingAdapter()
        wrapped = CachedAdapter(inner, cache=cache)

        result = wrapped.fetch(_make_context())
        assert inner.call_count == 1
        assert isinstance(result, AdapterSuccess)

    def test_second_call_within_ttl_served_from_cache(self) -> None:
        """Core AC: repeated lookup within TTL served from cache (no 2nd call)."""
        cache = AdapterCache(default_ttl=3600)
        inner = _CountingAdapter()
        wrapped = CachedAdapter(inner, cache=cache)

        ctx = _make_context()
        wrapped.fetch(ctx)
        wrapped.fetch(ctx)

        # Second call should NOT hit the inner adapter.
        assert inner.call_count == 1

    def test_second_call_after_ttl_refetches(self) -> None:
        cache = AdapterCache(default_ttl=0)  # instant expiry
        inner = _CountingAdapter()
        wrapped = CachedAdapter(inner, cache=cache)

        ctx = _make_context()
        wrapped.fetch(ctx)
        time.sleep(0.01)  # Let TTL expire.
        wrapped.fetch(ctx)

        assert inner.call_count == 2

    def test_proxy_exposes_name_and_tier(self) -> None:
        inner = _CountingAdapter()
        wrapped = CachedAdapter(inner)
        assert wrapped.name == inner.name
        assert wrapped.tier == inner.tier

    def test_different_contexts_cached_separately(self) -> None:
        cache = AdapterCache(default_ttl=3600)
        inner = _CountingAdapter()
        wrapped = CachedAdapter(inner, cache=cache)

        ctx1 = _make_context("Acme Corp", "acme.com")
        ctx2 = _make_context("Evil Corp", "evil.com")

        wrapped.fetch(ctx1)
        wrapped.fetch(ctx2)

        # Both contexts → two distinct cache keys → two fetches.
        assert inner.call_count == 2
