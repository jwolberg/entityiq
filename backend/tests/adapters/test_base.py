"""Tests for P1-T4 — adapter base contract (base.py).

Verifies:
  - AdapterContext, AdapterSuccess, AdapterFailure dataclasses
  - SourceAdapter protocol is runtime-checkable
  - A conforming adapter satisfies the protocol
  - A non-conforming class does not
"""

from app.adapters.base import (
    AdapterContext,
    AdapterFailure,
    AdapterSuccess,
    SourceAdapter,
)

# ---------------------------------------------------------------------------
# Conforming stub adapter
# ---------------------------------------------------------------------------


class _StubAdapter:
    name = "stub"
    tier = 1

    def fetch(self, context: AdapterContext) -> AdapterSuccess | AdapterFailure:
        return AdapterSuccess(evidence=[])


class _NoNameAdapter:
    tier = 1

    def fetch(self, context: AdapterContext) -> AdapterSuccess:
        return AdapterSuccess(evidence=[])


# ---------------------------------------------------------------------------
# Tests: dataclasses
# ---------------------------------------------------------------------------


def test_adapter_context_defaults():
    ctx = AdapterContext(run_id="run-1")
    assert ctx.run_id == "run-1"
    assert ctx.company_name is None
    assert ctx.domain is None
    assert ctx.country_iso is None


def test_adapter_context_with_values():
    ctx = AdapterContext(
        run_id="run-2",
        company_name="Acme Corp",
        domain="acme.example",
        country_iso="US",
        tax_id="12-3456789",
    )
    assert ctx.company_name == "Acme Corp"
    assert ctx.country_iso == "US"


def test_adapter_success_empty_evidence():
    result = AdapterSuccess()
    assert result.evidence == []


def test_adapter_success_with_evidence():
    result = AdapterSuccess(evidence=["fake_ev"])
    assert len(result.evidence) == 1


def test_adapter_failure_kinds():
    for kind in ("timeout", "unavailable", "not_found", "rate_limited"):
        f = AdapterFailure(kind=kind, message="detail")
        assert f.kind == kind
        assert f.message == "detail"


def test_adapter_failure_default_message():
    f = AdapterFailure(kind="timeout")
    assert f.message == ""


# ---------------------------------------------------------------------------
# Tests: SourceAdapter protocol
# ---------------------------------------------------------------------------


def test_conforming_adapter_satisfies_protocol():
    adapter = _StubAdapter()
    assert isinstance(adapter, SourceAdapter)


def test_adapter_missing_name_does_not_satisfy_protocol():
    adapter = _NoNameAdapter()
    assert not isinstance(adapter, SourceAdapter)


def test_adapter_result_union_types():
    """Both result variants are valid AdapterResult values."""
    success: AdapterSuccess | AdapterFailure = AdapterSuccess(evidence=[])
    failure: AdapterSuccess | AdapterFailure = AdapterFailure(kind="not_found")
    assert isinstance(success, AdapterSuccess)
    assert isinstance(failure, AdapterFailure)
