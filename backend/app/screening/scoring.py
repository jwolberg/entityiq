"""Screening scoring terms and versioned rule config (IS2-T3, ticket 0038).

Scores come from named terms only (F7). Each term carries its weight from the
rule version and names the subject and record fields it compared, so it can
cite both claims. Weights and thresholds are data (``rule_version.config``),
not code (F13).

Name comparison yields one best term per candidate. Corroboration (ID, full
DOB) outweighs name similarity (F8). Conflicts are emitted as their own
negative terms next to the matches, never averaged away (F10).
"""

from __future__ import annotations

import re

from app.lists.countries import to_iso2
from app.screening.names import canonical_given, token_keys, tokens

TERM_CATALOG = (
    "name_exact_normalized",
    "name_token_reordered",
    "name_translit_equivalent",
    "name_initials_compatible",
    "name_partial_overlap",
    "dob_full_match",
    "dob_year_match",
    "dob_within_range",
    "dob_conflict",
    "id_number_match",
    "id_number_conflict",
    "nationality_match",
    "nationality_conflict",
    "pob_match",
    "common_name_penalty",
)

NAME_TERMS = TERM_CATALOG[:5]
CORROBORATING = frozenset(
    {"dob_full_match", "dob_year_match", "dob_within_range", "id_number_match"}
)

DEFAULT_RULE: dict = {
    "version": 1,
    "weights": {
        "name_exact_normalized": 0.5,
        "name_token_reordered": 0.45,
        "name_translit_equivalent": 0.4,
        "name_initials_compatible": 0.35,
        "name_partial_overlap": 0.2,
        "dob_full_match": 0.3,
        "dob_year_match": 0.15,
        "dob_within_range": 0.1,
        "dob_conflict": -0.35,
        "id_number_match": 0.6,
        "id_number_conflict": -0.3,
        "nationality_match": 0.1,
        "nationality_conflict": -0.1,
        "pob_match": 0.05,
        "common_name_penalty": -0.15,
    },
    # score < clear_below -> CLEAR candidate; score >= match_at -> MATCH.
    "thresholds": {"clear_below": 0.35, "match_at": 0.9},
    # Full-name matches across the lists at or above this count = common name.
    "common_name_threshold": 10,
}


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


def _tok_match(a: str, b: str) -> str | None:
    """'full' if the tokens are equivalent, 'initial' if one is an initial."""
    if len(a) == 1 or len(b) == 1:
        long_, short = (b, a) if len(a) == 1 else (a, b)
        return "initial" if long_.startswith(short) else None
    if a == b or canonical_given(a) == canonical_given(b):
        return "full"
    return "full" if token_keys(a) & token_keys(b) else None


def _compare(subject_tokens: list[str], record_tokens: list[str]) -> str | None:
    if not subject_tokens or not record_tokens:
        return None
    if subject_tokens == record_tokens:
        return "name_exact_normalized"
    if sorted(subject_tokens) == sorted(record_tokens):
        return "name_token_reordered"
    unused = list(record_tokens)
    matched = 0
    initials = False
    for st in subject_tokens:
        best = None
        for rt in unused:
            kind = _tok_match(st, rt)
            if kind == "full":
                best = (rt, kind)
                break
            if kind == "initial" and best is None:
                best = (rt, kind)
        if best is not None:
            unused.remove(best[0])
            matched += 1
            initials = initials or best[1] == "initial"
    all_matched = matched == len(subject_tokens)
    # One unmatched record token is allowed (a patronymic or middle name).
    if all_matched and len(subject_tokens) >= 2 and len(unused) <= 1:
        return "name_initials_compatible" if initials else "name_translit_equivalent"
    return "name_partial_overlap" if matched else None


def compare_names(subject_name: str, record_names: list[dict]) -> dict | None:
    """Best name term across the record's name variants."""
    st = tokens(subject_name)
    best: dict | None = None
    for variant in record_names:
        term = _compare(st, tokens(variant["name"]))
        if term is None:
            continue
        if best is None or NAME_TERMS.index(term) < NAME_TERMS.index(best["name"]):
            best = {"name": term, "record_name": variant["name"]}
    return best


# ---------------------------------------------------------------------------
# DOB, IDs, nationality, place of birth
# ---------------------------------------------------------------------------


def _parse_dob(value: str | None) -> tuple[int, int | None, int | None] | None:
    if not value:
        return None
    m = re.fullmatch(r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", value.strip())
    if not m:
        return None
    return (
        int(m.group(1)),
        (int(m.group(2)) if m.group(2) else None),
        (int(m.group(3)) if m.group(3) else None),
    )


def _dob_term(subject_dob: str | None, record_dobs: list[dict]) -> str | None:
    parsed = _parse_dob(subject_dob)
    if parsed is None or not record_dobs:
        return None
    y, m, d = parsed
    best = None
    rank = ("dob_full_match", "dob_year_match", "dob_within_range")
    for dob in record_dobs:
        term = None
        if "date" in dob:
            ry, rm, rd = _parse_dob(dob["date"]) or (None, None, None)
            if (ry, rm, rd) == (y, m, d):
                term = "dob_full_match"
            elif d is None and ry == y and (m is None or rm == m):
                term = "dob_year_match"
        elif "from_year" in dob:
            if dob["from_year"] <= y <= dob["to_year"]:
                term = "dob_within_range"
        elif "year" in dob:
            if dob.get("circa"):
                if abs(dob["year"] - y) <= 2:
                    term = "dob_within_range"
            elif dob["year"] == y and (
                dob.get("month") is None or m is None or dob["month"] == m
            ):
                term = "dob_year_match"
        if term and (best is None or rank.index(term) < rank.index(best)):
            best = term
    return best or "dob_conflict"


def _norm_doc(number: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", number or "").upper()


def _id_terms(subject_docs: list[dict], record_docs: list[dict]) -> list[str]:
    match = conflict = False
    for sd in subject_docs or []:
        for rd in record_docs or []:
            if (sd.get("type") or "") != (rd.get("type") or ""):
                continue
            if _norm_doc(sd.get("number")) == _norm_doc(rd.get("number")):
                match = True
            elif sd.get("country") and sd.get("country") == rd.get("country"):
                conflict = True
    if match:
        return ["id_number_match"]
    return ["id_number_conflict"] if conflict else []


def _country(value: str | None) -> str | None:
    if not value:
        return None
    return to_iso2(value) or value.strip().lower()


def _nationality_term(subject_nat: str | None, record_nats: list[str]) -> str | None:
    s = _country(subject_nat)
    rs = {_country(n) for n in record_nats or [] if n}
    if not s or not rs:
        return None
    return "nationality_match" if s in rs else "nationality_conflict"


def _pob_term(subject_pob: str | None, record_pobs: list[str]) -> str | None:
    want = set(tokens(subject_pob))
    if not want:
        return None
    for pob in record_pobs or []:
        if want <= set(tokens(pob)):
            return "pob_match"
    return None


# ---------------------------------------------------------------------------
# Pair scoring
# ---------------------------------------------------------------------------


def _term(
    rule: dict,
    name: str,
    subject_field: str,
    record_field: str,
    subject_value,
    record_value,
) -> dict:
    return {
        "name": name,
        "weight": rule["weights"][name],
        "subject_field": subject_field,
        "record_field": record_field,
        "subject_value": subject_value,
        "record_value": record_value,
    }


def score_pair(
    subject: dict,
    record: dict,
    rule: dict,
    *,
    name_frequency: int | None = None,
) -> tuple[float, list[dict]]:
    """Score one subject against one watchlist record under a rule version."""
    terms: list[dict] = []

    best_name: dict | None = None
    for name in [subject.get("name"), *subject.get("aliases", [])]:
        if not name:
            continue
        found = compare_names(name, record.get("names", []))
        if found and (
            best_name is None
            or NAME_TERMS.index(found["name"]) < NAME_TERMS.index(best_name["name"])
        ):
            best_name = {**found, "subject_name": name}
    if best_name:
        terms.append(
            _term(
                rule,
                best_name["name"],
                "name",
                "names",
                best_name["subject_name"],
                best_name["record_name"],
            )
        )

    dob = _dob_term(subject.get("dob"), record.get("dobs", []))
    if dob:
        terms.append(
            _term(rule, dob, "dob", "dobs", subject.get("dob"), record.get("dobs"))
        )
    for id_term in _id_terms(subject.get("documents", []), record.get("documents", [])):
        terms.append(
            _term(
                rule,
                id_term,
                "documents",
                "documents",
                subject.get("documents"),
                record.get("documents"),
            )
        )
    nat = _nationality_term(subject.get("nationality"), record.get("nationalities", []))
    if nat:
        terms.append(
            _term(
                rule,
                nat,
                "nationality",
                "nationalities",
                subject.get("nationality"),
                record.get("nationalities"),
            )
        )
    pob = _pob_term(subject.get("pob"), record.get("pobs", []))
    if pob:
        terms.append(
            _term(rule, pob, "pob", "pobs", subject.get("pob"), record.get("pobs"))
        )

    corroborated = any(t["name"] in CORROBORATING for t in terms)
    if (
        best_name
        and name_frequency is not None
        and name_frequency >= rule["common_name_threshold"]
        and not corroborated
    ):
        terms.append(
            _term(
                rule,
                "common_name_penalty",
                "name",
                "names",
                best_name["subject_name"],
                name_frequency,
            )
        )

    score = round(sum(t["weight"] for t in terms), 6)
    return score, terms
