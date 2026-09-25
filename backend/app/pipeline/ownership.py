"""Domain-ownership verification (ticket 0003, plan unit U24).

Issues a challenge token for a submission's domain and verifies it via one
of three methods, per PRD § Domain Ownership Verification and USERS § 4:

  - dns_txt:   the registrant publishes
               "entityiq-domain-verification=<token>" as a DNS TXT record on
               the domain.
  - html_meta: the registrant places
               <meta name="entityiq-domain-verification" content="<token>">
               on the domain's homepage.
  - email:     EntityIQ sends the token to an address at the domain; the
               registrant submits the token back (e.g. via a link in the
               email).  There is no independent network check at verify
               time for this method — the submitted token itself is the
               proof of receipt.

All three methods share the same issue/verify contract: issue_challenge()
returns a pending OwnershipChallenge with a token; attempt_verification()
performs the method-specific proof check and, only on success, records a
bounded trust signal.

IMPORTANT — bounded signal, never authorization (PRD, USERS § 4):
  A verified challenge only ever contributes a small, bounded TRUST signal in
  the representation-confidence layer (see app.scoring.signals). It never
  implies the registrant is authorized to represent the organization. An
  absent/incorrect token contributes nothing (no elevated signal, no
  penalty) — it is simply "unverified", and verification may be retried
  (e.g. while DNS propagates).

All network access (DNS TXT lookup, HTML fetch, email send) is behind
injectable client Protocols so every test here runs fully offline — the
same pattern as app.adapters.domain and app.adapters.web.
"""

from __future__ import annotations

import hmac
import logging
import re
import secrets
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.models.ownership_challenge import OwnershipChallenge

logger = logging.getLogger(__name__)

# Evidence contract consumed by app.scoring.signals — keep these in sync.
EVIDENCE_SOURCE = "ownership"
EVIDENCE_FIELD = "domain_ownership_verified"

CHALLENGE_METHODS: tuple[str, ...] = ("dns_txt", "email", "html_meta")

_TXT_PREFIX = "entityiq-domain-verification="
_META_TAG_NAME = "entityiq-domain-verification"


class UnknownChallengeMethod(ValueError):
    """Raised when a caller asks for a method outside CHALLENGE_METHODS."""


# ---------------------------------------------------------------------------
# Token + instruction formatting
# ---------------------------------------------------------------------------


def generate_challenge_token() -> str:
    """A short, random, URL/DNS/HTML-safe challenge token."""
    return secrets.token_hex(16)  # 32 hex chars


def dns_txt_record_value(token: str) -> str:
    """The exact TXT record value the registrant must publish."""
    return f"{_TXT_PREFIX}{token}"


def html_meta_snippet(token: str) -> str:
    """The exact <meta> tag the registrant must place on their homepage."""
    return f'<meta name="{_META_TAG_NAME}" content="{token}">'


# ---------------------------------------------------------------------------
# Injectable clients — no live network in tests
# ---------------------------------------------------------------------------


class DnsTxtClient(Protocol):
    """Minimal interface for DNS TXT lookups."""

    def query_txt(self, domain: str) -> list[str]:
        """Return a list of TXT record strings for domain (may be empty)."""
        ...


class HttpPageFetcher(Protocol):
    """Minimal interface for fetching a page's HTML."""

    def fetch_text(self, url: str) -> str:
        """Return the page body as text. Raise on any failure."""
        ...


class EmailSender(Protocol):
    """Minimal interface for delivering the challenge token by email."""

    def send(self, to: str, token: str) -> None:
        """Send the token to `to`. Raise on any failure."""
        ...


def _default_dns_txt_client() -> DnsTxtClient:
    """Return a DNS TXT client using dnspython (optional dep, guarded import)."""
    try:
        import dns.resolver  # noqa: PLC0415

        class _Impl:
            def query_txt(self, domain: str) -> list[str]:
                try:
                    answers = dns.resolver.resolve(domain, "TXT")
                    return [b.decode() for r in answers for b in r.strings]
                except Exception:
                    return []

        return _Impl()
    except ImportError:

        class _Unavailable:
            def query_txt(self, domain: str) -> list[str]:
                raise RuntimeError("dnspython is not installed")

        return _Unavailable()


class UnsafeFetchTarget(ValueError):
    """The html_meta target isn't a public internet host (SSRF guard)."""


def _system_resolve(host: str) -> list[str]:
    import socket  # noqa: PLC0415

    return sorted({info[4][0] for info in socket.getaddrinfo(host, 443)})


def assert_public_host(domain: str, resolve=_system_resolve) -> None:
    """Refuse IP literals and hosts resolving to non-public addresses.

    The submitted domain is caller-controlled, so fetching it could otherwise
    reach cloud metadata (169.254.169.254), localhost or internal services.
    """
    import ipaddress  # noqa: PLC0415

    host = domain.strip().strip("[]").rstrip(".")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise UnsafeFetchTarget(f"IP literal {host!r} is not a domain")
    try:
        addresses = resolve(host)
    except OSError as exc:
        raise UnsafeFetchTarget(f"cannot resolve {host!r}: {exc}") from exc
    if not addresses:
        raise UnsafeFetchTarget(f"{host!r} has no addresses")
    for raw in addresses:
        addr = ipaddress.ip_address(raw)
        if not addr.is_global or addr.is_multicast:
            raise UnsafeFetchTarget(f"{host!r} resolves to non-public {raw}")


def _default_http_page_fetcher() -> HttpPageFetcher:
    """Return an HTTP fetcher using httpx (a core dependency of this project).

    SSRF-guarded: only public hosts, and redirects are not followed (a
    redirect could point back inside the network).
    """

    class _Impl:
        def fetch_text(self, url: str) -> str:
            from urllib.parse import urlsplit  # noqa: PLC0415

            import httpx  # noqa: PLC0415

            assert_public_host(urlsplit(url).hostname or "")
            resp = httpx.get(
                url,
                timeout=10.0,
                follow_redirects=False,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (compatible; EntityIQ/1.0; "
                        "+https://github.com/jwolberg/entityiq)"
                    )
                },
            )
            resp.raise_for_status()
            return resp.text

    return _Impl()


def _default_email_sender() -> EmailSender:
    """Production default: no email backend is wired up for this MVP.

    Mirrors app.adapters.tax_id's UnconfiguredTaxIdProvider — the send
    attempt fails loudly (caught by the caller), never silently.
    """

    class _Unconfigured:
        def send(self, to: str, token: str) -> None:
            raise RuntimeError(
                "No email backend configured for domain-ownership verification"
            )

    return _Unconfigured()


# ---------------------------------------------------------------------------
# Verification checks — pure, easily unit-testable
# ---------------------------------------------------------------------------


def check_dns_txt(domain: str, token: str, client: DnsTxtClient) -> bool:
    """True if `domain` publishes a TXT record proving `token`."""
    expected = dns_txt_record_value(token)
    try:
        records = client.query_txt(domain)
    except Exception as exc:
        logger.debug("DNS TXT lookup failed for %s: %s", domain, exc)
        return False
    return any(expected in record for record in records)


def _extract_meta_content(html: str, name: str) -> str | None:
    """Extract a <meta name="..." content="..."> value (attribute order agnostic)."""
    pattern = re.compile(
        r'<meta[^>]+name\s*=\s*["\']'
        + re.escape(name)
        + r'["\'][^>]+content\s*=\s*["\']([^"\']+)["\']',
        re.IGNORECASE,
    )
    m = pattern.search(html)
    if m:
        return m.group(1).strip()
    pattern2 = re.compile(
        r'<meta[^>]+content\s*=\s*["\']([^"\']+)["\'][^>]+name\s*=\s*["\']'
        + re.escape(name)
        + r'["\']',
        re.IGNORECASE,
    )
    m2 = pattern2.search(html)
    return m2.group(1).strip() if m2 else None


def check_html_meta(domain: str, token: str, fetcher: HttpPageFetcher) -> bool:
    """True if `domain`'s homepage carries the expected verification meta tag."""
    url = f"https://{domain}/"
    try:
        html = fetcher.fetch_text(url)
    except Exception as exc:
        logger.debug("HTML fetch failed for %s: %s", url, exc)
        return False
    content = _extract_meta_content(html, _META_TAG_NAME)
    if content is None:
        return False
    return hmac.compare_digest(content, token)


def check_email_token(submitted_token: str | None, expected_token: str) -> bool:
    """True if the token submitted back (e.g. via the emailed link) matches.

    There is no external lookup for this method — the emailed token itself
    (sent by EmailSender at issue time) IS the proof of mailbox control.
    """
    if not submitted_token:
        return False
    return hmac.compare_digest(submitted_token, expected_token)


# ---------------------------------------------------------------------------
# DB-backed orchestration
# ---------------------------------------------------------------------------


def issue_challenge(
    db: "Session",
    *,
    run_id: str,
    method: str,
    target: str | None = None,
    operator_id: str | None = None,
    api_client_id: str | None = None,
    email_sender: EmailSender | None = None,
) -> "OwnershipChallenge":
    """Create and persist a new domain-ownership challenge for a run's submission.

    The challenge is scoped to `run_id`: on success, its evidence is attached
    to this same verification run (Evidence.verification_run_id is required).

    Raises:
        UnknownChallengeMethod: method not one of CHALLENGE_METHODS.
        ValueError: the run (or its submission) cannot be found, or
                    method == "email" with no target address.
    """
    from app.models.ownership_challenge import OwnershipChallenge  # noqa: PLC0415
    from app.models.submission import Submission  # noqa: PLC0415
    from app.models.verification_run import VerificationRun  # noqa: PLC0415

    if method not in CHALLENGE_METHODS:
        raise UnknownChallengeMethod(
            f"Unknown method {method!r}; expected one of {CHALLENGE_METHODS}"
        )
    if method == "email" and not target:
        raise ValueError("An email target address is required for the 'email' method")

    run = db.get(VerificationRun, run_id)
    if run is None:
        raise ValueError(f"Verification run {run_id!r} not found")
    submission = db.get(Submission, run.submission_id)
    if submission is None:
        raise ValueError(f"Submission for run {run_id!r} not found")

    challenge = OwnershipChallenge(
        verification_run_id=run.id,
        submission_id=submission.id,
        domain=submission.domain,
        method=method,
        token=generate_challenge_token(),
        target=target,
        status="pending",
        operator_id=operator_id,
        api_client_id=api_client_id,
    )
    db.add(challenge)
    db.commit()

    if method == "email":
        # Best-effort: a missing/unconfigured email backend never blocks
        # issuing the challenge (the token is still returned to the caller,
        # e.g. for an operator to relay manually).
        try:
            (email_sender or _default_email_sender()).send(target, challenge.token)
        except Exception as exc:
            logger.warning(
                "Ownership challenge %s: email send to %r failed: %s",
                challenge.id,
                target,
                exc,
            )
            challenge.last_error = f"email send failed: {exc}"
            db.commit()

    return challenge


def attempt_verification(
    db: "Session",
    challenge: "OwnershipChallenge",
    *,
    submitted_token: str | None = None,
    dns_client: DnsTxtClient | None = None,
    http_fetcher: HttpPageFetcher | None = None,
) -> bool:
    """Run the method-specific proof check and, on success, persist evidence.

    Idempotent: calling this again on an already-verified challenge returns
    True without re-checking or writing a second Evidence row.

    Returns:
        True if the challenge is (now, or already) verified; False if the
        proof check failed — the challenge stays "pending" so it can be
        retried (e.g. while DNS propagates).
    """
    from app.models.evidence import Evidence  # noqa: PLC0415

    if challenge.status == "verified":
        return True

    if challenge.method == "dns_txt":
        ok = check_dns_txt(
            challenge.domain, challenge.token, dns_client or _default_dns_txt_client()
        )
    elif challenge.method == "html_meta":
        ok = check_html_meta(
            challenge.domain,
            challenge.token,
            http_fetcher or _default_http_page_fetcher(),
        )
    elif challenge.method == "email":
        ok = check_email_token(submitted_token, challenge.token)
    else:  # pragma: no cover — guarded at issue time by UnknownChallengeMethod
        ok = False

    if not ok:
        return False

    evidence = Evidence(
        verification_run_id=challenge.verification_run_id,
        source=EVIDENCE_SOURCE,
        tier=3,
        field=EVIDENCE_FIELD,
        raw_value=challenge.method,
        normalized_value="true",
        confidence=0.6,
        raw_payload={
            "domain": challenge.domain,
            "method": challenge.method,
            "challenge_id": challenge.id,
        },
        attribution={"provider": "ownership", "method": challenge.method},
        fetched_at=datetime.now(tz=timezone.utc),
    )
    db.add(evidence)
    db.flush()  # assigns evidence.id

    challenge.status = "verified"
    challenge.verified_at = datetime.now(tz=timezone.utc)
    challenge.evidence_id = evidence.id
    db.commit()
    return True
