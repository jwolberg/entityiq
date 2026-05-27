"""In-process adapter result cache (P2-T5).

Caches AdapterResult objects keyed by (source, lookup_key) with a per-source
TTL.  Designed to bound cost and latency on re-analysis runs where the same
company is looked up multiple times within a short window.

Design decisions
----------------
- **In-process dict** — no Redis dependency for the cache layer.  This is an
  in-memory cache per worker process; it is intentionally simple and testable.
  A Redis-backed distributed cache (with the same interface) is a P3 upgrade.
- **Per-source TTL** — different sources have different freshness requirements.
  OFAC SDN is valid for days; IP geo is valid for hours; web pages change
  frequently.  Callers configure TTL per source at construction time.
- **Thread-safe** — uses a simple lock around get/set.  For async callers,
  this is synchronous; async-safe locking is deferred to when the pipeline
  moves to async stages.
- **Cache does not persist** across process restarts.  Each worker starts
  with an empty cache.  The cache's purpose is to avoid redundant network
  calls within a single run or across closely-spaced re-analyses, not to
  replace a durable data store.
- **Size-bounded** — maximum ``max_size`` entries per cache instance.
  When full, the oldest entry (by insertion order) is evicted (FIFO).  This
  prevents unbounded memory growth without adding a full LRU implementation.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Default per-source TTLs (seconds).  Callers may override these.
DEFAULT_TTLS: dict[str, int] = {
    "opencorporates": 86400,  # 24 h — registry data is stable
    "sanctions": 3600,  # 1 h — SDN list refreshed ~daily; cache conservatively
    "domain": 3600,  # 1 h — WHOIS/DNS changes infrequently
    "ipinfo": 3600,  # 1 h — geo data for an IP changes rarely
    "web": 1800,  # 30 min — web content changes more often
}

# Default maximum number of entries across all sources.
DEFAULT_MAX_SIZE = 1000


@dataclass
class _CacheEntry:
    result: Any  # AdapterResult
    expires_at: float  # time.monotonic() timestamp


class AdapterCache:
    """Thread-safe in-process cache for adapter results.

    Args:
        ttls:     Per-source TTL mapping (seconds).  Falls back to
                  ``default_ttl`` for sources not in the map.
        default_ttl: TTL to use for sources not in ``ttls``.
        max_size: Maximum number of entries before FIFO eviction.
    """

    def __init__(
        self,
        ttls: dict[str, int] | None = None,
        default_ttl: int = 3600,
        max_size: int = DEFAULT_MAX_SIZE,
    ) -> None:
        self._ttls: dict[str, int] = {**DEFAULT_TTLS, **(ttls or {})}
        self._default_ttl = default_ttl
        self._max_size = max_size
        # Insertion-ordered dict for FIFO eviction.
        self._store: dict[tuple[str, str], _CacheEntry] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get(self, source: str, lookup_key: str) -> Any | None:
        """Return a cached result or None if absent/expired.

        Args:
            source:     Adapter name (e.g. "opencorporates").
            lookup_key: The key used for the lookup (e.g. company name + country).

        Returns:
            The cached AdapterResult, or None if not cached / expired.
        """
        cache_key = (source, lookup_key)
        with self._lock:
            entry = self._store.get(cache_key)
            if entry is None:
                return None
            if time.monotonic() >= entry.expires_at:
                # Expired — remove and return miss.
                del self._store[cache_key]
                logger.debug("Cache EXPIRED %s/%s", source, lookup_key)
                return None
            logger.debug("Cache HIT %s/%s", source, lookup_key)
            return entry.result

    def set(self, source: str, lookup_key: str, result: Any) -> None:
        """Store a result in the cache.

        Args:
            source:     Adapter name.
            lookup_key: The key used for the lookup.
            result:     The AdapterResult to cache.
        """
        ttl = self._ttls.get(source, self._default_ttl)
        expires_at = time.monotonic() + ttl
        cache_key = (source, lookup_key)

        with self._lock:
            # Evict oldest entry if at capacity (FIFO).
            if cache_key not in self._store and len(self._store) >= self._max_size:
                oldest_key = next(iter(self._store))
                del self._store[oldest_key]
                logger.debug("Cache EVICT (capacity) %s/%s", *oldest_key)

            self._store[cache_key] = _CacheEntry(result=result, expires_at=expires_at)
            logger.debug("Cache SET %s/%s (ttl=%ds)", source, lookup_key, ttl)

    def invalidate(self, source: str, lookup_key: str) -> None:
        """Remove a specific entry from the cache."""
        cache_key = (source, lookup_key)
        with self._lock:
            self._store.pop(cache_key, None)

    def clear(self) -> None:
        """Remove all entries."""
        with self._lock:
            self._store.clear()

    @property
    def size(self) -> int:
        """Current number of cache entries."""
        with self._lock:
            return len(self._store)


# ---------------------------------------------------------------------------
# Module-level shared cache instance (used by CachedAdapter wrapper)
# ---------------------------------------------------------------------------

_shared_cache = AdapterCache()


def get_shared_cache() -> AdapterCache:
    """Return the module-level shared cache instance."""
    return _shared_cache


# ---------------------------------------------------------------------------
# CachedAdapter wrapper
# ---------------------------------------------------------------------------


class CachedAdapter:
    """Wraps any SourceAdapter to add transparent caching.

    The adapter's public evidence output is unchanged — callers see the same
    AdapterResult whether it came from the cache or a live fetch.

    Cache key is ``(adapter.name, lookup_key_from_context(context))``.

    Args:
        adapter:    The underlying SourceAdapter to wrap.
        cache:      Cache instance.  Defaults to the module-level shared cache.
        key_fn:     Function that extracts a string lookup key from
                    AdapterContext.  Defaults to company_name + domain.
    """

    def __init__(
        self,
        adapter: Any,  # SourceAdapter
        cache: AdapterCache | None = None,
        key_fn: "Any | None" = None,
    ) -> None:
        self._adapter = adapter
        self._cache = cache if cache is not None else _shared_cache
        self._key_fn = key_fn if key_fn is not None else _default_key_fn

    @property
    def name(self) -> str:
        return self._adapter.name

    @property
    def tier(self) -> int:
        return self._adapter.tier

    def fetch(self, context: Any) -> Any:  # AdapterContext → AdapterResult
        """Return cached result if available; otherwise fetch and cache."""
        lookup_key = self._key_fn(context)
        cached = self._cache.get(self._adapter.name, lookup_key)
        if cached is not None:
            logger.info(
                "CachedAdapter: serving cached result for %s/%s",
                self._adapter.name,
                lookup_key,
            )
            return cached

        result = self._adapter.fetch(context)
        self._cache.set(self._adapter.name, lookup_key, result)
        return result


def _default_key_fn(context: Any) -> str:
    """Default lookup key: company_name|domain (normalized to lowercase)."""
    parts = []
    if context.company_name:
        parts.append(context.company_name.lower().strip())
    if getattr(context, "domain", None):
        parts.append(context.domain.lower().strip())
    return "|".join(parts) if parts else str(id(context))
