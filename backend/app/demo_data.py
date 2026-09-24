"""Demo dataset (P5-T3): fictional companies run through the REAL pipeline.

Every stage is the production stage class; only the network clients (registry,
sanctions list, WHOIS/DNS/SSL, IPinfo, website fetch) are replaced by recorded
responses, so the demo is deterministic, offline, and never contacts the
companies' (fictional) domains.  Scores, triage tiers, signals, and reports are
all computed by the real code.

    python -m app.demo_data        # from backend/, DATABASE_URL set

Idempotent: scenarios whose company is already submitted are skipped.
All companies, domains, people, and sanctions entries are fictional.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.adapters.domain import AnalyzeDomainStage
from app.adapters.geocode import GeocodeAdapter, GeocodeHQStage
from app.adapters.ipinfo import EnrichNetworkIPStage, IPInfoAdapter
from app.adapters.linkedin import (
    LinkedInAdapter,
    StubLinkedInProvider,
    VerifyLinkedInStage,
)
from app.adapters.opencorporates import OpenCorporatesAdapter, QueryRegistriesStage
from app.adapters.sanctions import SanctionsScreeningStage
from app.adapters.tax_id import StubTaxIdProvider, TaxIdAdapter, VerifyTaxIdStage
from app.adapters.web import FetchResult, WebEvidenceStage
from app.audit.recorder import record_event
from app.models.entity import Entity
from app.models.submission import Submission
from app.models.verification_run import VerificationRun
from app.pipeline.consistency import ConsistencyChecksStage
from app.pipeline.normalize import NormalizeInputStage
from app.pipeline.orchestrator import Orchestrator
from app.pipeline.resolve import ResolveEntityCandidatesStage
from app.scoring.engine import ScoringStage
from app.scoring.report import StoreReportStage

# Fictional sanctions list (OFAC SDN CSV shape: ent_num, name, type, program, ...).
_DEMO_SDN_CSV = (
    "9001,VOLGA MARITIME TRADING,entity,DEMO,,,,,,,,\n"
    "9002,CRIMSON PEAK LOGISTICS,entity,DEMO,,,,,,,,\n"
)


@dataclass
class Scenario:
    company_name: str
    domain: str
    work_email: str
    country: str
    billing_address: str
    requester_full_name: str
    source_ip: str
    expected_tier: str  # what this scenario is meant to illustrate
    story: str  # one line, for the README / demo narration
    domain_age_days: int | None  # None → WHOIS unavailable
    mx: list[str] = field(default_factory=list)
    txt: list[str] = field(default_factory=list)
    registry: list[dict] | None = None  # None → registry source unavailable
    ip: dict = field(default_factory=dict)
    html: str = ""
    hq: tuple[float, float] | None = None  # recorded geocode (lat, lon)


def _registry_company(name: str, number: str, status: str, jur: str, addr: str):
    return {
        "company": {
            "name": name,
            "company_number": number,
            "current_status": status,
            "jurisdiction_code": jur,
            "registered_address_in_full": addr,
            "opencorporates_url": f"https://opencorporates.com/companies/{jur}/{number}",
        }
    }


def _site(title: str, body: str) -> str:
    return (
        f"<!DOCTYPE html><html><head><title>{title}</title></head>"
        f"<body><h1>{title}</h1>{body}</body></html>"
    )


SCENARIOS: list[Scenario] = [
    Scenario(
        company_name="Northwind Traders Inc",
        domain="northwindtraders.com",
        work_email="maria.anders@northwindtraders.com",
        country="US",
        billing_address="400 Pine Street, Seattle, WA 98101",
        requester_full_name="Maria Anders",
        hq=(47.6114, -122.3366),
        source_ip="73.162.40.18",
        expected_tier="pre_clear",
        story="Established distributor: 20-year domain, registry match, clean.",
        domain_age_days=7300,
        mx=["aspmx.l.google.com"],
        txt=["v=spf1 include:_spf.google.com ~all"],
        registry=[
            _registry_company(
                "Northwind Traders Inc",
                "601847223",
                "Active",
                "us_wa",
                "400 Pine Street, Seattle, WA 98101",
            )
        ],
        ip={
            "ip": "73.162.40.18",
            "city": "Seattle",
            "region": "Washington",
            "country": "US",
            "org": "AS7922 Comcast Cable Communications, LLC",
        },
        html=_site(
            "Northwind Traders",
            "<p>Specialty food distribution since 2004. Visit us at 400 Pine"
            " Street, Seattle, WA 98101.</p><p>Sales: sales@northwindtraders.com"
            " · Support: support@northwindtraders.com · +1 (206) 555-0182</p>"
            "<p>Team: maria.anders@northwindtraders.com,"
            " thomas.hardy@northwindtraders.com</p>",
        ),
    ),
    Scenario(
        company_name="Fabrikam Robotics Corp",
        domain="fabrikamrobotics.com",
        work_email="ops@fabrikamrobotics.com",
        country="US",
        billing_address="1 Innovation Way, Austin, TX 78701",
        requester_full_name="Dana Whitfield",
        hq=(30.2672, -97.7431),
        source_ip="104.28.9.77",
        expected_tier="pre_clear",
        story="Mid-size manufacturer; all tiers corroborate the submission.",
        domain_age_days=4200,
        mx=["fabrikamrobotics-com.mail.protection.outlook.com"],
        txt=["v=spf1 include:spf.protection.outlook.com -all"],
        registry=[
            _registry_company(
                "Fabrikam Robotics Corp",
                "0803341971",
                "Active",
                "us_tx",
                "1 Innovation Way, Austin, TX 78701",
            )
        ],
        ip={
            "ip": "104.28.9.77",
            "city": "Austin",
            "region": "Texas",
            "country": "US",
            "org": "AS11427 Charter Communications Inc",
        },
        html=_site(
            "Fabrikam Robotics",
            "<p>Industrial automation cells. HQ: 1 Innovation Way, Austin.</p>"
            "<p>hello@fabrikamrobotics.com · careers@fabrikamrobotics.com ·"
            " +1 (512) 555-0147</p>",
        ),
    ),
    Scenario(
        company_name="Contoso Analytics GmbH",
        domain="contoso-analytics.de",
        work_email="j.becker@contoso-analytics.de",
        country="DE",
        billing_address="12 Hauptstrasse, Berlin 10115",
        requester_full_name="Jonas Becker",
        hq=(52.5321, 13.3849),
        source_ip="185.220.101.33",
        expected_tier="review",
        story="Real-looking German firm, but no registry record found and the"
        " signup came from a hosting network in another country.",
        domain_age_days=900,
        mx=["mx.contoso-analytics.de"],
        txt=["v=spf1 mx -all"],
        registry=[],
        ip={
            "ip": "185.220.101.33",
            "city": "Amsterdam",
            "region": "North Holland",
            "country": "NL",
            "org": "AS24940 Hetzner Online GmbH",
        },
        html=_site(
            "Contoso Analytics",
            "<p>Data consulting for the Mittelstand.</p>"
            "<p>kontakt@contoso-analytics.de</p>",
        ),
    ),
    Scenario(
        company_name="Brightpath Logistics LLC",
        domain="brightpathlogistics.co",
        work_email="founder@brightpathlogistics.co",
        country="US",
        billing_address="88 Harbor Blvd, Oakland, CA 94607",
        requester_full_name="Sam Ortega",
        hq=(37.7955, -122.2789),
        source_ip="98.207.12.200",
        expected_tier="review",
        story="Young company: 8-month domain, registered name differs from the"
        " submission, registry lookup was down during the run.",
        domain_age_days=240,
        mx=["mx1.privateemail.com"],
        txt=[],
        registry=None,
        ip={
            "ip": "98.207.12.200",
            "city": "Oakland",
            "region": "California",
            "country": "US",
            "org": "AS7922 Comcast Cable Communications, LLC",
        },
        html=_site(
            "Brightpath",
            "<p>Last-mile delivery for independent retailers.</p>",
        ),
    ),
    Scenario(
        company_name="Quantum Ledger Holdings",
        domain="quantumledger-holdings.net",
        work_email="admin@quantumledger-holdings.net",
        country="GB",
        billing_address="Suite 400, 1 Canada Square, London",
        requester_full_name="Alex Reed",
        hq=(51.5049, -0.0195),
        source_ip="159.89.14.2",
        expected_tier="escalate",
        story="Likely shell: 3-week-old domain, no mail server, no registry"
        " record, empty website, signup from a cloud datacenter.",
        domain_age_days=21,
        mx=[],
        txt=[],
        registry=[],
        ip={
            "ip": "159.89.14.2",
            "city": "Frankfurt am Main",
            "region": "Hesse",
            "country": "DE",
            "org": "AS14061 DigitalOcean, LLC",
        },
        html=_site("Coming soon", "<p>Site under construction.</p>"),
    ),
    Scenario(
        company_name="Volga Maritime Trading Ltd",
        domain="volgamaritime.com",
        work_email="chartering@volgamaritime.com",
        country="CY",
        billing_address="5 Makariou Avenue, Limassol",
        requester_full_name="Ivan Petrov",
        hq=(34.6786, 33.0413),
        source_ip="45.137.21.9",
        expected_tier="escalate",
        story="Name matches an entry on the (fictional) sanctions list.",
        domain_age_days=1500,
        mx=["mail.volgamaritime.com"],
        txt=["v=spf1 mx ~all"],
        registry=[
            _registry_company(
                "Volga Maritime Trading Ltd",
                "HE401122",
                "Active",
                "cy",
                "5 Makariou Avenue, Limassol",
            )
        ],
        ip={
            "ip": "45.137.21.9",
            "city": "Limassol",
            "region": "Limassol",
            "country": "CY",
            "org": "AS50673 Serverius Holding B.V.",
        },
        html=_site(
            "Volga Maritime",
            "<p>Dry bulk chartering.</p><p>chartering@volgamaritime.com</p>",
        ),
    ),
]


# ---------------------------------------------------------------------------
# Recorded-response clients
# ---------------------------------------------------------------------------


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


class _Whois:
    def __init__(self, age_days: int | None):
        self._age = age_days

    def query(self, domain: str) -> dict:
        if self._age is None:
            raise RuntimeError("WHOIS unavailable")
        now = datetime.now(tz=timezone.utc)
        return {
            "creation_date": now - timedelta(days=self._age),
            "expiration_date": now + timedelta(days=365),
            "registrar": "Demo Registrar, Inc.",
        }


class _Dns:
    def __init__(self, mx: list[str], txt: list[str]):
        self._mx, self._txt = mx, txt

    def query_mx(self, domain: str) -> list[str]:
        return self._mx

    def query_txt(self, domain: str) -> list[str]:
        return self._txt

    def query_a(self, domain: str) -> list[str]:
        return ["192.0.2.10"]


class _Ssl:
    def get_cert(self, domain: str, port: int = 443) -> dict:
        return {
            "issuer": "Let's Encrypt",
            "subject": domain,
            "not_before": "Aug 1 00:00:00 2026 GMT",
            "not_after": "Oct 30 00:00:00 2026 GMT",
        }


class _Sdn:
    def fetch_csv(self) -> str:
        return _DEMO_SDN_CSV


class _Web:
    def __init__(self, html: str):
        self._html = html

    def fetch(self, url: str) -> FetchResult:
        return FetchResult(url=url, text=self._html)


def _stages(s: Scenario) -> list:
    if s.registry is None:
        registry_http = _Http(503, {"error": "service unavailable"})
    else:
        registry_http = _Http(
            200, {"results": {"companies": s.registry, "total_count": len(s.registry)}}
        )
    geocode = (
        [{"lat": str(s.hq[0]), "lon": str(s.hq[1]), "display_name": s.billing_address}]
        if s.hq
        else []
    )
    return [
        NormalizeInputStage(),
        ResolveEntityCandidatesStage(),
        QueryRegistriesStage(OpenCorporatesAdapter(http_client=registry_http)),
        VerifyTaxIdStage(TaxIdAdapter(provider=StubTaxIdProvider())),
        SanctionsScreeningStage(fetcher=_Sdn()),
        AnalyzeDomainStage(
            whois_client=_Whois(s.domain_age_days),
            dns_client=_Dns(s.mx, s.txt),
            ssl_client=_Ssl(),
        ),
        EnrichNetworkIPStage(IPInfoAdapter(http_client=_Http(200, s.ip))),
        WebEvidenceStage(fetcher=_Web(s.html)),
        VerifyLinkedInStage(LinkedInAdapter(provider=StubLinkedInProvider())),
        ConsistencyChecksStage(),
        GeocodeHQStage(GeocodeAdapter(http_client=_Http(200, geocode))),
        ScoringStage(),
        StoreReportStage(),
    ]


def load_demo_data(db: Session) -> int:
    """Submit and verify every scenario not already present. Returns count added."""
    added = 0
    for s in SCENARIOS:
        exists = (
            db.query(Submission).filter(Submission.company_name == s.company_name)
        ).first()
        if exists:
            continue
        entity = Entity(canonical_name=s.company_name, canonical_domain=s.domain)
        db.add(entity)
        db.flush()
        sub = Submission(
            company_name=s.company_name,
            domain=s.domain,
            work_email=s.work_email,
            country=s.country,
            billing_address=s.billing_address,
            requester_full_name=s.requester_full_name,
            source_ip=s.source_ip,
            user_agent="Mozilla/5.0 (demo dataset)",
            endpoint="/submissions",
            submitted_at=datetime.now(tz=timezone.utc),
            entity_id=entity.id,
        )
        db.add(sub)
        db.flush()
        run = VerificationRun(
            submission_id=sub.id, entity_id=entity.id, status="pending"
        )
        db.add(run)
        db.commit()
        record_event(
            db,
            "system.submission_received",
            submission_id=sub.id,
            verification_run_id=run.id,
            payload={"system": "demo-dataset", "endpoint": "/submissions"},
            description=(
                f"Demo dataset submitted registration for {s.company_name!r}; "
                "verification run enqueued."
            ),
        )
        Orchestrator(_stages(s)).run_sync(run.id, db)
        added += 1
    return added


def main() -> None:
    from app.db.session import DATABASE_URL, SessionLocal  # noqa: PLC0415
    from app.seed import check_seed_target  # noqa: PLC0415

    check_seed_target(DATABASE_URL)
    db = SessionLocal()
    try:
        added = load_demo_data(db)
    finally:
        db.close()
    print(f"Demo dataset: {added} compan{'y' if added == 1 else 'ies'} added.")


if __name__ == "__main__":
    main()
