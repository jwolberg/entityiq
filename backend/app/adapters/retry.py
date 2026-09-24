"""Bounded retries for transient adapter failures (P3-T1, ticket 0001).

Wraps any SourceAdapter, following the CachedAdapter / RateLimitedAdapter
pattern. A "timeout" or "unavailable" failure is retried with exponential
backoff, up to ``max_attempts`` calls in total. The last failure is returned
if every attempt fails, so the stage still reports the source as unavailable.

Not retried:
  - "not_found": a legitimate answer, not an outage.
  - "rate_limited": RateLimitedAdapter already backed off before giving up.

Defaults come from env so operators can tune them without a deploy:
  ENTITYIQ_ADAPTER_MAX_ATTEMPTS           (default 3)
  ENTITYIQ_ADAPTER_RETRY_BACKOFF_SECONDS  (default 1.0; doubles each retry,
                                           which also keeps Nominatim's
                                           1 req/s usage policy)
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from typing import Any

from app.adapters.base import AdapterFailure

logger = logging.getLogger(__name__)

TRANSIENT_KINDS = frozenset({"timeout", "unavailable"})

_DEFAULT_MAX_ATTEMPTS = 3
_DEFAULT_BACKOFF_SECONDS = 1.0


class RetryingAdapter:
    """Wraps a SourceAdapter to retry transient failures.

    Args:
        adapter:         The underlying SourceAdapter.
        max_attempts:    Total calls allowed (1 = no retry). Env default.
        backoff_seconds: Delay before the first retry; doubles each retry.
                         Env default.
        sleep:           Injected for tests.
    """

    def __init__(
        self,
        adapter: Any,  # SourceAdapter
        max_attempts: int | None = None,
        backoff_seconds: float | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._adapter = adapter
        self._max_attempts = max(
            1,
            max_attempts
            if max_attempts is not None
            else int(
                os.environ.get("ENTITYIQ_ADAPTER_MAX_ATTEMPTS", _DEFAULT_MAX_ATTEMPTS)
            ),
        )
        self._backoff = (
            backoff_seconds
            if backoff_seconds is not None
            else float(
                os.environ.get(
                    "ENTITYIQ_ADAPTER_RETRY_BACKOFF_SECONDS", _DEFAULT_BACKOFF_SECONDS
                )
            )
        )
        self._sleep = sleep

    @property
    def name(self) -> str:
        return self._adapter.name

    @property
    def tier(self) -> int:
        return self._adapter.tier

    def fetch(self, *args: Any, **kwargs: Any) -> Any:  # → AdapterResult
        return self._call(self._adapter.fetch, *args, **kwargs)

    def __getattr__(self, attr: str) -> Any:
        # Adapters with a non-standard entry point (e.g. GeocodeAdapter.geocode)
        # get the same retry behavior on every method call.
        value = getattr(self._adapter, attr)
        if not callable(value):
            return value

        def retrying(*args: Any, **kwargs: Any) -> Any:
            return self._call(value, *args, **kwargs)

        return retrying

    def _call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        result = fn(*args, **kwargs)
        attempt = 1
        while (
            isinstance(result, AdapterFailure)
            and result.kind in TRANSIENT_KINDS
            and attempt < self._max_attempts
        ):
            delay = self._backoff * (2 ** (attempt - 1))
            logger.info(
                "RetryingAdapter: %s %s (attempt %d/%d); retrying in %.1fs",
                self._adapter.name,
                result.kind,
                attempt,
                self._max_attempts,
                delay,
            )
            self._sleep(delay)
            result = fn(*args, **kwargs)
            attempt += 1
        return result
