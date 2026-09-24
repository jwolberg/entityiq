"""Person-record parsers for official sanctions lists (IS1-T2, ticket 0030).

Each parser turns one list's native format into ``PersonRecord`` objects
with one shape for every source:

  names          [{"name", "kind", ["script"], ["quality"], ["language"]}]
                 kind: primary | aka | fka | variation | original
  dobs           [{"date": "YYYY-MM-DD"} | {"year", ["month"]} |
                  {"from_year", "to_year"}]  (+ "circa": True when approximate)
  pobs           ["City, Country"]
  nationalities  country names (OFAC, UN, UK) or ISO-2 codes (EU), as published
  documents      [{"type", "number", "country"}]
                 type: passport | national_id | tax_id | <source code>

Only natural persons are kept: entities, vessels and aircraft are skipped.
Formats were checked against the live files on 2026-09-24 (OFAC sdn.csv, UN
consolidated.xml, EU xmlFullSanctionsList_1_1, UK OFSI ConList.csv 2022 format).
"""

from __future__ import annotations

import csv
import io
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.lists.ofac import parse_sdn_csv

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.models.list_snapshot import ListSnapshot
    from app.models.watchlist_record import WatchlistRecord

_MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "jan",
            "feb",
            "mar",
            "apr",
            "may",
            "jun",
            "jul",
            "aug",
            "sep",
            "oct",
            "nov",
            "dec",
        ],
        start=1,
    )
}


@dataclass
class PersonRecord:
    source: str
    source_entry_id: str
    primary_name: str
    names: list[dict] = field(default_factory=list)
    dobs: list[dict] = field(default_factory=list)
    pobs: list[str] = field(default_factory=list)
    nationalities: list[str] = field(default_factory=list)
    documents: list[dict] = field(default_factory=list)
    gender: str | None = None
    program: str | None = None


def _add_unique(items: list, value) -> None:
    if value and value not in items:
        items.append(value)


def _name(name: str, kind: str, **extra) -> dict:
    return {"name": name, "kind": kind, **{k: v for k, v in extra.items() if v}}


# ---------------------------------------------------------------------------
# OFAC sdn.csv
# ---------------------------------------------------------------------------


def _ofac_reorder(name: str) -> str:
    """OFAC writes 'LAST, First Middle'; return 'First Middle LAST'."""
    if "," not in name:
        return name.strip()
    last, _, first = name.partition(",")
    return f"{first.strip()} {last.strip()}".strip()


def _parse_ofac_dob(text: str) -> dict | None:
    text = text.strip()
    circa = text.lower().startswith("circa ")
    if circa:
        text = text[6:].strip()
    m = re.fullmatch(r"(\d{4})\s+to\s+(\d{4})", text)
    if m:
        return {"from_year": int(m.group(1)), "to_year": int(m.group(2))}
    m = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})", text)
    if m and m.group(2).lower() in _MONTHS:
        month = _MONTHS[m.group(2).lower()]
        return {"date": f"{int(m.group(3)):04d}-{month:02d}-{int(m.group(1)):02d}"}
    m = re.fullmatch(r"([A-Za-z]{3})\s+(\d{4})", text)
    if m and m.group(1).lower() in _MONTHS:
        dob: dict = {"year": int(m.group(2)), "month": _MONTHS[m.group(1).lower()]}
        return {**dob, "circa": True} if circa else dob
    m = re.fullmatch(r"(\d{4})", text)
    if m:
        dob = {"year": int(m.group(1))}
        return {**dob, "circa": True} if circa else dob
    return None


def _ofac_document(part: str, prefix: str, doc_type: str) -> dict | None:
    m = re.match(rf"{prefix}\s+(\S+)(?:\s+\(([^)]+)\))?", part)
    if not m:
        return None
    return {"type": doc_type, "number": m.group(1).rstrip(".,"), "country": m.group(2)}


def parse_ofac_individuals(csv_text: str) -> list[PersonRecord]:
    records: list[PersonRecord] = []
    for entry in parse_sdn_csv(csv_text):
        if (entry.get("sdn_type") or "").lower() != "individual":
            continue
        raw = entry["name"]
        rec = PersonRecord(
            source="ofac_sdn",
            source_entry_id=entry["id"],
            primary_name=_ofac_reorder(raw),
            program=entry.get("program"),
        )
        rec.names.append(_name(raw, "primary"))
        _add_unique(rec.names, _name(rec.primary_name, "primary"))

        for part in (entry.get("remarks") or "").split(";"):
            part = part.strip().rstrip(".").strip()
            if not part:
                continue
            lowered = part.lower()
            if lowered.startswith(("dob ", "alt. dob ")):
                dob = _parse_ofac_dob(part.split("DOB", 1)[1])
                _add_unique(rec.dobs, dob)
            elif lowered.startswith("pob "):
                _add_unique(rec.pobs, part[4:].strip())
            elif re.match(r"(alt\. )?(nationality|citizen) ", lowered):
                _add_unique(
                    rec.nationalities,
                    part.split(" ", 2 if lowered.startswith("alt.") else 1)[-1].strip(),
                )
            elif lowered.startswith("gender "):
                rec.gender = part[7:].strip()
            elif lowered.startswith("passport "):
                _add_unique(rec.documents, _ofac_document(part, "Passport", "passport"))
            elif lowered.startswith("national id no. "):
                _add_unique(
                    rec.documents,
                    _ofac_document(part, r"National ID No\.", "national_id"),
                )
            elif lowered.startswith(("a.k.a. ", "f.k.a. ")):
                m = re.match(r"[af]\.k\.a\.\s+'([^']+)'", part)
                if m:
                    kind = "fka" if lowered.startswith("f") else "aka"
                    _add_unique(rec.names, _name(_ofac_reorder(m.group(1)), kind))
        records.append(rec)
    return records


# ---------------------------------------------------------------------------
# UN consolidated.xml
# ---------------------------------------------------------------------------

_UN_DOC_TYPES = {
    "passport": "passport",
    "national identification number": "national_id",
    "national identity card": "national_id",
}


def _text(el: ET.Element | None, tag: str) -> str | None:
    if el is None:
        return None
    child = el.find(tag)
    value = (child.text or "").strip() if child is not None else ""
    return value or None


def parse_un_individuals(xml_text: str) -> list[PersonRecord]:
    root = ET.fromstring(xml_text)
    records: list[PersonRecord] = []
    for ind in root.iter("INDIVIDUAL"):
        parts = [
            _text(ind, t)
            for t in ("FIRST_NAME", "SECOND_NAME", "THIRD_NAME", "FOURTH_NAME")
        ]
        primary = " ".join(p for p in parts if p)
        rec = PersonRecord(
            source="un_consolidated",
            source_entry_id=_text(ind, "DATAID") or "",
            primary_name=primary,
            gender=_text(ind, "GENDER"),
            program=_text(ind, "UN_LIST_TYPE"),
        )
        rec.names.append(_name(primary, "primary"))
        original = _text(ind, "NAME_ORIGINAL_SCRIPT")
        if original:
            rec.names.append(_name(original, "original", script="original"))
        for alias in ind.findall("INDIVIDUAL_ALIAS"):
            alias_name = _text(alias, "ALIAS_NAME")
            if alias_name:
                quality = (_text(alias, "QUALITY") or "").lower()
                _add_unique(rec.names, _name(alias_name, "aka", quality=quality))
        for dob in ind.findall("INDIVIDUAL_DATE_OF_BIRTH"):
            kind = (_text(dob, "TYPE_OF_DATE") or "").upper()
            if kind == "BETWEEN":
                lo, hi = _text(dob, "FROM_YEAR"), _text(dob, "TO_YEAR")
                if lo and hi:
                    _add_unique(rec.dobs, {"from_year": int(lo), "to_year": int(hi)})
                continue
            date, year = _text(dob, "DATE"), _text(dob, "YEAR")
            value = (
                {"date": date[:10]} if date else ({"year": int(year)} if year else None)
            )
            if value and kind == "APPROXIMATELY":
                value["circa"] = True
            _add_unique(rec.dobs, value)
        for pob in ind.findall("INDIVIDUAL_PLACE_OF_BIRTH"):
            place = ", ".join(
                p for p in (_text(pob, "CITY"), _text(pob, "COUNTRY")) if p
            )
            _add_unique(rec.pobs, place)
        for nat in ind.findall("NATIONALITY"):
            for value in nat.findall("VALUE"):
                _add_unique(rec.nationalities, (value.text or "").strip())
        for doc in ind.findall("INDIVIDUAL_DOCUMENT"):
            number = _text(doc, "NUMBER")
            if not number:
                continue
            raw_type = (_text(doc, "TYPE_OF_DOCUMENT") or "other").lower()
            _add_unique(
                rec.documents,
                {
                    "type": _UN_DOC_TYPES.get(raw_type, raw_type.replace(" ", "_")),
                    "number": number,
                    "country": _text(doc, "ISSUING_COUNTRY")
                    or _text(doc, "COUNTRY_OF_ISSUE"),
                },
            )
        records.append(rec)
    return records


# ---------------------------------------------------------------------------
# EU xmlFullSanctionsList_1_1
# ---------------------------------------------------------------------------

_EU_NS = {"e": "http://eu.europa.ec/fpi/fsd/export"}
_EU_DOC_TYPES = {
    "passport": "passport",
    "id": "national_id",
    "fiscalcode": "tax_id",
    "taxid": "tax_id",
}


def _eu_country(code: str | None) -> str | None:
    return code if code and code != "00" else None


def parse_eu_persons(xml_text: str) -> list[PersonRecord]:
    root = ET.fromstring(xml_text)
    records: list[PersonRecord] = []
    for ent in root.findall("e:sanctionEntity", _EU_NS):
        subject = ent.find("e:subjectType", _EU_NS)
        if subject is None or subject.get("code") != "person":
            continue
        aliases = [a for a in ent.findall("e:nameAlias", _EU_NS) if a.get("wholeName")]
        if not aliases:
            continue
        programme = next(
            (
                r.get("programme")
                for r in ent.findall("e:regulation", _EU_NS)
                if r.get("programme")
            ),
            None,
        )
        rec = PersonRecord(
            source="eu_fsf",
            source_entry_id=ent.get("logicalId") or "",
            primary_name=aliases[0].get("wholeName"),
            gender=aliases[0].get("gender") or None,
            program=programme,
        )
        for i, alias in enumerate(aliases):
            _add_unique(
                rec.names,
                _name(
                    alias.get("wholeName"),
                    "primary" if i == 0 else "aka",
                    language=alias.get("nameLanguage") or None,
                    quality="low" if alias.get("strong") == "false" else None,
                ),
            )
        for bd in ent.findall("e:birthdate", _EU_NS):
            circa = bd.get("circa") == "true"
            if bd.get("birthdate"):
                value: dict | None = {"date": bd.get("birthdate")[:10]}
            elif bd.get("yearRangeFrom") and bd.get("yearRangeTo"):
                value = {
                    "from_year": int(bd.get("yearRangeFrom")),
                    "to_year": int(bd.get("yearRangeTo")),
                }
            elif bd.get("year"):
                value = {"year": int(bd.get("year"))}
                if bd.get("monthOfYear"):
                    value["month"] = int(bd.get("monthOfYear"))
            else:
                value = None
            if value is not None and circa:
                value["circa"] = True
            _add_unique(rec.dobs, value)
            place = ", ".join(
                p for p in (bd.get("city"), _eu_country(bd.get("countryIso2Code"))) if p
            )
            _add_unique(rec.pobs, place)
        for cit in ent.findall("e:citizenship", _EU_NS):
            _add_unique(rec.nationalities, _eu_country(cit.get("countryIso2Code")))
        for ident in ent.findall("e:identification", _EU_NS):
            if not ident.get("number"):
                continue
            code = (ident.get("identificationTypeCode") or "other").lower()
            _add_unique(
                rec.documents,
                {
                    "type": _EU_DOC_TYPES.get(code, code),
                    "number": ident.get("number"),
                    "country": _eu_country(ident.get("countryIso2Code")),
                },
            )
        records.append(rec)
    return records


# ---------------------------------------------------------------------------
# UK OFSI ConList.csv (2022 format)
# ---------------------------------------------------------------------------

_UK_ALIAS_KIND = {
    "primary name": "primary",
    "primary name variation": "variation",
    "aka": "aka",
    "fka": "fka",
}


def _uk_numbered(value: str | None) -> list[str]:
    """'(1) A (2) B' -> ['A', 'B']; plain values -> [value]."""
    value = (value or "").strip()
    if not value:
        return []
    if not value.startswith("("):
        return [value]
    return [p.strip() for p in re.split(r"\(\d+\)", value) if p.strip()]


def _uk_dob(value: str) -> dict | None:
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", value.strip())
    if not m:
        return None
    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if day and month:
        return {"date": f"{year:04d}-{month:02d}-{day:02d}"}
    if month:
        return {"year": year, "month": month}
    return {"year": year}


def parse_uk_individuals(csv_text: str) -> list[PersonRecord]:
    lines = csv_text.lstrip("﻿").splitlines()
    if lines and lines[0].startswith("Last Updated"):
        lines = lines[1:]
    reader = csv.DictReader(io.StringIO("\n".join(lines)))

    groups: dict[str, list[dict]] = {}
    for row in reader:
        if (row.get("Group Type") or "").strip() != "Individual":
            continue
        groups.setdefault(row["Group ID"].strip(), []).append(row)

    records: list[PersonRecord] = []
    for group_id, rows in groups.items():
        rows.sort(key=lambda r: 0 if r.get("Alias Type") == "Primary name" else 1)
        rec: PersonRecord | None = None
        for row in rows:
            given = [row.get(f"Name {i}", "").strip() for i in range(1, 6)]
            full = " ".join(
                [*(g for g in given if g), row.get("Name 6", "").strip()]
            ).strip()
            kind = _UK_ALIAS_KIND.get((row.get("Alias Type") or "").lower(), "aka")
            if rec is None:
                rec = PersonRecord(
                    source="uk_ofsi",
                    source_entry_id=group_id,
                    primary_name=full,
                    program=(row.get("Regime") or "").strip() or None,
                )
            quality = (row.get("Alias Quality") or "").strip().lower() or None
            _add_unique(rec.names, _name(full, kind, quality=quality))
            original = (row.get("Name Non-Latin Script") or "").strip()
            if original:
                _add_unique(rec.names, _name(original, "original", script="original"))
            for dob in _uk_numbered(row.get("DOB")):
                _add_unique(rec.dobs, _uk_dob(dob))
            place = ", ".join(
                p
                for p in (
                    (row.get("Town of Birth") or "").strip(),
                    (row.get("Country of Birth") or "").strip(),
                )
                if p
            )
            _add_unique(rec.pobs, place)
            for nat in _uk_numbered(row.get("Nationality")):
                _add_unique(rec.nationalities, nat)
            for number in _uk_numbered(row.get("Passport Number")):
                _add_unique(
                    rec.documents,
                    {"type": "passport", "number": number, "country": None},
                )
            for number in _uk_numbered(row.get("National Identification Number")):
                _add_unique(
                    rec.documents,
                    {"type": "national_id", "number": number, "country": None},
                )
        if rec is not None:
            records.append(rec)
    return records


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def store_records(
    db: "Session", snapshot: "ListSnapshot", records: list[PersonRecord]
) -> list["WatchlistRecord"]:
    """Persist parsed records against the snapshot they came from."""
    from app.models.watchlist_record import WatchlistRecord  # noqa: PLC0415

    rows = [
        WatchlistRecord(
            snapshot_id=snapshot.id,
            source=r.source,
            source_entry_id=r.source_entry_id,
            primary_name=r.primary_name,
            names=r.names,
            dobs=r.dobs,
            pobs=r.pobs,
            nationalities=r.nationalities,
            documents=r.documents,
            gender=r.gender,
            program=r.program,
        )
        for r in records
    ]
    db.add_all(rows)
    db.commit()
    return rows
