"""OFAC SDN list: fetch, parse, snapshot (moved from adapters/sanctions, IS1-T1).

Source: https://www.treasury.gov/ofac/downloads/sdn.csv (no header row).

Columns (1-indexed): 1 Ent_Num, 2 SDN_Name, 3 SDN_Type, 4 Program, 5 Title,
6 Call_Sign, 7 Vess_type, 8 Tonnage, 9 GRT, 10 Vess_flag, 11 Vess_owner,
12 Remarks. OFAC writes "-0- " for empty fields.
"""

from __future__ import annotations

import csv
import io
from typing import TYPE_CHECKING, Protocol

from app.lists.snapshot import record_snapshot

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.models.list_snapshot import ListSnapshot

OFAC_SDN_URL = "https://www.treasury.gov/ofac/downloads/sdn.csv"
SOURCE = "ofac_sdn"


class SdnListFetcher(Protocol):
    """Anything that can return the raw SDN CSV text."""

    def fetch_csv(self) -> str:
        """Return the raw CSV text. Raises RuntimeError if unavailable."""
        ...


def default_fetcher() -> SdnListFetcher:
    """Production fetcher: download sdn.csv from OFAC with httpx."""
    try:
        import httpx  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError("httpx is required for the production SDN fetcher") from exc

    class _OFACFetcher:
        def fetch_csv(self) -> str:
            try:
                response = httpx.get(OFAC_SDN_URL, timeout=30.0, follow_redirects=True)
                if response.status_code != 200:
                    raise RuntimeError(
                        f"OFAC SDN fetch returned HTTP {response.status_code}"
                    )
                return response.text
            except Exception as exc:
                raise RuntimeError(f"Could not fetch OFAC SDN list: {exc}") from exc

    return _OFACFetcher()


def _field(row: list[str], index: int) -> str | None:
    if len(row) <= index:
        return None
    value = row[index].strip()
    return None if value in ("", "-0-") else value


def parse_sdn_csv(csv_text: str) -> list[dict[str, str | None]]:
    """Parse sdn.csv into dicts with id, name, sdn_type, program, remarks.

    ``id`` and ``name`` are always present; the other keys are None when OFAC
    left the field empty ("-0- ").
    """
    entries: list[dict[str, str | None]] = []
    for row in csv.reader(io.StringIO(csv_text)):
        if len(row) < 2:
            continue
        ent_num = row[0].strip()
        sdn_name = row[1].strip()
        if not sdn_name or sdn_name.upper() == "SDN_NAME":
            continue
        entries.append(
            {
                "id": ent_num,
                "name": sdn_name,
                "sdn_type": _field(row, 2),
                "program": _field(row, 3),
                "remarks": _field(row, 11),
            }
        )
    return entries


def load(
    db: "Session", fetcher: SdnListFetcher | None = None
) -> tuple["ListSnapshot", list[dict[str, str | None]]]:
    """Fetch, parse and snapshot the SDN list in one step."""
    text = (fetcher or default_fetcher()).fetch_csv()
    entries = parse_sdn_csv(text)
    snapshot = record_snapshot(
        db, source=SOURCE, content=text, record_count=len(entries)
    )
    return snapshot, entries
