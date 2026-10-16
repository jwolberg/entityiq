"""Guard the public-records fixtures (ticket 0090).

The fixtures copy the *shape* of live CourtListener v4 and SAM exclusions
responses (verified 2026-10-16, docs/implementation-notes.md) but must contain
only fictional parties and no credentials. Later adapter tests (ticket 0092)
rely on these shapes, so a drifted or leaked fixture fails here first.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "public_records"

# Fictional organisations used across the demo data. Every party name and case
# name in a fixture must contain one of these.
FICTIONAL = (
    "Contoso",
    "Fabrikam",
    "Northwind",
    "Tailwind",
    "Wingtip",
    "Adventure Works",
    "Litware",
    "Proseware",
    "Example",
)

# Field sets observed on live responses. Fixtures may not drop any of these.
SEARCH_RESULT_FIELDS = {
    "docket_id",
    "caseName",
    "court_id",
    "court",
    "docketNumber",
    "dateFiled",
    "dateTerminated",
    "chapter",
    "suitNature",
    "party",
}
DOCKET_FIELDS = {
    "id",
    "case_name",
    "court_id",
    "docket_number",
    "date_filed",
    "date_terminated",
    "date_last_filing",
    "date_modified",
}
# Real header of SAM_Exclusions_Public_Extract_V2_26289.CSV, downloaded
# 2026-10-16 (ticket 0090). It differs from the 2017 published layout: 31
# columns, "Open Data Flag" and "Blank (Deprecated)" sit before "Unique Entity
# ID", and the last column is spelled "Creation_Date". Parse by header name.
SAM_COLUMNS = [
    "Classification",
    "Name",
    "Prefix",
    "First",
    "Middle",
    "Last",
    "Suffix",
    "Address 1",
    "Address 2",
    "Address 3",
    "Address 4",
    "City",
    "State / Province",
    "Country",
    "Zip Code",
    "Open Data Flag",
    "Blank (Deprecated)",
    "Unique Entity ID",
    "Exclusion Program",
    "Excluding Agency",
    "CT Code",
    "Exclusion Type",
    "Additional Comments",
    "Active Date",
    "Termination Date",
    "Record Status",
    "Cross-Reference",
    "SAM Number",
    "CAGE",
    "NPI",
    "Creation_Date",
]

_TOKEN_LIKE = re.compile(r"\b[0-9a-f]{40}\b")


def _load(name: str):
    return json.loads((FIXTURES / name).read_text())


def _is_fictional(text: str) -> bool:
    return any(marker.lower() in text.lower() for marker in FICTIONAL)


@pytest.mark.parametrize(
    "name",
    ["search_bankruptcy.json", "search_civil.json", "search_empty.json"],
)
def test_search_fixtures_keep_live_shape(name):
    payload = _load(name)
    assert {"count", "next", "previous", "results"} <= payload.keys()
    assert payload["count"] >= len(payload["results"])
    for result in payload["results"]:
        missing = SEARCH_RESULT_FIELDS - result.keys()
        assert not missing, f"{name} result missing {missing}"
        assert isinstance(result["party"], list)


def test_bankruptcy_search_has_debtor_and_counsel_only_case():
    """The debtor is the caseName; counsel only appears in `party`.

    Covers the U1 finding that `party` matches every role, so the adapter
    must identify debtors by caseName (ticket 0092).
    """
    results = _load("search_bankruptcy.json")["results"]
    debtor_cases = [r for r in results if "Contoso" in r["caseName"]]
    counsel_only = [
        r
        for r in results
        if "Contoso" not in r["caseName"] and any("Contoso" in p for p in r["party"])
    ]
    assert debtor_cases and counsel_only
    assert all(r["chapter"] for r in results)
    assert all(r["court_id"].endswith("b") for r in results)


def test_civil_search_reports_true_count_above_page():
    payload = _load("search_civil.json")
    assert payload["count"] > len(payload["results"])
    assert all(r["chapter"] in (None, "") for r in payload["results"])


def test_docket_detail_carries_freshness_fields():
    for name in ("docket_fresh.json", "docket_stale.json"):
        missing = DOCKET_FIELDS - _load(name).keys()
        assert not missing, f"{name} missing {missing}"
    assert (
        _load("docket_fresh.json")["date_last_filing"]
        > _load("docket_stale.json")["date_last_filing"]
    )


def test_sam_extract_header_and_rows():
    with (FIXTURES / "sam_exclusions_extract.csv").open(newline="") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == SAM_COLUMNS
        rows = list(reader)
    classes = {row["Classification"] for row in rows}
    assert {"Firm", "Individual"} <= classes
    assert any(row["Termination Date"] == "Indefinite" for row in rows)
    for row in rows:
        if row["Classification"] == "Individual":
            assert row["Name"] == "" and row["Last"]


def test_fixtures_are_fictional_and_credential_free():
    files = sorted(FIXTURES.iterdir())
    assert files, "no public-records fixtures"
    for path in files:
        text = path.read_text()
        assert "Authorization" not in text, path.name
        assert not _TOKEN_LIKE.search(text), f"token-like string in {path.name}"
    for name in ("search_bankruptcy.json", "search_civil.json"):
        for result in _load(name)["results"]:
            assert _is_fictional(result["caseName"]), result["caseName"]
            for party in result["party"]:
                assert _is_fictional(party), f"{name}: {party!r}"
    with (FIXTURES / "sam_exclusions_extract.csv").open(newline="") as fh:
        for row in csv.DictReader(fh):
            who = row["Name"] or f"{row['First']} {row['Last']}"
            assert _is_fictional(who) or row["Last"] == "Example", who
