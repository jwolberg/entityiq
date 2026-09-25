"""Individual screening demo data (IS2-T9, ticket 0044).

A small, fictional watchlist under a clearly non-official source name, plus
fictional subjects that land in every disposition, screened through the
real pipeline. No network. Idempotent: re-running adds nothing.

    python -m app.screening.demo_data     # needs ENTITYIQ_SCREENING_MASTER_KEY

Refuses non-SQLite databases unless ENTITYIQ_ALLOW_DEMO_SEED=1, like the
business-verification demo seed.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.lists.persons import PersonRecord, store_records
from app.lists.snapshot import record_snapshot
from app.models.list_snapshot import ListSnapshot
from app.screening import crypto
from app.screening.models import ScreeningRun, ScreeningSubject
from app.screening.pipeline import run_screening

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

DEMO_SOURCE = "demo_watchlist"

# Fictional listed people (not real sanctioned persons).
DEMO_WATCHLIST: list[dict] = [
    {
        "name": "Teodor Vasilescu",
        "dobs": [{"date": "1962-08-30"}],
        "nat": ["RO"],
        "program": "DEMO-1",
    },
    {"name": "Helena Lindqvistad", "dobs": [], "nat": [], "program": "DEMO-2"},
    {
        "name": "Rafael Quintanero",
        "dobs": [{"year": 1975, "circa": True}],
        "nat": ["ES"],
        "program": "DEMO-1",
    },
]

# Fictional subjects, one per outcome.
DEMO_SUBJECTS: list[dict] = [
    # MATCH: name, full DOB and nationality agree.
    {"name": "Teodor Vasilescu", "dob": "1962-08-30", "nationality": "Romania"},
    # REVIEW: name only on both sides; absence of data is not "no risk".
    {"name": "Helena Lindqvistad"},
    # REVIEW: name matches but the DOB conflicts; a conflict can't clear alone.
    {"name": "Rafael Quintanero", "dob": "1994-02-02"},
    # CLEAR (auto): nobody on the list resembles this subject.
    {"name": "Chidi Okafor", "dob": "1988-11-05", "nationality": "Nigeria"},
]


class UnsafeDemoTarget(RuntimeError):
    """Refuse to write demo data into a non-local database."""


def check_demo_target(database_url: str) -> None:
    if database_url.startswith("sqlite"):
        return
    if os.environ.get("ENTITYIQ_ALLOW_DEMO_SEED") == "1":
        return
    raise UnsafeDemoTarget(
        "Refusing to load screening demo data into a non-SQLite database. "
        "Set ENTITYIQ_ALLOW_DEMO_SEED=1 if this really is a throwaway DB."
    )


def ensure_watchlist(db: "Session") -> None:
    if db.query(ListSnapshot).filter_by(source=DEMO_SOURCE).count():
        return
    content = json.dumps(DEMO_WATCHLIST, sort_keys=True)
    snapshot = record_snapshot(
        db, source=DEMO_SOURCE, content=content, record_count=len(DEMO_WATCHLIST)
    )
    snapshot.retrieved_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
    store_records(
        db,
        snapshot,
        [
            PersonRecord(
                source=DEMO_SOURCE,
                source_entry_id=f"DEMO-{i + 1:03d}",
                primary_name=p["name"],
                names=[{"name": p["name"], "kind": "primary"}],
                dobs=p["dobs"],
                nationalities=p["nat"],
                program=p["program"],
            )
            for i, p in enumerate(DEMO_WATCHLIST)
        ],
    )


def _already_screened(db: "Session", name: str) -> bool:
    # Walk-in subjects only: a company's officers (kyb_entity_id) are the
    # business demo's, even when they share a name (ticket 0085).
    for subject in db.query(ScreeningSubject).filter(
        ScreeningSubject.kyb_entity_id.is_(None)
    ):
        try:
            if crypto.get_subject_pii(db, subject).get("name") == name:
                return True
        except (crypto.SubjectShredded, crypto.DecryptionError):
            continue
    return False


def load_screening_demo(db: "Session") -> int:
    check_demo_target(str(db.get_bind().url))
    ensure_watchlist(db)
    added = 0
    for pii in DEMO_SUBJECTS:
        if _already_screened(db, pii["name"]):
            continue
        subject = ScreeningSubject()
        db.add(subject)
        db.flush()
        crypto.set_subject_pii(db, subject, pii)
        run = ScreeningRun(subject_id=subject.id, trigger="intake")
        db.add(run)
        db.commit()
        run_screening(run.id, db)
        added += 1
    return added


def main() -> None:
    from app.db.session import SessionLocal  # noqa: PLC0415

    db = SessionLocal()
    try:
        added = load_screening_demo(db)
    finally:
        db.close()
    print(f"Screening demo: {added} individual(s) screened.")


if __name__ == "__main__":
    main()
