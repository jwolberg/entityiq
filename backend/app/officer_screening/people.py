"""Officers and owners behind a company (tickets 0080, 0081).

``collect_people`` turns the submitter's declared people and the registry's
officers/owners into one de-duplicated list of typed person records, each
citing every source that named them. ``screen_people`` (ticket 0081) screens
each one through individual screening.

Registry people come from a ``RegistryPeopleProvider``. Live providers
(OpenCorporates officers, UK Companies House officers + PSC) need credentials
this build doesn't have, so they are deferred (ticket 0086). The production
default is ``UnconfiguredRegistryPeopleProvider``: the registry part reports
*unavailable* (a coverage gap, never a penalty) while declared people still
flow through. ``StubRegistryPeopleProvider`` is deterministic and backs tests
and the demo (ENTITYIQ_REGISTRY_PEOPLE_PROVIDER=stub).

Person records live only in the pipeline context. Names are persisted solely
in encrypted screening subjects (ADR-0004), never in KYB tables.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Protocol

from app.screening.names import tokens

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Attributes a person record carries besides name/relationships/roles/sources.
_ATTRIBUTES = ("dob", "nationality", "ownership_pct")


class ProviderNotConfigured(Exception):
    """No live registry-people provider has been selected (ticket 0086)."""


class RegistryPeopleProvider(Protocol):
    """Looks up a company's officers and owners.

    Returns a list of dicts: name, relationship ("officer" | "owner"), and
    optionally role, dob, nationality, ownership_pct, locator. Raises
    ProviderNotConfigured, or anything else on failure.
    """

    name: str

    def lookup(
        self, company_name: str | None, country_iso: str | None
    ) -> list[dict]: ...


class UnconfiguredRegistryPeopleProvider:
    """Production default until live registry sources are wired (0086)."""

    name = "unconfigured"

    def lookup(self, company_name: str | None, country_iso: str | None) -> list[dict]:
        raise ProviderNotConfigured("No registry people provider configured")


class StubRegistryPeopleProvider:
    """Deterministic provider keyed by normalized company name."""

    name = "stub"

    def __init__(self, records: dict[str, list[dict]] | None = None) -> None:
        self._records = {person_key(k): v for k, v in (records or {}).items()}

    def lookup(self, company_name: str | None, country_iso: str | None) -> list[dict]:
        return [dict(p) for p in self._records.get(person_key(company_name), [])]


def registry_people_provider_from_env() -> RegistryPeopleProvider:
    """Select the provider from ENTITYIQ_REGISTRY_PEOPLE_PROVIDER (default: none)."""
    choice = (os.environ.get("ENTITYIQ_REGISTRY_PEOPLE_PROVIDER") or "").strip().lower()
    if choice in ("", "none", "unconfigured"):
        return UnconfiguredRegistryPeopleProvider()
    if choice == "stub":
        return StubRegistryPeopleProvider()
    raise ValueError(f"Unknown ENTITYIQ_REGISTRY_PEOPLE_PROVIDER: {choice!r}")


def person_key(name: str | None) -> str:
    """Normalized name (screening's tokenizer: ASCII-folded, particles dropped)."""
    return " ".join(tokens(name))


def dob_compatible(a: str | None, b: str | None) -> bool:
    """Missing, or one a prefix of the other ("1968" vs "1968-02-14")."""
    if not a or not b:
        return True
    return a.startswith(b) or b.startswith(a)


def _record(raw: dict, source: dict) -> dict:
    person = {
        "name": " ".join(str(raw["name"]).split()),
        "relationships": [raw["relationship"]],
        "roles": [raw["role"]] if raw.get("role") else [],
        "sources": [source],
    }
    for attr in _ATTRIBUTES:
        person[attr] = raw.get(attr)
    return person


def _merge(people: list[dict], incoming: dict) -> None:
    """Fold ``incoming`` into a matching person, or append it.

    Same normalized name and compatible dates of birth → same person. The
    first-seen spelling and values win (declared people are added first);
    relationships, roles and sources accumulate.
    """
    key = person_key(incoming["name"])
    for person in people:
        if person_key(person["name"]) != key:
            continue
        if not dob_compatible(person["dob"], incoming["dob"]):
            continue
        for rel in incoming["relationships"]:
            if rel not in person["relationships"]:
                person["relationships"] = sorted([*person["relationships"], rel])
        for role in incoming["roles"]:
            if role not in person["roles"]:
                person["roles"].append(role)
        person["sources"].extend(incoming["sources"])
        for attr in _ATTRIBUTES:
            if person[attr] is None:
                person[attr] = incoming[attr]
        return
    people.append(incoming)


class CollectPeopleStage:
    """Pipeline stage: gather officers and owners (ticket 0080).

    Context out: ``people = {"status", "people": [record, ...]}``. Status is
    "unavailable" when the registry couldn't be asked; declared people are
    returned either way.
    """

    name = "collect_people"

    def __init__(self, provider: RegistryPeopleProvider | None = None) -> None:
        self._provider = (
            provider if provider is not None else registry_people_provider_from_env()
        )

    def run(self, run_id: str, db: "Session", context: dict) -> dict:
        from app.models.submission import Submission  # noqa: PLC0415
        from app.models.verification_run import VerificationRun  # noqa: PLC0415

        people: list[dict] = []
        run = db.get(VerificationRun, run_id)
        submission = db.get(Submission, run.submission_id) if run else None
        for raw in (submission.declared_people if submission else None) or []:
            _merge(
                people, _record(raw, {"source": "declared", "locator": "submission"})
            )

        normalized = context.get("normalized", {})
        status = "complete"
        try:
            found = self._provider.lookup(
                normalized.get("company_name"), normalized.get("country_iso")
            )
        except ProviderNotConfigured:
            status, found = "unavailable", []
        except Exception as exc:
            logger.warning("Registry people provider error: %s", exc)
            status, found = "unavailable", []

        for raw in found:
            source = {"source": "registry", "provider": self._provider.name}
            if raw.get("locator"):
                source["locator"] = raw["locator"]
            _merge(people, _record(raw, source))

        return {**context, "people": {"status": status, "people": people}}
