"""Person-record parsers for OFAC, UN, EU and UK lists (IS1-T2, ticket 0030).

Fixtures are synthetic, fictional entries written in each list's real format
(checked against the live files 2026-09-24). Tests make no network calls.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.db.session import Base
from app.lists.persons import (
    PersonRecord,
    parse_eu_persons,
    parse_ofac_individuals,
    parse_uk_individuals,
    parse_un_individuals,
    store_records,
)
from app.lists.snapshot import record_snapshot
from app.models.watchlist_record import WatchlistRecord

# --- OFAC sdn.csv (no header; "-0- " = empty) --------------------------------
OFAC_CSV = (
    '90001,"VOLKOV, Pyotr Andreyevich","individual","RUSSIA-EO14024",-0- ,-0- ,-0- ,'
    '-0- ,-0- ,-0- ,-0- ,"DOB 14 Mar 1971; alt. DOB 1972; POB Tver, Russia; '
    "nationality Russia; Gender Male; Passport 7512345678 (Russia); "
    "National ID No. 1234567890 (Russia); a.k.a. 'VOLKOFF, Peter'.\"\n"
    '90002,"HALDANE, Mira","individual","SDGT",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,'
    '"DOB circa 1980; citizen Utopia; alt. nationality Freedonia."\n'
    '90003,"DOE, Jonah","individual","SDGT",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,'
    '"DOB 1958 to 1962."\n'
    '90004,"FICTIONAL SHIPPING LTD","-0- ","SDGT",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,'
    "-0- \n"
    '90005,"EXAMPLE VESSEL","vessel","IRAN",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- \n'
)

# --- UN consolidated.xml ------------------------------------------------------
UN_XML = """<?xml version="1.0" encoding="UTF-8"?>
<CONSOLIDATED_LIST>
  <INDIVIDUALS>
    <INDIVIDUAL>
      <DATAID>8800001</DATAID>
      <FIRST_NAME>ANNA</FIRST_NAME>
      <SECOND_NAME>PETROVNA</SECOND_NAME>
      <THIRD_NAME>SIDOROVA</THIRD_NAME>
      <UN_LIST_TYPE>DPRK</UN_LIST_TYPE>
      <REFERENCE_NUMBER>KPi.999</REFERENCE_NUMBER>
      <NAME_ORIGINAL_SCRIPT>Анна Петровна Сидорова</NAME_ORIGINAL_SCRIPT>
      <GENDER>Female</GENDER>
      <NATIONALITY><VALUE>Utopia</VALUE></NATIONALITY>
      <INDIVIDUAL_ALIAS><QUALITY>Good</QUALITY><ALIAS_NAME>Anya Sidorova</ALIAS_NAME></INDIVIDUAL_ALIAS>
      <INDIVIDUAL_ALIAS><QUALITY>Low</QUALITY><ALIAS_NAME>The Accountant</ALIAS_NAME></INDIVIDUAL_ALIAS>
      <INDIVIDUAL_DATE_OF_BIRTH><TYPE_OF_DATE>EXACT</TYPE_OF_DATE><DATE>1975-06-02</DATE></INDIVIDUAL_DATE_OF_BIRTH>
      <INDIVIDUAL_DATE_OF_BIRTH><TYPE_OF_DATE>BETWEEN</TYPE_OF_DATE><FROM_YEAR>1974</FROM_YEAR><TO_YEAR>1976</TO_YEAR></INDIVIDUAL_DATE_OF_BIRTH>
      <INDIVIDUAL_PLACE_OF_BIRTH><CITY>Metropolis</CITY><COUNTRY>Utopia</COUNTRY></INDIVIDUAL_PLACE_OF_BIRTH>
      <INDIVIDUAL_DOCUMENT><TYPE_OF_DOCUMENT>Passport</TYPE_OF_DOCUMENT><NUMBER>P0000001</NUMBER><ISSUING_COUNTRY>Utopia</ISSUING_COUNTRY></INDIVIDUAL_DOCUMENT>
    </INDIVIDUAL>
    <INDIVIDUAL>
      <DATAID>8800002</DATAID>
      <FIRST_NAME>KARIM</FIRST_NAME>
      <UN_LIST_TYPE>Al-Qaida</UN_LIST_TYPE>
      <REFERENCE_NUMBER>QDi.999</REFERENCE_NUMBER>
      <INDIVIDUAL_ALIAS><QUALITY/><ALIAS_NAME/></INDIVIDUAL_ALIAS>
      <INDIVIDUAL_DATE_OF_BIRTH><TYPE_OF_DATE>APPROXIMATELY</TYPE_OF_DATE><YEAR>1968</YEAR></INDIVIDUAL_DATE_OF_BIRTH>
      <INDIVIDUAL_PLACE_OF_BIRTH><COUNTRY/></INDIVIDUAL_PLACE_OF_BIRTH>
      <INDIVIDUAL_DOCUMENT/>
    </INDIVIDUAL>
  </INDIVIDUALS>
  <ENTITIES><ENTITY><DATAID>1</DATAID><FIRST_NAME>SOME FRONT CO</FIRST_NAME></ENTITY></ENTITIES>
</CONSOLIDATED_LIST>
"""

# --- EU xmlFullSanctionsList_1_1 ---------------------------------------------
EU_XML = """<?xml version="1.0" encoding="UTF-8"?>
<export xmlns="http://eu.europa.ec/fpi/fsd/export">
  <sanctionEntity euReferenceNumber="EU.9.99" logicalId="7001">
    <regulation programme="UTP"/>
    <subjectType code="person" classificationCode="P"/>
    <nameAlias wholeName="Lukas Brenner" firstName="Lukas" lastName="Brenner" gender="M" nameLanguage="" strong="true"/>
    <nameAlias wholeName="Лукас Бреннер" nameLanguage="RU" strong="true"/>
    <nameAlias wholeName="Luke Brenner" strong="false"/>
    <birthdate birthdate="1966-11-30" year="1966" monthOfYear="11" dayOfMonth="30" city="Oldtown" countryIso2Code="UT" circa="false"/>
    <birthdate year="1967" circa="true" countryIso2Code="00"/>
    <citizenship countryIso2Code="UT" countryDescription="UTOPIA"/>
    <identification identificationTypeCode="passport" number="UT1234567" countryIso2Code="UT"/>
    <identification identificationTypeCode="id" number="987-65-4321" countryIso2Code="UT"/>
  </sanctionEntity>
  <sanctionEntity euReferenceNumber="EU.9.98" logicalId="7002">
    <subjectType code="enterprise" classificationCode="E"/>
    <nameAlias wholeName="Fictional Holdings SA" strong="true"/>
  </sanctionEntity>
</export>
"""

# --- UK OFSI ConList.csv (2022 format) ---------------------------------------
_UK_HEADER = (
    "Name 6,Name 1,Name 2,Name 3,Name 4,Name 5,Title,Name Non-Latin Script,"
    "Non-Latin Script Type,Non-Latin Script Language,DOB,Town of Birth,"
    "Country of Birth,Nationality,Passport Number,Passport Details,"
    "National Identification Number,National Identification Details,Position,"
    "Address 1,Address 2,Address 3,Address 4,Address 5,Address 6,Post/Zip Code,"
    "Country,Other Information,Group Type,Alias Type,Alias Quality,Regime,"
    "Listed On,UK Sanctions List Date Designated,Last Updated,Group ID"
)


def _uk_row(**cols) -> str:
    names = _UK_HEADER.split(",")
    return ",".join(f'"{cols.get(n, "")}"' for n in names)


UK_CSV = "\n".join(
    [
        "Last Updated,03/06/2026",
        _UK_HEADER,
        _uk_row(
            **{
                "Name 6": "QUILL",
                "Name 1": "Tobias",
                "Name 2": "Ezra",
                "Name Non-Latin Script": "Тобиас Квилл",
                "DOB": "00/00/1959",
                "Town of Birth": "Harbourview",
                "Country of Birth": "Utopia",
                "Nationality": "(1) Utopia (2) Freedonia",
                "Passport Number": "(1) 55501 (2) 55502",
                "Group Type": "Individual",
                "Alias Type": "Primary name",
                "Regime": "Utopia",
                "Group ID": "99001",
            }
        ),
        _uk_row(
            **{
                "Name 6": "QUILLE",
                "Name 1": "Toby",
                "Group Type": "Individual",
                "Alias Type": "AKA",
                "Alias Quality": "Good",
                "Group ID": "99001",
            }
        ),
        _uk_row(
            **{
                "Name 6": "FICTIONAL TRADING LTD",
                "Group Type": "Entity",
                "Alias Type": "Primary name",
                "Group ID": "99002",
            }
        ),
    ]
)


def _names(record: PersonRecord) -> set[str]:
    return {n["name"] for n in record.names}


# ---------------------------------------------------------------------------
# OFAC
# ---------------------------------------------------------------------------


def test_ofac_keeps_individuals_only_with_attributes_from_remarks():
    records = parse_ofac_individuals(OFAC_CSV)
    by_id = {r.source_entry_id: r for r in records}

    assert set(by_id) == {"90001", "90002", "90003"}
    volkov = by_id["90001"]
    assert volkov.primary_name == "Pyotr Andreyevich VOLKOV"
    assert {"VOLKOV, Pyotr Andreyevich", "Peter VOLKOFF"} <= _names(volkov)
    assert {"date": "1971-03-14"} in volkov.dobs
    assert {"year": 1972} in volkov.dobs
    assert volkov.pobs == ["Tver, Russia"]
    assert volkov.nationalities == ["Russia"]
    assert volkov.gender == "Male"
    assert {
        "type": "passport",
        "number": "7512345678",
        "country": "Russia",
    } in volkov.documents
    assert {
        "type": "national_id",
        "number": "1234567890",
        "country": "Russia",
    } in volkov.documents
    assert volkov.program == "RUSSIA-EO14024"


def test_ofac_circa_citizen_and_year_range():
    by_id = {r.source_entry_id: r for r in parse_ofac_individuals(OFAC_CSV)}
    assert by_id["90002"].dobs == [{"year": 1980, "circa": True}]
    assert by_id["90002"].nationalities == ["Utopia", "Freedonia"]
    assert by_id["90003"].dobs == [{"from_year": 1958, "to_year": 1962}]


# ---------------------------------------------------------------------------
# UN
# ---------------------------------------------------------------------------


def test_un_individual_with_script_aliases_dobs_and_documents():
    records = parse_un_individuals(UN_XML)
    assert [r.source_entry_id for r in records] == ["8800001", "8800002"]

    anna = records[0]
    assert anna.primary_name == "ANNA PETROVNA SIDOROVA"
    names = {n["name"]: n for n in anna.names}
    assert names["Анна Петровна Сидорова"]["script"] == "original"
    assert names["Anya Sidorova"]["quality"] == "good"
    assert names["The Accountant"]["quality"] == "low"
    assert anna.dobs == [{"date": "1975-06-02"}, {"from_year": 1974, "to_year": 1976}]
    assert anna.pobs == ["Metropolis, Utopia"]
    assert anna.nationalities == ["Utopia"]
    assert anna.documents == [
        {"type": "passport", "number": "P0000001", "country": "Utopia"}
    ]
    assert anna.program == "DPRK"
    assert anna.gender == "Female"


def test_un_single_name_and_empty_blocks():
    karim = parse_un_individuals(UN_XML)[1]
    assert karim.primary_name == "KARIM"
    assert karim.names == [{"name": "KARIM", "kind": "primary"}]
    assert karim.dobs == [{"year": 1968, "circa": True}]
    assert karim.pobs == [] and karim.documents == []


# ---------------------------------------------------------------------------
# EU
# ---------------------------------------------------------------------------


def test_eu_persons_only_with_language_tagged_aliases():
    records = parse_eu_persons(EU_XML)
    assert [r.source_entry_id for r in records] == ["7001"]

    lukas = records[0]
    assert lukas.primary_name == "Lukas Brenner"
    names = {n["name"]: n for n in lukas.names}
    assert names["Лукас Бреннер"]["language"] == "RU"
    assert names["Luke Brenner"]["quality"] == "low"
    assert {"date": "1966-11-30"} in lukas.dobs
    assert {"year": 1967, "circa": True} in lukas.dobs
    assert lukas.pobs == ["Oldtown, UT"]
    assert lukas.nationalities == ["UT"]
    assert {
        "type": "passport",
        "number": "UT1234567",
        "country": "UT",
    } in lukas.documents
    assert {
        "type": "national_id",
        "number": "987-65-4321",
        "country": "UT",
    } in lukas.documents
    assert lukas.program == "UTP"


# ---------------------------------------------------------------------------
# UK
# ---------------------------------------------------------------------------


def test_uk_rows_group_into_one_individual():
    records = parse_uk_individuals(UK_CSV)
    assert [r.source_entry_id for r in records] == ["99001"]

    tobias = records[0]
    assert tobias.primary_name == "Tobias Ezra QUILL"
    names = {n["name"]: n for n in tobias.names}
    assert names["Toby QUILLE"]["kind"] == "aka"
    assert names["Тобиас Квилл"]["script"] == "original"
    assert tobias.dobs == [{"year": 1959}]
    assert tobias.pobs == ["Harbourview, Utopia"]
    assert tobias.nationalities == ["Utopia", "Freedonia"]
    assert {"type": "passport", "number": "55501", "country": None} in tobias.documents
    assert {"type": "passport", "number": "55502", "country": None} in tobias.documents
    assert tobias.program == "Utopia"


# ---------------------------------------------------------------------------
# Storage with provenance
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'persons.db'}")
    Base.metadata.create_all(engine)
    sess = Session(bind=engine)
    yield sess
    sess.close()
    engine.dispose()


def test_store_records_keeps_snapshot_and_entry_provenance(db):
    snapshot = record_snapshot(
        db, source="un_consolidated", content=UN_XML, record_count=2
    )
    stored = store_records(db, snapshot, parse_un_individuals(UN_XML))

    assert len(stored) == 2
    row = db.query(WatchlistRecord).filter_by(source_entry_id="8800001").one()
    assert row.snapshot_id == snapshot.id
    assert row.source == "un_consolidated"
    assert row.primary_name == "ANNA PETROVNA SIDOROVA"
    assert row.dobs[0] == {"date": "1975-06-02"}
    assert row.documents[0]["number"] == "P0000001"
