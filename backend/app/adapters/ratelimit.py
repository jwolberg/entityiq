"""Per-source rate limiter with backoff for adapters (P2-T5).

Implements a token-bucket per source with configurable capacity and
refill rate.  When the bucket is empty the caller is made to wait
(blocking backoff with jitter) rather than failing immediately — this
matches ARCHITECTURE § 4: "a source that stays down is recorded as
unavailable, not a failed run".

Design decisions
----------------
- **Token bucket per source** — simple, well-understood algorithm; accurate
  enough for the adapter concurrency model (one worker, sequential stages).
- **Blocking backoff, not immediate failure** — rate-limited sources should
  yield after a short wait, not cascade into AdapterFailure.  Callers that
  truly exhaust retries receive AdapterFailure(kind="rate_limited") — but
  only after ``max_wait_seconds`` has elapsed total.
- **Stdlib only** — no external deps (asyncio.sleep is intentionally avoided
  since pipeline stages are synchronous).
- **Thread-safe** — uses a lock; same reasoning as cache.py.
- **Run-level source availability summary** — ``SourceAvailabilityTracker``
  records which sources were available/unavailable during a run.  The scoring
  layer can use this to adjust confidence when coverage is reduced.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from app.adapters.base import AdapterFailure

logger = logging.getLogger(__name__)

# Default rate-limiter settings per source.
# (capacity, refill_rate_per_second, max_wait_seconds)
DEFAULT_RATE_LIMITS: dict[str, tuple[float, float, float]] = {
    "opencorporates": (5.0, 1.0, 30.0),  # 5 burst, 1 req/s, max 30s wait
    "sanctions": (10.0, 5.0, 10.0),  # generous — local file
    "domain": (10.0, 2.0, 20.0),  # DNS is cheap
    "ipinfo": (10.0, 2.0, 15.0),  # free tier is ~1000 req/day
    "web": (3.0, 0.5, 20.0),  # conservative — scraping
}

# Fallback defaults for sources not in the table.
_DEFAULT_CAPACITY = 5.0
_DEFAULT_REFILL = 1.0
_DEFAULT_MAX_WAIT = 30.0


@dataclass
class _Bucket:
    """Token bucket state for one source."""

    capacity: float
    refill_rate: float  # tokens per second
    tokens: float = field(init=False)
    last_refill: float = field(init=False)

    def __post_init__(self) -> None:
        self.tokens = self.capacity
        self.last_refill = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

    def consume(self) -> bool:
        """Attempt to consume one token.  Returns True if a token was available."""
        self._refill()
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class RateLimiter:
    """Per-source token-bucket rate limiter.

    Args:
        limits: Mapping of source name → (capacity, refill_rate, max_wait_s).
                Falls back to default values for unlisted sources.
    """

    def __init__(
        self,
        limits: dict[str, tuple[float, float, float]] | None = None,
    ) -> None:
        self._limits: dict[str, tuple[float, float, float]] = {
            **DEFAULT_RATE_LIMITS,
            **(limits or {}),
        }
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def _get_bucket(self, source: str) -> _Bucket:
        """Return (creating if necessary) the bucket for a source."""
        if source not in self._buckets:
            cap, rate, _ = self._limits.get(
                source, (_DEFAULT_CAPACITY, _DEFAULT_REFILL, _DEFAULT_MAX_WAIT)
            )
            self._buckets[source] = _Bucket(capacity=cap, refill_rate=rate)
        return self._buckets[source]

    def acquire(self, source: str) -> bool:
        """Acquire a token, waiting with backoff if needed.

        Blocks up to ``max_wait_seconds`` (jittered exponential backoff).
        Returns True if the token was acquired; False if max wait exceeded.

        Args:
            source: The adapter/source name.
        """
        _, _, max_wait = self._limits.get(
            source, (_DEFAULT_CAPACITY, _DEFAULT_REFILL, _DEFAULT_MAX_WAIT)
        )
        deadline = time.monotonic() + max_wait
        attempt = 0

        while True:
            with self._lock:
                bucket = self._get_bucket(source)
                if bucket.consume():
                    return True

            # Token not available — compute backoff.
            if time.monotonic() >= deadline:
                logger.warning(
                    "RateLimiter: max_wait %.1fs exhausted for source %r",
                    max_wait,
                    source,
                )
                return False

            # Jittered exponential backoff: base 0.1s, cap at 2s.
            base = min(0.1 * (2**attempt), 2.0)
            sleep_for = base * (0.5 + random.random() * 0.5)
            remaining = deadline - time.monotonic()
            sleep_for = min(sleep_for, remaining)
            if sleep_for > 0:
                time.sleep(sleep_for)
            attempt += 1


# ---------------------------------------------------------------------------
# RateLimitedAdapter wrapper
# ---------------------------------------------------------------------------


class RateLimitedAdapter:
    """Wraps any SourceAdapter to add per-source rate limiting with backoff.

    When the rate limiter's max_wait is exceeded the adapter returns
    AdapterFailure(kind="rate_limited") instead of calling the underlying
    adapter — preserving the "never fails silently" contract.

    Args:
        adapter:      The underlying SourceAdapter to wrap.
        rate_limiter: RateLimiter instance.  Defaults to a new instance with
                      default limits.
    """

    def __init__(
        self,
        adapter: "Any",  # SourceAdapter
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self._adapter = adapter
        self._limiter = rate_limiter if rate_limiter is not None else RateLimiter()

    @property
    def name(self) -> str:
        return self._adapter.name

    @property
    def tier(self) -> int:
        return self._adapter.tier

    def fetch(self, context: "Any") -> "Any":  # AdapterContext → AdapterResult
        """Acquire a rate-limit token then fetch; return rate_limited on exhaustion."""
        acquired = self._limiter.acquire(self._adapter.name)
        if not acquired:
            logger.warning(
                "RateLimitedAdapter: rate limit exhausted for %r",
                self._adapter.name,
            )
            return AdapterFailure(
                kind="rate_limited",
                message=f"Rate limit exhausted for source {self._adapter.name!r}",
            )
        return self._adapter.fetch(context)


# ---------------------------------------------------------------------------
# Run-level source availability tracker
# ---------------------------------------------------------------------------


class SourceAvailabilityTracker:
    """Records which sources were available/unavailable during a pipeline run.

    A stage calls ``record()`` after each adapter result; the final summary
    is available via ``summary()``.  The scoring layer reads this to adjust
    confidence when coverage is reduced (ARCHITECTURE § 4).

    Args:
        run_id: The VerificationRun.id this tracker is for (for logging).
    """

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._records: dict[str, str] = {}  # source → "available" | "unavailable"

    def record(self, source: str, result: "Any") -> None:
        """Record the outcome for a source.

        Args:
            source: Adapter name.
            result: AdapterResult (AdapterSuccess or AdapterFailure).
        """
        from app.adapters.base import AdapterSuccess  # noqa: PLC0415

        status = "available" if isinstance(result, AdapterSuccess) else "unavailable"
        self._records[source] = status
        logger.debug("SourceAvailability[%s]: %s → %s", self.run_id, source, status)

    def summary(self) -> dict[str, str]:
        """Return a snapshot of source availability for this run."""
        return dict(self._records)

    def is_available(self, source: str) -> bool:
        """Return True if the source was recorded as available."""
        return self._records.get(source) == "available"

    @property
    def available_sources(self) -> list[str]:
        return [s for s, st in self._records.items() if st == "available"]

    @property
    def unavailable_sources(self) -> list[str]:
        return [s for s, st in self._records.items() if st == "unavailable"]
