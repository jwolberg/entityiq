"""Person-name normalization and blocking keys (IS2-T2, ticket 0037).

Deterministic for pinned library versions (ADR-0003). Any change to the rules
below must bump NORMALIZER_VERSION: decisions record it, and replay compares
against it.

Pipeline for a name:
  1. transliterate to ASCII (anyascii), which handles Cyrillic, Arabic, CJK, etc.
  2. split CamelCase (CJK transliterations come out as "XiJinPing")
  3. lowercase, turn punctuation and hyphens into spaces
  4. drop particles (al, bin, ibn, van, von, de, ...)

Keys (strings "kind:value") per token:
  - mp:<Metaphone>   phonetic, survives spelling variants (Müller/Mueller)
  - sk:<skeleton>    consonant skeleton with common romanization folds
                     (y→i, ks→x, kh→h, ph→f, double letters collapsed)
Single-letter tokens (initials) produce no keys; the other tokens carry the
match. A name's keys are the union over its tokens.
"""

from __future__ import annotations

import re

import jellyfish
from anyascii import anyascii

NORMALIZER_VERSION = "n2"  # n2: nickname-canonical keys (review finding)

PARTICLES = frozenset(
    {
        "al",
        "el",
        "bin",
        "ibn",
        "bint",
        "ben",
        "binti",
        "van",
        "von",
        "der",
        "den",
        "de",
        "del",
        "della",
        "di",
        "da",
        "dos",
        "das",
        "du",
        "la",
        "le",
        "mac",
        "st",
    }
)

_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def tokens(name: str | None) -> list[str]:
    """Normalized name tokens, in order, particles removed."""
    if not name or not name.strip():
        return []
    ascii_name = anyascii(name)
    ascii_name = _CAMEL.sub(" ", ascii_name)
    parts = _NON_ALNUM.sub(" ", ascii_name.lower()).split()
    return [p for p in parts if p not in PARTICLES]


def _skeleton(token: str) -> str:
    t = token.replace("ks", "x").replace("kh", "h").replace("ph", "f")
    t = t.replace("y", "i").replace("j", "i").replace("q", "k").replace("c", "k")
    t = re.sub(r"(.)\1+", r"\1", t)
    consonants = re.sub(r"[aeiou]", "", t)
    # Keep a leading vowel so vowel-initial names don't collapse to a
    # single-letter key ("iulia" -> "il", not "i").
    return (t[0] + consonants if t and t[0] in "aeiou" else consonants) or t


def token_keys(token: str) -> set[str]:
    """Phonetic + skeleton keys; a nickname also carries its canonical name's
    keys, so "Bill X" and "William X" share a given-name key and match on
    every token (full matches are never capped at blocking)."""
    if len(token) < 2:
        return set()
    keys = {f"mp:{jellyfish.metaphone(token)}", f"sk:{_skeleton(token)}"}
    canonical = canonical_given(token)
    if canonical != token:
        keys |= {f"mp:{jellyfish.metaphone(canonical)}", f"sk:{_skeleton(canonical)}"}
    return keys


def name_keys(name: str | None) -> set[str]:
    keys: set[str] = set()
    for tok in tokens(name):
        keys |= token_keys(tok)
    return keys


# Common given-name hypocoristics → canonical form (used by scoring).
NICKNAMES: dict[str, str] = {
    "bill": "william",
    "will": "william",
    "billy": "william",
    "bob": "robert",
    "rob": "robert",
    "bobby": "robert",
    "sasha": "aleksandr",
    "alex": "aleksandr",
    "alexander": "aleksandr",
    "kate": "katherine",
    "katie": "katherine",
    "kathy": "katherine",
    "catherine": "katherine",
    "dick": "richard",
    "rick": "richard",
    "rich": "richard",
    "peggy": "margaret",
    "maggie": "margaret",
    "meg": "margaret",
    "paco": "francisco",
    "pancho": "francisco",
    "liz": "elizabeth",
    "beth": "elizabeth",
    "betty": "elizabeth",
    "beppe": "giuseppe",
    "pino": "giuseppe",
    "hans": "johannes",
    "jan": "johannes",
    "johann": "johannes",
    "jim": "james",
    "jimmy": "james",
    "mike": "michael",
    "mick": "michael",
    "tom": "thomas",
    "tommy": "thomas",
    "dave": "david",
    "davy": "david",
    "joe": "joseph",
    "jose": "joseph",
    "pepe": "joseph",
    "misha": "mikhail",
    "volodya": "vladimir",
    "dima": "dmitri",
    "kolya": "nikolai",
    "vanya": "ivan",
    "petya": "pyotr",
    "seryozha": "sergei",
}


def canonical_given(token: str) -> str:
    return NICKNAMES.get(token, token)
