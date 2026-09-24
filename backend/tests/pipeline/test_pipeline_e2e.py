"""End-to-end pipeline test (P4-T1).

Drives the REAL stage list (same classes, same order as default_stages())
with only the network clients replaced by deterministic fakes, then asserts on
the persisted RiskAssessment.  Unlike the scoring unit tests, nothing here
hand-builds Evidence rows — so a field-name drift between an adapter and the
signal catalog (e.g. adapter emits "mx_records" while scoring reads
"mx_present") fails this test.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.adapters.domain import AnalyzeDomainStage
from app.adapters.ipinfo import EnrichNetworkIPStage, IPInfoAdapter
from app.adapters.opencorporates import OpenCorporatesAdapter, QueryRegistriesStage
from app.adapters.sanctions import SanctionsScreeningStage
from app.adapters.web import FetchResult, WebEvidenceStage
from app.db.session import Base
from app.models.entity import Entity
from app.models.risk_assessment import RiskAssessment
from app.models.submission import Submission
from app.models.verification_run import VerificationRun
from app.pipeline.consistency import ConsistencyChecksStage
from app.pipeline.normalize import NormalizeInputStage
from app.pipeline.orchestrator import Orchestrator, default_stages
from app.pipeline.resolve import ResolveEntityCandidatesStage
from app.scoring.engine import ScoringStage
from app.scoring.report import StoreReportStage

# ---------------------------------------------------------------------------
# Fakes (network boundary only)
# ---------------------------------------------------------------------------


class _Whois:
    def __init__(self, age_days: int):
        self._created = datetime.now(tz=timezone.utc) - timedelta(days=age_days)

    def query(self, domain: str) -> dict:
        return {
            "creation_date": self._created,
            "registrar": "MarkMonitor Inc.",
            "expiration_date": datetime.now(tz=timezone.utc) + timedelta(days=365),
        }


class _Dns:
    def __init__(self, mx: list[str], txt: list[str]):
        self._mx, self._txt = mx, txt

    def query_mx(self, domain: str) -> list[str]:
        return self._mx

    def query_txt(self, domain: str) -> list[str]:
        return self._txt

    def query_a(self, domain: str) -> list[str]:
        return ["93.184.216.34"]


class _Ssl:
    def get_cert(self, domain: str, port: int = 443) -> dict:
        return {
            "issuer": "DigiCert Inc",
            "subject": domain,
            "not_before": "Jan 1 00:00:00 2026 GMT",
            "not_after": "Jan 1 00:00:00 2027 GMT",
        }


class _Resp:
    def __init__(self, status_code: int, payload: dict):
        self.status_code, self._payload = status_code, payload

    def json(self) -> dict:
        return self._payload


class _Http:
    def __init__(self, status_code: int, payload: dict):
        self._resp = _Resp(status_code, payload)

    def get(self, url, *, params=None, headers=None, timeout: float = 10.0):
        return self._resp


class _Sdn:
    def fetch_csv(self) -> str:
        return "1000,EVIL CORP,entity,SDGT,,,,,,,,\n"


class _Web:
    def __init__(self, html: str):
        self._html = html

    def fetch(self, url: str) -> FetchResult:
        return FetchResult(url=url, text=self._html)


_ACME_REGISTRY = {
    "results": {
        "companies": [
            {
                "company": {
                    "name": "Acme Corporation",
                    "company_number": "C123456",
                    "current_status": "Active",
                    "jurisdiction_code": "us_ca",
                    "registered_address_in_full": (
                        "1 Market St, San Francisco, CA 94105"
                    ),
                    "opencorporates_url": "https://opencorporates.com/c/1",
                }
            }
        ],
        "total_count": 1,
    }
}

_US_RESIDENTIAL_IP = {
    "ip": "8.8.8.8",
    "city": "San Francisco",
    "region": "California",
    "country": "US",
    "org": "AS7922 Comcast Cable Communications, LLC",
}

_ACME_HTML = """<!DOCTYPE html><html><head>
<title>Acme Corporation | Industrial Supplies</title>
<meta name="description" content="Acme Corporation makes industrial supplies.">
</head><body>
<h1>Acme Corporation</h1>
<p>Founded in 1998, Acme serves customers worldwide from our headquarters at
1 Market Street, San Francisco, CA 94105.</p>
<p>Contact sales@acme.com, support@acme.com, press@acme.com, or call
+1 (415) 555-0142.</p>
<p>Leadership: jane.smith@acme.com, raj.patel@acme.com</p>
</body></html>"""


def _stages(*, age_days: int, mx: list[str], txt: list[str], registry: dict):
    """Real stage classes in default_stages() order; only clients faked."""
    return [
        NormalizeInputStage(),
        ResolveEntityCandidatesStage(),
        QueryRegistriesStage(OpenCorporatesAdapter(http_client=_Http(200, registry))),
        SanctionsScreeningStage(fetcher=_Sdn()),
        AnalyzeDomainStage(
            whois_client=_Whois(age_days),
            dns_client=_Dns(mx, txt),
            ssl_client=_Ssl(),
        ),
        EnrichNetworkIPStage(IPInfoAdapter(http_client=_Http(200, _US_RESIDENTIAL_IP))),
        WebEvidenceStage(fetcher=_Web(_ACME_HTML)),
        ConsistencyChecksStage(),
        ScoringStage(),
        StoreReportStage(),
    ]


# ---------------------------------------------------------------------------
# DB fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def e2e_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def db(e2e_engine):
    connection = e2e_engine.connect()
    transaction = connection.begin()
    sess = Session(bind=connection)
    yield sess
    sess.close()
    transaction.rollback()
    connection.close()


def _submit(db: Session, *, domain: str = "acme.com") -> VerificationRun:
    entity = Entity(canonical_name="Acme Corporation", canonical_domain=domain)
    db.add(entity)
    db.flush()
    sub = Submission(
        company_name="Acme Corporation",
        domain=domain,
        work_email=f"jane.smith@{domain}",
        country="US",
        billing_address="1 Market St, San Francisco, CA 94105",
        source_ip="8.8.8.8",
        entity_id=entity.id,
    )
    db.add(sub)
    db.flush()
    run = VerificationRun(submission_id=sub.id, entity_id=entity.id, status="pending")
    db.add(run)
    db.commit()
    return run


def _run(db: Session, stages) -> tuple[VerificationRun, RiskAssessment, set[str]]:
    run = _submit(db)
    Orchestrator(stages).run_sync(run.id, db)
    db.refresh(run)
    ra = (
        db.query(RiskAssessment)
        .filter(RiskAssessment.verification_run_id == run.id)
        .one()
    )
    names = {s["name"] for s in (ra.contributing_signals or [])}
    return run, ra, names


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_stage_list_matches_default_stages():
    """The faked list must mirror production order, or this test proves nothing."""
    faked = _stages(age_days=4000, mx=["mx.acme.com"], txt=[], registry=_ACME_REGISTRY)
    assert [s.name for s in faked] == [s.name for s in default_stages()]


def test_established_company_gets_infrastructure_trust_signals(db):
    _, ra, names = _run(
        db,
        _stages(
            age_days=4000,
            mx=["aspmx.l.google.com"],
            txt=["v=spf1 include:_spf.google.com ~all"],
            registry=_ACME_REGISTRY,
        ),
    )
    for expected in (
        "has_mx_records",
        "spf_configured",
        "dkim_configured",
        "ssl_present",
        "long_lived_domain",
        "registry_name_confirmed",
        "sanctions_cleared",
    ):
        assert expected in names, f"{expected} missing; got {sorted(names)}"


def test_established_company_is_pre_cleared(db):
    run, ra, _ = _run(
        db,
        _stages(
            age_days=4000,
            mx=["aspmx.l.google.com"],
            txt=["v=spf1 include:_spf.google.com ~all"],
            registry=_ACME_REGISTRY,
        ),
    )
    assert run.status == "complete"
    assert all(v == "complete" for v in run.source_availability.values())
    assert ra.triage_tier == "pre_clear", (ra.overall_score, ra.contributing_signals)


def test_fresh_shell_domain_is_not_pre_cleared(db):
    _, ra, names = _run(
        db,
        _stages(
            age_days=20,
            mx=[],
            txt=[],
            registry={"results": {"companies": [], "total_count": 0}},
        ),
    )
    assert "recently_registered_domain" in names
    assert "no_mx_records" in names
    assert "has_mx_records" not in names
    assert "spf_configured" not in names
    assert ra.triage_tier != "pre_clear"


def test_failed_source_is_reported_unavailable_not_dropped(db):
    """A 401 from the registry must surface as an unavailable source (P4-T3)."""
    from app.models.report import Report

    stages = _stages(
        age_days=4000,
        mx=["aspmx.l.google.com"],
        txt=["v=spf1 ~all"],
        registry={},
    )
    stages[2] = QueryRegistriesStage(
        OpenCorporatesAdapter(http_client=_Http(401, {"error": "token required"}))
    )
    run, _, _ = _run(db, stages)

    assert run.source_availability["query_registries"] == "unavailable"
    report = db.query(Report).filter(Report.verification_run_id == run.id).one()
    by_source = {s["source"]: s for s in report.summary["sources"]}
    assert by_source["opencorporates"]["status"] == "unavailable"
    assert by_source["opencorporates"]["evidence_count"] == 0
    assert by_source["domain"]["status"] == "available"


def test_not_found_is_a_result_not_an_outage(db):
    """No registry match is a finding; the source itself was available."""
    run, _, _ = _run(
        db,
        _stages(
            age_days=4000,
            mx=["aspmx.l.google.com"],
            txt=[],
            registry={"results": {"companies": [], "total_count": 0}},
        ),
    )
    assert run.source_availability["query_registries"] == "complete"
