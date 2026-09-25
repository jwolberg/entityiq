"""The officer-screening bridge stays the only coupling point (ADR-0006).

KYB code never imports ``app.screening`` (tests/screening/test_models.py);
this bridge package may import both offerings. These guards keep it that way:
screening never depends on the bridge, and KYB reaches it only by registering
its pipeline stages.
"""

from pathlib import Path

from tests.screening.test_models import _imports

_APP = Path(__file__).resolve().parents[2] / "app"
_KYB_PACKAGES = ("adapters", "pipeline", "scoring", "api", "audit")
# The one KYB module allowed to import the bridge: stage registration.
_REGISTRATION = _APP / "pipeline" / "orchestrator.py"


def test_screening_never_imports_the_bridge():
    for path in (_APP / "screening").rglob("*.py"):
        bad = {m for m in _imports(path) if m.startswith("app.officer_screening")}
        assert not bad, f"{path} imports {bad}"


def test_kyb_reaches_the_bridge_only_through_stage_registration():
    for sub in _KYB_PACKAGES:
        for path in (_APP / sub).rglob("*.py"):
            if path == _REGISTRATION:
                continue
            bad = {m for m in _imports(path) if m.startswith("app.officer_screening")}
            assert not bad, f"{path} imports {bad}"
