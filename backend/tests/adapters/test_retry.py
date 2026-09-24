"""Tests for RetryingAdapter — bounded retries on transient failures (ticket 0001).

Transient outages ("timeout", "unavailable") are retried with exponential
backoff up to a fixed number of attempts. Real answers ("not_found") and
rate-limit exhaustion ("rate_limited", already backed off by the rate limiter)
are returned immediately.
"""

import pytest

from app.adapters.base import AdapterContext, AdapterFailure, AdapterSuccess
from app.adapters.retry import RetryingAdapter

_CTX = AdapterContext(run_id="run-1", company_name="Acme", domain="acme.com")


class ScriptedAdapter:
    """Returns the scripted results in order; records how often it was called."""

    name = "scripted"
    tier = 1

    def __init__(self, *results):
        self._results = list(results)
        self.calls = 0

    def fetch(self, context):
        self.calls += 1
        return self._results.pop(0)


class SleepRecorder:
    def __init__(self):
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def _retrying(inner, **kw):
    sleep = SleepRecorder()
    return (
        RetryingAdapter(inner, max_attempts=3, backoff_seconds=1.0, sleep=sleep, **kw),
        sleep,
    )


@pytest.mark.parametrize("kind", ["timeout", "unavailable"])
def test_transient_failure_is_retried_then_succeeds(kind):
    ok = AdapterSuccess(evidence=["row"])
    inner = ScriptedAdapter(AdapterFailure(kind=kind), ok)
    adapter, sleep = _retrying(inner)

    assert adapter.fetch(_CTX) is ok
    assert inner.calls == 2
    assert sleep.delays == [1.0]


def test_retries_are_bounded_and_last_failure_is_returned():
    last = AdapterFailure(kind="timeout", message="third")
    inner = ScriptedAdapter(
        AdapterFailure(kind="timeout", message="first"),
        AdapterFailure(kind="unavailable", message="second"),
        last,
    )
    adapter, sleep = _retrying(inner)

    assert adapter.fetch(_CTX) is last
    assert inner.calls == 3
    # Exponential backoff between attempts, none after the final one.
    assert sleep.delays == [1.0, 2.0]


@pytest.mark.parametrize("kind", ["not_found", "rate_limited"])
def test_non_transient_failure_is_not_retried(kind):
    failure = AdapterFailure(kind=kind)
    inner = ScriptedAdapter(failure)
    adapter, sleep = _retrying(inner)

    assert adapter.fetch(_CTX) is failure
    assert inner.calls == 1
    assert sleep.delays == []


def test_success_on_first_try_does_not_sleep():
    ok = AdapterSuccess()
    inner = ScriptedAdapter(ok)
    adapter, sleep = _retrying(inner)

    assert adapter.fetch(_CTX) is ok
    assert inner.calls == 1
    assert sleep.delays == []


def test_non_fetch_entry_points_are_retried_too():
    """GeocodeAdapter exposes geocode(address), not fetch(context)."""

    class Geocoder:
        name = "geo"
        tier = 3

        def __init__(self):
            self.calls = []
            self._results = [AdapterFailure(kind="timeout"), "coords"]

        def geocode(self, address):
            self.calls.append(address)
            return self._results.pop(0)

    inner = Geocoder()
    adapter, sleep = _retrying(inner)

    assert adapter.geocode("1 Main St") == "coords"
    assert inner.calls == ["1 Main St", "1 Main St"]
    assert sleep.delays == [1.0]


def test_wrapper_preserves_adapter_identity():
    adapter, _ = _retrying(ScriptedAdapter())
    assert adapter.name == "scripted"
    assert adapter.tier == 1


def test_defaults_come_from_env(monkeypatch):
    monkeypatch.setenv("ENTITYIQ_ADAPTER_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("ENTITYIQ_ADAPTER_RETRY_BACKOFF_SECONDS", "0.5")
    inner = ScriptedAdapter(
        AdapterFailure(kind="timeout"), AdapterFailure(kind="timeout")
    )
    sleep = SleepRecorder()

    RetryingAdapter(inner, sleep=sleep).fetch(_CTX)

    assert inner.calls == 2
    assert sleep.delays == [0.5]


@pytest.mark.parametrize(
    "stage_path",
    [
        "app.adapters.opencorporates.QueryRegistriesStage",
        "app.adapters.sanctions.SanctionsScreeningStage",
        "app.adapters.domain.AnalyzeDomainStage",
        "app.adapters.ipinfo.EnrichNetworkIPStage",
        "app.adapters.web.WebEvidenceStage",
        "app.adapters.geocode.GeocodeHQStage",
    ],
)
def test_every_external_source_stage_retries(stage_path):
    """Each stage that calls an external source wraps its adapter."""
    module_path, cls_name = stage_path.rsplit(".", 1)
    module = __import__(module_path, fromlist=[cls_name])
    stage = getattr(module, cls_name)()
    assert isinstance(stage._adapter, RetryingAdapter)
