"""P2-T9 — HQ geocoding stage (OpenStreetMap Nominatim, client injected)."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.adapters.geocode import GeocodeAdapter, GeocodeHQStage
from app.db.session import Base
from app.models.evidence import Evidence
from app.models.field_comparison import FieldComparison


class _Resp:
    def __init__(self, status_code, payload):
        self.status_code, self._payload = status_code, payload

    def json(self):
        return self._payload


class _Http:
    def __init__(self, status_code=200, payload=None):
        self._resp = _Resp(status_code, payload if payload is not None else [])
        self.calls: list[dict] = []

    def get(self, url, *, params=None, headers=None, timeout=10.0):
        self.calls.append({"url": url, "params": params, "headers": headers})
        return self._resp


_HIT = [{"lat": "47.6116", "lon": "-122.3358", "display_name": "400 Pine St, Seattle"}]


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess
    engine.dispose()


def _legal_address(db, run_id, value):
    db.add(
        Evidence(
            verification_run_id=run_id,
            source="opencorporates",
            tier=1,
            field="legal_address",
            raw_value=value,
            normalized_value=value,
            confidence=0.9,
            fetched_at=datetime.now(tz=timezone.utc),
        )
    )
    db.commit()


def _billing_comparison(db, run_id, status):
    db.add(
        FieldComparison(
            verification_run_id=run_id,
            field_name="billing_address",
            submitted_value="400 Pine Street, Seattle",
            discovered_value="400 Pine Street, Seattle",
            match_status=status,
        )
    )
    db.commit()


def _run(db, http, billing="400 Pine Street, Seattle, WA"):
    stage = GeocodeHQStage(GeocodeAdapter(http_client=http))
    ctx = stage.run("run-1", db, {"normalized": {"billing_address": billing}})
    fields = {
        e.field: e.normalized_value
        for e in db.query(Evidence).filter(Evidence.source == "geocode").all()
    }
    return ctx, fields


def test_geocodes_registry_address_first_with_identifying_user_agent(db):
    _legal_address(db, "run-1", "400 PINE ST, SEATTLE, WA 98101")
    http = _Http(payload=_HIT)
    _, fields = _run(db, http)

    assert http.calls[0]["params"]["q"] == "400 PINE ST, SEATTLE, WA 98101"
    assert "EntityIQ" in http.calls[0]["headers"]["User-Agent"]  # Nominatim policy
    assert fields["hq_latitude"] == "47.6116"
    assert fields["hq_longitude"] == "-122.3358"
    assert fields["hq_address_source"] == "registry"


def test_falls_back_to_submitted_billing_address(db):
    http = _Http(payload=_HIT)
    _, fields = _run(db, http)
    assert http.calls[0]["params"]["q"] == "400 Pine Street, Seattle, WA"
    assert fields["hq_address_source"] == "submitted"


@pytest.mark.parametrize(
    "legal, comparison, expected",
    [
        ("400 PINE ST, SEATTLE", "match", "high"),  # two sources agree
        ("400 PINE ST, SEATTLE", None, "medium"),  # registry only
        ("400 PINE ST, SEATTLE", "mismatch", "low"),  # sources disagree
        (None, None, "low"),  # self-reported only
    ],
)
def test_address_confidence(db, legal, comparison, expected):
    if legal:
        _legal_address(db, "run-1", legal)
    if comparison:
        _billing_comparison(db, "run-1", comparison)
    _, fields = _run(db, _Http(payload=_HIT))
    assert fields["hq_address_confidence"] == expected


def test_no_geocode_result_is_not_found_not_an_outage(db):
    ctx, fields = _run(db, _Http(payload=[]))
    assert ctx["geocode_hq"]["status"] == "not_found"
    assert "hq_latitude" not in fields


def test_provider_error_is_unavailable(db):
    ctx, _ = _run(db, _Http(status_code=503))
    assert ctx["geocode_hq"]["status"] == "unavailable"


def test_no_address_at_all_skips_the_lookup(db):
    http = _Http(payload=_HIT)
    ctx, _ = _run(db, http, billing=None)
    assert http.calls == []
    assert ctx["geocode_hq"]["status"] == "not_found"
